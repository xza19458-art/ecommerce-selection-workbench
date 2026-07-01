"""Keyword opportunity aggregation backed by warehouse or MySQL tables."""

from __future__ import annotations

import logging
from typing import Any

from database.mysql_client import MySQLClient
from services.analytics_warehouse import query_warehouse

logger = logging.getLogger(__name__)


def fetch_keyword_opportunities(
    limit: int = 100,
    *,
    keyword: str | None = None,
    min_products: int | None = None,
    client: MySQLClient | None = None,
    prefer_warehouse: bool = True,
) -> list[dict[str, Any]]:
    """Aggregate keyword-level demand, competition, and opportunity signals."""
    return fetch_keyword_opportunities_page(
        limit=limit,
        keyword=keyword,
        min_products=min_products,
        client=client,
        prefer_warehouse=prefer_warehouse,
    )["rows"]


def fetch_keyword_opportunities_page(
    limit: int = 100,
    *,
    offset: int = 0,
    keyword: str | None = None,
    min_products: int | None = None,
    client: MySQLClient | None = None,
    prefer_warehouse: bool = True,
) -> dict[str, Any]:
    """Aggregate keyword opportunities with true pagination metadata."""
    if prefer_warehouse and client is None:
        try:
            page = _fetch_keyword_opportunities_page_from_warehouse(
                limit=limit,
                offset=offset,
                keyword=keyword,
                min_products=min_products,
            )
            page["rows"] = [_enrich_row(_normalize_row(row)) for row in page["rows"]]
            return page
        except Exception:
            # The warehouse is an optional acceleration layer: when it has not
            # been synced yet we must keep the MySQL-backed UI path usable. But
            # a swallowed exception here previously hid real query bugs (the
            # warehouse path silently never ran). Log before falling back so a
            # broken warehouse query is visible instead of silently degrading.
            logger.warning(
                "Warehouse keyword-opportunity query failed; falling back to MySQL.",
                exc_info=True,
            )

    page = _fetch_keyword_opportunities_page_from_mysql(
        limit=limit,
        offset=offset,
        keyword=keyword,
        min_products=min_products,
        client=client,
    )
    page["rows"] = [_enrich_row(_normalize_row(row)) for row in page["rows"]]
    return page


def _fetch_keyword_opportunities_from_mysql(
    limit: int,
    *,
    keyword: str | None,
    min_products: int | None,
    client: MySQLClient | None,
) -> list[dict[str, Any]]:
    return _fetch_keyword_opportunities_page_from_mysql(
        limit=limit,
        offset=0,
        keyword=keyword,
        min_products=min_products,
        client=client,
    )["rows"]


