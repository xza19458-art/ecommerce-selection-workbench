"""Review pain-point queries backed by MySQL."""

from __future__ import annotations

import json
from typing import Any

from database.mysql_client import MySQLClient


def fetch_review_insight_list(
    limit: int = 100,
    *,
    keyword: str | None = None,
    client: MySQLClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch latest review insights across products."""
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_title_zh = db.has_columns(cursor, "products", ("title_zh",))
            title_select = _product_title_select(has_title_zh)
            where_sql, params = _build_product_filter(keyword, has_title_zh=has_title_zh)
            params.append(_normalize_limit(limit))
            cursor.execute(
                f"""
                SELECT
                  p.asin,
                  {title_select},
                  insight.insight_date,
                  insight.review_count,
                  insight.negative_count,
                  insight.avg_rating,
                  insight.pain_points_json,
                  insight.positive_points_json,
                  insight.opportunity_summary,
                  insight.risk_summary,
                  insight.updated_at,
                  COALESCE(evidence.imported_review_count, 0) AS imported_review_count,
                  COALESCE(evidence.traceable_review_count, 0) AS traceable_review_count
                FROM product_review_insights insight
                JOIN products p ON p.id = insight.product_id
                JOIN (
                  SELECT product_id, MAX(insight_date) AS insight_date
                  FROM product_review_insights
                  GROUP BY product_id
                ) latest
                  ON latest.product_id = insight.product_id
                 AND latest.insight_date = insight.insight_date
                LEFT JOIN (
                  SELECT
                    product_id,
                    COUNT(*) AS imported_review_count,
                    SUM(
                      CASE
                        WHEN COALESCE(NULLIF(source_url, ''), '') <> ''
                          OR COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.source_file')), ''), '') <> ''
                        THEN 1 ELSE 0
                      END
                    ) AS traceable_review_count
                  FROM product_reviews
                  GROUP BY product_id
                ) evidence ON evidence.product_id = insight.product_id
                {where_sql}
                ORDER BY insight.negative_count DESC, insight.review_count DESC, insight.updated_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cursor.fetchall()
    return [_normalize_insight_list_row(row) for row in rows]


def fetch_product_review_insight(asin: str, client: MySQLClient | None = None) -> dict[str, Any]:
    """Fetch latest review insight and low-rating review samples for a product."""
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_review_translation = db.has_columns(cursor, "product_reviews", ("title_zh", "body_zh"))
            review_select = _review_sample_select(has_review_translation)
            cursor.execute(
                """
                SELECT id, asin
                FROM products
                WHERE asin = %s
                LIMIT 1
                """,
                (asin,),
            )
            product = cursor.fetchone()
            if not product:
                return _empty_result("未找到商品，暂无法读取评论痛点。")

            product_id = product["id"]
            cursor.execute(
                """
                SELECT
                  insight_date,
                  review_count,
                  negative_count,
                  avg_rating,
                  pain_points_json,
                  positive_points_json,
                  opportunity_summary,
                  risk_summary,
                  updated_at
                FROM product_review_insights
                WHERE product_id = %s
                ORDER BY insight_date DESC
                LIMIT 1
                """,
                (product_id,),
            )
            insight = cursor.fetchone()

            cursor.execute(
                f"""
                SELECT
                  rating,
                  {review_select},
                  review_at,
                  verified_purchase,
                  helpful_votes
                FROM product_reviews
                WHERE product_id = %s
                  AND rating IS NOT NULL
                  AND rating <= 3
                ORDER BY
                  rating ASC,
                  review_at DESC,
                  collected_at DESC
                LIMIT 5
                """,
                (product_id,),
            )
            samples = cursor.fetchall()
            cursor.execute(
                """
                SELECT
                  COUNT(*) AS imported_review_count,
                  SUM(
                    CASE
                      WHEN COALESCE(NULLIF(source_url, ''), '') <> ''
                        OR COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.source_file')), ''), '') <> ''
                      THEN 1 ELSE 0
                    END
                  ) AS traceable_review_count
                FROM product_reviews
                WHERE product_id = %s
                """,
                (product_id,),
            )
            evidence_row = cursor.fetchone() or {}

    if not insight and not samples:
        return _empty_result("暂未采集评论内容，无法形成评论痛点分析。建议后续采集商品详情页评论或导入评论样本。")

    return {
        "status": "ready" if insight else "samples_only",
        "insight": _normalize_insight(insight) if insight else None,
        "low_rating_reviews": [_normalize_review(row) for row in samples],
        "evidence": _review_evidence(evidence_row),
    }


def _empty_result(message: str) -> dict[str, Any]:
    return {
        "status": "empty",
        "message": message,
        "insight": None,
        "low_rating_reviews": [],
        "evidence": _review_evidence({}),
    }


def _normalize_insight(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in ("avg_rating",):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = float(value)
    for key in ("insight_date", "updated_at"):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = str(value)
    normalized["pain_points"] = _decode_json(normalized.pop("pain_points_json", None))
    normalized["positive_points"] = _decode_json(normalized.pop("positive_points_json", None))
    return normalized


def _normalize_review(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    if normalized.get("rating") is not None:
        normalized["rating"] = float(normalized["rating"])
    if normalized.get("review_at") is not None:
        normalized["review_at"] = str(normalized["review_at"])
    if normalized.get("verified_purchase") is not None:
        normalized["verified_purchase"] = "是" if normalized["verified_purchase"] else "否"
    return normalized


def _normalize_insight_list_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    if normalized.get("avg_rating") is not None:
        normalized["avg_rating"] = float(normalized["avg_rating"])
    for key in ("insight_date", "updated_at"):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = str(value)
    normalized["pain_points"] = _decode_json(normalized.pop("pain_points_json", None))
    normalized["positive_points"] = _decode_json(normalized.pop("positive_points_json", None))
    review_count = normalized.get("review_count") or 0
    negative_count = normalized.get("negative_count") or 0
    normalized["negative_rate"] = round(negative_count / review_count * 100, 1) if review_count else 0.0
    normalized["evidence"] = _review_evidence(normalized)
    return normalized


def _review_evidence(row: dict[str, Any]) -> dict[str, Any]:
    total = int(row.get("imported_review_count") or row.get("review_count") or 0)
    traceable = int(row.get("traceable_review_count") or 0)
    if total <= 0:
        status = "无样本"
        message = "尚无评论样本。"
    elif traceable <= 0:
        status = "不可核验"
        message = "评论样本缺少来源链接或来源 HTML 文件标记，只能作为演示/待核验线索，不能作为正式选品证据。"
    elif traceable < total:
        status = "部分可追溯"
        message = f"{traceable}/{total} 条评论带有来源定位；未定位样本不应参与高置信结论。"
    else:
        status = "可追溯"
        message = f"{traceable}/{total} 条评论带有来源链接或来源 HTML 文件标记。"
    return {
        "status": status,
        "sample_count": total,
        "traceable_count": traceable,
        "traceable_rate": round(traceable / total * 100, 1) if total else 0.0,
        "message": message,
    }


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 500))


def _build_product_filter(keyword: str | None, *, has_title_zh: bool) -> tuple[str, list[Any]]:
    if not keyword:
        return "", []
    like = f"%{keyword.strip()}%"
    if has_title_zh:
        return "WHERE p.asin LIKE %s OR p.title LIKE %s OR p.title_zh LIKE %s", [like, like, like]
    return "WHERE p.asin LIKE %s OR p.title LIKE %s", [like, like]


def _product_title_select(has_title_zh: bool) -> str:
    if has_title_zh:
        return "COALESCE(NULLIF(p.title_zh, ''), p.title) AS title, p.title AS title_original, p.title_zh"
    return "p.title"


def _review_sample_select(has_review_translation: bool) -> str:
    if has_review_translation:
        return (
            "COALESCE(NULLIF(title_zh, ''), title) AS title, "
            "title AS title_original, title_zh, "
            "COALESCE(NULLIF(body_zh, ''), body) AS body, "
            "body AS body_original, body_zh"
        )
    return "title, body"


def _decode_json(value: Any) -> Any:
    if value in (None, ""):
        return []
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []
