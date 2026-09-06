"""Product pool and product history queries."""

from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any

from database.mysql_client import MySQLClient


logger = logging.getLogger(__name__)


def fetch_product_pool(
    limit: int = 100,
    *,
    keyword: str | None = None,
    keyword_exact: bool = False,
    keyword_scope: str = "current",
    min_score: float | None = None,
    max_score: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    max_rating: float | None = None,
    min_reviews: int | None = None,
    max_reviews: int | None = None,
    min_bought: int | None = None,
    max_bought: int | None = None,
    min_rank: int | None = None,
    max_rank: int | None = None,
    deal_status: str = "all",
    size_status: str = "all",
    sort_by: str = "total_score",
    sort_dir: str = "desc",
    client: MySQLClient | None = None,
) -> list[dict[str, Any]]:
    return fetch_product_pool_page(
        limit=limit,
        keyword=keyword,
        keyword_exact=keyword_exact,
        keyword_scope=keyword_scope,
        min_score=min_score,
        max_score=max_score,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        max_rating=max_rating,
        min_reviews=min_reviews,
        max_reviews=max_reviews,
        min_bought=min_bought,
        max_bought=max_bought,
        min_rank=min_rank,
        max_rank=max_rank,
        deal_status=deal_status,
        size_status=size_status,
        sort_by=sort_by,
        sort_dir=sort_dir,
        client=client,
    )["rows"]