def _fetch_keyword_opportunities_page_from_mysql(
    limit: int,
    *,
    offset: int,
    keyword: str | None,
    min_products: int | None,
    client: MySQLClient | None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    where_sql, where_params = _build_filters(keyword=keyword)
    having_sql, having_params = _build_having(min_products=min_products)
    params = where_params + having_params
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    ctes = _keyword_opportunity_mysql_ctes()
    select_sql = _keyword_opportunity_mysql_select(where_sql, having_sql)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                {ctes}
                SELECT COUNT(*) AS total
                FROM ({select_sql}) AS q
                """,
                params,
            )
            total_row = cursor.fetchone() or {}
            total = int(total_row.get("total") or 0)
            cursor.execute(
                f"""
                {ctes}
                {select_sql}
                ORDER BY avg_total_score DESC, total_monthly_bought DESC, product_count DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = cursor.fetchall()

    return {"rows": rows, "total": total, "limit": limit_value, "offset": offset_value}


def _fetch_keyword_opportunities_from_warehouse(
    limit: int,
    *,
    keyword: str | None,
    min_products: int | None,
) -> list[dict[str, Any]]:
    return _fetch_keyword_opportunities_page_from_warehouse(
        limit=limit,
        offset=0,
        keyword=keyword,
        min_products=min_products,
    )["rows"]


def _fetch_keyword_opportunities_page_from_warehouse(
    limit: int,
    *,
    offset: int,
    keyword: str | None,
    min_products: int | None,
) -> dict[str, Any]:
    where_sql, where_params = _build_filters(keyword=keyword, table_alias="k", placeholder="?")
    having_sql, having_params = _build_having(
        min_products=min_products,
        count_expr="COUNT(DISTINCT krs.product_id)",
        placeholder="?",
    )
    params = where_params + having_params
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    ctes = _keyword_opportunity_warehouse_ctes()
    select_sql = _keyword_opportunity_warehouse_select(where_sql, having_sql)

    total_rows = query_warehouse(
        f"""
        {ctes}
        SELECT COUNT(*) AS total
        FROM ({select_sql}) AS q
        """,
        params,
    )
    total = int((total_rows[0] if total_rows else {}).get("total") or 0)
    rows = query_warehouse(
        f"""
        {ctes}
        {select_sql}
        ORDER BY avg_total_score DESC, total_monthly_bought DESC, product_count DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit_value, offset_value],
    )
    return {"rows": rows, "total": total, "limit": limit_value, "offset": offset_value}


def _keyword_opportunity_mysql_ctes() -> str:
    return """
        WITH latest_snaps AS (
          SELECT snap.*
          FROM product_snapshots snap
          JOIN (
            SELECT product_id, MAX(snapshot_at) AS snapshot_at
            FROM product_snapshots
            GROUP BY product_id
          ) latest
            ON latest.product_id = snap.product_id
           AND latest.snapshot_at = snap.snapshot_at
        ),
        latest_ranks AS (
          SELECT krs.*
          FROM keyword_rank_snapshots krs
          JOIN (
            SELECT keyword_id, product_id, MAX(snapshot_at) AS snapshot_at
            FROM keyword_rank_snapshots
            GROUP BY keyword_id, product_id
          ) latest
            ON latest.keyword_id = krs.keyword_id
           AND latest.product_id = krs.product_id
           AND latest.snapshot_at = krs.snapshot_at
        ),
        latest_scores AS (
          SELECT ps.*
          FROM product_scores ps
          JOIN (
            SELECT product_id, keyword_id, MAX(score_date) AS score_date
            FROM product_scores
            GROUP BY product_id, keyword_id
          ) latest
            ON latest.product_id = ps.product_id
           AND latest.keyword_id = ps.keyword_id
           AND latest.score_date = ps.score_date
        )
    """


def _keyword_opportunity_mysql_select(where_sql: str, having_sql: str) -> str:
    return f"""
        SELECT
          k.id AS keyword_id,
          k.marketplace,
          k.keyword,
          COUNT(DISTINCT p.id) AS product_count,
          AVG(ps.total_score) AS avg_total_score,
          AVG(ps.demand_score) AS avg_demand_score,
          AVG(ps.competition_score) AS avg_competition_score,
          AVG(ps.rating_score) AS avg_rating_score,
          AVG(ps.price_score) AS avg_price_score,
          AVG(ps.rank_score) AS avg_rank_score,
          AVG(snap.price) AS avg_price,
          MIN(snap.price) AS min_price,
          MAX(snap.price) AS max_price,
          AVG(snap.rating) AS avg_rating,
          AVG(snap.review_count) AS avg_review_count,
          SUM(snap.monthly_bought) AS total_monthly_bought,
          AVG(snap.monthly_bought) AS avg_monthly_bought,
          AVG(krs.organic_rank) AS avg_organic_rank,
          SUM(CASE WHEN krs.organic_rank IS NOT NULL AND krs.organic_rank <= 10 THEN 1 ELSE 0 END) AS top10_count,
          SUM(CASE WHEN krs.is_sponsored = 1 OR snap.is_sponsored = 1 THEN 1 ELSE 0 END) AS sponsored_count,
          MAX(snap.snapshot_at) AS latest_snapshot_at
        FROM keywords k
        JOIN latest_ranks krs ON krs.keyword_id = k.id
        JOIN products p ON p.id = krs.product_id
        JOIN latest_snaps snap ON snap.product_id = p.id
        LEFT JOIN latest_scores ps
          ON ps.product_id = p.id
         AND ps.keyword_id = k.id
        {where_sql}
        GROUP BY k.id, k.marketplace, k.keyword
        {having_sql}
    """


def _keyword_opportunity_warehouse_ctes() -> str:
    return """
        WITH latest_snaps AS (
          SELECT *
          FROM fact_product_snapshots
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY product_id
            ORDER BY snapshot_at DESC, snapshot_id DESC
          ) = 1
        ),
        latest_ranks AS (
          SELECT *
          FROM fact_keyword_rank_snapshots
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY keyword_id, product_id
            ORDER BY snapshot_at DESC, rank_snapshot_id DESC
          ) = 1
        ),
        latest_scores AS (
          SELECT *
          FROM fact_product_scores
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY product_id, keyword_id
            ORDER BY score_date DESC, score_id DESC
          ) = 1
        )
    """


def _keyword_opportunity_warehouse_select(where_sql: str, having_sql: str) -> str:
    return f"""
        SELECT
          k.keyword_id,
          k.marketplace,
          k.keyword,
          COUNT(DISTINCT krs.product_id) AS product_count,
          AVG(ps.total_score) AS avg_total_score,
          AVG(ps.demand_score) AS avg_demand_score,
          AVG(ps.competition_score) AS avg_competition_score,
          AVG(ps.rating_score) AS avg_rating_score,
          AVG(ps.price_score) AS avg_price_score,
          AVG(ps.rank_score) AS avg_rank_score,
          AVG(snap.price) AS avg_price,
          MIN(snap.price) AS min_price,
          MAX(snap.price) AS max_price,
          AVG(snap.rating) AS avg_rating,
          AVG(snap.review_count) AS avg_review_count,
          SUM(snap.monthly_bought) AS total_monthly_bought,
          AVG(snap.monthly_bought) AS avg_monthly_bought,
          AVG(krs.organic_rank) AS avg_organic_rank,
          SUM(CASE WHEN krs.organic_rank IS NOT NULL AND krs.organic_rank <= 10 THEN 1 ELSE 0 END) AS top10_count,
          SUM(CASE WHEN krs.is_sponsored = 1 OR snap.is_sponsored = 1 THEN 1 ELSE 0 END) AS sponsored_count,
          MAX(snap.snapshot_at) AS latest_snapshot_at
        FROM dim_keywords k
        JOIN latest_ranks krs ON krs.keyword_id = k.keyword_id
        JOIN latest_snaps snap ON snap.product_id = krs.product_id
        LEFT JOIN latest_scores ps
          ON ps.product_id = krs.product_id
         AND ps.keyword_id = k.keyword_id
        {where_sql}
        GROUP BY k.keyword_id, k.marketplace, k.keyword
        {having_sql}
    """


def _build_filters(*, keyword: str | None, table_alias: str = "k", placeholder: str = "%s") -> tuple[str, list[Any]]:
    if not keyword:
        return "", []
    # 关键词机会走 DuckDB 仓库（dim_keywords），而 DuckDB 的 LIKE **区分大小写**（不同于
    # MySQL 的 ci collation）。两侧都 LOWER() 统一不区分大小写，MySQL/DuckDB 路径通用。
    like = f"%{keyword.strip().lower()}%"
    return f"WHERE LOWER({table_alias}.keyword) LIKE {placeholder}", [like]


def _build_having(
    *,
    min_products: int | None,
    count_expr: str = "COUNT(DISTINCT p.id)",
    placeholder: str = "%s",
) -> tuple[str, list[Any]]:
    # count_expr/placeholder must match the path: MySQL uses the `p` alias and
    # `%s`, the DuckDB warehouse has no `p` alias and binds with `?`. Sharing a
    # single hardcoded fragment across both silently broke the warehouse path.
    if min_products is None:
        return "", []
    return f"HAVING {count_expr} >= {placeholder}", [min_products]


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    float_keys = (
        "avg_total_score",
        "avg_demand_score",
        "avg_competition_score",
        "avg_rating_score",
        "avg_price_score",
        "avg_rank_score",
        "avg_price",
        "min_price",
        "max_price",
        "avg_rating",
        "avg_review_count",
        "total_monthly_bought",
        "avg_monthly_bought",
        "avg_organic_rank",
    )
    int_keys = ("keyword_id", "product_count", "top10_count", "sponsored_count")

    for key in float_keys:
        value = normalized.get(key)
        if value is not None:
            normalized[key] = float(value)
    for key in int_keys:
        value = normalized.get(key)
        if value is not None:
            normalized[key] = int(value)
    if normalized.get("latest_snapshot_at") is not None:
        normalized["latest_snapshot_at"] = str(normalized["latest_snapshot_at"])
    return normalized


def _enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    opportunity_score = _calculate_opportunity_score(row)
    row["opportunity_score"] = opportunity_score
    row["opportunity_level"] = _opportunity_level(opportunity_score)
    row["opportunity_reason"] = _build_reason(row)
    row["risk_warnings"] = _build_risk_warnings(row)
    row["entry_strategy"] = _build_entry_strategy(row)
    return row


def _calculate_opportunity_score(row: dict[str, Any]) -> float:
    demand = row.get("avg_demand_score") or 0.0
    competition = row.get("avg_competition_score") or 0.0
    rating = row.get("avg_rating_score") or 0.0
    price = row.get("avg_price_score") or 0.0
    rank = row.get("avg_rank_score") or 0.0
    depth = _score_product_depth(row.get("product_count") or 0)
    score = demand * 0.30 + competition * 0.25 + rating * 0.15 + price * 0.10 + rank * 0.10 + depth * 0.10
    return round(max(0.0, min(100.0, score)), 2)


def _score_product_depth(product_count: int) -> float:
    if product_count <= 0:
        return 0.0
    if product_count < 3:
        return 35.0
    if product_count <= 10:
        return 75.0
    if product_count <= 50:
        return 100.0
    return 85.0


def _opportunity_level(score: float) -> str:
    if score >= 75:
        return "高机会"
    if score >= 60:
        return "可观察"
    return "谨慎"


def _build_reason(row: dict[str, Any]) -> str:
    parts: list[str] = []
    avg_bought = row.get("avg_monthly_bought") or 0.0
    total_bought = row.get("total_monthly_bought") or 0.0
    avg_reviews = row.get("avg_review_count") or 0.0
    competition = row.get("avg_competition_score") or 0.0
    avg_price = row.get("avg_price")
    top10_count = row.get("top10_count") or 0
    sponsored_count = row.get("sponsored_count") or 0
    product_count = row.get("product_count") or 0

    if avg_bought >= 10000 or total_bought >= 50000:
        parts.append("需求强")
    elif avg_bought >= 1000:
        parts.append("需求有一定基础")
    else:
        parts.append("需求信号偏弱")

    if avg_reviews >= 3000 or competition < 35:
        parts.append("评论壁垒较高，竞争压力大")
    elif avg_reviews <= 800 or competition >= 60:
        parts.append("竞争压力相对可控")
    else:
        parts.append("竞争压力中等")

    if avg_price is not None:
        if 15 <= avg_price <= 60:
            parts.append("价格带较适合早期验证")
        elif avg_price < 10:
            parts.append("价格偏低，需重点核算利润")
        else:
            parts.append("价格偏高，需验证转化和履约成本")

    if top10_count:
        parts.append(f"已有 {top10_count} 个商品进入自然序位估算前 10")
    if product_count and sponsored_count / product_count >= 0.4:
        parts.append("广告/赞助占比较高，需关注投放压力")

    if "需求强" in parts and any("竞争压力大" in part for part in parts):
        parts.append("适合做对标与差异化分析，不宜直接同质化进入")

    return "；".join(parts)


def _build_risk_warnings(row: dict[str, Any]) -> str:
    warnings: list[str] = []
    avg_reviews = row.get("avg_review_count") or 0.0
    avg_bought = row.get("avg_monthly_bought") or 0.0
    avg_price = row.get("avg_price")
    avg_rating = row.get("avg_rating")
    top10_count = row.get("top10_count") or 0
    product_count = row.get("product_count") or 0
    sponsored_count = row.get("sponsored_count") or 0

    if avg_reviews >= 10000:
        warnings.append("头部评论壁垒很高，新品冷启动难度大")
    elif avg_reviews >= 3000:
        warnings.append("评论数偏高，需要明确差异化卖点")

    if avg_bought >= 5000 and avg_reviews >= 3000:
        warnings.append("需求强但竞争强，直接同质化进入风险高")
    if avg_price is not None and avg_price < 12:
        warnings.append("价格偏低，利润和广告容错空间可能不足")
    if avg_price is not None and avg_price > 80:
        warnings.append("客单价偏高，需要验证转化率和退货成本")
    if avg_rating is not None and avg_rating < 4.2:
        warnings.append("评分偏低，可能存在质量或预期管理问题")
    if product_count and top10_count / product_count >= 0.5:
        warnings.append("前排集中度较高，需评估头部品牌占位")
    if product_count and sponsored_count / product_count >= 0.4:
        warnings.append("赞助/广告占比较高，获客成本压力可能较大")

    return "；".join(warnings) if warnings else "暂无明显硬风险，仍需补充评论、利润和供应链验证。"


def _build_entry_strategy(row: dict[str, Any]) -> str:
    opportunity_score = row.get("opportunity_score") or 0.0
    avg_reviews = row.get("avg_review_count") or 0.0
    avg_bought = row.get("avg_monthly_bought") or 0.0
    avg_price = row.get("avg_price")
    avg_rating = row.get("avg_rating")
    competition = row.get("avg_competition_score") or 0.0

    if avg_bought >= 5000 and (avg_reviews >= 3000 or competition < 35):
        return (
            "建议作为对标市场：优先拆解头部商品差评、套装规格、材质/功能差异和价格带，"
            "找到明确差异化后再小批量验证。"
        )
    if opportunity_score >= 75 and avg_reviews < 3000:
        return "可作为优先验证关键词：先抓取更多页和历史快照，再评估利润、FBA 成本和广告预算。"
    if opportunity_score >= 60:
        return "建议放入观察池：继续积累趋势数据，重点看需求是否稳定、评论痛点是否可改进。"
    if avg_price is not None and avg_price < 12:
        return "暂不建议优先进入：先核算采购、FBA、退货和广告后是否仍有足够毛利。"
    if avg_rating is not None and avg_rating < 4.2:
        return "可作为痛点挖掘对象：先分析差评原因，判断是否存在产品改良机会。"
    return "建议暂缓进入，等待更多关键词、评论和趋势数据支撑判断。"


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 500))


def _normalize_offset(offset: int) -> int:
    try:
        value = int(offset)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)
