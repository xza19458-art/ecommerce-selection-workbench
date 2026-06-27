"""Recommendation queries backed by MySQL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from database.mysql_client import MySQLClient


RECOMMENDATION_COLUMNS = {
    "asin": "ASIN",
    "title": "商品标题",
    "keyword": "关键词",
    "total_score": "综合得分",
    "price": "价格",
    "rating": "评分",
    "review_count": "评论数",
    "monthly_bought": "近月购买量",
    "organic_rank": "自然排名",
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
    "organic_rank": "snap.organic_rank",
}


def fetch_top_recommendations(limit: int = 50, client: MySQLClient | None = None) -> list[dict[str, Any]]:
    return fetch_recommendations_page(limit=limit, client=client)["rows"]


def fetch_recommendations_page(
    limit: int = 50,
    *,
    offset: int = 0,
    sort_by: str = "total_score",
    sort_dir: str = "desc",
    min_score: float | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    order_sql, normalized_sort, normalized_dir = _recommendation_order_by(sort_by, sort_dir)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_title_zh = db.has_columns(cursor, "products", ("title_zh",))
            title_select = _product_title_select(has_title_zh)
            where_sql, params = _recommendation_filters(min_score=min_score)
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
                WHERE ps.score_date = (
                  SELECT MAX(ps2.score_date)
                  FROM product_scores ps2
                  WHERE ps2.product_id = ps.product_id
                    AND (ps2.keyword_id <=> ps.keyword_id)
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
                  snap.price,
                  snap.rating,
                  snap.review_count,
                  snap.monthly_bought,
                  snap.organic_rank,
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
    output = Path(output_dir)
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


def _recommendation_filters(*, min_score: float | None) -> tuple[str, list[Any]]:
    if min_score is None:
        return "", []
    try:
        score = float(min_score)
    except (TypeError, ValueError):
        return "", []
    return "AND ps.total_score >= %s", [score]


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
