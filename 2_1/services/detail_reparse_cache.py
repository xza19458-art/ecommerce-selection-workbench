"""Versioned local cache for expensive product-detail HTML analysis.

The cache is derived data only. It stores file fingerprints and structured
parser output, never raw HTML, and can be deleted at any time. Callers remain
responsible for re-reading and re-parsing files before any database write.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable
import uuid

from parsers.amazon_detail_parser import (
    AmazonDetailRecord,
    BestSellerRank,
    ProductOfferFacts,
    ProductPhysicalSpecs,
    ProductVariant,
)


CACHE_SCHEMA_VERSION = 1
_CACHE_LOCK = threading.RLock()
_HASH_CHUNK_SIZE = 1024 * 1024

DetailAnalyzer = Callable[[Path], dict[str, Any]]
DetailAnalysisRefresher = Callable[[dict[str, Any]], dict[str, Any]]


def load_or_analyze_detail_files(
    paths: Iterable[Path],
    *,
    root: Path,
    cache_path: Path,
    analysis_version: str,
    analyzer: DetailAnalyzer,
    refresher: DetailAnalysisRefresher,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return analyses, reusing only entries with identical content and version."""

    started = time.perf_counter()
    selected = list(paths)
    root_resolved = root.resolve()
    version = str(analysis_version or "").strip()
    if not version:
        raise ValueError("详情解析缓存必须提供分析器版本")

    with _CACHE_LOCK:
        manifest, read_warning = _read_manifest(cache_path)
        root_id = _root_id(root_resolved)
        entries: dict[str, Any] = {}
        invalidated = 0
        cache_was_compatible = _manifest_is_compatible(
            manifest,
            root_id=root_id,
            analysis_version=version,
        )
        if cache_was_compatible:
            entries = dict(manifest.get("entries") or {})
        elif manifest:
            invalidated = len(manifest.get("entries") or {})

        hits = 0
        misses = 0
        changed = bool(read_warning or (manifest and not cache_was_compatible))
        analyses: list[dict[str, Any]] = []
        for path in selected:
            key = _cache_key(path, root=root_resolved)
            fingerprint = _file_fingerprint(path)
            entry = entries.get(key)
            item: dict[str, Any] | None = None
            if not force_refresh and _entry_matches(entry, fingerprint):
                try:
                    item = _deserialize_analysis(entry)
                    item = refresher(item)
                except (KeyError, TypeError, ValueError):
                    item = None
                    invalidated += 1
            if item is not None:
                hits += 1
                analyses.append(item)
                continue

            misses += 1
            parsed = refresher(analyzer(path))
            analyses.append(parsed)
            entries[key] = _serialize_analysis(parsed, fingerprint=fingerprint)
            changed = True

        write_warning = None
        if changed:
            next_manifest = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "analysis_version": version,
                "root_id": root_id,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "entries": entries,
            }
            try:
                _write_manifest(cache_path, next_manifest)
            except OSError as exc:
                write_warning = f"详情解析缓存写入失败，本次结果仍可使用：{exc}"

    warning = "；".join(item for item in (read_warning, write_warning) if item) or None
    if write_warning:
        status = "write_failed"
    elif force_refresh:
        status = "rebuilt"
    elif misses and hits:
        status = "updated"
    elif misses:
        status = "created" if not manifest else "updated"
    else:
        status = "hit"
    return analyses, {
        "enabled": True,
        "schema_version": CACHE_SCHEMA_VERSION,
        "analysis_version": version,
        "status": status,
        "hit_count": hits,
        "miss_count": misses,
        "invalidated_count": invalidated,
        "entry_count": len(entries),
        "forced_refresh": bool(force_refresh),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "warning": warning,
    }


def _read_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if path.is_symlink():
        return None, "详情解析缓存路径不可用，已忽略旧缓存"
    if not path.exists():
        return None, None
    if not path.is_file():
        return None, "详情解析缓存路径不可用，已忽略旧缓存"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "详情解析缓存清单损坏，已重新生成"
    if not isinstance(payload, dict):
        return None, "详情解析缓存清单格式不正确，已重新生成"
    return payload, None


