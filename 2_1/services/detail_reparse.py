"""Offline replay and evidence-quality inspection for saved product detail HTML."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import re
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from parsers.amazon_detail_parser import AmazonDetailRecord, parse_amazon_detail_content
from pkg_paths import user_data_path
from services.amazon_urls import normalize_asin
from services.detail_reparse_cache import load_or_analyze_detail_files
from services.product_detail_collection import (
    DETAIL_HTML_ROOT,
    ROOT,
    classify_amazon_detail_page,
    persist_detail_record,
)
from services.research_workspace import PROJECT_STATUS_LABELS, TERMINAL_STATUSES


STALE_DAYS = 30
MAX_SCAN_FILES = 200
ALLOWED_SUFFIXES = {".html", ".htm"}
DETAIL_ANALYSIS_VERSION = "detail-evidence-analysis-v1"
DETAIL_ANALYSIS_CACHE_PATH = user_data_path("cache", "detail_reparse_manifest.json")
DETAIL_PRIORITY_METHOD_VERSION = "detail-evidence-priority-v1.1"
DETAIL_PRIORITY_RECENT_DAYS = 30
DETAIL_DISPOSITION_METHOD_VERSION = "detail-evidence-disposition-v1"
DETAIL_PRIORITY_FILTER_LABELS = {
    "all": "全部优先级",
    "focus": "项目重点（候选/对标）",
    "project": "进行中项目关联",
    "planned": "人工计划关注",
    "recent": "近期活跃",
    "routine": "常规积压",
}
DETAIL_PRIORITY_TIER_LABELS = {
    "project_candidate": "项目候选",
    "project_benchmark": "项目对标",
    "project_related": "项目关联",
    "recent_observation": "近期活跃",
    "historical_project": "历史项目",
    "routine": "常规补全",
}
DETAIL_PROJECT_ROLE_LABELS = {
    "candidate": "候选商品",
    "benchmark": "对标商品",
    "competitor": "竞品",
    "reference": "参考商品",
}
DETAIL_DISPOSITION_DEFINITIONS = {
    "collect_first": {
        "label": "首次采集",
        "kind": "network",
        "primary_command": "collect_detail",
        "button_label": "首次采集",
    },
    "refresh_stale": {
        "label": "刷新过期证据",
        "kind": "network",
        "primary_command": "collect_detail",
        "button_label": "刷新详情",
    },
    "replay_local": {
        "label": "先预览本地回填",
        "kind": "local",
        "primary_command": "preview_local",
        "button_label": "预览本地证据",
    },
    "adapt_parser": {
        "label": "检查解析适配",
        "kind": "review",
        "primary_command": "preview_local",
        "button_label": "检查本地证据",
    },
    "page_missing": {
        "label": "页面未提供，保留缺失",
        "kind": "hold",
        "primary_command": "preview_local",
        "button_label": "核对页面证据",
    },
    "collect_gap": {
        "label": "补采缺失字段",
        "kind": "network",
        "primary_command": "collect_detail",
        "button_label": "补采详情",
    },
    "manual_review": {
        "label": "人工核对字段映射",
        "kind": "review",
        "primary_command": "preview_local",
        "button_label": "核对本地证据",
    },
}
_ASIN_IN_PATH_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{10})(?![A-Z0-9])", re.I)
_CAPTURED_AT_RE = re.compile(r"_(\d{8})_(\d{6})(?:_|\.)")

_FIELD_DEFINITIONS = (
    ("category_path", "商品类别", ("wayfinding-breadcrumbs", "a-breadcrumb", '"category"')),
    ("date_first_available", "首次可售日期", ("date first available", '"releasedate"')),
    ("best_seller_ranks", "热销榜排名", ("best sellers rank", "best seller rank", "/gp/bestsellers/")),
    ("offer", "报价与履约", ("coreprice", "priceblock_", 'id="availability"', "buybox")),
    (
        "physical_specs",
        "结构化物理规格",
        ("product dimensions", "package dimensions", "item weight", "unit count", "item model number"),
    ),
    ("variants", "商品变体", ("dimensionvaluesdisplaydata", 'id="variation_', "id='variation_")),
)


class DetailReparseError(ValueError):
    """Raised when local detail replay input is unsafe or unusable."""


def list_detail_reparse_candidates(
    *,
    limit: int = 50,
    offset: int = 0,
    file_limit: int = 100,
    stale_days: int = STALE_DAYS,
    client: MySQLClient | None = None,
    now: datetime | None = None,
    force_refresh: bool = False,
    cache_path: Path | None = None,
    priority: str = "all",
    project_id: int | None = None,
) -> dict[str, Any]:
    """Return local replay candidates plus a paged product evidence-gap queue."""
    db = client or MySQLClient()
    current_now = (now or datetime.now()).replace(microsecond=0)
    safe_file_limit = max(1, min(int(file_limit or MAX_SCAN_FILES), MAX_SCAN_FILES))
    paths = list_detail_html_files(limit=safe_file_limit)
    local_scan_complete = len(paths) < safe_file_limit
    analyses, cache = analyze_detail_html_files_cached(
        paths,
        root=DETAIL_HTML_ROOT,
        now=current_now,
        stale_days=stale_days,
        force_refresh=force_refresh,
        cache_path=cache_path,
    )
    current = _fetch_current_products(db, [item["asin"] for item in analyses if item.get("asin")])
    files = [_attach_current(item, current.get(str(item.get("asin") or ""))) for item in analyses]
    gap_summary, gaps = _fetch_product_gaps(
        db,
        limit=limit,
        offset=offset,
        stale_days=stale_days,
        now=current_now,
        priority=priority,
        project_id=project_id,
    )
    latest_local: dict[str, dict[str, Any]] = {}
    for item in sorted(files, key=lambda row: str(row.get("captured_at") or ""), reverse=True):
        latest_local.setdefault(str(item.get("asin") or ""), item)
    for row in gaps["rows"]:
        local = latest_local.get(str(row.get("asin") or ""))
        row["local_file_status"] = local.get("status") if local else None
        row["local_file_path"] = local.get("path") if local else None
        row["can_reparse"] = bool(local and local.get("can_apply"))
        _decorate_detail_gap_disposition(
            row,
            local,
            local_evidence_checked=True,
            local_evidence_scan_complete=local_scan_complete,
        )
    gaps["disposition_summary"] = _detail_disposition_summary(gaps["rows"])
    gaps["disposition_policy"] = _detail_disposition_policy(
        local_evidence_checked=True,
        local_evidence_scan_complete=local_scan_complete,
        local_file_count=len(paths),
        local_file_limit=safe_file_limit,
    )

    file_status_counts: dict[str, int] = {}
    for item in files:
        status = str(item.get("status") or "invalid")
        file_status_counts[status] = file_status_counts.get(status, 0) + 1
    summary = {
        **gap_summary,
        "local_file_count": len(files),
        "replayable_file_count": sum(1 for item in files if item.get("can_apply")),
        "parser_unrecognized_file_count": file_status_counts.get("parser_unrecognized", 0),
        "page_missing_file_count": file_status_counts.get("page_missing", 0),
        "stale_file_count": file_status_counts.get("stale", 0),
        "invalid_file_count": file_status_counts.get("invalid", 0),
        "cache_hit_count": cache["hit_count"],
        "cache_miss_count": cache["miss_count"],
    }
    return {
        "root": _display_path(DETAIL_HTML_ROOT),
        "stale_days": stale_days,
        "summary": summary,
        "cache": cache,
        "files": [_public_item(item) for item in files],
        "gaps": gaps,
    }


def list_detail_evidence_priorities(
    *,
    limit: int = 20,
    offset: int = 0,
    stale_days: int = STALE_DAYS,
    priority: str = "all",
    project_id: int | None = None,
    client: MySQLClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the read-only product-detail evidence priority queue."""

    current_now = (now or datetime.now()).replace(microsecond=0)
    summary, gaps = _fetch_product_gaps(
        client or MySQLClient(),
        limit=limit,
        offset=offset,
        stale_days=stale_days,
        now=current_now,
        priority=priority,
        project_id=project_id,
    )
    return {
        "generated_at": current_now.isoformat(sep=" "),
        "stale_days": max(1, min(int(stale_days or STALE_DAYS), 3650)),
        "summary": summary,
        "gaps": gaps,
    }


