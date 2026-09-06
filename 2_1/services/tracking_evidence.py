"""Read-only evidence review for one keyword tracking task."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from statistics import mean
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from pkg_paths import resolve_user_writable_path, user_data_root
from services.keyword_tracking import KeywordTrackingTask, get_tracking_task
from services.keyword_tracking_scheduler import is_tracking_task_due
from services.research_decision_report import (
    TREND_INDEPENDENT_WINDOW_HOURS,
    TREND_MIN_DAYS,
    TREND_MIN_POINTS,
    TREND_STABLE_DAYS,
    TREND_STABLE_POINTS,
    qualify_keyword_rank_timepoints,
    select_independent_keyword_timepoints,
)
from services.settings import get_collection_limits


EVIDENCE_SCHEMA_VERSION = "tracking-evidence-v1.1"
DEFAULT_MANIFEST_ROOT = Path("数据结果") / "keyword_tracking_runs"
LOCAL_IMPORT_PREFIX = "local_html_import:"
ROLE_LABELS = {
    "candidate": "候选商品",
    "benchmark": "对标商品",
    "competitor": "竞争商品",
    "reference": "参考商品",
}
_SOURCE_TIME_RE = re.compile(r"(?P<day>20\d{6})[_-](?P<clock>\d{4,6})")


def build_tracking_task_evidence(
    task_id: int,
    *,
    client: MySQLClient | None = None,
    now: datetime | None = None,
    manifest_root: str | Path = DEFAULT_MANIFEST_ROOT,
    movement_limit: int = 20,
) -> dict[str, Any]:
    """Build a task-level evidence bundle without refreshing or writing task state."""

    db = client or MySQLClient()
    task = get_tracking_task(int(task_id), client=db)
    if task is None:
        raise ValueError(f"关键词追踪任务不存在: {task_id}")

    with db.connect() as conn:
        with conn.cursor() as cursor:
            keyword_row = _fetch_keyword(cursor, task)
            keyword_id = int(keyword_row["keyword_id"]) if keyword_row else None
            timepoints = _fetch_timepoints(cursor, keyword_id)
            observations = _fetch_rank_observations(cursor, keyword_id)
            research_rows = _fetch_research_products(cursor, task)
            jobs = _fetch_keyword_jobs(cursor, task)

    evaluated_at = (now or datetime.now()).replace(microsecond=0)
    raw_first = timepoints[0]["snapshot_at"] if timepoints else None
    raw_latest = timepoints[-1]["snapshot_at"] if timepoints else None
    quality = qualify_keyword_rank_timepoints(
        timepoints,
        raw_timepoint_count=len(timepoints),
        raw_first_snapshot_at=raw_first,
        raw_latest_snapshot_at=raw_latest,
        evaluated_on=evaluated_at.date(),
    )
    decorated_timepoints = classify_tracking_timepoints(timepoints)
    adjacent = build_adjacent_batch_comparison(
        observations,
        limit=max(1, min(int(movement_limit), 100)),
    )
    watch = build_research_watch(
        research_rows,
        observations,
        [row["snapshot_at"] for row in timepoints],
        task=task,
    )
    manifests = load_tracking_manifests(task.id, root=manifest_root)
    source_files = collect_tracking_source_files(manifests, jobs)
    related_jobs = relate_jobs_to_timepoints(jobs, [row["snapshot_at"] for row in timepoints])
    schedule = build_tracking_schedule(task, now=evaluated_at)
    trend_gate = build_trend_gate(quality)

    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": _format_datetime(evaluated_at),
        "task": {
            **task.to_dict(),
            "progress_note": "任务进度按已入库原始快照时点计算，不等同于趋势合格时点。",
        },
        "keyword": keyword_row
        or {
            "keyword_id": None,
            "marketplace": task.marketplace,
            "keyword": task.keyword,
        },
        "schedule": schedule,
        "trend_gate": trend_gate,
        "timepoints": list(reversed(decorated_timepoints)),
        "adjacent_comparison": adjacent,
        "research_watch": watch,
        "local_evidence": {
            "manifests": manifests,
            "files": source_files,
            "jobs": related_jobs,
            "note": "原始文件与任务日志用于追溯采集事实；旧批次若缺少清单，不影响已入库快照，但证据链完整度较低。",
        },
        "boundaries": [
            "自然序位是搜索结果页可见商品的本系统估算，不是 Amazon 内部真实自然排名。",
            "本批未观察到商品不等于商品下架、断货或长期排名下降。",
            "相邻批次变化只描述两个采集时点，不单独形成长期趋势结论。",
            "本接口只读，不创建任务、不触发联网采集、不修改项目或评分。",
        ],
    }


def compact_tracking_task_evidence(
    evidence: dict[str, Any],
    *,
    movement_limit: int = 5,
    research_product_limit: int = 10,
    local_item_limit: int = 8,
) -> dict[str, Any]:
    """Return an Agent-safe summary without local paths or raw job URLs."""

    task = evidence.get("task") or {}
    schedule = evidence.get("schedule") or {}
    trend = evidence.get("trend_gate") or {}
    adjacent = evidence.get("adjacent_comparison") or {}
    watch = evidence.get("research_watch") or {}
    local = evidence.get("local_evidence") or {}
    movement_limit = max(1, min(int(movement_limit), 10))
    research_product_limit = max(1, min(int(research_product_limit), 20))
    local_item_limit = max(1, min(int(local_item_limit), 20))

    def batch_rows(key: str) -> list[dict[str, Any]]:
        fields = (
            "product_id",
            "marketplace",
            "asin",
            "title",
            "title_zh",
            "brand",
            "previous_rank",
            "current_rank",
            "rank_delta",
            "movement",
            "movement_label",
            "is_sponsored",
        )
        return [
            {field: row.get(field) for field in fields if field in row}
            for row in (adjacent.get(key) or [])[:movement_limit]
            if isinstance(row, dict)
        ]

    research_products = []
    for product in (watch.get("products") or [])[:research_product_limit]:
        if not isinstance(product, dict):
            continue
        research_products.append(
            {
                "product_id": product.get("product_id"),
                "asin": product.get("asin"),
                "title": product.get("title"),
                "title_zh": product.get("title_zh"),
                "brand": product.get("brand"),
                "projects": product.get("projects") or [],
                "current_observed": product.get("current_observed"),
                "previous_observed": product.get("previous_observed"),
                "current_rank": product.get("current_rank"),
                "previous_rank": product.get("previous_rank"),
                "rank_delta": product.get("rank_delta"),
                "observed_timepoint_count": product.get("observed_timepoint_count"),
                "total_timepoint_count": product.get("total_timepoint_count"),
                "absence_streak": product.get("absence_streak"),
                "latest_observed_at": product.get("latest_observed_at"),
                "history": (product.get("history") or [])[-8:],
                "interpretation": product.get("interpretation"),
            }
        )

    files = [row for row in local.get("files") or [] if isinstance(row, dict)]
    jobs = [row for row in local.get("jobs") or [] if isinstance(row, dict)]
    compact_files = [
        {
            "name": row.get("name"),
            "source_type": row.get("source_type"),
            "snapshot_at": row.get("snapshot_at"),
            "exists": row.get("exists"),
            "size_bytes": row.get("size_bytes"),
            "sha256": row.get("sha256"),
        }
        for row in files[:local_item_limit]
    ]
    compact_jobs = [
        {
            "id": row.get("id"),
            "job_type": row.get("job_type"),
            "status": row.get("status"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "total_found": row.get("total_found"),
            "total_valid": row.get("total_valid"),
            "total_inserted": row.get("total_inserted"),
            "has_error": bool(row.get("error_message")),
            "related_snapshot_at": row.get("related_snapshot_at"),
            "association_basis": row.get("association_basis"),
        }
        for row in jobs[:local_item_limit]
    ]
    adjacent_fields = (
        "available",
        "current_snapshot_at",
        "previous_snapshot_at",
        "current_product_count",
        "previous_product_count",
        "retained_product_count",
        "entered_product_count",
        "exited_product_count",
        "retention_rate",
        "comparable_rank_count",
        "improved_count",
        "worsened_count",
        "unchanged_count",
        "average_rank_delta",
        "summary",
        "confidence_note",
    )
    return {
        "schema_version": evidence.get("schema_version"),
        "generated_at": evidence.get("generated_at"),
        "task": {
            "id": task.get("id"),
            "marketplace": task.get("marketplace"),
            "keyword": task.get("keyword"),
            "status": task.get("status"),
            "current_snapshots": task.get("current_snapshots"),
            "target_snapshots": task.get("target_snapshots"),
            "pages_per_keyword": task.get("pages_per_keyword"),
            "last_collected_at": task.get("last_collected_at"),
            "last_checked_at": task.get("last_checked_at"),
            "has_error": bool(task.get("error_message")),
            "progress_note": task.get("progress_note"),
        },
        "schedule": {
            key: schedule.get(key)
            for key in (
                "state",
                "label",
                "due",
                "reason",
                "minimum_interval_hours",
                "next_collectible_at",
                "hours_until_next",
                "target_reached",
            )
        },
        "trend": {
            "raw_points": trend.get("raw_points"),
            "qualified_points": trend.get("qualified_points"),
            "span_days": trend.get("span_days"),
            "preliminary": trend.get("preliminary") or {},
            "stable": trend.get("stable") or {},
            "scope_note": trend.get("scope_note"),
        },
        "latest_timepoints": [
            {
                key: row.get(key)
                for key in (
                    "snapshot_at",
                    "qualification",
                    "qualification_label",
                    "exclusion_reason",
                    "rank_integrity",
                    "product_count",
                    "organic_row_count",
                    "sponsored_row_count",
                    "min_organic_rank",
                    "max_organic_rank",
                    "page_count",
                    "data_coverage",
                )
            }
            for row in (evidence.get("timepoints") or [])[:8]
            if isinstance(row, dict)
        ],
        "adjacent_comparison": {
            **{key: adjacent.get(key) for key in adjacent_fields},
            "entered": batch_rows("entered"),
            "exited": batch_rows("exited"),
            "movements": batch_rows("movements"),
        },
        "research_watch": {
            "project_count": watch.get("project_count"),
            "product_count": watch.get("product_count"),
            "projects": watch.get("projects") or [],
            "products": research_products,
            "note": watch.get("note"),
        },
        "local_evidence": {
            "manifest_count": len(local.get("manifests") or []),
            "readable_manifest_count": sum(
                1 for row in local.get("manifests") or [] if isinstance(row, dict) and row.get("readable")
            ),
            "source_file_count": len(files),
            "existing_file_count": sum(1 for row in files if row.get("exists")),
            "missing_file_count": sum(1 for row in files if not row.get("exists")),
            "job_count": len(jobs),
            "files": compact_files,
            "jobs": compact_jobs,
            "note": local.get("note"),
        },
        "boundaries": evidence.get("boundaries") or [],
        "policy": {
            "read_only": True,
            "refreshes_task_state": False,
            "automatic_collection": False,
            "writes_database": False,
        },
        "route": f"#/tracking/{task.get('id')}" if task.get("id") else "#/tracking",
    }


def classify_tracking_timepoints(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the same rank-integrity and 24-hour window rules used by reports."""

    normalized: list[dict[str, Any]] = []
    for raw in rows:
        snapshot_at = _parse_datetime(raw.get("snapshot_at"))
        if snapshot_at is None:
            continue
        row = _json_safe(dict(raw))
        row_count = _int(row.get("row_count"))
        product_count = _int(row.get("product_count"))
        organic_count = _int(row.get("organic_row_count"))
        distinct_ranks = _int(row.get("distinct_organic_rank_count"))
        invalid_ranks = _int(row.get("invalid_organic_rank_count"))
        duplicate_products = max(0, row_count - product_count)
        rank_integrity = (
            organic_count > 0
            and distinct_ranks == organic_count
            and invalid_ranks == 0
            and duplicate_products == 0
        )
        normalized.append(
            {
                **row,
                "_snapshot_at": snapshot_at,
                "snapshot_at": _format_datetime(snapshot_at),
                "duplicate_product_count": duplicate_products,
                "rank_integrity": rank_integrity,
            }
        )

    selection_input = [
        {
            **row,
            "snapshot_at": row["_snapshot_at"],
            "product_count": _int(row.get("product_count")),
        }
        for row in normalized
        if row["rank_integrity"]
    ]
    selected = select_independent_keyword_timepoints(selection_input)
    selected_times = {_format_datetime(row["snapshot_at"]) for row in selected}
    for row in normalized:
        if not row["rank_integrity"]:
            row["qualification"] = "invalid"
            row["qualification_label"] = "仅作出现范围"
            row["exclusion_reason"] = "自然序位存在重复、缺失或商品重复，未计入趋势。"
        elif row["snapshot_at"] not in selected_times:
            row["qualification"] = "near_duplicate"
            row["qualification_label"] = "间隔不足"
            row["exclusion_reason"] = (
                f"与相邻合格观察不足 {TREND_INDEPENDENT_WINDOW_HOURS} 小时，未单独计入趋势。"
            )
        else:
            row["qualification"] = "qualified"
            row["qualification_label"] = "合格时间点"
            row["exclusion_reason"] = None
        row.pop("_snapshot_at", None)
    return normalized


