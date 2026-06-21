"""Import local review CSV/JSON files and build simple pain-point insights."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from database.mysql_client import MySQLClient
from services.translation import BaseTranslator, TranslationConfig, build_translator, load_translation_config
from services.translation_cache import translate_with_cache


@dataclass(frozen=True)
class ReviewRecord:
    asin: str
    review_id: str | None
    rating: float | None
    title: str | None
    body: str
    review_at: datetime | None
    reviewer_name: str | None
    verified_purchase: bool | None
    helpful_votes: int | None
    variant_info: str | None
    source_url: str | None
    raw: dict[str, Any]
    title_zh: str | None = None
    body_zh: str | None = None
    review_lang: str | None = None
    review_translation_status: str | None = None
    review_translation_engine: str | None = None
    review_translated_at: datetime | None = None

    @property
    def content_hash(self) -> str:
        text = "|".join(
            [
                self.asin.strip().upper(),
                str(self.rating or ""),
                (self.title or "").strip().lower(),
                self.body.strip().lower(),
            ]
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ReviewImportSummary:
    total_found: int = 0
    total_valid: int = 0
    total_rejected: int = 0
    total_upserted: int = 0
    insights_generated: int = 0
    involved_asins: set[str] = field(default_factory=set)
    rejected_reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.total_rejected += 1
        self.rejected_reasons[reason] = self.rejected_reasons.get(reason, 0) + 1


PAIN_THEMES = {
    "尺寸/适配问题": ("size", "fit", "small", "large", "tight", "loose", "尺寸", "尺码", "太小", "太大"),
    "质量/做工问题": ("quality", "cheap", "poor", "defect", "broken", "质量", "做工", "瑕疵"),
    "气味/刺激问题": ("smell", "odor", "scent", "chemical", "rash", "irritat", "气味", "异味", "刺激"),
    "包装/泄漏问题": ("package", "packaging", "leak", "spill", "damaged", "包装", "破损", "漏"),
    "耐用/易坏问题": ("durable", "broke", "rip", "tear", "last", "耐用", "坏了", "破了"),
    "功能/效果不达预期": ("not work", "doesn't work", "ineffective", "waste", "效果", "没用", "无效"),
    "缺件/物流问题": ("missing", "delivery", "arrived", "late", "缺", "少", "物流", "配送"),
}

POSITIVE_THEMES = {
    "质量认可": ("quality", "well made", "great", "excellent", "质量", "做工"),
    "使用方便": ("easy", "convenient", "simple", "方便", "好用"),
    "舒适体验": ("comfortable", "soft", "gentle", "舒适", "柔软"),
    "性价比认可": ("value", "price", "worth", "性价比", "划算"),
    "包装认可": ("package", "packaging", "sealed", "包装"),
}

NEGATIVE_STAR_WEIGHTS = {
    1: 1.00,
    2: 0.80,
    3: 0.45,
}

POSITIVE_STAR_WEIGHTS = {
    4: 0.60,
    5: 1.00,
}


def import_reviews_from_file(
    file_path: str | Path,
    *,
    default_asin: str | None = None,
    client: MySQLClient | None = None,
) -> ReviewImportSummary:
    """Import review records from CSV or JSON and refresh review insights."""
    path = Path(file_path)
    rows = _read_review_rows(path)
    summary = ReviewImportSummary(total_found=len(rows))
    db = client or MySQLClient()
    translation_config = load_translation_config()
    translator = build_translator(translation_config)
    touched_product_ids: set[int] = set()
    seen_keys: set[tuple[str, str]] = set()

    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_translation_columns(cursor)
            if translation_config.enabled and translation_config.use_cache:
                db.ensure_translation_cache_table(cursor)
            for row in rows:
                try:
                    record = _row_to_record(row, default_asin=default_asin)
                except ValueError as exc:
                    summary.reject(str(exc))
                    continue
                duplicate_key = (record.asin, record.content_hash)
                if duplicate_key in seen_keys:
                    summary.reject("文件内重复评论")
                    continue
                seen_keys.add(duplicate_key)

                cursor.execute(
                    """
                    SELECT id
                    FROM products
                    WHERE asin = %s
                    LIMIT 1
                    """,
                    (record.asin,),
                )
                product = cursor.fetchone()
                if not product:
                    summary.reject(f"ASIN 未入库: {record.asin}")
                    continue

                product_id = int(product["id"])
                summary.total_valid += 1
                summary.involved_asins.add(record.asin)
                record = _apply_review_translation(cursor, record, translator, translation_config)
                affected = _upsert_review(cursor, product_id, record)
                if affected:
                    summary.total_upserted += 1
                touched_product_ids.add(product_id)

            for product_id in touched_product_ids:
                _refresh_review_insight(cursor, product_id)
                summary.insights_generated += 1

    return summary


def preview_reviews_from_file(
    file_path: str | Path,
    *,
    default_asin: str | None = None,
    client: MySQLClient | None = None,
) -> ReviewImportSummary:
    """Validate a review CSV/JSON file without writing to MySQL."""
    path = Path(file_path)
    rows = _read_review_rows(path)
    summary = ReviewImportSummary(total_found=len(rows))
    db = client or MySQLClient()
    seen_keys: set[tuple[str, str]] = set()

    with db.connect() as conn:
        with conn.cursor() as cursor:
            for row in rows:
                try:
                    record = _row_to_record(row, default_asin=default_asin)
                except ValueError as exc:
                    summary.reject(str(exc))
                    continue

                duplicate_key = (record.asin, record.content_hash)
                if duplicate_key in seen_keys:
                    summary.reject("文件内重复评论")
                    continue
                seen_keys.add(duplicate_key)

                cursor.execute(
                    """
                    SELECT id
                    FROM products
                    WHERE asin = %s
                    LIMIT 1
                    """,
                    (record.asin,),
                )
                product = cursor.fetchone()
                if not product:
                    summary.reject(f"ASIN 未入库: {record.asin}")
                    continue

                summary.total_valid += 1
                summary.involved_asins.add(record.asin)

    return summary


def _read_review_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("reviews", [])
        if not isinstance(data, list):
            raise ValueError("JSON 评论文件必须是数组，或包含 reviews 数组")
        return [dict(row) for row in data if isinstance(row, dict)]
    raise ValueError("仅支持 CSV 或 JSON 评论文件")


def _row_to_record(row: dict[str, Any], *, default_asin: str | None) -> ReviewRecord:
    asin = _first_value(row, "asin", "ASIN") or default_asin
    body = _first_value(row, "body", "content", "review_body", "评论正文", "评论内容")
    if not asin:
        raise ValueError("缺少 ASIN")
    if not body:
        raise ValueError("缺少评论正文")

    rating = _parse_float(_first_value(row, "rating", "stars", "评分"))
    if rating is not None and not 0 <= rating <= 5:
        raise ValueError("评论评分无效")

    return ReviewRecord(
        asin=str(asin).strip().upper(),
        review_id=_clean_text(_first_value(row, "review_id", "id", "评论ID")),
        rating=rating,
        title=_clean_text(_first_value(row, "title", "review_title", "评论标题")),
        body=str(body).strip(),
        review_at=_parse_datetime(_first_value(row, "review_at", "date", "评论时间")),
        reviewer_name=_clean_text(_first_value(row, "reviewer_name", "reviewer", "评论者")),
        verified_purchase=_parse_bool(_first_value(row, "verified_purchase", "verified", "是否验证购买")),
        helpful_votes=_parse_int(_first_value(row, "helpful_votes", "helpful", "有用票数")),
        variant_info=_clean_text(_first_value(row, "variant_info", "variant", "变体信息")),
        source_url=_clean_text(_first_value(row, "source_url", "url", "来源链接")),
        raw=row,
    )


def _apply_review_translation(
    cursor: object,
    record: ReviewRecord,
    translator: BaseTranslator,
    config: TranslationConfig,
) -> ReviewRecord:
    if not config.translate_reviews:
        return record

    title_result = translate_with_cache(cursor, translator, config, record.title)
    body_result = translate_with_cache(cursor, translator, config, record.body)
    translated_at = body_result.translated_at or title_result.translated_at

    return replace(
        record,
        title_zh=title_result.translated_text,
        body_zh=body_result.translated_text,
        review_lang=body_result.source_lang or title_result.source_lang,
        review_translation_status=_combine_translation_status(title_result.status, body_result.status),
        review_translation_engine=body_result.engine or title_result.engine,
        review_translated_at=translated_at,
    )


def _combine_translation_status(title_status: str, body_status: str) -> str:
    statuses = {title_status, body_status}
    if "translated" in statuses and statuses <= {"translated", "empty"}:
        return "translated"
    if "already_target" in statuses and statuses <= {"already_target", "empty"}:
        return "already_target"
    if "failed" in statuses and ("translated" in statuses or "already_target" in statuses):
        return "partial"
    if "failed" in statuses:
        return "failed"
    if "skipped" in statuses:
        return "skipped"
    if statuses == {"empty"}:
        return "empty"
    return body_status or title_status


def _upsert_review(cursor: Any, product_id: int, record: ReviewRecord) -> int:
    cursor.execute(
        """
        INSERT INTO product_reviews (
          product_id, review_id, content_hash, rating, title, title_zh, body, body_zh,
          review_lang, review_translation_status, review_translation_engine, review_translated_at, review_at,
          reviewer_name, verified_purchase, helpful_votes, variant_info,
          source_url, raw_json, collected_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          review_id = COALESCE(VALUES(review_id), review_id),
          rating = COALESCE(VALUES(rating), rating),
          title = COALESCE(VALUES(title), title),
          title_zh = COALESCE(VALUES(title_zh), title_zh),
          body = VALUES(body),
          body_zh = COALESCE(VALUES(body_zh), body_zh),
          review_lang = COALESCE(VALUES(review_lang), review_lang),
          review_translation_status = CASE
            WHEN VALUES(body_zh) IS NOT NULL OR VALUES(title_zh) IS NOT NULL THEN VALUES(review_translation_status)
            WHEN review_translation_status IS NULL THEN VALUES(review_translation_status)
            ELSE review_translation_status
          END,
          review_translation_engine = CASE
            WHEN VALUES(body_zh) IS NOT NULL OR VALUES(title_zh) IS NOT NULL THEN VALUES(review_translation_engine)
            WHEN review_translation_engine IS NULL THEN VALUES(review_translation_engine)
            ELSE review_translation_engine
          END,
          review_translated_at = CASE
            WHEN VALUES(body_zh) IS NOT NULL OR VALUES(title_zh) IS NOT NULL THEN VALUES(review_translated_at)
            ELSE review_translated_at
          END,
          review_at = COALESCE(VALUES(review_at), review_at),
          reviewer_name = COALESCE(VALUES(reviewer_name), reviewer_name),
          verified_purchase = COALESCE(VALUES(verified_purchase), verified_purchase),
          helpful_votes = COALESCE(VALUES(helpful_votes), helpful_votes),
          variant_info = COALESCE(VALUES(variant_info), variant_info),
          source_url = COALESCE(VALUES(source_url), source_url),
          raw_json = VALUES(raw_json),
          collected_at = VALUES(collected_at)
        """,
        (
            product_id,
            record.review_id,
            record.content_hash,
            record.rating,
            record.title,
            record.title_zh,
            record.body,
            record.body_zh,
            record.review_lang,
            record.review_translation_status,
            record.review_translation_engine,
            record.review_translated_at,
            record.review_at,
            record.reviewer_name,
            None if record.verified_purchase is None else int(record.verified_purchase),
            record.helpful_votes,
            record.variant_info,
            record.source_url,
            json.dumps(record.raw, ensure_ascii=False),
            datetime.now(),
        ),
    )
    return int(cursor.rowcount or 0)


def _refresh_review_insight(cursor: Any, product_id: int) -> None:
    cursor.execute(
        """
        SELECT rating, title, body, helpful_votes
        FROM product_reviews
        WHERE product_id = %s
        """,
        (product_id,),
    )
    reviews = cursor.fetchall()
    review_count = len(reviews)
    ratings = [float(row["rating"]) for row in reviews if row.get("rating") is not None]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
    negative_reviews = [row for row in reviews if row.get("rating") is not None and float(row["rating"]) <= 3]
    positive_reviews = [row for row in reviews if row.get("rating") is not None and float(row["rating"]) >= 4]
    star_counts = _count_stars(reviews)

    pain_points = _detect_weighted_themes(negative_reviews, PAIN_THEMES, NEGATIVE_STAR_WEIGHTS)
    positive_points = _detect_weighted_themes(positive_reviews, POSITIVE_THEMES, POSITIVE_STAR_WEIGHTS)
    risk_summary = _build_risk_summary(review_count, len(negative_reviews), pain_points, star_counts)
    opportunity_summary = _build_opportunity_summary(pain_points)

    cursor.execute(
        """
        INSERT INTO product_review_insights (
          product_id, insight_date, review_count, negative_count, avg_rating,
          pain_points_json, positive_points_json, opportunity_summary, risk_summary
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          review_count = VALUES(review_count),
          negative_count = VALUES(negative_count),
          avg_rating = VALUES(avg_rating),
          pain_points_json = VALUES(pain_points_json),
          positive_points_json = VALUES(positive_points_json),
          opportunity_summary = VALUES(opportunity_summary),
          risk_summary = VALUES(risk_summary)
        """,
        (
            product_id,
            date.today(),
            review_count,
            len(negative_reviews),
            avg_rating,
            json.dumps(pain_points, ensure_ascii=False),
            json.dumps(positive_points, ensure_ascii=False),
            opportunity_summary,
            risk_summary,
        ),
    )


def _detect_weighted_themes(
    rows: list[dict[str, Any]],
    themes: dict[str, tuple[str, ...]],
    star_weights: dict[int, float],
) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        text = f"{row.get('title') or ''} {row.get('body') or ''}".lower()
        star = _rating_to_star(row.get("rating"))
        weight = star_weights.get(star, 0.0)
        if weight <= 0:
            continue
        helpful_boost = _helpful_boost(row.get("helpful_votes"))
        for theme, keywords in themes.items():
            if any(keyword.lower() in text for keyword in keywords):
                item = stats.setdefault(
                    theme,
                    {
                        "theme": theme,
                        "count": 0,
                        "weighted_score": 0.0,
                        "star_counts": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
                    },
                )
                item["count"] += 1
                item["weighted_score"] += weight * helpful_boost
                item["star_counts"][str(star)] += 1

    results = []
    for item in stats.values():
        item["weighted_score"] = round(item["weighted_score"], 2)
        item["severity_level"] = _theme_severity(item["weighted_score"], item["count"])
        results.append(item)
    return sorted(results, key=lambda row: (row["weighted_score"], row["count"]), reverse=True)[:6]


def _count_stars(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "unknown": 0}
    for row in rows:
        star = _rating_to_star(row.get("rating"))
        if star is None:
            counts["unknown"] += 1
        else:
            counts[str(star)] += 1
    return counts


def _rating_to_star(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    star = int(round(rating))
    return max(1, min(5, star))


def _helpful_boost(value: Any) -> float:
    try:
        votes = int(value or 0)
    except (TypeError, ValueError):
        votes = 0
    if votes >= 50:
        return 1.35
    if votes >= 10:
        return 1.20
    if votes >= 3:
        return 1.10
    return 1.0


def _theme_severity(weighted_score: float, count: int) -> str:
    if weighted_score >= 8 or count >= 10:
        return "高"
    if weighted_score >= 3 or count >= 4:
        return "中"
    return "低"


def _build_risk_summary(
    review_count: int,
    negative_count: int,
    pain_points: list[dict[str, Any]],
    star_counts: dict[str, int],
) -> str:
    if review_count == 0:
        return "暂无评论样本，无法判断评论风险。"
    negative_rate = negative_count / review_count
    weighted_negative = sum(
        star_counts.get(str(star), 0) * weight
        for star, weight in NEGATIVE_STAR_WEIGHTS.items()
    )
    weighted_rate = weighted_negative / review_count
    parts: list[str] = []
    confidence = _sample_confidence(review_count, negative_count)
    parts.append(f"样本置信度{confidence}")

    if weighted_rate >= 0.25 or negative_rate >= 0.35:
        parts.append("低分加权风险较高，需谨慎验证产品质量和预期管理")
    elif weighted_rate >= 0.10 or negative_rate >= 0.15:
        parts.append("存在一定低分反馈，需要定位主要差评原因")
    else:
        parts.append("低分评论占比暂不高")
    if pain_points:
        top = pain_points[0]
        parts.append(f"主要痛点集中在{top['theme']}，风险权重{top.get('weighted_score')}")
    return "；".join(parts)


def _build_opportunity_summary(pain_points: list[dict[str, Any]]) -> str:
    if not pain_points:
        return "暂未识别出明确改良机会，建议补充更多低分评论样本。"
    top_themes = "、".join(point["theme"] for point in pain_points[:3])
    return f"可优先围绕{top_themes}做差异化改良验证。"


def _sample_confidence(review_count: int, negative_count: int) -> str:
    if review_count >= 100 and negative_count >= 20:
        return "高"
    if review_count >= 30 and negative_count >= 8:
        return "中"
    return "低"


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _clean_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def _parse_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "verified", "是"):
        return True
    if text in ("0", "false", "no", "n", "否"):
        return False
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