def fetch_product_pool_page(
    limit: int = 100,
    *,
    offset: int = 0,
    keyword: str | None = None,
    keyword_exact: bool = False,
    keyword_scope: str = "current",
    min_score: float | None = None,
    max_score: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    max_rating: float | None = None,
    min_reviews: int | None = None,
    max_reviews: int | None = None,
    min_bought: int | None = None,
    max_bought: int | None = None,
    min_rank: int | None = None,
    max_rank: int | None = None,
    deal_status: str = "all",
    size_status: str = "all",
    sort_by: str = "total_score",
    sort_dir: str = "desc",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    normalized_keyword_scope = _normalize_keyword_scope(keyword_scope)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_title_zh = db.has_columns(cursor, "products", ("title_zh",))
            has_product_size = db.has_columns(cursor, "products", ("product_size",))
            title_select = _product_title_select(has_title_zh)
            product_size_select = _product_size_select(has_product_size)
            order_sql, normalized_sort, normalized_dir = _pool_order_by(
                sort_by,
                sort_dir,
                has_title_zh=has_title_zh,
                has_product_size=has_product_size,
            )
            keyword_join_sql, keyword_join_params = _build_keyword_join(
                keyword=keyword,
                keyword_exact=keyword_exact,
                keyword_scope=normalized_keyword_scope,
            )
            snapshot_join_sql = _build_product_snapshot_join(keyword=keyword, keyword_exact=keyword_exact)
            where_sql, params = _build_pool_filters(
                keyword=keyword,
                keyword_exact=keyword_exact,
                min_score=min_score,
                max_score=max_score,
                min_price=min_price,
                max_price=max_price,
                min_rating=min_rating,
                max_rating=max_rating,
                min_reviews=min_reviews,
                max_reviews=max_reviews,
                min_bought=min_bought,
                max_bought=max_bought,
                min_rank=min_rank,
                max_rank=max_rank,
                deal_status=deal_status,
                size_status=size_status,
                has_title_zh=has_title_zh,
                has_product_size=has_product_size,
            )
            params = keyword_join_params + params
            from_sql = f"""
                FROM products p
                {keyword_join_sql}
                {snapshot_join_sql}
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
                  {product_size_select},
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
                  krs.organic_rank,
                  krs.snapshot_at AS rank_snapshot_at,
                  krs.is_sponsored AS rank_is_sponsored,
                  snap.is_deal
                {from_sql}
                {order_sql}
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = cursor.fetchall()
    result = {
        "rows": [_normalize_row(row) for row in rows],
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "sort_by": normalized_sort,
        "sort_dir": normalized_dir,
    }
    if keyword_exact and str(keyword or "").strip():
        result["keyword_scope"] = normalized_keyword_scope
        result["scope_message"] = (
            "仅展示该关键词最新完整采集批次中的商品，商品指标与该批次时间对齐。"
            if normalized_keyword_scope == "current"
            else "展示该关键词历史上曾观察到的商品；每个商品使用其最后一次出现时的排名与商品快照。"
        )
    return result


def fetch_product_history(
    asin: str,
    client: MySQLClient | None = None,
    *,
    prefer_warehouse: bool = True,
    score_keyword: str | None = None,
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
            has_product_size = db.has_columns(cursor, "products", ("product_size",))
            has_detail_columns = db.has_columns(
                cursor,
                "products",
                ("date_first_available", "detail_collected_at", "detail_source_file"),
            )
            title_select = _product_title_select(has_title_zh)
            product_size_select = _product_size_select(has_product_size)
            detail_select = _product_detail_select(has_detail_columns)
            score_join_sql, score_join_params = _build_product_score_context_join(score_keyword)
            cursor.execute(
                f"""
                SELECT
                  p.asin,
                  p.marketplace,
                  {title_select},
                  p.product_url,
                  p.image_url,
                  p.category_path,
                  {product_size_select},
                  {detail_select},
                  p.first_seen_at,
                  p.last_seen_at,
                  ps.total_score,
                  ps.demand_score,
                  ps.growth_score,
                  ps.competition_score,
                  ps.rating_score,
                  ps.price_score,
                  ps.rank_score,
                  ps.reason,
                  score_k.keyword AS score_keyword
                FROM products p
                {score_join_sql}
                LEFT JOIN keywords score_k ON score_k.id = ps.keyword_id
                WHERE p.asin = %s
                LIMIT 1
                """,
                [*score_join_params, asin],
            )
            product = cursor.fetchone()
            if not product:
                return {"product": None, "snapshots": []}

            snapshots = None
            snapshot_source = "mysql"
            snapshot_warning = ""
            mysql_snapshot_state = _fetch_product_snapshot_state(cursor, asin)
            if prefer_warehouse and client is None:
                try:
                    warehouse_snapshots = _fetch_product_snapshots_from_warehouse(asin)
                    if _snapshot_state_matches(warehouse_snapshots, mysql_snapshot_state):
                        snapshots = warehouse_snapshots
                        snapshot_source = "warehouse"
                    else:
                        snapshot_warning = "分析仓库副本落后于 MySQL，已自动改读主库。"
                except Exception:
                    snapshot_warning = "分析仓库暂不可用，已自动改读 MySQL 主库。"
                    logger.warning(
                        "Failed to query product history snapshots from warehouse; "
                        "falling back to MySQL for asin=%s",
                        asin,
                        exc_info=True,
                    )

            if not snapshots:
                snapshots = _fetch_product_snapshots_from_mysql(cursor, asin)
                snapshot_source = "mysql"

            score_context_keyword = str(product.get("score_keyword") or "").strip() or None
            rank_rows = _fetch_keyword_rank_history(cursor, asin, score_context_keyword)
            snapshots = _overlay_keyword_rank_context(snapshots, rank_rows)

            detail_captures = (
                _fetch_product_detail_captures(cursor, asin)
                if db.has_table(cursor, "product_offer_snapshots")
                else []
            )
            detail_captures = _ensure_latest_detail_capture(detail_captures, product)
            best_seller_ranks = (
                _fetch_latest_bsr_snapshots(cursor, asin)
                if db.has_table(cursor, "product_bsr_snapshots")
                else []
            )

    normalized_snapshots = [_normalize_row(row) for row in snapshots]
    capture_history = build_product_capture_history(normalized_snapshots, detail_captures)
    return {
        "product": _normalize_row(product),
        "snapshots": normalized_snapshots,
        "capture_history": capture_history,
        "capture_summary": _build_capture_summary(
            normalized_snapshots,
            detail_captures,
            capture_history,
            product,
        ),
        "best_seller_ranks": [_normalize_row(row) for row in best_seller_ranks],
        "snapshot_freshness": build_snapshot_freshness(normalized_snapshots),
        "snapshot_source": snapshot_source,
        "snapshot_warning": snapshot_warning,
        "rank_context": {
            "keyword": product.get("score_keyword"),
            "source": "keyword_rank_snapshots" if product.get("score_keyword") else "unavailable",
            "message": (
                f"自然序位按关键词“{product.get('score_keyword')}”读取，不混用其他关键词排名。"
                if product.get("score_keyword")
                else "该商品尚未关联关键词排名；专项模型可按最近商品快照降置信试算，自然序位保持未知。"
            ),
        },
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


def _fetch_product_detail_captures(cursor: Any, asin: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          o.snapshot_at,
          o.current_price,
          o.list_price,
          o.discount_percent,
          o.availability_status,
          o.fulfillment_channel,
          o.is_prime,
          o.source_file,
          o.raw_json
        FROM product_offer_snapshots o
        JOIN products p ON p.id = o.product_id
        WHERE p.asin = %s
        ORDER BY o.snapshot_at ASC, o.id ASC
        """,
        (asin,),
    )
    return list(cursor.fetchall())


