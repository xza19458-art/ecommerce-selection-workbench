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
    "product_scores",
    "keywords",
    "keyword_rank_snapshots",
    "keyword_tracking_tasks",
    "keyword_idea_runs",
    "keyword_ideas",
    "keyword_serp_snapshots",
    "crawl_jobs",
    "product_reviews",
    "product_review_insights",
    "product_bsr_snapshots",
    "product_offer_snapshots",
    "product_physical_specs",
    "product_variants",
    "product_metric_inputs",
    "product_estimates",
    "research_projects",
    "research_project_products",
    "research_project_keywords",
    "research_project_notes",
    "research_project_niches",
    "research_project_report_versions",
    "market_niches",
    "niche_keywords",
    "niche_products",
    "niche_snapshots",
    "translation_cache",
)

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def main() -> int:
    results: list[CheckResult] = []
    client: MySQLClient | None = None

    def run(name: str, func: Callable[[], str | tuple[str, str]]) -> None:
        try:
            outcome = func()
            if isinstance(outcome, tuple):
                status, detail = outcome
            else:
                status, detail = PASS, outcome
            results.append(CheckResult(name=name, status=status, detail=detail))
        except Exception as exc:  # noqa: BLE001 - report all independent checks.
            results.append(CheckResult(name=name, status=FAIL, detail=str(exc)))

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
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (db.config.database,),
                )
                tables = {_first_value(row, "table_name", "TABLE_NAME") for row in cursor.fetchall()}
        missing = [table for table in EXPECTED_TABLES if table not in tables]
        if missing:
            raise RuntimeError("缺少业务表: " + "、".join(missing) + "。请先运行 python scripts/init_mysql.py")
        return f"当前模块所需 {len(EXPECTED_TABLES)} 张表完整"

    def check_migration_readiness() -> str:
        from database.migration_runner import get_database_readiness

        report = get_database_readiness(_require_client(client))
        if not report["ready"]:
            raise RuntimeError(report["message"])
        return (
            f"迁移台账 {report['migration_count']} 项："
            f"基线 {report['baseline_count']}，增量 {report['applied_count']}"
        )

    def check_table_counts() -> str:
        db = _require_client(client)
        counts: dict[str, int] = {}
        with db.connect() as conn:
            with conn.cursor() as cursor:
                for table in EXPECTED_TABLES:
                    cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
                    counts[table] = int(cursor.fetchone()["count"])
        important = (
            "products",
            "product_snapshots",
            "product_scores",
            "keyword_rank_snapshots",
            "keyword_ideas",
            "product_reviews",
            "research_projects",
            "research_project_report_versions",
            "market_niches",
        )
        return "；".join(f"{name}={counts[name]}" for name in important)

    def check_data_integrity() -> str | tuple[str, str]:
        db = _require_client(client)
        with db.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS total FROM products")
                product_count = int(cursor.fetchone()["total"])
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM (
                      SELECT product_id, score_date
                      FROM product_scores
                      WHERE keyword_id IS NULL
                      GROUP BY product_id, score_date
                      HAVING COUNT(*) > 1
                    ) duplicate_scores
                    """
                )
                null_score_duplicates = int(cursor.fetchone()["total"])
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM crawl_jobs
                    WHERE status = '运行中' AND started_at < DATE_SUB(NOW(), INTERVAL 6 HOUR)
                    """
                )
                stale_jobs = int(cursor.fetchone()["total"])

        from services.product_pool import fetch_product_pool_page

        pool = fetch_product_pool_page(limit=1, client=db)
        warnings: list[str] = []
        if int(pool["total"]) != product_count:
            raise RuntimeError(f"商品池总数 {pool['total']} 与 products {product_count} 不一致")
        if null_score_duplicates:
            warnings.append(f"旧版空关键词评分有 {null_score_duplicates} 组重复记录；查询已去重，待 schema 迁移时清理")
        if stale_jobs:
            warnings.append(f"任务中心有 {stale_jobs} 条超过 6 小时仍为运行中的任务")
        detail = f"商品池唯一商品 {product_count} 条"
        if warnings:
            return WARN, detail + "；" + "；".join(warnings)
        return detail

    def check_search_parser() -> str | tuple[str, str]:
        from services.ingestion import count_rejected_reasons, parse_html_files

        search_files = [
            path
            for path in sorted((ROOT / "html").glob("**/*.html"), reverse=True)
            if "_details" not in path.parts and "_blocked" not in path.parts
        ]
        if not search_files:
            return WARN, "没有本地搜索页 HTML；解析器单测已通过，但无法做真实样本冒烟"

        attempts: list[str] = []
        for path in search_files[:30]:
            valid, rejected = parse_html_files([path], keyword="smoke_check", require_complete=True)
            if valid:
                return f"真实文件 {path.relative_to(ROOT)}：有效 {len(valid)}，过滤 {len(rejected)}"
            reasons = count_rejected_reasons(rejected)
            attempts.extend(reasons)
        reason_text = "、".join(sorted(set(attempts))) or "无商品节点/阻断页"
        return WARN, f"检查前 {min(30, len(search_files))} 个搜索 HTML 均无有效商品：{reason_text}"

    def check_detail_evidence_files() -> str | tuple[str, str]:
        db = _require_client(client)
        with db.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT asin, detail_source_file FROM products WHERE detail_source_file IS NOT NULL"
                )
                rows = cursor.fetchall()
        missing = []
        for row in rows:
            source = Path(str(row.get("detail_source_file") or ""))
            path = source if source.is_absolute() else ROOT / source
            if not path.is_file():
                missing.append(str(row.get("asin") or "?"))
        if missing:
            return WARN, f"{len(rows)} 个商品记录详情来源，其中 {len(missing)} 个文件缺失：{','.join(missing[:8])}"
        return f"{len(rows)} 个详情来源记录均可定位到本地 HTML"

    def check_review_parser_fixture() -> str:
        from parsers.amazon_review_parser import parse_amazon_review_content

        fixture = """
        <html><body><link rel="canonical" href="https://www.amazon.com/product-reviews/B010NE2XPC">
          <div data-hook="review" id="customer_review-R1TESTABC">
            <i data-hook="review-star-rating"><span class="a-icon-alt">2.0 out of 5 stars</span></i>
            <a data-hook="review-title" href="/gp/customer-reviews/R1TESTABC"><span>Package leaked</span></a>
            <span data-hook="review-body"><span>The package arrived damaged and leaking.</span></span>
          </div></body></html>
        """
        parsed = parse_amazon_review_content(fixture, default_asin="B010NE2XPC", source_file="unit_fixture")
        if len(parsed.records) != 1 or parsed.rejected_records:
            raise RuntimeError(f"评论解析夹具异常: 有效 {len(parsed.records)}，过滤 {len(parsed.rejected_records)}")
        return "内联单元夹具解析 1 条；这里只证明解析器可运行，不代表已有真实评论 HTML"

    def check_review_evidence() -> str | tuple[str, str]:
        from services.review_insights import fetch_review_insight_list

        db = _require_client(client)
        review_html = sorted((ROOT / "reviews").glob("**/*.html")) if (ROOT / "reviews").exists() else []
        insights = fetch_review_insight_list(limit=500, client=db)
        untraceable = [row for row in insights if row.get("evidence", {}).get("status") != "可追溯"]
        details = [f"真实评论 HTML={len(review_html)}", f"洞察={len(insights)}"]
        if untraceable:
            details.append(f"不可完整核验={len(untraceable)}")
        if not review_html or untraceable:
            return WARN, "；".join(details) + "；评论模块当前不能宣称高置信完成"
        return "；".join(details)

    def check_warehouse_freshness() -> str | tuple[str, str]:
        from services.analytics_warehouse import get_warehouse_status, query_warehouse

        db = _require_client(client)
        status = get_warehouse_status(client=db)
        if status["status"] != "current":
            return WARN, status["message"]
        with db.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS total FROM products")
                mysql_products = int(cursor.fetchone()["total"])
                cursor.execute("SELECT COUNT(*) AS total FROM product_snapshots")
                mysql_snapshots = int(cursor.fetchone()["total"])
        try:
            warehouse_products = int(query_warehouse("SELECT COUNT(*) AS total FROM dim_products")[0]["total"])
            warehouse_snapshots = int(
                query_warehouse("SELECT COUNT(*) AS total FROM fact_product_snapshots")[0]["total"]
            )
        except Exception as exc:  # optional acceleration layer
            return WARN, f"分析仓库不可读，查询会回退 MySQL：{exc}"
        if (warehouse_products, warehouse_snapshots) != (mysql_products, mysql_snapshots):
            return WARN, (
                f"仓库副本落后：products {warehouse_products}/{mysql_products}，"
                f"snapshots {warehouse_snapshots}/{mysql_snapshots}；请执行仓库同步"
            )
        return (
            f"14 张副本来源标记一致且可读：products={mysql_products}，"
            f"snapshots={mysql_snapshots}；最近同步 {status.get('last_synced_at') or '未知'}"
        )

    def check_services() -> str:
        from services.keyword_opportunities import fetch_keyword_opportunities
        from services.recommendations import fetch_recommendations_page
        from services.task_center import fetch_task_jobs

        db = _require_client(client)
        recommendations = fetch_recommendations_page(limit=5, client=db)
        tasks = fetch_task_jobs(limit=5, client=db)
        keywords = fetch_keyword_opportunities(limit=5, client=db)
        return (
            f"推荐上下文总数 {recommendations['total']}；任务样本 {len(tasks)}；"
            f"关键词机会样本 {len(keywords)}"
        )

    run("数据库配置", check_config)
    run("MySQL 连接", check_connection)
    run("业务表结构", check_tables)
    run("迁移与就绪状态", check_migration_readiness)
    run("表数据概览", check_table_counts)
    run("数据一致性", check_data_integrity)
    run("搜索 HTML 真实样本", check_search_parser)
    run("详情 HTML 证据文件", check_detail_evidence_files)
    run("评论解析器单元夹具", check_review_parser_fixture)
    run("评论真实证据", check_review_evidence)
    run("分析仓库新鲜度", check_warehouse_freshness)
    run("服务层查询", check_services)

    print("Amazon 选品系统当前版本健康检查")
    print("=" * 56)
    for result in results:
        print(f"[{result.status}] {result.name}: {result.detail}")

    failures = [result for result in results if result.status == FAIL]
    warnings = [result for result in results if result.status == WARN]
    print("=" * 56)
    if failures:
        print(f"健康检查失败：{len(failures)} 个 FAIL，{len(warnings)} 个 WARN。")
        return 1
    if warnings:
        print(f"核心链路可用，但有 {len(warnings)} 个 WARN；不得把警告模块描述为已完成。")
        return 0
    print("健康检查通过，当前检查范围内未发现警告。")
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