def build_trend_gate(quality: dict[str, Any]) -> dict[str, Any]:
    points = _int(quality.get("qualified_timepoint_count"))
    raw_points = _int(quality.get("raw_timepoint_count"))
    span_days = _int(quality.get("qualified_span_days"))
    preliminary = _gate_status(points, span_days, TREND_MIN_POINTS, TREND_MIN_DAYS, "初步趋势")
    stable = _gate_status(points, span_days, TREND_STABLE_POINTS, TREND_STABLE_DAYS, "稳定趋势")
    return {
        **_json_safe(quality),
        "raw_points": raw_points,
        "qualified_points": points,
        "span_days": span_days,
        "preliminary": preliminary,
        "stable": stable,
        "seasonality_ready": False,
        "scope_note": "趋势门槛只使用排名结构完整且相邻至少 24 小时的独立时间点。",
    }


def build_tracking_schedule(
    task: KeywordTrackingTask,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = (now or datetime.now()).replace(microsecond=0)
    minimum_hours = max(72, int(get_collection_limits().tracking_min_interval_hours))
    due, reason = is_tracking_task_due(
        task,
        now=current_time,
        min_interval_hours=minimum_hours,
    )
    last_collected = _parse_datetime(task.last_collected_at)
    target_reached = task.current_snapshots >= task.target_snapshots
    next_at = None
    if task.status == "active" and not target_reached:
        next_at = current_time if last_collected is None else last_collected + timedelta(hours=minimum_hours)
    hours_until_next = None
    if next_at is not None:
        hours_until_next = max(0.0, (next_at - current_time).total_seconds() / 3600)
    if target_reached:
        state, label = "target_reached", "已达到任务目标"
    elif task.status != "active":
        state, label = task.status, "任务当前不执行采集"
    elif due:
        state, label = "due", "已满足安全间隔"
    else:
        state, label = "waiting", "等待安全间隔"
    return {
        "state": state,
        "label": label,
        "due": due,
        "reason": reason,
        "minimum_interval_hours": minimum_hours,
        "next_collectible_at": _format_datetime(next_at),
        "hours_until_next": round(hours_until_next, 1) if hours_until_next is not None else None,
        "target_reached": target_reached,
    }


def build_adjacent_batch_comparison(
    observations: Iterable[dict[str, Any]],
    *,
    limit: int = 20,
) -> dict[str, Any]:
    grouped = _group_observations(observations)
    snapshot_times = sorted(grouped)
    if len(snapshot_times) < 2:
        return {
            "available": False,
            "current_snapshot_at": _format_datetime(snapshot_times[-1]) if snapshot_times else None,
            "previous_snapshot_at": None,
            "summary": "至少需要两个采集时点才能比较相邻批次。",
            "confidence_note": "相邻批次比较尚不可用。",
            "entered": [],
            "exited": [],
            "movements": [],
        }

    previous_at, current_at = snapshot_times[-2], snapshot_times[-1]
    previous = grouped[previous_at]
    current = grouped[current_at]
    previous_ids = set(previous)
    current_ids = set(current)
    retained_ids = previous_ids & current_ids
    entered_ids = current_ids - previous_ids
    exited_ids = previous_ids - current_ids
    movements: list[dict[str, Any]] = []
    improved = worsened = unchanged = 0
    deltas: list[int] = []
    for product_id in retained_ids:
        before = previous[product_id]
        after = current[product_id]
        before_rank = _organic_rank(before)
        after_rank = _organic_rank(after)
        if before_rank is None or after_rank is None:
            continue
        delta = after_rank - before_rank
        deltas.append(delta)
        if delta < 0:
            movement, movement_label = "improved", "序位提升"
            improved += 1
        elif delta > 0:
            movement, movement_label = "worsened", "序位下降"
            worsened += 1
        else:
            movement, movement_label = "unchanged", "序位不变"
            unchanged += 1
        movements.append(
            {
                **_product_identity(after),
                "previous_rank": before_rank,
                "current_rank": after_rank,
                "rank_delta": delta,
                "movement": movement,
                "movement_label": movement_label,
            }
        )

    entered = [_batch_member(current[product_id], "current_rank") for product_id in entered_ids]
    exited = [_batch_member(previous[product_id], "previous_rank") for product_id in exited_ids]
    entered.sort(key=lambda row: (_rank_sort(row.get("current_rank")), row.get("asin") or ""))
    exited.sort(key=lambda row: (_rank_sort(row.get("previous_rank")), row.get("asin") or ""))
    movements.sort(key=lambda row: (-abs(_int(row.get("rank_delta"))), row.get("current_rank") or 999999))
    retention = len(retained_ids) / len(previous_ids) if previous_ids else None
    average_delta = mean(deltas) if deltas else None
    summary = (
        f"当前批次 {len(current_ids)} 个，留存 {len(retained_ids)} 个，"
        f"本批新观察到 {len(entered_ids)} 个，本批未观察到 {len(exited_ids)} 个。"
    )
    return {
        "available": True,
        "current_snapshot_at": _format_datetime(current_at),
        "previous_snapshot_at": _format_datetime(previous_at),
        "current_product_count": len(current_ids),
        "previous_product_count": len(previous_ids),
        "retained_product_count": len(retained_ids),
        "entered_product_count": len(entered_ids),
        "exited_product_count": len(exited_ids),
        "retention_rate": round(retention, 4) if retention is not None else None,
        "comparable_rank_count": len(deltas),
        "improved_count": improved,
        "worsened_count": worsened,
        "unchanged_count": unchanged,
        "average_rank_delta": round(average_delta, 4) if average_delta is not None else None,
        "summary": summary,
        "confidence_note": "这里只比较相邻两个采集时点；未观察到不等于下架，序位变化也不等于长期趋势。",
        "entered": entered[:limit],
        "exited": exited[:limit],
        "movements": movements[:limit],
        "list_limit": limit,
    }


def build_research_watch(
    research_rows: Iterable[dict[str, Any]],
    observations: Iterable[dict[str, Any]],
    snapshot_times: Iterable[Any],
    *,
    task: KeywordTrackingTask,
) -> dict[str, Any]:
    times = sorted(filter(None, (_parse_datetime(value) for value in snapshot_times)))
    grouped = _group_observations(observations)
    projects: dict[int, dict[str, Any]] = {}
    products: dict[int, dict[str, Any]] = {}
    for raw in research_rows:
        row = _json_safe(dict(raw))
        project_id = _int(row.get("project_id"))
        product_id = _int(row.get("product_id"))
        projects[project_id] = {
            "project_id": project_id,
            "name": row.get("project_name"),
            "status": row.get("project_status"),
            "detail_route": f"#/research-projects/{project_id}",
            "report_route": f"#/research-projects/{project_id}/report",
        }
        product = products.setdefault(
            product_id,
            {
                **_product_identity(row),
                "product_id": product_id,
                "projects": [],
            },
        )
        relation = {
            "project_id": project_id,
            "project_name": row.get("project_name"),
            "role": row.get("role"),
            "role_label": ROLE_LABELS.get(str(row.get("role") or ""), row.get("role") or "未分类"),
        }
        if relation not in product["projects"]:
            product["projects"].append(relation)

    result_products: list[dict[str, Any]] = []
    for product_id, product in products.items():
        history: list[dict[str, Any]] = []
        for snapshot_at in times:
            observed = grouped.get(snapshot_at, {}).get(product_id)
            if observed:
                history.append(
                    {
                        "snapshot_at": _format_datetime(snapshot_at),
                        "organic_rank": _organic_rank(observed),
                        "is_sponsored": bool(observed.get("is_sponsored")),
                    }
                )
        current = grouped.get(times[-1], {}).get(product_id) if times else None
        previous = grouped.get(times[-2], {}).get(product_id) if len(times) >= 2 else None
        absence_streak = 0
        for snapshot_at in reversed(times):
            if product_id in grouped.get(snapshot_at, {}):
                break
            absence_streak += 1
        current_rank = _organic_rank(current) if current else None
        previous_rank = _organic_rank(previous) if previous else None
        product.update(
            {
                "current_observed": current is not None,
                "previous_observed": previous is not None,
                "current_rank": current_rank,
                "previous_rank": previous_rank,
                "rank_delta": (
                    current_rank - previous_rank
                    if current_rank is not None and previous_rank is not None
                    else None
                ),
                "observed_timepoint_count": len(history),
                "total_timepoint_count": len(times),
                "absence_streak": absence_streak,
                "latest_observed_at": history[-1]["snapshot_at"] if history else None,
                "history": history,
                "detail_route": (
                    f"#/product/{product.get('asin')}?score_keyword={_encode_route_value(task.keyword)}"
                    if product.get("asin")
                    else None
                ),
            }
        )
        product["interpretation"] = research_watch_interpretation(product, total_points=len(times))
        result_products.append(product)

    role_order = {"candidate": 0, "benchmark": 1, "competitor": 2, "reference": 3}
    result_products.sort(
        key=lambda row: (
            min((role_order.get(str(item.get("role")), 9) for item in row["projects"]), default=9),
            row.get("asin") or "",
        )
    )
    return {
        "project_count": len(projects),
        "product_count": len(result_products),
        "projects": list(projects.values()),
        "products": result_products,
        "note": "项目商品只按本追踪关键词的批次观察；没有出现时必须保留‘未观察到’表述。",
    }


def research_watch_interpretation(product: dict[str, Any], *, total_points: int) -> str:
    current = bool(product.get("current_observed"))
    previous = bool(product.get("previous_observed"))
    current_rank = product.get("current_rank")
    previous_rank = product.get("previous_rank")
    observed_count = _int(product.get("observed_timepoint_count"))
    absence_streak = _int(product.get("absence_streak"))
    if current and previous and current_rank is not None and previous_rank is not None:
        delta = _int(current_rank) - _int(previous_rank)
        if delta < 0:
            return f"相邻批次自然序位估算由 {previous_rank} 提升至 {current_rank}；仍需更多时间点确认持续性。"
        if delta > 0:
            return f"相邻批次自然序位估算由 {previous_rank} 下降至 {current_rank}；单次变化不代表长期走弱。"
        return f"相邻批次自然序位估算均为 {current_rank}，当前仅能说明短期稳定。"
    if current:
        return f"本批新观察到，自然序位估算为 {current_rank or '未知'}；需要后续批次确认是否持续。"
    if observed_count:
        return (
            f"最近 {max(1, absence_streak)} 个批次未观察到，历史共出现 {observed_count}/{total_points} 个时点；"
            "这不等于下架、断货或长期排名下降。"
        )
    return f"本关键词现有 {total_points} 个批次均未观察到；不能据此判断商品状态或其他关键词表现。"


def load_tracking_manifests(
    task_id: int,
    *,
    root: str | Path = DEFAULT_MANIFEST_ROOT,
) -> list[dict[str, Any]]:
    manifest_root = resolve_user_writable_path(root)
    if not manifest_root.exists():
        return []
    manifests: list[dict[str, Any]] = []
    for path in sorted(manifest_root.glob(f"tracking_{int(task_id)}_*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            manifests.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "readable": False,
                    "error": f"清单无法读取：{exc}",
                    "files": [],
                }
            )
            continue
        pages = payload.get("pages") if isinstance(payload.get("pages"), list) else []
        files = []
        for page in pages:
            if not isinstance(page, dict) or not page.get("保存文件"):
                continue
            files.append(
                _file_metadata(
                    page["保存文件"],
                    source_type="追踪采集 HTML",
                    snapshot_at=payload.get("snapshot_at"),
                )
            )
        manifests.append(
            {
                "path": str(path),
                "name": path.name,
                "readable": True,
                "status": payload.get("status"),
                "message": payload.get("message"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "snapshot_at": payload.get("snapshot_at"),
                "manifest_fingerprint": _sha256(path) if _path_is_safe(path) else None,
                "page_count": len(pages),
                "total_found": sum(_int(page.get("解析商品数")) for page in pages if isinstance(page, dict)),
                "total_valid": sum(_int(page.get("有效商品数")) for page in pages if isinstance(page, dict)),
                "files": files,
            }
        )
    return manifests


def collect_tracking_source_files(
    manifests: Iterable[dict[str, Any]],
    jobs: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        for item in manifest.get("files") or []:
            key = str(item.get("path") or "")
            if key:
                files[key.casefold()] = item
    for job in jobs:
        url = str(job.get("url") or "")
        if not url.startswith(LOCAL_IMPORT_PREFIX):
            continue
        raw_path = url[len(LOCAL_IMPORT_PREFIX) :]
        item = _file_metadata(
            raw_path,
            source_type="人工确认入库 HTML",
            snapshot_at=_source_snapshot_at(raw_path),
        )
        key = str(item.get("path") or "")
        if key:
            files.setdefault(key.casefold(), item)
    return sorted(
        files.values(),
        key=lambda row: (str(row.get("snapshot_at") or ""), str(row.get("name") or "")),
        reverse=True,
    )


def relate_jobs_to_timepoints(
    jobs: Iterable[dict[str, Any]],
    snapshot_times: Iterable[Any],
) -> list[dict[str, Any]]:
    snapshots = sorted(filter(None, (_parse_datetime(value) for value in snapshot_times)))
    related: list[dict[str, Any]] = []
    for raw in jobs:
        row = _json_safe(dict(raw))
        inferred = _parse_datetime(_source_snapshot_at(str(row.get("url") or "")))
        basis = "文件名时间" if inferred else None
        if inferred is None:
            started_at = _parse_datetime(row.get("started_at"))
            if started_at is not None and snapshots:
                nearest = min(snapshots, key=lambda value: abs((value - started_at).total_seconds()))
                if abs((nearest - started_at).total_seconds()) <= 2 * 3600:
                    inferred = nearest
                    basis = "关键词与执行时间近似"
        row["related_snapshot_at"] = _format_datetime(inferred)
        row["association_basis"] = basis
        related.append(row)
    return related


def _fetch_keyword(cursor: Any, task: KeywordTrackingTask) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT id AS keyword_id, marketplace, keyword, created_at
        FROM keywords
        WHERE marketplace = %s AND keyword = %s
        LIMIT 1
        """,
        (task.marketplace, task.keyword),
    )
    row = cursor.fetchone()
    return _json_safe(dict(row)) if row else None


def _fetch_timepoints(cursor: Any, keyword_id: int | None) -> list[dict[str, Any]]:
    if keyword_id is None:
        return []
    cursor.execute(
        """
        SELECT
          krs.snapshot_at,
          COUNT(*) AS row_count,
          COUNT(DISTINCT krs.product_id) AS product_count,
          SUM(CASE WHEN krs.is_sponsored = 0 THEN 1 ELSE 0 END) AS organic_row_count,
          SUM(CASE WHEN krs.is_sponsored = 1 THEN 1 ELSE 0 END) AS sponsored_row_count,
          COUNT(DISTINCT CASE
            WHEN krs.is_sponsored = 0 THEN krs.organic_rank ELSE NULL
          END) AS distinct_organic_rank_count,
          SUM(CASE
            WHEN krs.is_sponsored = 0
             AND (krs.organic_rank IS NULL OR krs.organic_rank < 1)
            THEN 1 ELSE 0
          END) AS invalid_organic_rank_count,
          COUNT(DISTINCT krs.page_no) AS observed_page_count,
          MIN(CASE WHEN krs.is_sponsored = 0 THEN krs.organic_rank END) AS min_organic_rank,
          MAX(CASE WHEN krs.is_sponsored = 0 THEN krs.organic_rank END) AS max_organic_rank,
          serp.page_count,
          serp.total_card_count,
          serp.organic_count AS serp_organic_count,
          serp.sponsored_count AS serp_sponsored_count,
          serp.unique_asin_count,
          serp.ad_density,
          serp.data_coverage
        FROM keyword_rank_snapshots krs
        LEFT JOIN keyword_serp_snapshots serp
          ON serp.keyword_id = krs.keyword_id
         AND serp.snapshot_at = krs.snapshot_at
        WHERE krs.keyword_id = %s
        GROUP BY krs.snapshot_at, serp.id
        ORDER BY krs.snapshot_at
        """,
        (keyword_id,),
    )
    return [_json_safe(dict(row)) for row in cursor.fetchall()]


def _fetch_rank_observations(cursor: Any, keyword_id: int | None) -> list[dict[str, Any]]:
    if keyword_id is None:
        return []
    cursor.execute(
        """
        SELECT
          krs.snapshot_at,
          krs.product_id,
          krs.organic_rank,
          krs.is_sponsored,
          krs.page_no,
          p.marketplace,
          p.asin,
          p.title,
          p.title_zh,
          p.brand,
          p.product_url
        FROM keyword_rank_snapshots krs
        JOIN products p ON p.id = krs.product_id
        WHERE krs.keyword_id = %s
        ORDER BY krs.snapshot_at, krs.product_id
        """,
        (keyword_id,),
    )
    return [_json_safe(dict(row)) for row in cursor.fetchall()]


def _fetch_research_products(cursor: Any, task: KeywordTrackingTask) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          rp.id AS project_id,
          rp.name AS project_name,
          rp.status AS project_status,
          rpp.role,
          p.id AS product_id,
          p.marketplace,
          p.asin,
          p.title,
          p.title_zh,
          p.brand,
          p.product_url
        FROM research_project_keywords rpk
        JOIN keywords k ON k.id = rpk.keyword_id
        JOIN research_projects rp ON rp.id = rpk.project_id
        JOIN research_project_products rpp ON rpp.project_id = rp.id
        JOIN products p ON p.id = rpp.product_id
        WHERE k.marketplace = %s
          AND k.keyword = %s
        ORDER BY rp.id, FIELD(rpp.role, 'candidate', 'benchmark', 'competitor', 'reference'), p.id
        """,
        (task.marketplace, task.keyword),
    )
    return [_json_safe(dict(row)) for row in cursor.fetchall()]


def _fetch_keyword_jobs(cursor: Any, task: KeywordTrackingTask) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          id, keyword, url, pages, status, started_at, finished_at,
          total_found, total_valid, total_inserted, error_message
        FROM crawl_jobs
        WHERE keyword = %s
        ORDER BY started_at DESC, id DESC
        LIMIT 50
        """,
        (task.keyword,),
    )
    rows = []
    for raw in cursor.fetchall():
        row = _json_safe(dict(raw))
        row["job_type"] = (
            "入库"
            if str(row.get("url") or "").startswith(LOCAL_IMPORT_PREFIX)
            or _int(row.get("total_inserted")) > 0
            else "采集"
        )
        rows.append(row)
    return rows


def _group_observations(
    observations: Iterable[dict[str, Any]],
) -> dict[datetime, dict[int, dict[str, Any]]]:
    grouped: dict[datetime, dict[int, dict[str, Any]]] = {}
    for raw in observations:
        snapshot_at = _parse_datetime(raw.get("snapshot_at"))
        product_id = _int(raw.get("product_id"))
        if snapshot_at is None or product_id <= 0:
            continue
        grouped.setdefault(snapshot_at, {})[product_id] = _json_safe(dict(raw))
    return grouped


def _gate_status(points: int, days: int, required_points: int, required_days: int, label: str) -> dict[str, Any]:
    remaining_points = max(0, required_points - points)
    remaining_days = max(0, required_days - days)
    ready = remaining_points == 0 and remaining_days == 0
    if ready:
        message = f"已达到{label}门槛：{points} 个合格点 / {days} 天跨度。"
    else:
        gaps = []
        if remaining_points:
            gaps.append(f"{remaining_points} 个合格时间点")
        if remaining_days:
            gaps.append(f"{remaining_days} 天跨度")
        message = f"距离{label}门槛还差 " + "、".join(gaps) + "。"
    return {
        "label": label,
        "ready": ready,
        "required_points": required_points,
        "required_days": required_days,
        "remaining_points": remaining_points,
        "remaining_days": remaining_days,
        "message": message,
    }


def _batch_member(row: dict[str, Any], rank_key: str) -> dict[str, Any]:
    return {
        **_product_identity(row),
        rank_key: _organic_rank(row),
        "is_sponsored": bool(row.get("is_sponsored")),
    }


def _product_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": _int(row.get("product_id")),
        "marketplace": row.get("marketplace") or "US",
        "asin": row.get("asin"),
        "title": row.get("title"),
        "title_zh": row.get("title_zh"),
        "brand": row.get("brand"),
        "product_url": row.get("product_url"),
    }


def _organic_rank(row: dict[str, Any] | None) -> int | None:
    if not row or bool(row.get("is_sponsored")):
        return None
    value = row.get("organic_rank")
    if value in (None, ""):
        return None
    rank = _int(value)
    return rank if rank > 0 else None


def _rank_sort(value: Any) -> int:
    rank = _int(value)
    return rank if rank > 0 else 999999


def _file_metadata(value: Any, *, source_type: str, snapshot_at: Any) -> dict[str, Any]:
    raw_path = Path(str(value or ""))
    path = raw_path if raw_path.is_absolute() else user_data_root().joinpath(raw_path)
    path = path.resolve(strict=False)
    safe = _path_is_safe(path)
    exists = safe and path.is_file()
    return {
        "path": str(path),
        "name": path.name,
        "source_type": source_type,
        "snapshot_at": _format_datetime(_parse_datetime(snapshot_at)),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else None,
        "sha256": _sha256(path) if exists else None,
        "safe_path": safe,
    }


def _path_is_safe(path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(user_data_root().resolve(strict=False))
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source_snapshot_at(value: str) -> str | None:
    match = _SOURCE_TIME_RE.search(str(value or ""))
    if not match:
        return None
    clock = match.group("clock")
    if len(clock) == 4:
        clock += "00"
    try:
        observed = datetime.strptime(match.group("day") + clock, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return _format_datetime(observed.replace(minute=0, second=0, microsecond=0))


def _encode_route_value(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value or ""), safe="")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value is not None else None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, Decimal):
        return float(value)
    return value