def _ensure_latest_detail_capture(
    rows: list[dict[str, Any]],
    product: dict[str, Any],
) -> list[dict[str, Any]]:
    latest_at = _parse_datetime(product.get("detail_collected_at"))
    if latest_at is None:
        return list(rows)
    result = [dict(row) for row in rows]
    if any(_parse_datetime(row.get("snapshot_at")) == latest_at for row in result):
        return result
    result.append(
        {
            "snapshot_at": latest_at,
            "source_file": product.get("detail_source_file"),
            "raw_json": None,
        }
    )
    result.sort(key=lambda row: _parse_datetime(row.get("snapshot_at")) or datetime.min)
    return result


def build_product_capture_history(
    search_snapshots: list[dict[str, Any]],
    detail_captures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge search and detail observations without changing either fact table."""

    merged: dict[str, dict[str, Any]] = {}
    for snapshot in search_snapshots:
        row = dict(snapshot)
        row.update(
            {
                "source_type": "search",
                "source_label": "搜索页",
                "source_types": ["search"],
                "observed_fields": _observed_capture_fields(row, source_type="search"),
            }
        )
        merged[_capture_time_key(row.get("snapshot_at"))] = row

    for capture in detail_captures:
        raw_metrics = _detail_capture_metrics(capture.get("raw_json"))
        detail_row = {
            "snapshot_at": capture.get("snapshot_at"),
            "price": capture.get("current_price"),
            "rating": raw_metrics.get("rating"),
            "review_count": raw_metrics.get("review_count"),
            "monthly_bought": raw_metrics.get("monthly_bought"),
            "organic_rank": None,
            "is_deal": raw_metrics.get("is_deal"),
            "availability_status": capture.get("availability_status"),
            "fulfillment_channel": capture.get("fulfillment_channel"),
            "is_prime": capture.get("is_prime"),
            "source_file": capture.get("source_file"),
            "source_type": "detail",
            "source_label": "详情页",
            "source_types": ["detail"],
        }
        detail_row["observed_fields"] = _observed_capture_fields(
            detail_row,
            source_type="detail",
        )
        key = _capture_time_key(detail_row.get("snapshot_at"))
        current = merged.get(key)
        if current is None:
            merged[key] = detail_row
            continue

        combined = dict(current)
        for field in (
            "price",
            "rating",
            "review_count",
            "monthly_bought",
            "is_deal",
            "availability_status",
            "fulfillment_channel",
            "is_prime",
            "source_file",
        ):
            if detail_row.get(field) is not None:
                combined[field] = detail_row[field]
        combined["source_type"] = "combined"
        combined["source_label"] = "搜索页 + 详情页"
        combined["source_types"] = ["search", "detail"]
        combined["observed_fields"] = list(
            dict.fromkeys(
                [
                    *(current.get("observed_fields") or []),
                    *(detail_row.get("observed_fields") or []),
                ]
            )
        )
        merged[key] = combined

    return [
        _normalize_row(row)
        for _key, row in sorted(
            merged.items(),
            key=lambda item: _parse_datetime(item[1].get("snapshot_at")) or datetime.min,
        )
    ]


def _detail_capture_metrics(value: Any) -> dict[str, Any]:
    payload = value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(payload, dict):
        return {}
    metrics = payload.get("detail_page_metrics")
    return dict(metrics) if isinstance(metrics, dict) else {}


def _observed_capture_fields(row: dict[str, Any], *, source_type: str) -> list[str]:
    labels = (
        ("price", "价格"),
        ("rating", "评分"),
        ("review_count", "评论数"),
        ("monthly_bought", "近月购买"),
        ("organic_rank", "自然序位"),
        ("availability_status", "库存"),
        ("fulfillment_channel", "履约"),
    )
    fields = [label for key, label in labels if row.get(key) is not None]
    if source_type == "detail" and not fields:
        fields.append("详情页有效性")
    return fields


def _capture_time_key(value: Any) -> str:
    parsed = _parse_datetime(value)
    return parsed.isoformat(sep=" ") if parsed is not None else str(value or "")


def _build_capture_summary(
    search_snapshots: list[dict[str, Any]],
    detail_captures: list[dict[str, Any]],
    capture_history: list[dict[str, Any]],
    product: dict[str, Any],
) -> dict[str, Any]:
    latest = capture_history[-1] if capture_history else {}
    latest_at = latest.get("snapshot_at")
    latest_source = latest.get("source_label")
    if latest_at is None:
        candidates = [
            ("搜索页", product.get("last_seen_at")),
            ("详情页", product.get("detail_collected_at")),
        ]
        valid = [
            (label, _parse_datetime(value))
            for label, value in candidates
            if _parse_datetime(value) is not None
        ]
        if valid:
            latest_source, parsed_at = max(valid, key=lambda item: item[1])
            latest_at = parsed_at.isoformat(sep=" ")
    return {
        "search_capture_count": len(search_snapshots),
        "detail_capture_count": len(detail_captures),
        "timeline_count": len(capture_history),
        "latest_capture_at": str(latest_at) if latest_at is not None else None,
        "latest_capture_source": latest_source,
        "latest_search_at": (
            str(search_snapshots[-1].get("snapshot_at"))
            if search_snapshots
            else None
        ),
        "latest_detail_at": (
            str(detail_captures[-1].get("snapshot_at"))
            if detail_captures
            else None
        ),
    }


def _fetch_product_snapshot_state(cursor: Any, asin: str) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT COUNT(*) AS row_count, MAX(snapshot_at) AS latest_snapshot_at
        FROM product_snapshots
        WHERE product_id = (
          SELECT id FROM products WHERE asin = %s LIMIT 1
        )
        """,
        (asin,),
    )
    row = cursor.fetchone() or {}
    return {
        "row_count": int(row.get("row_count") or 0),
        "latest_snapshot_at": _parse_datetime(row.get("latest_snapshot_at")),
    }


