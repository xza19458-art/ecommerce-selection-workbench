"""Project-scoped product-detail evidence readiness without changing report gates."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from services.detail_reparse import (
    DETAIL_ANALYSIS_CACHE_PATH,
    DETAIL_DISPOSITION_METHOD_VERSION,
    DETAIL_HTML_ROOT,
    MAX_SCAN_FILES,
    STALE_DAYS,
    _attach_current,
    _decorate_detail_gap_disposition,
    _detail_disposition_policy,
    analyze_detail_html_files_cached,
    list_detail_html_files,
)
from services.research_workspace import get_research_project


READINESS_SCHEMA_VERSION = "research-detail-readiness-v1"
READINESS_METHOD_VERSION = "research-detail-readiness-v1"
READINESS_LABELS = {
    "empty": "尚未关联商品",
    "ready": "详情证据齐备",
    "action_required": "存在可处理缺口",
    "page_limited": "页面证据受限",
    "mixed": "详情证据部分可用",
}
EVIDENCE_STATUS_LABELS = {
    "ready": "当前可用",
    "partial": "字段不全",
    "stale": "证据过期",
    "not_collected": "尚未采集",
}
CORE_FIELDS = (
    ("category_path", "商品类别"),
    ("date_first_available", "首次可售日期"),
    ("best_seller_ranks", "热销榜排名"),
)
ROLE_ORDER = {"candidate": 0, "benchmark": 1, "competitor": 2, "reference": 3}
ACTION_ORDER = {
    "adapt_parser": 0,
    "replay_local": 1,
    "collect_first": 2,
    "refresh_stale": 3,
    "collect_gap": 4,
    "manual_review": 5,
    "page_missing": 6,
    "current_ready": 7,
}


def get_research_project_detail_readiness(
    project_id: int,
    *,
    stale_days: int = STALE_DAYS,
    file_limit: int = 100,
    client: MySQLClient | None = None,
    now: datetime | None = None,
    force_refresh: bool = False,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Return bounded, read-only detail readiness for one project's products."""

    db = client or MySQLClient()
    current_now = (now or datetime.now()).replace(microsecond=0)
    safe_stale_days = max(1, min(int(stale_days or STALE_DAYS), 3650))
    safe_file_limit = max(1, min(int(file_limit or MAX_SCAN_FILES), MAX_SCAN_FILES))
    detail = get_research_project(project_id, client=db)
    products = list(detail.get("products") or [])
    target_asins = {str(row.get("asin") or "").upper() for row in products if row.get("asin")}

    paths = list_detail_html_files(limit=safe_file_limit)
    local_scan_complete = len(paths) < safe_file_limit
    analyses, cache = analyze_detail_html_files_cached(
        paths,
        root=DETAIL_HTML_ROOT,
        now=current_now,
        stale_days=safe_stale_days,
        force_refresh=force_refresh,
        cache_path=cache_path or DETAIL_ANALYSIS_CACHE_PATH,
    )
    current_by_asin = {
        str(row.get("asin") or "").upper(): {
            **dict(row),
            "bsr_snapshot_count": int(row.get("bsr_count") or 0),
        }
        for row in products
        if row.get("asin")
    }
    local_items = [
        _attach_current(item, current_by_asin.get(str(item.get("asin") or "").upper()))
        for item in analyses
        if str(item.get("asin") or "").upper() in target_asins
    ]
    return assemble_research_project_detail_readiness(
        detail,
        local_items=local_items,
        now=current_now,
        stale_days=safe_stale_days,
        local_evidence_checked=True,
        local_evidence_scan_complete=local_scan_complete,
        local_file_count=len(paths),
        local_file_limit=safe_file_limit,
        cache={
            "hit_count": int(cache.get("hit_count") or 0),
            "miss_count": int(cache.get("miss_count") or 0),
            "duration_ms": int(cache.get("duration_ms") or 0),
            "warning": "本地缓存读写异常，本次结果仍可使用。" if cache.get("warning") else None,
        },
    )


