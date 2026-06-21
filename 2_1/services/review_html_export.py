"""Export saved Amazon review-page HTML into review CSV/JSON files."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Iterable

from parsers.amazon_review_parser import (
    REVIEW_EXPORT_FIELDS,
    AmazonReviewRecord,
    count_review_rejected_reasons,
    parse_amazon_review_html,
)


@dataclass
class ReviewHtmlExportSummary:
    total_found: int = 0
    total_valid: int = 0
    total_rejected: int = 0
    output_path: Path | None = None
    rejected_output_path: Path | None = None
    involved_asins: set[str] = field(default_factory=set)
    rejected_reasons: dict[str, int] = field(default_factory=dict)


def export_review_html_files(
    html_files: Iterable[str | Path],
    *,
    output_path: str | Path | None = None,
    output_format: str = "csv",
    default_asin: str | None = None,
    rejected_output_path: str | Path | None = None,
) -> ReviewHtmlExportSummary:
    """Parse local review HTML files and export import-ready CSV/JSON."""
    valid_records: list[AmazonReviewRecord] = []
    rejected_records: list[AmazonReviewRecord] = []
    seen_hashes: set[str] = set()

    for html_file in html_files:
        result = parse_amazon_review_html(html_file, default_asin=default_asin)
        for record in result.records:
            if record.content_hash in seen_hashes:
                record.reject_reasons.append("跨文件重复评论")
                rejected_records.append(record)
                continue
            seen_hashes.add(record.content_hash)
            valid_records.append(record)
        rejected_records.extend(result.rejected_records)

    fmt = _normalize_format(output_format, output_path)
    output = _resolve_output_path(output_path, fmt)
    rejected_output = Path(rejected_output_path) if rejected_output_path else _default_rejected_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rejected_output.parent.mkdir(parents=True, exist_ok=True)

    _write_valid_records(output, fmt, valid_records)
    _write_rejected_records(rejected_output, rejected_records)

    summary = ReviewHtmlExportSummary(
        total_found=len(valid_records) + len(rejected_records),
        total_valid=len(valid_records),
        total_rejected=len(rejected_records),
        output_path=output,
        rejected_output_path=rejected_output,
        involved_asins={record.asin for record in valid_records if record.asin},
        rejected_reasons=count_review_rejected_reasons(rejected_records),
    )
    return summary


def _normalize_format(output_format: str, output_path: str | Path | None) -> str:
    if output_path:
        suffix = Path(output_path).suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix == ".csv":
            return "csv"
    fmt = output_format.lower().strip()
    if fmt not in {"csv", "json"}:
        raise ValueError("输出格式仅支持 csv 或 json")
    return fmt


def _resolve_output_path(output_path: str | Path | None, fmt: str) -> Path:
    if output_path:
        return Path(output_path)
    suffix = ".json" if fmt == "json" else ".csv"
    return Path("数据结果") / f"评论HTML解析{suffix}"


def _default_rejected_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_rejected.csv")


def _write_valid_records(path: Path, fmt: str, records: list[AmazonReviewRecord]) -> None:
    rows = [record.to_import_dict() for record in records]
    if fmt == "json":
        payload = {"reviews": rows}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(REVIEW_EXPORT_FIELDS))
        writer.writeheader()
        writer.writerows(rows)


def _write_rejected_records(path: Path, records: list[AmazonReviewRecord]) -> None:
    fieldnames = list(REVIEW_EXPORT_FIELDS) + ["reject_reasons", "拒绝原因"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_rejected_dict())