def _snapshot_state_matches(rows: list[dict[str, Any]], mysql_state: dict[str, Any]) -> bool:
    if len(rows) != int(mysql_state.get("row_count") or 0):
        return False
    mysql_latest = mysql_state.get("latest_snapshot_at")
    warehouse_latest = _latest_snapshot_time(rows)
    return mysql_latest == warehouse_latest


def _fetch_keyword_rank_history(
    cursor: Any,
    asin: str,
    keyword: str | None,
) -> list[dict[str, Any]]:
    if not keyword:
        return []
    cursor.execute(
        """
        SELECT
          krs.snapshot_at,
          krs.page_no,
          krs.organic_rank,
          krs.is_sponsored,
          krs.id AS rank_snapshot_id
        FROM keyword_rank_snapshots krs
        JOIN products p ON p.id = krs.product_id
        JOIN keywords k ON k.id = krs.keyword_id
        WHERE p.asin = %s AND k.keyword = %s
        ORDER BY krs.snapshot_at ASC, krs.id ASC
        """,
        (asin, keyword),
    )
    return list(cursor.fetchall())


def _overlay_keyword_rank_context(
    snapshots: list[dict[str, Any]],
    rank_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ranks_by_time: dict[datetime, dict[str, Any]] = {}
    for row in rank_rows:
        snapshot_at = _parse_datetime(row.get("snapshot_at"))
        if snapshot_at is None:
            continue
        previous = ranks_by_time.get(snapshot_at)
        if previous is None or int(row.get("rank_snapshot_id") or 0) > int(previous.get("rank_snapshot_id") or 0):
            ranks_by_time[snapshot_at] = row

    contextual: list[dict[str, Any]] = []
    for raw in snapshots:
        row = dict(raw)
        rank = ranks_by_time.get(_parse_datetime(row.get("snapshot_at")))
        row["organic_rank"] = None
        row["rank_confidence"] = "unknown"
        row["rank_is_sponsored"] = None
        if rank is not None:
            sponsored = bool(rank.get("is_sponsored"))
            row["rank_is_sponsored"] = sponsored
            row["rank_confidence"] = _rank_confidence_from_rank_row(rank)
            if not sponsored and rank.get("organic_rank") is not None:
                row["organic_rank"] = rank.get("organic_rank")
        contextual.append(row)
    return contextual


def _rank_confidence_from_rank_row(row: dict[str, Any]) -> str:
    if bool(row.get("is_sponsored")):
        return "sponsored"
    if row.get("organic_rank") is None:
        return "unknown"
    page_no = int(row.get("page_no") or 0)
    return "page_first" if page_no <= 1 else "batch_continuous"


def _fetch_latest_bsr_snapshots(cursor: Any, asin: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          b.snapshot_at,
          b.rank_value AS `rank`,
          b.category_name,
          b.category_url,
          b.is_primary
        FROM product_bsr_snapshots b
        JOIN products p ON p.id = b.product_id
        WHERE p.asin = %s
          AND b.snapshot_at = (
            SELECT MAX(b2.snapshot_at)
            FROM product_bsr_snapshots b2
            WHERE b2.product_id = b.product_id
          )
        ORDER BY b.is_primary DESC, b.rank_value ASC, b.id ASC
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
    for key in (
        "first_seen_at",
        "last_seen_at",
        "snapshot_at",
        "rank_snapshot_at",
        "date_first_available",
        "detail_collected_at",
    ):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = str(value)
    if "is_deal" in normalized and normalized.get("is_deal") is not None:
        normalized["is_deal"] = "是" if normalized.get("is_deal") else "否"
    if "is_primary" in normalized:
        normalized["is_primary"] = bool(normalized.get("is_primary"))
    if "rank_is_sponsored" in normalized and normalized.get("rank_is_sponsored") is not None:
        normalized["rank_is_sponsored"] = bool(normalized.get("rank_is_sponsored"))
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
    max_score: float | None,
    min_price: float | None,
    max_price: float | None,
    min_rating: float | None,
    max_rating: float | None,
    min_reviews: int | None,
    max_reviews: int | None,
    min_bought: int | None,
    max_bought: int | None,
    min_rank: int | None,
    max_rank: int | None,
    deal_status: str = "all",
    size_status: str = "all",
    has_title_zh: bool = False,
    has_product_size: bool = False,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if keyword:
        term = keyword.strip()
        if keyword_exact:
            pass
        else:
            like = f"%{term}%"
            keyword_clauses = ["p.asin LIKE %s", "p.title LIKE %s", "k.keyword LIKE %s"]
            keyword_params = [like, like, like]
            if has_title_zh:
                keyword_clauses.insert(2, "p.title_zh LIKE %s")
                keyword_params.insert(2, like)
            if has_product_size:
                keyword_clauses.append("p.product_size LIKE %s")
                keyword_params.append(like)
            clauses.append("(" + " OR ".join(keyword_clauses) + ")")
            params.extend(keyword_params)
    if min_score is not None:
        clauses.append("ps.total_score >= %s")
        params.append(min_score)
    if max_score is not None:
        clauses.append("ps.total_score <= %s")
        params.append(max_score)
    if min_price is not None:
        clauses.append("snap.price >= %s")
        params.append(min_price)
    if max_price is not None:
        clauses.append("snap.price <= %s")
        params.append(max_price)
    if min_rating is not None:
        clauses.append("snap.rating >= %s")
        params.append(min_rating)
    if max_rating is not None:
        clauses.append("snap.rating <= %s")
        params.append(max_rating)
    if min_reviews is not None:
        clauses.append("snap.review_count >= %s")
        params.append(min_reviews)
    if max_reviews is not None:
        clauses.append("snap.review_count <= %s")
        params.append(max_reviews)
    if min_bought is not None:
        clauses.append("snap.monthly_bought >= %s")
        params.append(min_bought)
    if max_bought is not None:
        clauses.append("snap.monthly_bought <= %s")
        params.append(max_bought)
    if min_rank is not None:
        clauses.append("krs.organic_rank >= %s")
        params.append(min_rank)
    if max_rank is not None:
        clauses.append("krs.organic_rank <= %s")
        params.append(max_rank)

    normalized_deal_status = str(deal_status or "all").strip().lower()
    if normalized_deal_status == "deal":
        clauses.append("snap.is_deal = 1")
    elif normalized_deal_status == "regular":
        clauses.append("COALESCE(snap.is_deal, 0) = 0")

    normalized_size_status = str(size_status or "all").strip().lower()
    if normalized_size_status == "known":
        clauses.append("NULLIF(TRIM(p.product_size), '') IS NOT NULL" if has_product_size else "1 = 0")
    elif normalized_size_status == "missing" and has_product_size:
        clauses.append("NULLIF(TRIM(p.product_size), '') IS NULL")

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 500))