def assemble_research_project_detail_readiness(
    detail: dict[str, Any],
    *,
    local_items: Iterable[dict[str, Any]] = (),
    now: datetime | None = None,
    stale_days: int = STALE_DAYS,
    local_evidence_checked: bool = False,
    local_evidence_scan_complete: bool = False,
    local_file_count: int = 0,
    local_file_limit: int = 0,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure assembly entry used by tests and the API-facing loader."""

    project = dict(detail.get("project") or {})
    products = [dict(row) for row in detail.get("products") or []]
    current_now = (now or datetime.now()).replace(microsecond=0)
    safe_stale_days = max(1, min(int(stale_days or STALE_DAYS), 3650))
    latest_local: dict[str, dict[str, Any]] = {}
    for raw in sorted(local_items, key=lambda row: str(row.get("captured_at") or ""), reverse=True):
        asin = str(raw.get("asin") or "").upper()
        if asin:
            latest_local.setdefault(asin, dict(raw))

    rows = [
        _build_readiness_row(
            product,
            latest_local.get(str(product.get("asin") or "").upper()),
            now=current_now,
            stale_days=safe_stale_days,
            local_evidence_checked=local_evidence_checked,
            local_evidence_scan_complete=local_evidence_scan_complete,
        )
        for product in products
    ]
    rows.sort(
        key=lambda row: (
            ROLE_ORDER.get(str(row.get("role") or "reference"), 9),
            ACTION_ORDER.get(str((row.get("recommended_action") or {}).get("code") or ""), 9),
            str(row.get("asin") or ""),
        )
    )
    summary = _build_summary(rows)
    local_policy = _detail_disposition_policy(
        local_evidence_checked=local_evidence_checked,
        local_evidence_scan_complete=local_evidence_scan_complete,
        local_file_count=local_file_count,
        local_file_limit=local_file_limit,
    )
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "method_version": READINESS_METHOD_VERSION,
        "generated_at": current_now.isoformat(sep=" "),
        "project": {
            "id": project.get("id"),
            "name": project.get("name"),
            "marketplace": project.get("marketplace"),
            "status": project.get("status"),
            "status_label": project.get("status_label"),
        },
        "summary": summary,
        "rows": rows,
        "local_scan": {
            "checked": bool(local_evidence_checked),
            "complete": bool(local_evidence_scan_complete),
            "file_count": max(0, int(local_file_count or 0)),
            "file_limit": max(0, int(local_file_limit or 0)),
            "meaning": local_policy["local_scan_meaning"],
            "cache": dict(cache or {}),
        },
        "policy": {
            "scope": "project_members",
            "core_fields": [{"key": key, "label": label} for key, label in CORE_FIELDS],
            "stale_days": safe_stale_days,
            "disposition_method_version": DETAIL_DISPOSITION_METHOD_VERSION,
            "numeric_score": False,
            "changes_decision_gate": False,
            "changes_report_fingerprint": False,
            "automatic_collection": False,
            "automatic_task_creation": False,
            "missing_is_zero": False,
            "statement": (
                "准备度只汇总项目成员的详情证据与人工处置入口，不进入综合评分、决策门禁或冻结报告指纹。"
            ),
        },
        "routes": {
            "detail_evidence": "#/metrics/evidence",
            "project": f"#/research-projects/{project.get('id')}",
            "report": f"#/research-projects/{project.get('id')}/report",
        },
    }


def compact_research_project_detail_readiness(result: dict[str, Any]) -> dict[str, Any]:
    """Return the path-free subset appropriate for the in-app Agent."""

    return {
        "schema_version": result.get("schema_version"),
        "method_version": result.get("method_version"),
        "generated_at": result.get("generated_at"),
        "project": result.get("project"),
        "summary": result.get("summary"),
        "rows": [
            {
                key: row.get(key)
                for key in (
                    "product_id",
                    "asin",
                    "title",
                    "role",
                    "evidence_status",
                    "evidence_status_label",
                    "detail_collected_at",
                    "detail_age_days",
                    "evidence_collected",
                    "evidence_total",
                    "missing_fields",
                    "reasons",
                    "has_local_evidence",
                    "local_evidence_status",
                    "recommended_action",
                )
            }
            for row in result.get("rows") or []
        ],
        "local_scan": {
            key: (result.get("local_scan") or {}).get(key)
            for key in ("checked", "complete", "file_count", "file_limit", "meaning")
        },
        "policy": result.get("policy"),
    }


def _build_readiness_row(
    product: dict[str, Any],
    local: dict[str, Any] | None,
    *,
    now: datetime,
    stale_days: int,
    local_evidence_checked: bool,
    local_evidence_scan_complete: bool,
) -> dict[str, Any]:
    asin = str(product.get("asin") or "").upper()
    detail_at = _as_datetime(product.get("detail_collected_at"))
    bsr_count = int(product.get("bsr_count") or product.get("bsr_snapshot_count") or 0)
    missing_fields: list[dict[str, str]] = []
    if not str(product.get("category_path") or "").strip():
        missing_fields.append({"key": "category_path", "label": "商品类别"})
    if product.get("date_first_available") in (None, ""):
        missing_fields.append({"key": "date_first_available", "label": "首次可售日期"})
    if bsr_count <= 0:
        missing_fields.append({"key": "best_seller_ranks", "label": "热销榜排名"})

    age_days = _age_days(detail_at, now)
    if detail_at is None:
        evidence_status = "not_collected"
        reasons = ["尚无有效详情采集"]
    elif detail_at < now - timedelta(days=stale_days):
        evidence_status = "stale"
        reasons = [f"详情证据超过 {stale_days} 天"]
    elif missing_fields:
        evidence_status = "partial"
        reasons = []
    else:
        evidence_status = "ready"
        reasons = []
    reasons.extend(f"缺{field['label']}" for field in missing_fields)
    evidence_collected = len(CORE_FIELDS) - len(missing_fields)

    item = {
        "evidence_status": evidence_status,
        "detail_collected_at": detail_at,
        "local_replayable_gap_fields": [
            field["key"]
            for field in missing_fields
            if _local_field_status(local, field["key"]) == "collected"
        ],
    }
    targeted_local = _target_local_evidence(local, [field["key"] for field in missing_fields])
    if evidence_status == "ready":
        action = _ready_action(detail_at)
    else:
        _decorate_detail_gap_disposition(
            item,
            targeted_local,
            local_evidence_checked=local_evidence_checked,
            local_evidence_scan_complete=local_evidence_scan_complete,
        )
        action = _compact_action(item.get("recommended_action") or {})

    return {
        "product_id": product.get("product_id"),
        "asin": asin,
        "title": product.get("title_zh") or product.get("title"),
        "role": product.get("role"),
        "role_notes": product.get("notes"),
        "evidence_status": evidence_status,
        "evidence_status_label": EVIDENCE_STATUS_LABELS[evidence_status],
        "detail_collected_at": _stringify(detail_at),
        "detail_age_days": age_days,
        "evidence_collected": evidence_collected,
        "evidence_total": len(CORE_FIELDS),
        "missing_fields": missing_fields,
        "reasons": reasons,
        "field_statuses": [
            {
                "key": key,
                "label": label,
                "status": "missing" if any(field["key"] == key for field in missing_fields) else "collected",
            }
            for key, label in CORE_FIELDS
        ],
        "has_local_evidence": bool(local),
        "local_evidence_status": local.get("status") if local else None,
        "local_evidence_captured_at": _stringify((local or {}).get("_captured_at") or (local or {}).get("captured_at")),
        "recommended_action": action,
        "routes": {
            "product": f"#/product/{asin}",
            "metrics": f"#/metrics/{asin}",
            "detail_evidence": "#/metrics/evidence",
        },
    }


def _target_local_evidence(
    local: dict[str, Any] | None,
    missing_fields: list[str],
) -> dict[str, Any] | None:
    if local is None or not missing_fields:
        return local
    targeted = dict(local)
    missing = set(missing_fields)
    targeted["changes"] = [
        dict(change)
        for change in local.get("changes") or []
        if str(change.get("field") or "") in missing
    ]
    if str(targeted.get("status") or "") in {"invalid", "stale"}:
        return targeted
    statuses = [_local_field_status(targeted, field) for field in missing_fields]
    if "parser_unrecognized" in statuses:
        targeted["status"] = "parser_unrecognized"
    elif statuses and all(status == "page_missing" for status in statuses):
        targeted["status"] = "page_missing"
    return targeted


def _local_field_status(local: dict[str, Any] | None, field: str) -> str | None:
    coverage = (local or {}).get("coverage") or {}
    value = coverage.get(field) or {}
    return str(value.get("status") or "") or None


def _ready_action(detail_at: datetime | None) -> dict[str, Any]:
    return {
        "code": "current_ready",
        "label": "当前详情证据可用",
        "kind": "ready",
        "reason": f"核心详情字段已具备，最近详情采集为 {_stringify(detail_at) or '未知时间'}。",
        "follow_up": "继续按项目观察节奏复核时效；无需仅为补字段重复采集。",
        "requires_amazon": False,
        "requires_database_write": False,
        "requires_explicit_user_action": False,
        "automatic": False,
        "local_evidence_checked": False,
        "local_evidence_scan_complete": False,
        "useful_local_change_count": 0,
    }


def _compact_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": action.get("code"),
        "label": action.get("label"),
        "kind": action.get("kind"),
        "reason": action.get("reason"),
        "follow_up": action.get("follow_up"),
        "requires_amazon": bool(action.get("accesses_amazon")),
        "requires_database_write": bool(action.get("writes_database")),
        "requires_explicit_user_action": bool(action.get("user_confirmation_required")),
        "automatic": False,
        "local_evidence_checked": bool(action.get("local_evidence_checked")),
        "local_evidence_scan_complete": bool(action.get("local_evidence_scan_complete")),
        "useful_local_change_count": int(action.get("useful_local_change_count") or 0),
    }


def _build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = {key: 0 for key in EVIDENCE_STATUS_LABELS}
    kind_counts = {"network": 0, "local": 0, "hold": 0, "review": 0, "ready": 0}
    missing_counts = {key: {"label": label, "count": 0} for key, label in CORE_FIELDS}
    for row in rows:
        status = str(row.get("evidence_status") or "partial")
        status_counts[status] = status_counts.get(status, 0) + 1
        kind = str((row.get("recommended_action") or {}).get("kind") or "review")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        for field in row.get("missing_fields") or []:
            key = str(field.get("key") or "")
            if key in missing_counts:
                missing_counts[key]["count"] += 1

    if not rows:
        level = "empty"
        statement = "项目尚未关联商品，无法评估详情证据准备度。"
    elif status_counts["ready"] == len(rows):
        level = "ready"
        statement = "全部项目商品的核心详情字段在当前时效阈值内可用。"
    elif kind_counts["network"] + kind_counts["local"] + kind_counts["review"] > 0:
        level = "action_required"
        statement = "项目中存在可补采、可回填或需人工核对的详情缺口。"
    elif kind_counts["hold"] > 0:
        level = "page_limited"
        statement = "主要缺口已在本地页面中确认未提供，应保留缺失并等待新证据。"
    else:
        level = "mixed"
        statement = "项目详情证据部分可用，仍需按成员逐项核对。"

    next_row = next(
        (row for row in rows if (row.get("recommended_action") or {}).get("code") != "current_ready"),
        None,
    )
    return {
        "level": level,
        "label": READINESS_LABELS[level],
        "statement": statement,
        "product_total": len(rows),
        "ready_total": status_counts["ready"],
        "partial_total": status_counts["partial"],
        "stale_total": status_counts["stale"],
        "not_collected_total": status_counts["not_collected"],
        "network_action_total": kind_counts["network"],
        "local_action_total": kind_counts["local"],
        "hold_total": kind_counts["hold"],
        "review_total": kind_counts["review"],
        "missing_fields": missing_counts,
        "next_action": (
            {
                "asin": next_row.get("asin"),
                "title": next_row.get("title"),
                "role": next_row.get("role"),
                **dict(next_row.get("recommended_action") or {}),
            }
            if next_row
            else None
        ),
    }


def _age_days(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    delta = now - value
    return max(0, int(delta.total_seconds() // 86_400))


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _stringify(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value not in (None, "") else None
