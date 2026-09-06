"""Parse, validate, score, and persist Amazon search HTML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
from typing import Iterable

import pandas as pd

from analysis.scoring import score_record
from database.mysql_client import MySQLClient
from parsers.amazon_search_parser import DISPLAY_FIELDS, AmazonProductRecord, parse_amazon_search_html
from pkg_paths import resolve_user_writable_path
from services.translation import BaseTranslator, TranslationConfig, build_translator, load_translation_config
from services.translation_cache import translate_with_cache


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionSummary:
    total_found: int
    total_valid: int
    total_rejected: int
    total_inserted: int
    rejected_reasons: dict[str, int]
    warnings: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class PreparedIngestionBatch:
    """Immutable parse result used to bind a write to one explicit preview."""

    files: tuple[Path, ...]
    records: tuple[AmazonProductRecord, ...]
    rejected_records: tuple[AmazonProductRecord, ...]
    keyword: str | None
    marketplace: str
    snapshot_at: datetime | None
    require_complete: bool
    confirmation_token: str
    batch_fingerprint: str


@dataclass(frozen=True)
class _ParsedPage:
    order: int
    page_no: int | None
    records: list[AmazonProductRecord]
    rejected_records: list[AmazonProductRecord]


def parse_html_files(
    html_files: Iterable[str | Path],
    *,
    keyword: str | None = None,
    marketplace: str = "US",
    snapshot_at: datetime | None = None,
    require_complete: bool = True,
) -> tuple[list[AmazonProductRecord], list[AmazonProductRecord]]:
    file_paths = [Path(item) for item in html_files]
    inferred_times = _snapshot_times_for_files(file_paths, snapshot_at)
    valid_records: list[AmazonProductRecord] = []
    rejected_records: list[AmazonProductRecord] = []
    parsed_pages: list[_ParsedPage] = []
    seen: set[tuple[str, datetime]] = set()

    for order, file_path in enumerate(file_paths):
        result = parse_amazon_search_html(
            file_path,
            keyword=keyword,
            marketplace=marketplace,
            snapshot_at=inferred_times[file_path],
            require_complete=require_complete,
        )
        parsed_pages.append(
            _ParsedPage(
                order=order,
                page_no=_page_no_from_records(result.records, result.rejected_records),
                records=result.records,
                rejected_records=result.rejected_records,
            )
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

    _normalize_parsed_page_batches(parsed_pages)
    return valid_records, rejected_records


def prepare_ingestion_batch(
    html_files: Iterable[str | Path],
    *,
    keyword: str | None = None,
    marketplace: str = "US",
    snapshot_at: datetime | None = None,
    require_complete: bool = True,
) -> PreparedIngestionBatch:
    """Parse once and bind the result to stable source-file fingerprints."""

    files = tuple(Path(item).resolve() for item in html_files)
    if not files:
        raise ValueError("未选择任何 HTML 文件")
    normalized_keyword = _normalize_import_keyword(keyword)
    normalized_marketplace = (marketplace or "US").strip().upper() or "US"
    before_manifest = _source_file_manifest(files)
    records, rejected = parse_html_files(
        files,
        keyword=normalized_keyword,
        marketplace=normalized_marketplace,
        snapshot_at=snapshot_at,
        require_complete=require_complete,
    )
    after_manifest = _source_file_manifest(files)
    if before_manifest != after_manifest:
        raise ValueError("HTML 文件在预览过程中发生变化，请等待文件保存完成后重新预览。")

    payload = {
        "version": 1,
        "keyword": normalized_keyword,
        "marketplace": normalized_marketplace,
        "snapshot_at": snapshot_at.isoformat(sep=" ") if snapshot_at else None,
        "require_complete": require_complete,
        "files": after_manifest,
        "records": [record.to_storage_dict() for record in records],
        "rejected_records": [record.to_storage_dict() for record in rejected],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return PreparedIngestionBatch(
        files=files,
        records=tuple(records),
        rejected_records=tuple(rejected),
        keyword=normalized_keyword,
        marketplace=normalized_marketplace,
        snapshot_at=snapshot_at,
        require_complete=require_complete,
        confirmation_token=f"html-import-v1:{digest}",
        batch_fingerprint=digest[:12].upper(),
    )


def validate_ingestion_confirmation(
    batch: PreparedIngestionBatch,
    confirmation_token: str | None,
    expected_valid: int | None,
) -> None:
    """Reject writes that are not bound to the exact previewed candidate set."""

    token = str(confirmation_token or "").strip()
    if not token or not hmac.compare_digest(token, batch.confirmation_token):
        raise ValueError("HTML 文件、关键词或候选数据已变化，请重新预览后再确认写入。")
    if expected_valid is None or int(expected_valid) != len(batch.records):
        raise ValueError("有效候选数量与预览不一致，请重新预览后再确认写入。")


def estimate_ingestion_impact(
    records: Iterable[AmazonProductRecord],
    *,
    keyword: str | None = None,
    marketplace: str = "US",
    client: MySQLClient | None = None,
) -> dict[str, int | str | None]:
    """Read-only estimate of rows that a prepared batch would add or update."""

    candidates = list(records)
    normalized_keyword = _normalize_import_keyword(keyword)
    normalized_marketplace = (marketplace or "US").strip().upper() or "US"
    candidate_keys = {(str(record.asin), _datetime_key(record.snapshot_at)) for record in candidates}
    asins = sorted({asin for asin, _snapshot_at in candidate_keys})
    snapshot_times = sorted({_snapshot_at for _asin, _snapshot_at in candidate_keys})
    result: dict[str, int | str | None] = {
        "candidate_records": len(candidate_keys),
        "unique_asins": len(asins),
        "new_products": len(asins),
        "existing_products": 0,
        "new_snapshots": len(candidate_keys),
        "existing_snapshots": 0,
        "new_keyword_ranks": len(candidate_keys) if normalized_keyword else None,
        "existing_keyword_ranks": 0 if normalized_keyword else None,
        "keyword_status": "未填写关键词，不写关键词排名" if not normalized_keyword else "将新建关键词",
    }
    if not asins:
        return result

    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            asin_placeholders = ",".join(["%s"] * len(asins))
            cursor.execute(
                f"SELECT id, asin FROM products WHERE marketplace = %s AND asin IN ({asin_placeholders})",
                (normalized_marketplace, *asins),
            )
            product_rows = list(cursor.fetchall() or [])
            product_ids = [int(row["id"]) for row in product_rows]
            asin_by_product_id = {int(row["id"]): str(row["asin"]) for row in product_rows}

            existing_snapshot_keys: set[tuple[str, str]] = set()
            if product_ids and snapshot_times:
                product_placeholders = ",".join(["%s"] * len(product_ids))
                time_placeholders = ",".join(["%s"] * len(snapshot_times))
                cursor.execute(
                    "SELECT product_id, snapshot_at FROM product_snapshots "
                    f"WHERE product_id IN ({product_placeholders}) AND snapshot_at IN ({time_placeholders})",
                    (*product_ids, *snapshot_times),
                )
                existing_snapshot_keys = {
                    (asin_by_product_id[int(row["product_id"])], _datetime_key(row["snapshot_at"]))
                    for row in (cursor.fetchall() or [])
                }

            existing_rank_keys: set[tuple[str, str]] = set()
            keyword_status = result["keyword_status"]
            if normalized_keyword:
                cursor.execute(
                    "SELECT id FROM keywords WHERE marketplace = %s AND keyword = %s",
                    (normalized_marketplace, normalized_keyword),
                )
                keyword_row = cursor.fetchone()
                if keyword_row:
                    keyword_status = "复用已入库关键词"
                    if product_ids and snapshot_times:
                        cursor.execute(
                            "SELECT product_id, snapshot_at FROM keyword_rank_snapshots "
                            f"WHERE keyword_id = %s AND product_id IN ({product_placeholders}) "
                            f"AND snapshot_at IN ({time_placeholders})",
                            (int(keyword_row["id"]), *product_ids, *snapshot_times),
                        )
                        existing_rank_keys = {
                            (asin_by_product_id[int(row["product_id"])], _datetime_key(row["snapshot_at"]))
                            for row in (cursor.fetchall() or [])
                        }

    existing_products = len(product_rows)
    result.update(
        {
            "new_products": len(asins) - existing_products,
            "existing_products": existing_products,
            "new_snapshots": len(candidate_keys - existing_snapshot_keys),
            "existing_snapshots": len(candidate_keys & existing_snapshot_keys),
            "new_keyword_ranks": (
                len(candidate_keys - existing_rank_keys) if normalized_keyword else None
            ),
            "existing_keyword_ranks": (
                len(candidate_keys & existing_rank_keys) if normalized_keyword else None
            ),
            "keyword_status": keyword_status,
        }
    )
    return result


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
    record_job: bool = True,
    prepared_batch: PreparedIngestionBatch | None = None,
) -> IngestionSummary:
    db = client or MySQLClient()
    files = tuple(Path(item).resolve() for item in html_files)
    records: list[AmazonProductRecord] = []
    rejected: list[AmazonProductRecord] = []
    inserted = 0
    warnings: list[str] = []
    if snapshot_at is None and any(_embedded_snapshot_time(path) is None for path in files):
        warnings.append("部分 HTML 文件名未包含采集时间，已使用文件修改时间作为快照时间，请确认文件未被复制改写。")
    job_id = _create_ingestion_job(db, keyword, url, pages) if record_job else None
    if record_job and job_id is None:
        warnings.append("任务中心日志创建失败；本次仍继续尝试写入业务数据。")

    try:
        translation_config = load_translation_config()
        translator = build_translator(translation_config)
        if prepared_batch is None:
            records, rejected = parse_html_files(
                files,
                keyword=keyword,
                marketplace=marketplace,
                snapshot_at=snapshot_at,
                require_complete=require_complete,
            )
        else:
            _validate_prepared_batch_options(
                prepared_batch,
                files=files,
                keyword=keyword,
                marketplace=marketplace,
                snapshot_at=snapshot_at,
                require_complete=require_complete,
            )
            records = list(prepared_batch.records)
            rejected = list(prepared_batch.rejected_records)
        from services.serp_metrics import aggregate_serp_snapshot

        serp_snapshot = aggregate_serp_snapshot(records, rejected) if keyword else None
        with db.connect() as conn:
            with conn.cursor() as cursor:
                keyword_id = db.upsert_keyword(cursor, keyword, marketplace)
                if keyword_id is not None and serp_snapshot is not None:
                    try:
                        from services.serp_metrics import upsert_serp_snapshot

                        upsert_serp_snapshot(cursor, keyword_id, serp_snapshot)
                    except Exception:
                        warning = "SERP 聚合证据写入失败；商品与评分已继续入库，请检查日志后重试仓库证据。"
                        warnings.append(warning)
                        logger.warning(
                            "SERP aggregate persistence failed; continuing core product ingestion for keyword=%s",
                            keyword,
                            exc_info=True,
                        )
                for record in records:
                    _apply_product_translation(cursor, record, translator, translation_config)
                    product_id = db.upsert_product(cursor, record)
                    db.upsert_snapshot(cursor, product_id, record)
                    db.upsert_keyword_rank(cursor, keyword_id, product_id, record)
                    db.upsert_score(cursor, product_id, keyword_id, score_record(record), record.snapshot_at.date())
                    inserted += 1
    except Exception as exc:
        _finish_ingestion_job(
            db,
            job_id,
            "失败",
            total_found=len(records) + len(rejected),
            total_valid=len(records),
            total_inserted=0,
            error_message=str(exc),
        )
        raise

    if not _finish_ingestion_job(
        db,
        job_id,
        "完成",
        total_found=len(records) + len(rejected),
        total_valid=len(records),
        total_inserted=inserted,
        error_message=None,
    ):
        warnings.append("数据已写入，但任务中心状态更新失败，请查看应用日志。")

    return IngestionSummary(
        total_found=len(records) + len(rejected),
        total_valid=len(records),
        total_rejected=len(rejected),
        total_inserted=inserted,
        rejected_reasons=count_rejected_reasons(rejected),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _normalize_import_keyword(keyword: str | None) -> str | None:
    value = str(keyword or "").strip()
    return value or None


def _source_file_manifest(files: tuple[Path, ...]) -> tuple[dict[str, int | str], ...]:
    manifest: list[dict[str, int | str]] = []
    for order, path in enumerate(files):
        if not path.is_file():
            raise ValueError(f"HTML 文件不存在或不可读取：{path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = path.stat()
        manifest.append(
            {
                "order": order,
                "path": path.as_posix(),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
                "sha256": digest.hexdigest(),
            }
        )
    return tuple(manifest)


def _datetime_key(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    return str(value).strip().replace("T", " ")[:19]


def _validate_prepared_batch_options(
    batch: PreparedIngestionBatch,
    *,
    files: tuple[Path, ...],
    keyword: str | None,
    marketplace: str,
    snapshot_at: datetime | None,
    require_complete: bool,
) -> None:
    if files != batch.files:
        raise ValueError("准备批次与待入库 HTML 文件不一致")
    if _normalize_import_keyword(keyword) != batch.keyword:
        raise ValueError("准备批次与待入库关键词不一致")
    if (marketplace or "US").strip().upper() != batch.marketplace:
        raise ValueError("准备批次与待入库站点不一致")
    if snapshot_at != batch.snapshot_at or require_complete != batch.require_complete:
        raise ValueError("准备批次与待入库解析参数不一致")


def _create_ingestion_job(
    db: MySQLClient,
    keyword: str | None,
    url: str | None,
    pages: int | None,
) -> int | None:
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                return db.create_job(cursor, keyword, url, pages)
    except Exception:
        logger.warning("Failed to create ingestion task log; continuing data ingestion.", exc_info=True)
        return None


def _finish_ingestion_job(
    db: MySQLClient,
    job_id: int | None,
    status: str,
    *,
    total_found: int,
    total_valid: int,
    total_inserted: int,
    error_message: str | None,
) -> bool:
    if job_id is None:
        return True
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                db.finish_job(
                    cursor,
                    job_id,
                    status,
                    total_found=total_found,
                    total_valid=total_valid,
                    total_inserted=total_inserted,
                    error_message=error_message,
                )
        return True
    except Exception:
        logger.warning("Failed to finish ingestion task log id=%s status=%s.", job_id, status, exc_info=True)
        return False


def count_rejected_reasons(records: Iterable[AmazonProductRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for reason in record.reject_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


_SNAPSHOT_STAMP_RE = re.compile(r"(?<!\d)(20\d{6})[_-]?(\d{4}(?:\d{2})?)(?!\d)")


def _embedded_snapshot_time(path: Path) -> datetime | None:
    matches = list(_SNAPSHOT_STAMP_RE.finditer(path.as_posix()))
    if not matches:
        return None
    match = matches[-1]
    value = "".join(match.groups())
    fmt = "%Y%m%d%H%M%S" if len(value) == 14 else "%Y%m%d%H%M"
    try:
        return datetime.strptime(value, fmt)
    except ValueError:
        return None


def _snapshot_time_for_file(path: Path, explicit: datetime | None) -> datetime | None:
    if explicit is not None:
        return explicit
    embedded = _embedded_snapshot_time(path)
    if embedded is not None:
        return embedded
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)
    except OSError:
        return None


def _snapshot_times_for_files(
    paths: list[Path],
    explicit: datetime | None,
) -> dict[Path, datetime | None]:
    if explicit is not None:
        return {path: explicit for path in paths}

    result: dict[Path, datetime | None] = {}
    fallback_by_parent: dict[Path, datetime | None] = {}
    for path in paths:
        embedded = _embedded_snapshot_time(path)
        if embedded is not None:
            result[path] = embedded
            continue
        parent = path.parent.resolve()
        if parent not in fallback_by_parent:
            mtimes: list[datetime] = []
            for sibling in paths:
                if sibling.parent.resolve() != parent or _embedded_snapshot_time(sibling) is not None:
                    continue
                try:
                    mtimes.append(datetime.fromtimestamp(sibling.stat().st_mtime).replace(microsecond=0))
                except OSError:
                    continue
            fallback_by_parent[parent] = min(mtimes) if mtimes else None
        result[path] = fallback_by_parent[parent]
    return result


def _normalize_parsed_page_batches(parsed_pages: list[_ParsedPage]) -> None:
    batches: dict[datetime | None, list[_ParsedPage]] = {}
    for page in parsed_pages:
        records = [*page.records, *page.rejected_records]
        snapshot_at = records[0].snapshot_at if records else None
        batches.setdefault(snapshot_at, []).append(page)
    for pages in batches.values():
        _normalize_batch_organic_ranks(pages)


def _page_no_from_records(
    records: list[AmazonProductRecord],
    rejected_records: list[AmazonProductRecord],
) -> int | None:
    for record in [*records, *rejected_records]:
        if record.page_no is not None:
            return record.page_no
    return None


def _normalize_batch_organic_ranks(parsed_pages: list[_ParsedPage]) -> None:
    """Convert page-local organic positions into conservative batch-level ranks.

    `organic_rank` is an estimate, not Amazon's internal ranking. We only continue
    ranks across pages when the batch starts at page 1 and page numbers are
    consecutive; otherwise we clear the global rank to avoid false top-10 signals.
    Raw page-local metadata remains in raw_json via record.to_storage_dict().
    """

    pages = [page for page in parsed_pages if page.records or page.rejected_records]
    if not pages:
        return

    sorted_pages = sorted(
        pages,
        key=lambda page: (
            page.page_no if page.page_no is not None else 1_000_000 + page.order,
            page.order,
        ),
    )
    expected_page = 1
    offset = 0
    continuous = True

    for page in sorted_pages:
        natural_records = sorted(
            [
                record
                for record in [*page.records, *page.rejected_records]
                if not record.is_sponsored and record.page_organic_rank is not None
            ],
            key=lambda record: (record.page_organic_rank or 1_000_000, record.result_slot or 1_000_000),
        )
        if not natural_records:
            continue

        if continuous and page.page_no == expected_page:
            confidence = "batch_continuous" if len(sorted_pages) > 1 else "page_first"
            for record in natural_records:
                record.organic_rank = offset + int(record.page_organic_rank or 0)
                record.rank_confidence = confidence
            offset += max(int(record.page_organic_rank or 0) for record in natural_records)
            expected_page += 1
            continue

        continuous = False
        for record in natural_records:
            record.organic_rank = None
            record.rank_confidence = "page_gap"


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
    output = resolve_user_writable_path(output_dir)
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
