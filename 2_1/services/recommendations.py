"""Recommendation queries backed by MySQL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from database.mysql_client import MySQLClient
from pkg_paths import resolve_user_writable_path


RECOMMENDATION_COLUMNS = {
    "asin": "ASIN",
    "title": "商品标题",
    "keyword": "关键词",
    "total_score": "综合得分",
    "product_size": "尺寸/规格",
    "price": "价格",
    "rating": "评分",
    "review_count": "评论数",
    "monthly_bought": "近月购买量",
    "organic_rank": "自然序位估算",
    "is_deal": "是否促销",
    "score_date": "评分日期",
    "reason": "推荐理由",
    "product_url": "商品链接",
}

_RECOMMENDATION_SORTS = {
    "total_score": "ps.total_score",
    "growth_score": "ps.growth_score",
    "price": "snap.price",
    "rating": "snap.rating",
    "review_count": "snap.review_count",
    "monthly_bought": "snap.monthly_bought",
    "organic_rank": "krs.organic_rank",
}


def fetch_top_recommendations(limit: int = 50, client: MySQLClient | None = None) -> list[dict[str, Any]]:
    return fetch_recommendations_page(limit=limit, client=client)["rows"]


def fetch_recommendations_page(
    limit: int = 50,
    *,
    offset: int = 0,
    sort_by: str = "total_score",
    sort_dir: str = "desc",
    keyword: str | None = None,
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
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    order_sql, normalized_sort, normalized_dir = _recommendation_order_by(sort_by, sort_dir)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_title_zh = db.has_columns(cursor, "products", ("title_zh",))
            has_product_size = db.has_columns(cursor, "products", ("product_size",))
            title_select = _product_title_select(has_title_zh)
            product_size_select = _product_size_select(has_product_size)
            where_sql, params = _recommendation_filters(
                keyword=keyword,
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
            from_sql = f"""
                FROM product_scores ps
                JOIN products p ON p.id = ps.product_id
                LEFT JOIN keywords k ON k.id = ps.keyword_id
                JOIN product_snapshots snap
                  ON snap.product_id = p.id
                 AND snap.snapshot_at = (
                   SELECT MAX(s2.snapshot_at)
                   FROM product_snapshots s2
                   WHERE s2.product_id = p.id
                 )
                LEFT JOIN keyword_rank_snapshots krs
                  ON krs.id = (
                   SELECT krs2.id
                   FROM keyword_rank_snapshots krs2
                   WHERE krs2.keyword_id = ps.keyword_id
                     AND krs2.product_id = ps.product_id
                   ORDER BY krs2.snapshot_at DESC, krs2.id DESC
                   LIMIT 1
                 )
                WHERE ps.id = (
                  SELECT ps2.id
                  FROM product_scores ps2
                  WHERE ps2.product_id = ps.product_id
                    AND (ps2.keyword_id <=> ps.keyword_id)
                  ORDER BY ps2.score_date DESC, ps2.id DESC
                  LIMIT 1
                )
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
                  k.keyword,
                  ps.total_score,
                  ps.growth_score,
                  {product_size_select},
                  snap.price,
                  snap.rating,
                  snap.review_count,
                  snap.monthly_bought,
                  krs.organic_rank,
                  snap.is_deal,
                  ps.score_date,
                  ps.reason,
                  p.product_url
                {from_sql}
                {order_sql}
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
        "sort_by": normalized_sort,
        "sort_dir": normalized_dir,
    }


def export_recommendations_csv(
    output_dir: str | Path = "数据结果",
    *,
    limit: int = 50,
    client: MySQLClient | None = None,
) -> Path:
    rows = fetch_top_recommendations(limit=limit, client=client)
    output = resolve_user_writable_path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "推荐榜单.csv"
    chinese_rows = [to_chinese_row(row) for row in rows]
    pd.DataFrame(chinese_rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def to_chinese_row(row: dict[str, Any]) -> dict[str, Any]:
    return {RECOMMENDATION_COLUMNS.get(key, key): value for key, value in row.items()}


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in ("total_score", "growth_score", "price", "rating"):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = float(value)
    if normalized.get("score_date") is not None:
        normalized["score_date"] = str(normalized["score_date"])
    normalized["is_deal"] = "是" if normalized.get("is_deal") else "否"
    return normalized


def _product_title_select(has_title_zh: bool) -> str:
    if has_title_zh:
        return "COALESCE(NULLIF(p.title_zh, ''), p.title) AS title, p.title AS title_original, p.title_zh"
    return "p.title"


def _product_size_select(has_product_size: bool) -> str:
    if has_product_size:
        return "p.product_size"
    return "NULL AS product_size"


def _recommendation_order_by(sort_by: str, sort_dir: str) -> tuple[str, str, str]:
    normalized_sort = str(sort_by or "total_score").strip()
    if normalized_sort not in _RECOMMENDATION_SORTS:
        normalized_sort = "total_score"
    normalized_dir = "asc" if str(sort_dir or "").lower() == "asc" else "desc"
    direction = "ASC" if normalized_dir == "asc" else "DESC"
    expr = _RECOMMENDATION_SORTS[normalized_sort]
    return (
        f"ORDER BY {expr} {direction}, ps.total_score DESC, snap.monthly_bought DESC, p.asin ASC",
        normalized_sort,
        normalized_dir,
    )


def _recommendation_filters(
    *,
    keyword: str | None = None,
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
    has_title_zh: bool = False,
    has_product_size: bool = False,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    term = str(keyword or "").strip()
    if term:
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

    numeric_filters = (
        ("ps.total_score >= %s", min_score),
        ("ps.total_score <= %s", max_score),
        ("snap.price >= %s", min_price),
        ("snap.price <= %s", max_price),
        ("snap.rating >= %s", min_rating),
        ("snap.rating <= %s", max_rating),
        ("snap.review_count >= %s", min_reviews),
        ("snap.review_count <= %s", max_reviews),
        ("snap.monthly_bought >= %s", min_bought),
        ("snap.monthly_bought <= %s", max_bought),
        ("krs.organic_rank >= %s", min_rank),
        ("krs.organic_rank <= %s", max_rank),
    )
    for clause, value in numeric_filters:
        if value is not None:
            clauses.append(clause)
            params.append(value)

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
    return "AND " + " AND ".join(clauses), params


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 50
    return max(1, min(value, 500))


def _normalize_offset(offset: int) -> int:
    try:
        value = int(offset)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)
