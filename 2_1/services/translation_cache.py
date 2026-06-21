"""Database-backed translation cache helpers."""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any

from services.translation import BaseTranslator, TranslationConfig, TranslationResult
from services.translation_rules import detect_language


def translate_with_cache(
    cursor: Any,
    translator: BaseTranslator,
    config: TranslationConfig,
    text: str | None,
) -> TranslationResult:
    """Translate text with optional database cache reuse."""
    if not text or not text.strip():
        return translator.translate_text(text)
    if not config.enabled or not config.use_cache:
        return translator.translate_text(text)

    source_lang = detect_language(text) or config.source_lang or "unknown"
    source_hash = _hash_text(text)
    engine = translator.engine
    cached = _fetch_cached_result(
        cursor,
        source_hash=source_hash,
        source_lang=source_lang,
        target_lang=config.target_lang,
        engine=engine,
    )
    if cached:
        return cached

    result = translator.translate_text(text)
    _store_result(
        cursor,
        source_hash=source_hash,
        source_lang=result.source_lang or source_lang,
        target_lang=result.target_lang,
        engine=result.engine,
        source_text=text,
        result=result,
    )
    return result


def _fetch_cached_result(
    cursor: Any,
    *,
    source_hash: str,
    source_lang: str,
    target_lang: str,
    engine: str,
) -> TranslationResult | None:
    cursor.execute(
        """
        SELECT source_text, translated_text, source_lang, target_lang, engine,
               status, error_message, translated_at
        FROM translation_cache
        WHERE source_hash = %s
          AND source_lang = %s
          AND target_lang = %s
          AND engine = %s
        LIMIT 1
        """,
        (source_hash, source_lang, target_lang, engine),
    )
    row = cursor.fetchone()
    if not row:
        return None
    translated_at = row.get("translated_at")
    if translated_at is not None and not isinstance(translated_at, datetime):
        translated_at = None
    return TranslationResult(
        source_text=row.get("source_text"),
        translated_text=row.get("translated_text"),
        source_lang=row.get("source_lang"),
        target_lang=row.get("target_lang") or target_lang,
        engine=row.get("engine") or engine,
        status=row.get("status") or "cached",
        translated_at=translated_at,
        error_message=row.get("error_message"),
    )


def _store_result(
    cursor: Any,
    *,
    source_hash: str,
    source_lang: str,
    target_lang: str,
    engine: str,
    source_text: str,
    result: TranslationResult,
) -> None:
    cursor.execute(
        """
        INSERT INTO translation_cache (
          source_hash, source_lang, target_lang, engine, source_text,
          translated_text, status, error_message, translated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          source_text = VALUES(source_text),
          translated_text = VALUES(translated_text),
          status = VALUES(status),
          error_message = VALUES(error_message),
          translated_at = VALUES(translated_at)
        """,
        (
            source_hash,
            source_lang,
            target_lang,
            engine,
            source_text,
            result.translated_text,
            result.status,
            result.error_message,
            result.translated_at,
        ),
    )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