def _build_keyword_join(
    *,
    keyword: str | None,
    keyword_exact: bool,
    keyword_scope: str = "current",
) -> tuple[str, list[Any]]:
    if keyword_exact and keyword and keyword.strip():
        rank_scope_sql = (
            """
                 AND krs.snapshot_at = (
                   SELECT MAX(krs2.snapshot_at)
                   FROM keyword_rank_snapshots krs2
                   WHERE krs2.keyword_id = k.id
                 )
            """
            if _normalize_keyword_scope(keyword_scope) == "current"
            else """
                 AND krs.id = (
                   SELECT krs2.id
                   FROM keyword_rank_snapshots krs2
                   WHERE krs2.keyword_id = k.id
                     AND krs2.product_id = p.id
                   ORDER BY krs2.snapshot_at DESC, krs2.id DESC
                   LIMIT 1
                 )
            """
        )
        return (
            f"""
                JOIN keywords k
                  ON k.keyword = %s
                 AND k.marketplace = p.marketplace
                JOIN keyword_rank_snapshots krs
                  ON krs.keyword_id = k.id
                 AND krs.product_id = p.id
                 {rank_scope_sql}
                LEFT JOIN product_scores ps
                  ON ps.id = (
                   SELECT ps2.id
                   FROM product_scores ps2
                   WHERE ps2.product_id = p.id
                     AND ps2.keyword_id = k.id
                   ORDER BY ps2.score_date DESC, ps2.id DESC
                   LIMIT 1
                 )
            """,
            [keyword.strip()],
        )
    return (
        """
                LEFT JOIN product_scores ps
                  ON ps.id = (
                   SELECT ps2.id
                   FROM product_scores ps2
                   WHERE ps2.product_id = p.id
                   ORDER BY ps2.score_date DESC, ps2.total_score DESC, ps2.id DESC
                   LIMIT 1
                 )
                LEFT JOIN keywords k ON k.id = ps.keyword_id
                LEFT JOIN keyword_rank_snapshots krs
                  ON krs.id = (
                   SELECT krs2.id
                   FROM keyword_rank_snapshots krs2
                   WHERE krs2.keyword_id = ps.keyword_id
                     AND krs2.product_id = p.id
                   ORDER BY krs2.snapshot_at DESC, krs2.id DESC
                   LIMIT 1
                 )
        """,
        [],
    )