def preview_detail_files(
    paths: Iterable[str],
    *,
    client: MySQLClient | None = None,
    now: datetime | None = None,
    force_refresh: bool = False,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    selected = resolve_detail_html_paths(paths)
    current_now = (now or datetime.now()).replace(microsecond=0)
    analyses, cache = analyze_detail_html_files_cached(
        selected,
        root=DETAIL_HTML_ROOT,
        now=current_now,
        force_refresh=force_refresh,
        cache_path=cache_path,
    )
    db = client or MySQLClient()
    current = _fetch_current_products(db, [item["asin"] for item in analyses if item.get("asin")])
    items = [_attach_current(item, current.get(str(item.get("asin") or ""))) for item in analyses]
    return {
        "total": len(items),
        "replayable": sum(1 for item in items if item.get("can_apply")),
        "cache": cache,
        "items": [_public_item(item) for item in items],
    }


def apply_detail_files(
    paths: Iterable[str],
    *,
    client: MySQLClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-read and persist selected files; callers must confirm before invoking."""
    db = client or MySQLClient()
    selected = resolve_detail_html_paths(paths)
    current_now = (now or datetime.now()).replace(microsecond=0)
    applied = 0
    rejected: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for path in selected:
        item = analyze_detail_html_file(path, now=current_now)
        current = _fetch_current_products(db, [item["asin"]] if item.get("asin") else [])
        item = _attach_current(item, current.get(str(item.get("asin") or "")))
        if not item.get("can_apply"):
            message = str(item.get("reason") or "文件没有可回填的可信详情证据")
            rejected.append({"path": item["path"], "asin": item.get("asin"), "message": message})
            results.append({"path": item["path"], "asin": item.get("asin"), "status": "rejected", "message": message})
            continue
        record = item.get("_record")
        if not isinstance(record, AmazonDetailRecord):
            message = "解析记录不可用"
            rejected.append({"path": item["path"], "asin": item.get("asin"), "message": message})
            results.append({"path": item["path"], "asin": item.get("asin"), "status": "rejected", "message": message})
            continue
        try:
            persist_detail_record(
                record,
                collected_at=item["_captured_at"],
                source_file=item["path"],
                client=db,
            )
            applied += 1
            results.append(
                {
                    "path": item["path"],
                    "asin": item.get("asin"),
                    "status": "applied",
                    "captured_at": item.get("captured_at"),
                    "change_count": item.get("change_count", 0),
                }
            )
        except Exception as exc:
            message = str(exc)
            rejected.append({"path": item["path"], "asin": item.get("asin"), "message": message})
            results.append({"path": item["path"], "asin": item.get("asin"), "status": "rejected", "message": message})
    return {
        "total": len(selected),
        "applied": applied,
        "rejected": rejected,
        "results": results,
        "message": "离线详情证据已按原始采集时间幂等回填。" if applied else "没有文件完成回填。",
    }


def list_detail_html_files(
    *,
    root: Path = DETAIL_HTML_ROOT,
    limit: int = MAX_SCAN_FILES,
) -> list[Path]:
    safe_limit = max(1, min(int(limit or MAX_SCAN_FILES), MAX_SCAN_FILES))
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.casefold() not in ALLOWED_SUFFIXES:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            continue
        files.append(path)
    files.sort(key=lambda item: (item.stat().st_mtime, item.as_posix()), reverse=True)
    return files[:safe_limit]


def resolve_detail_html_paths(
    paths: Iterable[str],
    *,
    root: Path = DETAIL_HTML_ROOT,
) -> list[Path]:
    allowed = {_source_file(path, root=root): path for path in list_detail_html_files(root=root, limit=MAX_SCAN_FILES)}
    selected: list[Path] = []
    seen: set[str] = set()
    for raw in paths or []:
        name = str(raw or "").strip().replace("\\", "/")
        if not name or name in seen or name not in allowed:
            raise DetailReparseError(f"非法或不存在的详情 HTML：{raw!r}")
        seen.add(name)
        selected.append(allowed[name])
    if not selected:
        raise DetailReparseError("未选择任何详情 HTML 文件。")
    return selected


def analyze_detail_html_file(
    path: Path,
    *,
    root: Path = DETAIL_HTML_ROOT,
    now: datetime | None = None,
    stale_days: int = STALE_DAYS,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if path.is_symlink() or not path.is_file() or not resolved_path.is_relative_to(resolved_root):
        raise DetailReparseError(f"详情 HTML 不在允许目录内：{path}")
    if path.suffix.casefold() not in ALLOWED_SUFFIXES:
        raise DetailReparseError(f"不支持的详情文件类型：{path.suffix}")
    html = path.read_text(encoding="utf-8", errors="ignore")
    expected_asin = _asin_from_path(path)
    captured_at = _captured_at(path)
    record = parse_amazon_detail_content(html, expected_asin=expected_asin)
    page_state, reason = classify_amazon_detail_page(html, record=record)
    asin = record.asin or expected_asin
    if expected_asin and record.asin and expected_asin != record.asin:
        page_state = "invalid"
        reason = f"文件 ASIN {expected_asin} 与页面 ASIN {record.asin} 不一致"
    coverage = _field_coverage(html, record)
    item = {
        "path": _source_file(path, root=root),
        "file_name": path.name,
        "file_size": path.stat().st_size,
        "asin": asin,
        "expected_asin": expected_asin,
        "captured_at": captured_at.isoformat(sep=" "),
        "page_state": page_state,
        "reason": reason,
        "coverage": coverage,
        "_captured_at": captured_at,
        "_record": record,
    }
    return _refresh_analysis_state(item, now=now, stale_days=stale_days)


def analyze_detail_html_files_cached(
    paths: Iterable[Path],
    *,
    root: Path = DETAIL_HTML_ROOT,
    now: datetime | None = None,
    stale_days: int = STALE_DAYS,
    force_refresh: bool = False,
    cache_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Analyze detail files with a content- and parser-version-bound cache."""

    current_now = now or datetime.now()
    selected_cache = cache_path or DETAIL_ANALYSIS_CACHE_PATH
    analyses, cache = load_or_analyze_detail_files(
        paths,
        root=root,
        cache_path=selected_cache,
        analysis_version=DETAIL_ANALYSIS_VERSION,
        analyzer=lambda path: analyze_detail_html_file(
            path,
            root=root,
            now=current_now,
            stale_days=stale_days,
        ),
        refresher=lambda item: _refresh_analysis_state(
            item,
            now=current_now,
            stale_days=stale_days,
        ),
        force_refresh=force_refresh,
    )
    cache["manifest"] = _cache_source_file(selected_cache)
    return analyses, cache


def _refresh_analysis_state(
    item: dict[str, Any],
    *,
    now: datetime | None,
    stale_days: int,
) -> dict[str, Any]:
    refreshed = dict(item)
    captured_at = refreshed.get("_captured_at")
    if not isinstance(captured_at, datetime):
        raise ValueError("详情解析结果缺少采集时间")
    coverage = refreshed.get("coverage") or {}
    page_state = str(refreshed.get("page_state") or "invalid")
    meaningful = any(value.get("status") == "collected" for value in coverage.values())
    if page_state != "valid":
        meaningful = False
    current_now = now or datetime.now()
    safe_stale_days = max(1, min(int(stale_days or STALE_DAYS), 3650))
    stale = captured_at < current_now - timedelta(days=safe_stale_days)
    refreshed["stale"] = stale
    refreshed["status"] = _file_status(page_state, coverage, stale=stale)
    refreshed["can_apply"] = bool(page_state == "valid" and refreshed.get("asin") and meaningful)
    if not meaningful and not refreshed.get("reason"):
        refreshed["reason"] = "页面有效，但没有解析出可回填的详情证据"
    return refreshed


def _field_coverage(html: str, record: AmazonDetailRecord) -> dict[str, dict[str, Any]]:
    lower = html.casefold()
    result: dict[str, dict[str, Any]] = {}
    for key, label, markers in _FIELD_DEFINITIONS:
        value = _record_evidence_value(record, key)
        collected = _has_value(value)
        marker_found = any(marker.casefold() in lower for marker in markers)
        result[key] = {
            "label": label,
            "status": "collected" if collected else ("parser_unrecognized" if marker_found else "page_missing"),
            "value": value,
            "marker_found": marker_found,
        }
    return result


def _record_evidence_value(record: AmazonDetailRecord, key: str) -> Any:
    if key == "category_path":
        return record.category_path
    if key == "date_first_available":
        return record.date_first_available.isoformat() if record.date_first_available else None
    if key == "best_seller_ranks":
        return [f"#{item.rank:,} {item.category_name}" for item in record.best_seller_ranks]
    if key == "offer":
        if not record.offer.has_data:
            return None
        values = {
            "current_price": record.offer.current_price,
            "availability_status": record.offer.availability_status,
            "fulfillment_channel": record.offer.fulfillment_channel,
            "is_prime": record.offer.is_prime,
        }
        return {key_: value for key_, value in values.items() if _has_value(value)} or None
    if key == "physical_specs":
        specs = record.physical_specs
        values = {
            "item_dimensions_in": _join_dimensions(specs.item_length_in, specs.item_width_in, specs.item_height_in),
            "package_dimensions_in": _join_dimensions(specs.package_length_in, specs.package_width_in, specs.package_height_in),
            "item_weight_oz": specs.item_weight_oz,
            "package_weight_oz": specs.package_weight_oz,
            "unit_count": specs.unit_count,
            "model_number": specs.model_number,
        }
        return {key_: value for key_, value in values.items() if _has_value(value)} or None
    if key == "variants":
        return [item.child_asin for item in record.variants] or None
    return None


def _file_status(page_state: str, coverage: dict[str, dict[str, Any]], *, stale: bool) -> str:
    if page_state != "valid":
        return "invalid"
    if any(item["status"] == "parser_unrecognized" for item in coverage.values()):
        return "parser_unrecognized"
    if stale:
        return "stale"
    if any(item["status"] == "page_missing" for item in coverage.values()):
        return "page_missing"
    return "ready"


def _attach_current(item: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    attached = dict(item)
    if current is None:
        attached["status"] = "invalid"
        attached["reason"] = "商品库中不存在该 ASIN"
        attached["can_apply"] = False
        attached["current"] = None
        attached["changes"] = []
        attached["change_count"] = 0
        return attached
    attached["current"] = {
        "detail_collected_at": _stringify(current.get("detail_collected_at")),
        "detail_source_file": current.get("detail_source_file"),
        "category_path": current.get("category_path"),
        "date_first_available": _stringify(current.get("date_first_available")),
    }
    changes = _build_changes(attached, current)
    attached["changes"] = changes
    attached["change_count"] = sum(
        1 for change in changes if change["action"] in {"fill", "update", "snapshot", "history"}
    )
    return attached


def _build_changes(item: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    record = item.get("_record")
    if not isinstance(record, AmazonDetailRecord):
        return []
    captured_at = item["_captured_at"]
    current_at = _as_datetime(current.get("detail_collected_at"))
    older = bool(current_at and captured_at < current_at)
    parsed_specs = _record_evidence_value(record, "physical_specs")
    current_specs = {
        "item_dimensions_in": _join_dimensions(
            current.get("item_length_in"), current.get("item_width_in"), current.get("item_height_in")
        ),
        "package_dimensions_in": _join_dimensions(
            current.get("package_length_in"), current.get("package_width_in"), current.get("package_height_in")
        ),
        "item_weight_oz": current.get("item_weight_oz"),
        "package_weight_oz": current.get("package_weight_oz"),
        "unit_count": current.get("unit_count"),
        "model_number": current.get("model_number"),
    }
    current_specs = {key: value for key, value in current_specs.items() if _has_value(value)} or None
    parsed_bsr = _record_evidence_value(record, "best_seller_ranks")
    parsed_offer = _record_evidence_value(record, "offer")
    parsed_variants = _record_evidence_value(record, "variants")
    definitions = (
        ("category_path", "商品类别", current.get("category_path"), record.category_path, "current"),
        (
            "date_first_available",
            "首次可售日期",
            _stringify(current.get("date_first_available")),
            record.date_first_available.isoformat() if record.date_first_available else None,
            "current",
        ),
        (
            "best_seller_ranks",
            "BSR 历史快照",
            current.get("bsr_snapshot_count"),
            len(parsed_bsr) if parsed_bsr else None,
            "snapshot",
        ),
        (
            "offer",
            "报价历史快照",
            current.get("latest_offer_price"),
            record.offer.current_price if record.offer.current_price is not None else parsed_offer,
            "snapshot",
        ),
        ("physical_specs", "物理规格", current_specs, parsed_specs, "current"),
        ("variants", "变体关系", current.get("variant_count"), len(parsed_variants) if parsed_variants else None, "history"),
    )
    changes: list[dict[str, Any]] = []
    for field, label, current_value, parsed_value, mode in definitions:
        if not _has_value(parsed_value):
            action = "missing"
        elif mode in {"snapshot", "history"}:
            action = mode
        elif _equivalent(current_value, parsed_value):
            action = "unchanged"
        elif not _has_value(current_value):
            action = "fill"
        elif older:
            action = "preserve_newer"
        else:
            action = "update"
        changes.append(
            {
                "field": field,
                "label": label,
                "current": current_value,
                "parsed": parsed_value,
                "action": action,
            }
        )
    return changes


def _fetch_current_products(db: MySQLClient, asins: Iterable[str]) -> dict[str, dict[str, Any]]:
    unique = sorted({str(asin).strip().upper() for asin in asins if asin})
    if not unique:
        return {}
    placeholders = ",".join(["%s"] * len(unique))
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT p.id, p.asin, p.marketplace, p.title, p.title_zh,
                       p.category_path, p.date_first_available,
                       p.detail_collected_at, p.detail_source_file,
                       s.item_length_in, s.item_width_in, s.item_height_in,
                       s.package_length_in, s.package_width_in, s.package_height_in,
                       s.item_weight_oz, s.package_weight_oz, s.unit_count, s.model_number,
                       (SELECT o.current_price FROM product_offer_snapshots o
                        WHERE o.product_id = p.id ORDER BY o.snapshot_at DESC, o.id DESC LIMIT 1) AS latest_offer_price,
                       (SELECT COUNT(*) FROM product_bsr_snapshots b WHERE b.product_id = p.id) AS bsr_snapshot_count,
                       (SELECT COUNT(*) FROM product_variants v WHERE v.source_product_id = p.id) AS variant_count
                FROM products p
                LEFT JOIN product_physical_specs s ON s.product_id = p.id
                WHERE p.asin IN ({placeholders})
                """,
                unique,
            )
            rows = cursor.fetchall()
    return {str(row["asin"]): dict(row) for row in rows}


_DETAIL_PRIORITY_CTE = """
WITH research_priority AS (
    SELECT
      rpp.product_id,
      COUNT(*) AS project_relation_count,
      SUM(CASE WHEN rp.status NOT IN ('approved', 'rejected') THEN 1 ELSE 0 END) AS active_project_count,
      SUM(CASE WHEN rp.status NOT IN ('approved', 'rejected') AND rpp.role = 'candidate' THEN 1 ELSE 0 END) AS active_candidate_count,
      SUM(CASE WHEN rp.status NOT IN ('approved', 'rejected') AND rpp.role = 'benchmark' THEN 1 ELSE 0 END) AS active_benchmark_count,
      SUM(CASE WHEN rp.status NOT IN ('approved', 'rejected') AND rop.status = 'active' THEN 1 ELSE 0 END) AS active_plan_count,
      SUM(CASE WHEN rp.status NOT IN ('approved', 'rejected') AND rop.status = 'active' AND rop.next_review_on <= %s THEN 1 ELSE 0 END) AS due_plan_count,
      MIN(CASE WHEN rp.status NOT IN ('approved', 'rejected') AND rop.status = 'active' THEN rop.next_review_on END) AS nearest_review_on
    FROM research_project_products rpp
    JOIN research_projects rp ON rp.id = rpp.project_id
    LEFT JOIN research_project_observation_plans rop ON rop.project_id = rp.id
    {research_project_where}
    GROUP BY rpp.product_id
),
prioritized_gaps AS (
    SELECT
      p.id AS product_id,
      p.asin,
      p.marketplace,
      p.title,
      p.title_zh,
      p.category_path,
      p.date_first_available,
      p.detail_collected_at,
      p.detail_source_file,
      p.first_seen_at,
      p.last_seen_at,
      COALESCE(rp.project_relation_count, 0) AS project_relation_count,
      COALESCE(rp.active_project_count, 0) AS active_project_count,
      COALESCE(rp.active_candidate_count, 0) AS active_candidate_count,
      COALESCE(rp.active_benchmark_count, 0) AS active_benchmark_count,
      COALESCE(rp.active_plan_count, 0) AS active_plan_count,
      COALESCE(rp.due_plan_count, 0) AS due_plan_count,
      rp.nearest_review_on,
      CASE
        WHEN COALESCE(rp.active_candidate_count, 0) > 0 THEN 0
        WHEN COALESCE(rp.active_benchmark_count, 0) > 0 THEN 1
        WHEN COALESCE(rp.active_project_count, 0) > 0 THEN 2
        WHEN p.last_seen_at >= %s THEN 3
        WHEN COALESCE(rp.project_relation_count, 0) > 0 THEN 4
        ELSE 5
      END AS priority_order,
      CASE
        WHEN p.detail_collected_at IS NULL THEN 0
        WHEN p.detail_collected_at < %s THEN 1
        ELSE 2
      END AS evidence_order
    FROM products p
    {research_priority_join}
    WHERE p.detail_collected_at IS NULL
       OR p.detail_collected_at < %s
       OR p.category_path IS NULL OR p.category_path = ''
       OR p.date_first_available IS NULL
)
"""

_DETAIL_PRIORITY_FILTER_SQL = {
    "all": "1 = 1",
    "focus": "priority_order IN (0, 1)",
    "project": "priority_order IN (0, 1, 2)",
    "planned": "active_plan_count > 0",
    "recent": "priority_order = 3",
    "routine": "priority_order IN (4, 5)",
}

_DETAIL_PRIORITY_TIER_BY_ORDER = {
    0: "project_candidate",
    1: "project_benchmark",
    2: "project_related",
    3: "recent_observation",
    4: "historical_project",
    5: "routine",
}

_DETAIL_PRIORITY_REASON_BY_TIER = {
    "project_candidate": "进行中研究项目的候选商品，优先补齐进入判断所需详情证据",
    "project_benchmark": "进行中研究项目的对标商品，优先补齐竞品事实",
    "project_related": "关联进行中的研究项目，补采可完善项目证据",
    "recent_observation": "近 30 天仍在搜索采集中观察到，适合优先补齐详情",
    "historical_project": "仅关联终态或历史项目，按人工回看需要补采",
    "routine": "暂无进行中研究项目关联，保留在常规补全队列",
}


def _fetch_product_gaps(
    db: MySQLClient,
    *,
    limit: int,
    offset: int,
    stale_days: int,
    now: datetime,
    priority: str = "all",
    project_id: int | None = None,
) -> tuple[dict[str, int], dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 50), 100))
    safe_offset = max(0, int(offset or 0))
    safe_stale_days = max(1, min(int(stale_days or STALE_DAYS), 3650))
    priority_filter = _detail_priority_filter(priority)
    scoped_project_id = _detail_project_id(project_id)
    priority_filter_sql = _DETAIL_PRIORITY_FILTER_SQL[priority_filter]
    cutoff = now - timedelta(days=safe_stale_days)
    recent_cutoff = now - timedelta(days=DETAIL_PRIORITY_RECENT_DAYS)
    priority_cte = _DETAIL_PRIORITY_CTE.format(
        research_project_where=(
            "WHERE rpp.project_id = %s" if scoped_project_id is not None else ""
        ),
        research_priority_join=(
            "JOIN research_priority rp ON rp.product_id = p.id"
            if scoped_project_id is not None
            else "LEFT JOIN research_priority rp ON rp.product_id = p.id"
        ),
    )
    priority_params = (
        (now.date(), scoped_project_id, recent_cutoff, cutoff, cutoff)
        if scoped_project_id is not None
        else (now.date(), recent_cutoff, cutoff, cutoff)
    )
    project_filter: dict[str, Any] = {
        "active": False,
        "project_id": None,
        "project_name": None,
        "marketplace": None,
        "project_status": None,
        "project_status_label": None,
        "meaning": "未限定研究项目，队列覆盖全部详情缺口商品。",
    }
    with db.connect() as conn:
        with conn.cursor() as cursor:
            if scoped_project_id is not None:
                cursor.execute(
                    """
                    SELECT id, name, marketplace, status
                    FROM research_projects
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (scoped_project_id,),
                )
                project_row = cursor.fetchone() or {}
                if not project_row:
                    raise DetailReparseError(f"研究项目 #{scoped_project_id} 不存在")
                project_status = str(project_row.get("status") or "")
                project_filter = {
                    "active": True,
                    "project_id": scoped_project_id,
                    "project_name": str(project_row.get("name") or f"项目 #{scoped_project_id}"),
                    "marketplace": str(project_row.get("marketplace") or "US"),
                    "project_status": project_status,
                    "project_status_label": PROJECT_STATUS_LABELS.get(
                        project_status,
                        project_status or "未知阶段",
                    ),
                    "meaning": "队列成员、优先级计数、排序和分页均限定在当前研究项目。",
                }
            cursor.execute(
                """
                SELECT COUNT(*) AS product_total,
                       SUM(detail_collected_at IS NULL) AS not_collected_total,
                       SUM(detail_collected_at IS NOT NULL AND detail_collected_at < %s) AS stale_total,
                       SUM(detail_collected_at IS NOT NULL AND (category_path IS NULL OR category_path = '' OR date_first_available IS NULL)) AS partial_total
                FROM products
                """,
                (cutoff,),
            )
            summary_row = cursor.fetchone() or {}
            cursor.execute(
                priority_cte
                + f"""
                SELECT
                  COUNT(*) AS source_total,
                  SUM(CASE WHEN priority_order = 0 THEN 1 ELSE 0 END) AS project_candidate_total,
                  SUM(CASE WHEN priority_order = 1 THEN 1 ELSE 0 END) AS project_benchmark_total,
                  SUM(CASE WHEN priority_order = 2 THEN 1 ELSE 0 END) AS project_related_total,
                  SUM(CASE WHEN priority_order = 3 THEN 1 ELSE 0 END) AS recent_observation_total,
                  SUM(CASE WHEN priority_order = 4 THEN 1 ELSE 0 END) AS historical_project_total,
                  SUM(CASE WHEN priority_order = 5 THEN 1 ELSE 0 END) AS routine_total,
                  SUM(CASE WHEN active_plan_count > 0 THEN 1 ELSE 0 END) AS planned_total,
                  SUM(CASE WHEN due_plan_count > 0 THEN 1 ELSE 0 END) AS due_plan_total,
                  SUM(CASE WHEN {priority_filter_sql} THEN 1 ELSE 0 END) AS filtered_total
                FROM prioritized_gaps
                """,
                priority_params,
            )
            priority_row = cursor.fetchone() or {}
            gap_total = int(priority_row.get("source_total") or 0)
            filtered_total = int(priority_row.get("filtered_total") or 0)
            cursor.execute(
                priority_cte
                + f"""
                SELECT *
                FROM prioritized_gaps
                WHERE {priority_filter_sql}
                ORDER BY
                  priority_order,
                  due_plan_count DESC,
                  active_plan_count DESC,
                  evidence_order,
                  (category_path IS NULL OR category_path = '') DESC,
                  (date_first_available IS NULL) DESC,
                  last_seen_at DESC, asin ASC
                LIMIT %s OFFSET %s
                """,
                (*priority_params, safe_limit, safe_offset),
            )
            rows = cursor.fetchall()
            contexts = _fetch_gap_project_context(
                cursor,
                [int(row["product_id"]) for row in rows],
                project_id=scoped_project_id,
            )
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        detail_at = _as_datetime(item.get("detail_collected_at"))
        if detail_at is None:
            status = "not_collected"
            reasons = ["尚无有效详情采集"]
        elif detail_at < cutoff:
            status = "stale"
            reasons = [f"详情证据超过 {safe_stale_days} 天"]
        else:
            status = "partial"
            reasons = []
        if not item.get("category_path"):
            reasons.append("缺商品类别")
        if item.get("date_first_available") is None:
            reasons.append("缺首次可售日期")
        item["evidence_status"] = status
        item["reasons"] = reasons
        _decorate_detail_gap_priority(
            item,
            contexts.get(int(item.get("product_id") or 0), []),
            now=now,
        )
        _decorate_detail_gap_disposition(item)
        for key in ("date_first_available", "detail_collected_at", "first_seen_at", "last_seen_at"):
            item[key] = _stringify(item.get(key))
        item.pop("product_id", None)
        normalized_rows.append(item)
    tier_counts = {
        tier: {
            "label": DETAIL_PRIORITY_TIER_LABELS[tier],
            "count": int(priority_row.get(f"{tier}_total") or 0),
        }
        for tier in DETAIL_PRIORITY_TIER_LABELS
    }
    priority_summary = {
        "source_total": gap_total,
        "filtered_total": filtered_total,
        "project_focus_total": (
            tier_counts["project_candidate"]["count"]
            + tier_counts["project_benchmark"]["count"]
        ),
        "active_project_total": (
            tier_counts["project_candidate"]["count"]
            + tier_counts["project_benchmark"]["count"]
            + tier_counts["project_related"]["count"]
        ),
        "planned_total": int(priority_row.get("planned_total") or 0),
        "due_plan_total": int(priority_row.get("due_plan_total") or 0),
        "recent_observation_total": tier_counts["recent_observation"]["count"],
        "tiers": tier_counts,
    }
    summary = {
        "product_total": int(summary_row.get("product_total") or 0),
        "not_collected_total": int(summary_row.get("not_collected_total") or 0),
        "stale_product_total": int(summary_row.get("stale_total") or 0),
        "partial_product_total": int(summary_row.get("partial_total") or 0),
        "gap_total": gap_total,
    }
    return summary, {
        "rows": normalized_rows,
        "total": filtered_total,
        "source_total": gap_total,
        "limit": safe_limit,
        "offset": safe_offset,
        "priority_filter": priority_filter,
        "priority_filter_label": DETAIL_PRIORITY_FILTER_LABELS[priority_filter],
        "project_filter": project_filter,
        "priority_summary": priority_summary,
        "priority_policy": _detail_priority_policy(),
        "disposition_summary": _detail_disposition_summary(normalized_rows),
        "disposition_policy": _detail_disposition_policy(),
    }


def _fetch_gap_project_context(
    cursor: Any,
    product_ids: Iterable[int],
    *,
    project_id: int | None = None,
) -> dict[int, list[dict[str, Any]]]:
    unique = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
    if not unique:
        return {}
    placeholders = ",".join(["%s"] * len(unique))
    cursor.execute(
        f"""
        SELECT
          rpp.product_id,
          rpp.role,
          rp.id AS project_id,
          rp.name AS project_name,
          rp.status AS project_status,
          rop.status AS plan_status,
          rop.next_review_on
        FROM research_project_products rpp
        JOIN research_projects rp ON rp.id = rpp.project_id
        LEFT JOIN research_project_observation_plans rop ON rop.project_id = rp.id
        WHERE rpp.product_id IN ({placeholders})
        {"AND rpp.project_id = %s" if project_id is not None else ""}
        ORDER BY
          rpp.product_id,
          (rp.status NOT IN ('approved', 'rejected')) DESC,
          FIELD(rpp.role, 'candidate', 'benchmark', 'competitor', 'reference'),
          rp.updated_at DESC,
          rp.id
        """,
        [*unique, project_id] if project_id is not None else unique,
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for raw in cursor.fetchall():
        row = dict(raw)
        grouped.setdefault(int(row["product_id"]), []).append(row)
    return grouped


def _decorate_detail_gap_priority(
    item: dict[str, Any],
    contexts: Iterable[dict[str, Any]],
    *,
    now: datetime,
) -> None:
    order = int(item.pop("priority_order", 5) or 0)
    tier = _DETAIL_PRIORITY_TIER_BY_ORDER.get(order, "routine")
    project_count = int(item.pop("project_relation_count", 0) or 0)
    active_project_count = int(item.pop("active_project_count", 0) or 0)
    active_plan_count = int(item.pop("active_plan_count", 0) or 0)
    due_plan_count = int(item.pop("due_plan_count", 0) or 0)
    item.pop("active_candidate_count", None)
    item.pop("active_benchmark_count", None)
    item.pop("evidence_order", None)

    cleaned_contexts: list[dict[str, Any]] = []
    for raw in contexts:
        status = str(raw.get("project_status") or "")
        role = str(raw.get("role") or "reference")
        project_active = status not in TERMINAL_STATUSES
        plan_status = str(raw.get("plan_status") or "") or None
        next_review = _as_date(raw.get("next_review_on"))
        plan_active = bool(project_active and plan_status == "active")
        cleaned_contexts.append(
            {
                "project_id": int(raw.get("project_id") or 0),
                "project_name": str(raw.get("project_name") or "未命名项目"),
                "project_status": status,
                "project_status_label": PROJECT_STATUS_LABELS.get(status, status or "未知阶段"),
                "project_active": project_active,
                "role": role,
                "role_label": DETAIL_PROJECT_ROLE_LABELS.get(role, role),
                "plan_status": plan_status,
                "next_review_on": next_review.isoformat() if next_review else None,
                "plan_active": plan_active,
                "plan_due": bool(plan_active and next_review and next_review <= now.date()),
            }
        )

    reasons = [_DETAIL_PRIORITY_REASON_BY_TIER[tier]]
    nearest_review = _as_date(item.pop("nearest_review_on", None))
    if due_plan_count:
        reasons.append("关联项目的人工复核日期已到；仅表示建议人工查看，不会自动采集")
    elif active_plan_count:
        suffix = f"，下次复核 {nearest_review.isoformat()}" if nearest_review else ""
        reasons.append(f"关联项目有生效中的人工观察计划{suffix}")

    item["priority_tier"] = tier
    item["priority_label"] = DETAIL_PRIORITY_TIER_LABELS[tier]
    item["priority_reasons"] = reasons
    item["priority_reason"] = "；".join(reasons)
    item["research_project_count"] = project_count
    item["active_research_project_count"] = active_project_count
    item["research_context"] = cleaned_contexts[:5]
    item["research_context_truncated"] = len(cleaned_contexts) > 5
    item["monitoring"] = {
        "active_plan_count": active_plan_count,
        "due_plan_count": due_plan_count,
        "nearest_review_on": nearest_review.isoformat() if nearest_review else None,
        "meaning": "人工计划只表示建议人工查看，不代表后台调度或自动采集。",
    }


def _detail_priority_filter(value: str | None) -> str:
    normalized = str(value or "all").strip().lower() or "all"
    if normalized not in DETAIL_PRIORITY_FILTER_LABELS:
        allowed = "、".join(DETAIL_PRIORITY_FILTER_LABELS.values())
        raise DetailReparseError(f"不支持的详情优先级筛选；可选：{allowed}")
    return normalized


def _detail_project_id(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise DetailReparseError("研究项目 ID 必须为正整数") from exc
    if normalized < 1:
        raise DetailReparseError("研究项目 ID 必须为正整数")
    return normalized


def _detail_priority_policy() -> dict[str, Any]:
    return {
        "method_version": DETAIL_PRIORITY_METHOD_VERSION,
        "read_only": True,
        "numeric_score": False,
        "automatic_collection": False,
        "automatic_task_creation": False,
        "recent_observation_days": DETAIL_PRIORITY_RECENT_DAYS,
        "sorting_scope": "优先级筛选与排序在数据库分页前作用；传入项目 ID 时先限定当前项目成员。",
        "monitoring_meaning": "人工计划只表示建议人工查看，不代表后台调度或自动采集。",
        "tier_labels": dict(DETAIL_PRIORITY_TIER_LABELS),
        "filter_labels": dict(DETAIL_PRIORITY_FILTER_LABELS),
    }


def _decorate_detail_gap_disposition(
    item: dict[str, Any],
    local: dict[str, Any] | None = None,
    *,
    local_evidence_checked: bool = False,
    local_evidence_scan_complete: bool | None = None,
) -> None:
    scan_complete = (
        bool(local_evidence_checked)
        if local_evidence_scan_complete is None
        else bool(local_evidence_scan_complete)
    )
    evidence_status = str(item.get("evidence_status") or "partial")
    local_status = str(
        (local.get("status") if local is not None else item.get("local_file_status")) or ""
    )
    local_path = (local.get("path") if local is not None else item.get("local_file_path")) or None
    can_reparse = bool(
        local.get("can_apply") if local is not None else item.get("can_reparse")
    )
    changes = list((local or {}).get("changes") or [])
    replayable_gap_fields = {
        str(field) for field in item.get("local_replayable_gap_fields") or [] if field
    }
    useful_changes = [
        change
        for change in changes
        if change.get("action") in {"fill", "update"}
        or (
            str(change.get("field") or "") in replayable_gap_fields
            and change.get("action") in {"snapshot", "history"}
        )
    ]
    detail_at = _as_datetime(item.get("detail_collected_at"))
    local_at = _as_datetime((local or {}).get("_captured_at") or (local or {}).get("captured_at"))
    local_is_newer = bool(local_at and (detail_at is None or local_at > detail_at))
    local_can_improve = bool(
        local
        and can_reparse
        and (
            evidence_status == "not_collected"
            or useful_changes
            or local_is_newer
        )
    )

    if local_status == "parser_unrecognized":
        code = "adapt_parser"
        reason = (
            "本地页面出现了字段标记，但当前解析器没有完整识别；应先核对原始证据和解析规则，"
            "直接重复采集通常不会修复解析问题。"
        )
        follow_up = "解析规则确认后，再由用户预览并决定是否回填。"
    elif local_can_improve:
        code = "replay_local"
        change_hint = f"，其中 {len(useful_changes)} 项可改善当前缺口" if useful_changes else ""
        reason = f"已有较新或可改善当前字段的本地详情证据{change_hint}；先离线预览，无需访问 Amazon。"
        follow_up = "预览确认后可由用户显式回填；回填路径仍会重新解析原始 HTML。"
    elif local_status == "page_missing":
        code = "page_missing"
        reason = (
            "最近本地页面没有出现当前缺失字段；先核对原始证据并保留缺失，"
            "等待页面变化或经人工核验的外部证据，重复采集未必有效。"
        )
        follow_up = "页面未提供不等于数值为 0，也不应据此降低事实值。"
    elif evidence_status == "not_collected":
        code = "collect_first"
        if scan_complete:
            local_hint = "已完成本地详情目录核对，未发现可用 HTML；"
        elif local_evidence_checked:
            local_hint = "当前扫描范围未发现匹配的本地详情 HTML；"
        else:
            local_hint = ""
        reason = f"{local_hint}数据库尚无有效详情证据，适合由用户显式完成一次单商品采集。"
        follow_up = "采集前仍需确认共享浏览器地址、登录和验证码状态。"
    elif evidence_status == "stale" or local_status == "stale":
        code = "refresh_stale"
        reason = "现有详情证据已超过时效阈值，旧本地文件不能代表当前页面，适合刷新单商品详情。"
        follow_up = "刷新只针对当前商品；遇到拦截、登录页或 ASIN 不一致立即停止。"
    elif local_status == "ready":
        code = "manual_review"
        reason = (
            "本地文件已完整解析且没有可改善当前字段的差异，但数据库仍报告缺口；"
            "应先核对字段映射或来源时点，不建议直接联网重采。"
        )
        follow_up = "确认是映射问题后再修解析或持久化逻辑。"
    else:
        code = "collect_gap"
        if local_status == "invalid":
            reason = "现有本地页面不可用，无法作为补证依据；可由用户显式采集一份新的有效详情页。"
        else:
            reason = "已有详情仍缺少关键字段，且当前没有可直接改善它的本地证据；可显式补采一次核验。"
        follow_up = "若新页面仍未提供字段，应转为保留缺失或人工核验，不连续重复采集。"

    definition = DETAIL_DISPOSITION_DEFINITIONS[code]
    command = str(definition["primary_command"])
    if command == "preview_local" and not local_path:
        command = ""
    item["recommended_action"] = {
        "code": code,
        "label": definition["label"],
        "kind": definition["kind"],
        "reason": reason,
        "follow_up": follow_up,
        "primary_command": command or None,
        "button_label": definition["button_label"] if command else None,
        "accesses_amazon": command == "collect_detail",
        "writes_database": command == "collect_detail",
        "user_confirmation_required": command == "collect_detail",
        "automatic": False,
        "local_evidence_checked": bool(local_evidence_checked),
        "local_evidence_scan_complete": scan_complete,
        "useful_local_change_count": len(useful_changes),
    }


def _detail_disposition_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts = {code: 0 for code in DETAIL_DISPOSITION_DEFINITIONS}
    kind_counts = {"network": 0, "local": 0, "hold": 0, "review": 0}
    row_total = 0
    local_checked_total = 0
    local_scan_complete_total = 0
    for row in rows:
        row_total += 1
        action = row.get("recommended_action") or {}
        code = str(action.get("code") or "manual_review")
        kind = str(action.get("kind") or "review")
        counts[code] = counts.get(code, 0) + 1
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if action.get("local_evidence_checked"):
            local_checked_total += 1
        if action.get("local_evidence_scan_complete"):
            local_scan_complete_total += 1
    return {
        "scope": "current_page",
        "row_total": row_total,
        "local_evidence_checked_total": local_checked_total,
        "local_evidence_scan_complete_total": local_scan_complete_total,
        "network_action_total": kind_counts["network"],
        "local_action_total": kind_counts["local"],
        "hold_total": kind_counts["hold"],
        "review_total": kind_counts["review"],
        "actions": {
            code: {"label": definition["label"], "count": counts.get(code, 0)}
            for code, definition in DETAIL_DISPOSITION_DEFINITIONS.items()
        },
    }


def _detail_disposition_policy(
    *,
    local_evidence_checked: bool = False,
    local_evidence_scan_complete: bool = False,
    local_file_count: int = 0,
    local_file_limit: int = 0,
) -> dict[str, Any]:
    return {
        "method_version": DETAIL_DISPOSITION_METHOD_VERSION,
        "read_only_classification": True,
        "numeric_score": False,
        "automatic_collection": False,
        "automatic_task_creation": False,
        "missing_is_zero": False,
        "page_missing_meaning": "页面未提供表示当前证据中没有该字段，重复采集未必有效。",
        "local_replay_meaning": "只有较新证据或可补空值/更新的字段才建议本地回填；历史快照动作不单独视为新字段。",
        "local_evidence_checked": bool(local_evidence_checked),
        "local_evidence_scan_complete": bool(local_evidence_scan_complete),
        "local_file_count": max(0, int(local_file_count or 0)),
        "local_file_limit": max(0, int(local_file_limit or 0)),
        "local_scan_meaning": (
            "本地文件数未触及扫描上限，本轮可视为完成目录核对。"
            if local_evidence_scan_complete
            else "未执行完整本地核对或扫描结果触及上限；未匹配只代表当前扫描范围，不能断言本地不存在证据。"
        ),
        "action_labels": {
            code: definition["label"] for code, definition in DETAIL_DISPOSITION_DEFINITIONS.items()
        },
    }


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _source_file(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().relative_to(root.resolve()).as_posix()


def _cache_source_file(path: Path) -> str:
    return _display_path(path)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def _asin_from_path(path: Path) -> str | None:
    for value in (path.parent.name, path.stem, path.name):
        for match in _ASIN_IN_PATH_RE.finditer(value.upper()):
            try:
                return normalize_asin(match.group(1))
            except ValueError:
                continue
    return None


def _captured_at(path: Path) -> datetime:
    match = _CAPTURED_AT_RE.search(path.name)
    if match:
        try:
            return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _join_dimensions(*values: float | None) -> str | None:
    if not all(value is not None for value in values):
        return None
    return " x ".join(f"{float(value):g}" for value in values)


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equivalent(left[key], right[key]) for key in left)
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        return abs(float(left) - float(right)) <= 1e-6
    return str(left or "").strip() == str(right or "").strip()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _stringify(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return None if value is None else str(value)
