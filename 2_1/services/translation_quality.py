"""Translation quality sampling without updating product or review rows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import csv
from typing import Any

from database.mysql_client import MySQLClient
from services.translation import BaseTranslator, TranslationConfig, build_translator, load_translation_config
from services.translation_cache import translate_with_cache


@dataclass(frozen=True)
class TranslationSample:
    source_type: str
    source_id: int
    asin: str | None
    rating: float | None
    source_text: str | None
    source_lang: str | None
    target_lang: str
    engine: str
    status: str
    translated_text: str | None
    error_message: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "asin": self.asin or "",
            "rating": "" if self.rating is None else self.rating,
            "source_lang": self.source_lang or "",
            "target_lang": self.target_lang,
            "engine": self.engine,
            "status": self.status,
            "source_text": self.source_text or "",
            "translated_text": self.translated_text or "",
            "error_message": self.error_message or "",
        }


def collect_translation_samples(
    *,
    product_limit: int = 20,
    review_limit: int = 3,
    include_products: bool = True,
    include_reviews: bool = True,
    use_cache: bool = False,
    client: MySQLClient | None = None,
) -> list[TranslationSample]:
    db = client or MySQLClient()
    config = load_translation_config()
    runtime_config = config if use_cache else replace(config, use_cache=False)
    translator = build_translator(runtime_config)
    samples: list[TranslationSample] = []

    with db.connect() as conn:
        with conn.cursor() as cursor:
            if use_cache and runtime_config.enabled and runtime_config.use_cache:
                db.ensure_translation_cache_table(cursor)
            if include_products:
                for row in _fetch_product_rows(cursor, product_limit):
                    samples.append(
                        _translate_sample(
                            cursor,
                            translator,
                            runtime_config,
                            source_type="product_title",
                            source_id=int(row["id"]),
                            asin=row.get("asin"),
                            rating=None,
                            text=row.get("title"),
                            use_cache=use_cache,
                        )
                    )
            if include_reviews:
                for row in _fetch_review_rows(cursor, review_limit):
                    title = row.get("title")
                    if title:
                        samples.append(
                            _translate_sample(
                                cursor,
                                translator,
                                runtime_config,
                                source_type="review_title",
                                source_id=int(row["id"]),
                                asin=row.get("asin"),
                                rating=_to_float(row.get("rating")),
                                text=title,
                                use_cache=use_cache,
                            )
                        )
                    body = row.get("body")
                    if body:
                        samples.append(
                            _translate_sample(
                                cursor,
                                translator,
                                runtime_config,
                                source_type="review_body",
                                source_id=int(row["id"]),
                                asin=row.get("asin"),
                                rating=_to_float(row.get("rating")),
                                text=body,
                                use_cache=use_cache,
                            )
                        )
    return samples


def export_translation_samples_csv(samples: list[TranslationSample], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_type",
        "source_id",
        "asin",
        "rating",
        "source_lang",
        "target_lang",
        "engine",
        "status",
        "source_text",
        "translated_text",
        "error_message",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            writer.writerow(sample.to_row())
    return path


def _fetch_product_rows(cursor: Any, limit: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, asin, title
        FROM products
        WHERE title IS NOT NULL
          AND title <> ''
        ORDER BY last_seen_at DESC, id DESC
        LIMIT %s
        """,
        (_normalize_limit(limit),),
    )
    return list(cursor.fetchall())


def _fetch_review_rows(cursor: Any, limit: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT r.id, p.asin, r.rating, r.title, r.body
        FROM product_reviews r
        JOIN products p ON p.id = r.product_id
        WHERE (r.title IS NOT NULL AND r.title <> '')
           OR (r.body IS NOT NULL AND r.body <> '')
        ORDER BY r.rating ASC, r.collected_at DESC, r.id DESC
        LIMIT %s
        """,
        (_normalize_limit(limit),),
    )
    return list(cursor.fetchall())


def _translate_sample(
    cursor: Any,
    translator: BaseTranslator,
    config: TranslationConfig,
    *,
    source_type: str,
    source_id: int,
    asin: str | None,
    rating: float | None,
    text: str | None,
    use_cache: bool,
) -> TranslationSample:
    if use_cache:
        result = translate_with_cache(cursor, translator, config, text)
    else:
        result = translator.translate_text(text)
    return TranslationSample(
        source_type=source_type,
        source_id=source_id,
        asin=asin,
        rating=rating,
        source_text=text,
        source_lang=result.source_lang,
        target_lang=result.target_lang,
        engine=result.engine,
        status=result.status,
        translated_text=result.translated_text,
        error_message=result.error_message,
    )


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(value, 500))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
