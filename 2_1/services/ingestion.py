"""Parse, validate, score, and persist Amazon search HTML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from analysis.scoring import score_record
from database.mysql_client import MySQLClient
from parsers.amazon_search_parser import DISPLAY_FIELDS, AmazonProductRecord, parse_amazon_search_html
from services.translation import BaseTranslator, TranslationConfig, build_translator, load_translation_config
from services.translation_cache import translate_with_cache


@dataclass(frozen=True)
class IngestionSummary:
    total_found: int
    total_valid: int
    total_rejected: int
    total_inserted: int
    rejected_reasons: dict[str, int]


def parse_html_files(
    html_files: Iterable[str | Path],
    *,
    keyword: str | None = None,
    marketplace: str = "US",
    snapshot_at: datetime | None = None,
    require_complete: bool = True,
) -> tuple[list[AmazonProductRecord], list[AmazonProductRecord]]:
    valid_records: list[AmazonProductRecord] = []
    rejected_records: list[AmazonProductRecord] = []
    seen: set[tuple[str, datetime]] = set()

    for html_file in html_files:
        result = parse_amazon_search_html(
            html_file,
            keyword=keyword,
            marketplace=marketplace,
            snapshot_at=snapshot_at,
            require_complete=require_complete,
        )
        for record in result.records:
            key = (record.asin, record.snapshot_at)
            if key in seen:
                record.reject_reasons.append("跨文件重复ASIN")
                rejected_records.append(record)
                continue
            seen.add(key)
            valid_records.append(record)
        rejected_records.extend(result.rejected_records)

    return valid_records, rejected_records


def ingest_html_files_to_mysql(
    html_files: Iterable[str | Path],
    *,
    keyword: str | None = None,
    marketplace: str = "US",
    snapshot_at: datetime | None = None,
    url: str | None = None,
    pages: int | None = None,
    client: MySQLClient | None = None,
    require_complete: bool = True,
) -> IngestionSummary:
    db = client or MySQLClient()
    translation_config = load_translation_config()
    translator = build_translator(translation_config)
    records, rejected = parse_html_files(
        html_files,
        keyword=keyword,
        marketplace=marketplace,
        snapshot_at=snapshot_at,
        require_complete=require_complete,
    )
    inserted = 0

    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_translation_columns(cursor)
            if translation_config.enabled and translation_config.use_cache:
                db.ensure_translation_cache_table(cursor)
            job_id = db.create_job(cursor, keyword, url, pages)
            keyword_id = db.upsert_keyword(cursor, keyword, marketplace)
            try:
                for record in records:
                    _apply_product_translation(cursor, record, translator, translation_config)
                    product_id = db.upsert_product(cursor, record)
                    db.upsert_snapshot(cursor, product_id, record)
                    db.upsert_keyword_rank(cursor, keyword_id, product_id, record)
                    db.upsert_score(cursor, product_id, keyword_id, score_record(record), record.snapshot_at.date())
                    inserted += 1
                db.finish_job(
                    cursor,
                    job_id,
                    "完成",
                    total_found=len(records) + len(rejected),
                    total_valid=len(records),
                    total_inserted=inserted,
                )
            except Exception as exc:
                db.finish_job(
                    cursor,
                    job_id,
                    "失败",
                    total_found=len(records) + len(rejected),
                    total_valid=len(records),
                    total_inserted=inserted,
                    error_message=str(exc),
                )
                raise

    return IngestionSummary(
        total_found=len(records) + len(rejected),
        total_valid=len(records),
        total_rejected=len(rejected),
        total_inserted=inserted,
        rejected_reasons=count_rejected_reasons(rejected),
    )


def count_rejected_reasons(records: Iterable[AmazonProductRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for reason in record.reject_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _apply_product_translation(
    cursor: object,
    record: AmazonProductRecord,
    translator: BaseTranslator,
    config: TranslationConfig,
) -> None:
    if not config.translate_products:
        return
    result = translate_with_cache(cursor, translator, config, record.title)
    record.title_lang = result.source_lang
    record.title_translation_status = result.status
    record.title_translation_engine = result.engine
    record.title_translated_at = result.translated_at
    if result.translated_text:
        record.title_zh = result.translated_text


def export_preview(
    valid_records: list[AmazonProductRecord],
    rejected_records: list[AmazonProductRecord],
    output_dir: str | Path,
    *,
    prefix: str = "amazon_parse_preview",
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    valid_path = output / f"{prefix}_valid.csv"
    rejected_path = output / f"{prefix}_rejected.csv"

    valid_df = pd.DataFrame([record.to_chinese_dict() for record in valid_records])
    rejected_rows = []
    for record in rejected_records:
        row = record.to_chinese_dict()
        row["拒绝原因"] = "；".join(record.reject_reasons)
        rejected_rows.append(row)
    rejected_df = pd.DataFrame(rejected_rows)

    if not valid_df.empty:
        valid_df.rename(columns=DISPLAY_FIELDS, inplace=True)
    valid_df.to_csv(valid_path, index=False, encoding="utf-8-sig")
    rejected_df.to_csv(rejected_path, index=False, encoding="utf-8-sig")
    return valid_path, rejected_path
