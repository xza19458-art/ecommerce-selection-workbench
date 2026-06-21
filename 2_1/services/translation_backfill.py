"""Backfill product and review translation fields from existing MySQL rows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from database.mysql_client import MySQLClient
from services.translation import BaseTranslator, TranslationConfig, build_translator, load_translation_config
from services.translation_cache import translate_with_cache


@dataclass
class TranslationBackfillSummary:
    products_checked: int = 0
    products_updated: int = 0
    reviews_checked: int = 0
    reviews_updated: int = 0
    translated: int = 0
    already_target: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = False
    migration_needed: bool = False


def backfill_translations(
    *,
    limit: int = 100,
    include_products: bool = True,
    include_reviews: bool = True,
    dry_run: bool = False,
    client: MySQLClient | None = None,
) -> TranslationBackfillSummary:
    db = client or MySQLClient()
    config = load_translation_config()
    runtime_config = replace(config, use_cache=False) if dry_run else config
    translator = build_translator(runtime_config)
    summary = TranslationBackfillSummary(dry_run=dry_run)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            has_product_columns = db.has_columns(cursor, "products", ("title_zh", "title_translation_status"))
            has_review_columns = db.has_columns(cursor, "product_reviews", ("title_zh", "body_zh", "review_translation_status"))
            if dry_run and (
                (include_products and not has_product_columns)
                or (include_reviews and not has_review_columns)
            ):
                summary.migration_needed = True
                return summary

            db.ensure_translation_columns(cursor)
            if runtime_config.enabled and runtime_config.use_cache:
                db.ensure_translation_cache_table(cursor)
            if include_products and runtime_config.translate_products:
                _backfill_products(cursor, translator, runtime_config, summary, limit=limit, dry_run=dry_run)
            if include_reviews and runtime_config.translate_reviews:
                _backfill_reviews(cursor, translator, runtime_config, summary, limit=limit, dry_run=dry_run)

    return summary


def _backfill_products(
    cursor: Any,
    translator: BaseTranslator,
    config: TranslationConfig,
    summary: TranslationBackfillSummary,
    *,
    limit: int,
    dry_run: bool,
) -> None:
    cursor.execute(
        """
        SELECT id, title
        FROM products
        WHERE title IS NOT NULL
          AND title <> ''
          AND (title_zh IS NULL OR title_zh = '')
        ORDER BY updated_at DESC, id DESC
        LIMIT %s
        """,
        (_normalize_limit(limit),),
    )
    rows = cursor.fetchall()
    summary.products_checked += len(rows)
    for row in rows:
        result = translate_with_cache(cursor, translator, config, row.get("title"))
        _count_result(summary, result.status)
        if dry_run or not _should_update(result.status):
            continue
        cursor.execute(
            """
            UPDATE products
            SET title_zh = %s,
                title_lang = %s,
                title_translation_status = %s,
                title_translation_engine = %s,
                title_translated_at = %s
            WHERE id = %s
            """,
            (
                result.translated_text,
                result.source_lang,
                result.status,
                result.engine,
                result.translated_at,
                row["id"],
            ),
        )
        summary.products_updated += int(cursor.rowcount or 0)


def _backfill_reviews(
    cursor: Any,
    translator: BaseTranslator,
    config: TranslationConfig,
    summary: TranslationBackfillSummary,
    *,
    limit: int,
    dry_run: bool,
) -> None:
    cursor.execute(
        """
        SELECT id, title, body
        FROM product_reviews
        WHERE (
            title IS NOT NULL
            AND title <> ''
            AND (title_zh IS NULL OR title_zh = '')
          )
          OR (
            body IS NOT NULL
            AND body <> ''
            AND (body_zh IS NULL OR body_zh = '')
          )
        ORDER BY collected_at DESC, id DESC
        LIMIT %s
        """,
        (_normalize_limit(limit),),
    )
    rows = cursor.fetchall()
    summary.reviews_checked += len(rows)
    for row in rows:
        title_result = translate_with_cache(cursor, translator, config, row.get("title"))
        body_result = translate_with_cache(cursor, translator, config, row.get("body"))
        _count_result(summary, title_result.status)
        _count_result(summary, body_result.status)
        if dry_run or not (_should_update(title_result.status) or _should_update(body_result.status)):
            continue
        cursor.execute(
            """
            UPDATE product_reviews
            SET title_zh = %s,
                body_zh = %s,
                review_lang = %s,
                review_translation_status = %s,
                review_translation_engine = %s,
                review_translated_at = %s
            WHERE id = %s
            """,
            (
                title_result.translated_text,
                body_result.translated_text,
                body_result.source_lang or title_result.source_lang,
                _combine_status(title_result.status, body_result.status),
                body_result.engine or title_result.engine,
                body_result.translated_at or title_result.translated_at,
                row["id"],
            ),
        )
        summary.reviews_updated += int(cursor.rowcount or 0)


def _count_result(summary: TranslationBackfillSummary, status: str) -> None:
    if status == "translated":
        summary.translated += 1
    elif status == "already_target":
        summary.already_target += 1
    elif status == "failed":
        summary.failed += 1
    else:
        summary.skipped += 1


def _should_update(status: str) -> bool:
    return status in {"translated", "already_target", "failed", "partial"}


def _combine_status(title_status: str, body_status: str) -> str:
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


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 5000))