def _manifest_is_compatible(
    manifest: dict[str, Any] | None,
    *,
    root_id: str,
    analysis_version: str,
) -> bool:
    if not manifest:
        return False
    return (
        manifest.get("schema_version") == CACHE_SCHEMA_VERSION
        and manifest.get("analysis_version") == analysis_version
        and manifest.get("root_id") == root_id
        and isinstance(manifest.get("entries"), dict)
    )


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("拒绝写入符号链接缓存文件")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _cache_key(path: Path, *, root: Path) -> str:
    resolved = path.resolve()
    if path.is_symlink() or not path.is_file() or not resolved.is_relative_to(root):
        raise ValueError(f"详情 HTML 不在缓存允许目录内：{path}")
    return resolved.relative_to(root).as_posix()


def _root_id(root: Path) -> str:
    normalized = str(root).replace("\\", "/").casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _file_fingerprint(path: Path) -> dict[str, Any]:
    for _attempt in range(2):
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        after = path.stat()
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            return {
                "size": int(after.st_size),
                "mtime_ns": int(after.st_mtime_ns),
                "sha256": digest.hexdigest().upper(),
            }
    raise OSError(f"详情 HTML 在读取期间发生变化：{path.name}")


def _entry_matches(entry: Any, fingerprint: dict[str, Any]) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("fingerprint") == fingerprint
        and isinstance(entry.get("analysis"), dict)
        and isinstance(entry.get("record"), dict)
        and bool(entry.get("captured_at"))
    )


def _serialize_analysis(
    item: dict[str, Any],
    *,
    fingerprint: dict[str, Any],
) -> dict[str, Any]:
    record = item.get("_record")
    captured_at = item.get("_captured_at")
    if not isinstance(record, AmazonDetailRecord) or not isinstance(captured_at, datetime):
        raise ValueError("详情解析结果缺少可缓存的记录或采集时间")
    public = {key: value for key, value in item.items() if not key.startswith("_")}
    return {
        "fingerprint": fingerprint,
        "captured_at": captured_at.isoformat(sep=" "),
        "analysis": public,
        "record": record.to_dict(),
        "cached_at": datetime.now().isoformat(timespec="seconds"),
    }


def _deserialize_analysis(entry: dict[str, Any]) -> dict[str, Any]:
    item = dict(entry["analysis"])
    item["_captured_at"] = datetime.fromisoformat(str(entry["captured_at"]))
    item["_record"] = _record_from_dict(entry["record"])
    return item


def _record_from_dict(raw: dict[str, Any]) -> AmazonDetailRecord:
    first_available = raw.get("date_first_available")
    parsed_date = date.fromisoformat(str(first_available)) if first_available else None
    ranks = tuple(
        BestSellerRank(**_known_fields(BestSellerRank, item))
        for item in _dict_rows(raw.get("best_seller_ranks"))
    )
    specs = ProductPhysicalSpecs(
        **_known_fields(ProductPhysicalSpecs, _dict_value(raw.get("physical_specs")))
    )
    offer_data = _known_fields(ProductOfferFacts, _dict_value(raw.get("offer")))
    if "badges" in offer_data:
        offer_data["badges"] = tuple(offer_data.get("badges") or ())
    offer = ProductOfferFacts(**offer_data)
    variants = tuple(
        ProductVariant(**_known_fields(ProductVariant, item))
        for item in _dict_rows(raw.get("variants"))
    )
    return AmazonDetailRecord(
        asin=raw.get("asin"),
        title=raw.get("title"),
        category_path=raw.get("category_path"),
        date_first_available=parsed_date,
        best_seller_ranks=ranks,
        physical_specs=specs,
        offer=offer,
        variants=variants,
        source_file=raw.get("source_file"),
    )


def _known_fields(model: type, raw: dict[str, Any]) -> dict[str, Any]:
    allowed = {item.name for item in fields(model)}
    return {key: value for key, value in raw.items() if key in allowed}


def _dict_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("缓存记录字段格式不正确")
    return dict(value)


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("缓存记录列表格式不正确")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError("缓存记录列表项格式不正确")
    return [dict(item) for item in value]
