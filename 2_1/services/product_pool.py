"""Product pool and product history queries."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from database.mysql_client import MySQLClient


logger = logging.getLogger(__name__)


def fetch_product_pool(
    limit: int = 100,
    *,
    keyword: str | None = None,
    keyword_exact: bool = False,
    min_score: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_reviews: int | None = None,
    client: MySQLClient | None = None,
) -> list[dict[str, Any]]:
    return fetch_product_pool_page(
        limit=limit,
        keyword=keyword,
        keyword_exact=keyword_exact,
        min_score=min_score,
        min_price=min_price,
        max_price=max_price,
        max_reviews=max_reviews,
        client=client,
    )["rows"]


def fetch_product_pool_page(
    limit: int = 100,
    *,
    offset: int = 0,
    keyword: str | None = None,
    keyword_exact: bool = False,
    min_score: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_reviews: int | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_title_zh = db.has_columns(cursor, "products", ("title_zh",))
            title_select = _product_title_select(has_title_zh)
            keyword_join_sql, keyword_join_params = _build_keyword_join(keyword=keyword, keyword_exact=keyword_exact)
            where_sql, params = _build_pool_filters(
                keyword=keyword,
                keyword_exact=keyword_exact,
                min_score=min_score,
                min_price=min_price,
                max_price=max_price,
                max_reviews=max_reviews,
                has_title_zh=has_title_zh,
            )
            params = keyword_join_params + params
            from_sql = f"""
                FROM products p
                JOIN product_snapshots snap
                  ON snap.product_id = p.id
                 AND snap.snapshot_at = (
                   SELECT MAX(s2.snapshot_at)
                   FROM product_snapshots s2
                   WHERE s2.product_id = p.id
                 )
                {keyword_join_sql}
                {where_sql}
            """
            cursor.execute(f"SELECT COUNT(*) AS total {from_sql}", params)
            total_row = cursor.fetchone() or {}
            total = int(total_row.get("total") or 0)
            cursor.execute(
                f"""
                SELECT
                  p.asin,
                  {title_select},
                  p.product_url,
                  p.image_url,
                  p.first_seen_at,
                  p.last_seen_at,
                  k.keyword,
                  ps.total_score,
                  ps.growth_score,
                  ps.reason,
                  snap.snapshot_at,
                  snap.price,
                  snap.rating,
                  snap.review_count,
                  snap.monthly_bought,
                  snap.organic_rank,
                  snap.is_deal
                {from_sql}
                ORDER BY ps.total_score DESC, snap.monthly_bought DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = cursor.fetchall()
    return {
        "rows": [_normalize_row(row) for row in rows],
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
    }


def fetch_product_history(
    asin: str,
    client: MySQLClient | None = None,
    *,
    prefer_warehouse: bool = True,
) -> dict[str, Any]:
    """Fetch one product and its snapshot time series.

    Product metadata and latest score still come from MySQL. The analytical
    time series prefers the DuckDB/Parquet warehouse and falls back to MySQL
    when the warehouse is unavailable or not yet synced for the product.
    """
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_title_zh = db.has_columns(cursor, "products", ("title_zh",))
            title_select = _product_title_select(has_title_zh)
            cursor.execute(
                f"""
                SELECT
                  p.asin,
                  {title_select},
                  p.product_url,
                  p.image_url,
                  p.first_seen_at,
                  p.last_seen_at,
                  ps.total_score,
                  ps.demand_score,
                  ps.competition_score,
                  ps.rating_score,
                  ps.price_score,
                  ps.rank_score,
                  ps.reason
                FROM products p
                LEFT JOIN product_scores ps
                  ON ps.product_id = p.id
                 AND ps.score_date = (
                   SELECT MAX(ps2.score_date)
                   FROM product_scores ps2
                   WHERE ps2.product_id = p.id
                 )
                WHERE p.asin = %s
                LIMIT 1
                """,
                (asin,),
            )
            product = cursor.fetchone()
            if not product:
                return {"product": None, "snapshots": []}

            snapshots = None
            if prefer_warehouse and client is None:
                try:
                    snapshots = _fetch_product_snapshots_from_warehouse(asin)
                except Exception:
                    logger.warning(
                        "Failed to query product history snapshots from warehouse; "
                        "falling back to MySQL for asin=%s",
                        asin,
                        exc_info=True,
                    )

            if not snapshots:
                snapshots = _fetch_product_snapshots_from_mysql(cursor, asin)

    normalized_snapshots = [_normalize_row(row) for row in snapshots]
    return {
        "product": _normalize_row(product),
        "snapshots": normalized_snapshots,
        "snapshot_freshness": build_snapshot_freshness(normalized_snapshots),
    }


def _fetch_product_snapshots_from_warehouse(asin: str) -> list[dict[str, Any]]:
    from services.analytics_warehouse import query_warehouse

    return query_warehouse(
        """
        SELECT
          snapshot_at,
          price,
          rating,
          review_count,
          monthly_bought,
          organic_rank,
          is_deal
        FROM fact_product_snapshots
        WHERE asin = ?
        ORDER BY snapshot_at ASC, snapshot_id ASC
        """,
        (asin,),
    )


