from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.mysql_client import DatabaseConfigError, MySQLClient


EXPECTED_TABLES = (
    "products",
    "product_snapshots",
    "product_reviews",
    "product_review_insights",
    "keywords",
    "keyword_rank_snapshots",
    "product_scores",
    "crawl_jobs",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def main() -> int:
    results: list[CheckResult] = []
    client: MySQLClient | None = None

    def run(name: str, func: Callable[[], str]) -> None:
        try:
            results.append(CheckResult(name=name, ok=True, detail=func()))
        except Exception as exc:  # noqa: BLE001 - smoke checks should report all failures.
            results.append(CheckResult(name=name, ok=False, detail=str(exc)))

    def check_config() -> str:
        nonlocal client
        client = MySQLClient()
        cfg = client.config
        return f"{cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}"

    def check_connection() -> str:
        db = _require_client(client)
        with db.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version")
                row = cursor.fetchone()
        return f"MySQL {row['version']}"

    def check_tables() -> str:
        db = _require_client(client)
        with db.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name AS table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s
                    """,
                    (db.config.database,),
                )
                tables = {_first_value(row, "table_name", "TABLE_NAME") for row in cursor.fetchall()}
        missing = [table for table in EXPECTED_TABLES if table not in tables]
        if missing:
            raise RuntimeError("缺少数据表: " + "、".join(missing) + "。请先运行 python scripts/init_mysql.py")
        return f"核心表完整: {len(EXPECTED_TABLES)} 张"

    def check_table_counts() -> str:
        db = _require_client(client)
        counts: dict[str, int] = {}
        with db.connect() as conn:
            with conn.cursor() as cursor:
                for table in EXPECTED_TABLES:
                    cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
                    counts[table] = int(cursor.fetchone()["count"])
        return "；".join(f"{name}={count}" for name, count in counts.items())

    def check_parser() -> str:
        from services.ingestion import count_rejected_reasons, parse_html_files

        html_files = sorted((ROOT / "html").glob("**/*.html"))
        if not html_files:
            return "未找到本地 HTML，跳过解析器样本检查"
        valid, rejected = parse_html_files([html_files[0]], keyword="smoke_check", require_complete=True)
        rejected_reasons = count_rejected_reasons(rejected)
        reason_text = "，".join(f"{reason}:{count}" for reason, count in rejected_reasons.items()) or "无"
        return f"{html_files[0].name}: 有效 {len(valid)}，过滤 {len(rejected)}，过滤原因 {reason_text}"

    def check_review_html_parser() -> str:
        from parsers.amazon_review_parser import parse_amazon_review_content

        sample_html = """
        <html><body>
          <link rel="canonical" href="https://www.amazon.com/product-reviews/B010NE2XPC">
          <div data-hook="review" id="customer_review-R1TESTABC">
            <span class="a-profile-name">Test Reviewer</span>
            <i data-hook="review-star-rating"><span class="a-icon-alt">2.0 out of 5 stars</span></i>
            <a data-hook="review-title" href="/gp/customer-reviews/R1TESTABC">
              <span>2.0 out of 5 stars</span><span>Package leaked</span>
            </a>
            <span data-hook="review-date">Reviewed in the United States on May 2, 2026</span>
            <span data-hook="avp-badge">Verified Purchase</span>
            <span data-hook="review-body"><span>The package arrived damaged and leaking.</span></span>
          </div>
        </body></html>
        """
        result = parse_amazon_review_content(sample_html, default_asin="B010NE2XPC", source_file="smoke")
        if len(result.records) != 1 or result.rejected_records:
            raise RuntimeError(f"评论 HTML 解析异常: 有效 {len(result.records)}，过滤 {len(result.rejected_records)}")
        record = result.records[0]
        return f"{record.asin}: {record.rating} 星，标题 {record.title}"

    def check_review_weight_model() -> str:
        from services.review_import import NEGATIVE_STAR_WEIGHTS, PAIN_THEMES, _detect_weighted_themes

        rows = [
            {"rating": 1, "title": "Leaking package", "body": "Package leaked and quality is poor", "helpful_votes": 0},
            {"rating": 2, "title": "Poor quality", "body": "Cheap quality and broken part", "helpful_votes": 10},
            {"rating": 3, "title": "Smell issue", "body": "Strong chemical smell", "helpful_votes": 0},
        ]
        points = _detect_weighted_themes(rows, PAIN_THEMES, NEGATIVE_STAR_WEIGHTS)
        if not points or "weighted_score" not in points[0]:
            raise RuntimeError("评论权重模型未生成 weighted_score")
        return f"已生成 {len(points)} 个加权痛点，最高权重 {points[0]['weighted_score']}"

    def check_services() -> str:
        from services.keyword_opportunities import fetch_keyword_opportunities
        from services.product_pool import fetch_product_pool
        from services.recommendations import fetch_top_recommendations
        from services.review_insights import fetch_review_insight_list
        from services.task_center import fetch_task_jobs

        db = _require_client(client)
        recommendations = fetch_top_recommendations(limit=5, client=db)
        products = fetch_product_pool(limit=5, client=db)
        tasks = fetch_task_jobs(limit=5, client=db)
        keywords = fetch_keyword_opportunities(limit=5, client=db)
        reviews = fetch_review_insight_list(limit=5, client=db)
        return (
            f"推荐榜单 {len(recommendations)} 条；商品池 {len(products)} 条；"
            f"任务 {len(tasks)} 条；关键词机会 {len(keywords)} 条；评论洞察 {len(reviews)} 条"
        )

    run("数据库配置", check_config)
    run("MySQL 连接", check_connection)
    run("核心表结构", check_tables)
    run("表数据概览", check_table_counts)
    run("HTML 解析器", check_parser)
    run("评论 HTML 解析器", check_review_html_parser)
    run("评论权重模型", check_review_weight_model)
    run("服务层查询", check_services)

    print("Amazon 选品系统当前版本健康检查")
    print("=" * 40)
    for result in results:
        mark = "OK" if result.ok else "FAIL"
        print(f"[{mark}] {result.name}: {result.detail}")

    failed = [result for result in results if not result.ok]
    if failed:
        print("=" * 40)
        print("健康检查未通过，请优先处理 FAIL 项。")
        return 1
    print("=" * 40)
    print("健康检查通过，当前版本核心链路可继续使用。")
    return 0


def _require_client(client: MySQLClient | None) -> MySQLClient:
    if client is None:
        raise DatabaseConfigError("数据库配置未初始化")
    return client


def _first_value(row: dict, *keys: str) -> str:
    for key in keys:
        if key in row:
            return str(row[key])
    raise KeyError(keys[0])


if __name__ == "__main__":
    raise SystemExit(main())