def _build_product_snapshot_join(*, keyword: str | None, keyword_exact: bool) -> str:
    if keyword_exact and keyword and keyword.strip():
        return """
                JOIN product_snapshots snap
                  ON snap.product_id = p.id
                 AND snap.snapshot_at = krs.snapshot_at
        """
    return """
                JOIN product_snapshots snap
                  ON snap.product_id = p.id
                 AND snap.snapshot_at = (
                   SELECT MAX(s2.snapshot_at)
                   FROM product_snapshots s2
                   WHERE s2.product_id = p.id
                 )
    """


def _normalize_keyword_scope(value: str | None) -> str:
    return "observed" if str(value or "").strip().lower() == "observed" else "current"


def _build_product_score_context_join(score_keyword: str | None) -> tuple[str, list[Any]]:
    keyword = str(score_keyword or "").strip()
    keyword_filter = ""
    params: list[Any] = []
    if keyword:
        keyword_filter = "AND EXISTS (SELECT 1 FROM keywords k2 WHERE k2.id = ps2.keyword_id AND k2.keyword = %s)"
        params.append(keyword)
    return (
        f"""
                LEFT JOIN product_scores ps
                  ON ps.id = (
                   SELECT ps2.id
                   FROM product_scores ps2
                   WHERE ps2.product_id = p.id
                     {keyword_filter}
                   ORDER BY ps2.score_date DESC, ps2.total_score DESC, ps2.id DESC
                   LIMIT 1
                 )
        """,
        params,
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


def _product_size_select(has_product_size: bool) -> str:
    if has_product_size:
        return "p.product_size"
    return "NULL AS product_size"


def _product_detail_select(has_detail_columns: bool) -> str:
    if has_detail_columns:
        return "p.date_first_available, p.detail_collected_at, p.detail_source_file"
    return "NULL AS date_first_available, NULL AS detail_collected_at, NULL AS detail_source_file"


def _pool_order_by(
    sort_by: str,
    sort_dir: str,
    *,
    has_title_zh: bool,
    has_product_size: bool,
) -> tuple[str, str, str]:
    title_expr = "COALESCE(NULLIF(p.title_zh, ''), p.title)" if has_title_zh else "p.title"
    product_size_expr = "p.product_size" if has_product_size else "p.asin"
    sorts = {
        "asin": "p.asin",
        "title": title_expr,
        "keyword": "k.keyword",
        "total_score": "ps.total_score",
        "growth_score": "ps.growth_score",
        "price": "snap.price",
        "product_size": product_size_expr,
        "rating": "snap.rating",
        "review_count": "snap.review_count",
        "monthly_bought": "snap.monthly_bought",
        "organic_rank": "krs.organic_rank",
        "snapshot_at": "snap.snapshot_at",
        "first_seen_at": "p.first_seen_at",
        "last_seen_at": "p.last_seen_at",
    }
    normalized_sort = str(sort_by or "total_score").strip()
    if normalized_sort not in sorts:
        normalized_sort = "total_score"
    normalized_dir = "asc" if str(sort_dir or "").lower() == "asc" else "desc"
    direction = "ASC" if normalized_dir == "asc" else "DESC"
    expr = sorts[normalized_sort]
    return (
        f"ORDER BY {expr} IS NULL, {expr} {direction}, ps.total_score DESC, snap.monthly_bought DESC, p.asin ASC",
        normalized_sort,
        normalized_dir,
    )