def _fetch_product_snapshots_from_mysql(cursor: Any, asin: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          snapshot_at,
          price,
          rating,
          review_count,
          monthly_bought,
          organic_rank,
          is_deal
        FROM product_snapshots
        WHERE product_id = (
          SELECT id FROM products WHERE asin = %s LIMIT 1
        )
        ORDER BY snapshot_at ASC
        """,
        (asin,),
    )
    return list(cursor.fetchall())


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in (
        "total_score",
        "growth_score",
        "demand_score",
        "competition_score",
        "rating_score",
        "price_score",
        "rank_score",
        "price",
        "rating",
    ):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = float(value)
    for key in ("first_seen_at", "last_seen_at", "snapshot_at"):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = str(value)
    if "is_deal" in normalized:
        normalized["is_deal"] = "是" if normalized.get("is_deal") else "否"
    return normalized


def build_snapshot_freshness(
    snapshots: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    expire_days: int | None = None,
) -> dict[str, Any]:
    """Return a settings-based freshness marker for the latest product snapshot."""

    effective_expire_days = expire_days if expire_days is not None else _snapshot_expire_days()
    latest_at = _latest_snapshot_time(snapshots)
    if latest_at is None:
        return {
            "latest_snapshot_at": None,
            "snapshot_expire_days": effective_expire_days,
            "age_days": None,
            "is_stale": True,
            "message": "暂无快照数据。",
        }

    current_time = now or datetime.now()
    age_days = max(0.0, (current_time - latest_at).total_seconds() / 86400)
    is_stale = age_days > effective_expire_days
    return {
        "latest_snapshot_at": latest_at.isoformat(sep=" "),
        "snapshot_expire_days": effective_expire_days,
        "age_days": round(age_days, 2),
        "is_stale": is_stale,
        "message": (
            f"最新快照约 {age_days:.1f} 天前，已超过 {effective_expire_days} 天设置阈值。"
            if is_stale
            else f"最新快照约 {age_days:.1f} 天前，未超过 {effective_expire_days} 天设置阈值。"
        ),
    }


def _snapshot_expire_days() -> int:
    from services.settings import get_collection_limits

    return get_collection_limits().snapshot_expire_days


def _latest_snapshot_time(snapshots: list[dict[str, Any]]) -> datetime | None:
    times = [_parse_datetime(row.get("snapshot_at")) for row in snapshots if row]
    valid = [value for value in times if value is not None]
    return max(valid) if valid else None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _build_pool_filters(
    *,
    keyword: str | None,
    keyword_exact: bool = False,
    min_score: float | None,
    min_price: float | None,
    max_price: float | None,
    max_reviews: int | None,
    has_title_zh: bool = False,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if keyword:
        term = keyword.strip()
        if keyword_exact:
            pass
        else:
            like = f"%{term}%"
            if has_title_zh:
                clauses.append("(p.asin LIKE %s OR p.title LIKE %s OR p.title_zh LIKE %s OR k.keyword LIKE %s)")
                params.extend([like, like, like, like])
            else:
                clauses.append("(p.asin LIKE %s OR p.title LIKE %s OR k.keyword LIKE %s)")
                params.extend([like, like, like])
    if min_score is not None:
        clauses.append("ps.total_score >= %s")
        params.append(min_score)
    if min_price is not None:
        clauses.append("snap.price >= %s")
        params.append(min_price)
    if max_price is not None:
        clauses.append("snap.price <= %s")
        params.append(max_price)
    if max_reviews is not None:
        clauses.append("snap.review_count <= %s")
        params.append(max_reviews)

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 500))


def _build_keyword_join(*, keyword: str | None, keyword_exact: bool) -> tuple[str, list[Any]]:
    if keyword_exact and keyword and keyword.strip():
        return (
            """
                JOIN keywords k ON k.keyword = %s
                JOIN keyword_rank_snapshots krs
                  ON krs.keyword_id = k.id
                 AND krs.product_id = p.id
                 AND krs.id = (
                   SELECT krs2.id
                   FROM keyword_rank_snapshots krs2
                   WHERE krs2.keyword_id = k.id
                     AND krs2.product_id = p.id
                   ORDER BY krs2.snapshot_at DESC, krs2.id DESC
                   LIMIT 1
                 )
                LEFT JOIN product_scores ps
                  ON ps.product_id = p.id
                 AND ps.keyword_id = k.id
                 AND ps.score_date = (
                   SELECT MAX(ps2.score_date)
                   FROM product_scores ps2
                   WHERE ps2.product_id = p.id
                     AND ps2.keyword_id = k.id
                 )
            """,
            [keyword.strip()],
        )
    return (
        """
                LEFT JOIN product_scores ps
                  ON ps.product_id = p.id
                 AND ps.score_date = (
                   SELECT MAX(ps2.score_date)
                   FROM product_scores ps2
                   WHERE ps2.product_id = p.id
                 )
                LEFT JOIN keywords k ON k.id = ps.keyword_id
        """,
        [],
    )


def _normalize_offset(offset: int) -> int:
    try:
        value = int(offset)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)


def _product_title_select(has_title_zh: bool) -> str:
    if has_title_zh:
        return "COALESCE(NULLIF(p.title_zh, ''), p.title) AS title, p.title AS title_original, p.title_zh"
    return "p.title"
