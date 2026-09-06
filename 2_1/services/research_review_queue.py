"""Read-only review queue for research projects.

The queue derives review reminders from existing project reports, immutable
report versions, and keyword tracking tasks. It never refreshes task progress,
creates tracking tasks, freezes reports, or changes project status.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from services.keyword_tracking import KeywordTrackingTask, list_tracking_tasks
from services.keyword_tracking_scheduler import is_tracking_task_due
from services.research_decision_report import (
    TREND_INDEPENDENT_WINDOW_HOURS,
    TREND_MIN_DAYS,
    TREND_MIN_POINTS,
    build_research_decision_report_from_cursor,
    parse_report_date,
)
from services.research_monitoring import (
    fetch_observation_plans_for_projects,
    monitoring_filter_matches,
    summarize_observation_plan,
)
from services.research_workspace import (
    PROJECT_STATUS_LABELS,
    TERMINAL_STATUSES,
    fetch_research_projects_page,
)
from services.settings import get_collection_limits


ATTENTION_GROUPS = {"action_required", "waiting", "terminal"}
MONITORING_FILTERS = {"active", "due", "paused", "unplanned"}
ATTENTION_LABELS = {
    "action_required": "需要处理",
    "waiting": "等待证据",
    "terminal": "终态稳定",
}
RELEVANT_KEYWORD_ROLES = {"seed", "core"}
PROJECT_SCAN_LIMIT = 500
PROJECT_PAGE_SIZE = 200

_SIGNAL_PRIORITY = {
    "terminal_evidence_changed": 10,
    "terminal_baseline_missing": 15,
    "evidence_missing": 20,
    "monitoring_new_evidence": 35,
    "tracking_error": 30,
    "evidence_changed": 40,
    "evidence_stale": 50,
    "first_review": 60,
    "tracking_paused": 70,
    "monitoring_due": 75,
    "tracking_due": 80,
    "tracking_target_reached": 90,
    "trend_tracking_missing": 100,
    "trend_tracking_finished": 110,
    "freshness_mixed": 120,
    "trend_waiting": 200,
    "terminal_current": 300,
    "evidence_building": 310,
    "review_current": 320,
}


class ResearchReviewQueueError(ValueError):
    """Raised when a review-queue request is invalid."""


def fetch_research_review_queue(
    limit: int = 25,
    *,
    offset: int = 0,
    marketplace: str = "US",
    status: str | None = None,
    keyword: str | None = None,
    attention: str | None = None,
    monitoring: str | None = None,
    as_of: date | str | None = None,
    client: MySQLClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the on-demand queue without mutating any source table."""

    limit_value = _bounded_int(limit, "每页数量", minimum=1, maximum=100)
    offset_value = _bounded_int(offset, "偏移量", minimum=0, maximum=1_000_000)
    attention_value = _attention(attention)
    monitoring_value = _monitoring_filter(monitoring)
    evaluated_on = parse_report_date(as_of)
    now_value = _effective_now(now, evaluated_on=evaluated_on, historical=as_of is not None)
    db = client or MySQLClient()
    projects, source_total, scan_truncated = _load_project_rows(
        marketplace=marketplace,
        status=status,
        keyword=keyword,
        client=db,
    )
    project_ids = [int(row["id"]) for row in projects]
    limits = get_collection_limits()
    task_rows = list_tracking_tasks(
        marketplace=marketplace,
        limit=1000,
        client=db,
    )
    tasks_by_keyword = _group_tracking_tasks(task_rows)

    items: list[dict[str, Any]] = []
    if project_ids:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                versions_by_project = _fetch_report_versions(cursor, project_ids)
                plans_by_project = fetch_observation_plans_for_projects(cursor, project_ids)
                for project in projects:
                    report = build_research_decision_report_from_cursor(
                        cursor,
                        int(project["id"]),
                        evaluated_on=evaluated_on,
                        generated_at=now_value,
                    )
                    items.append(
                        assemble_research_review_item(
                            project,
                            report,
                            versions=versions_by_project.get(int(project["id"]), []),
                            monitoring_plan=plans_by_project.get(int(project["id"])),
                            tasks_by_keyword=tasks_by_keyword,
                            now=now_value,
                            min_interval_hours=limits.tracking_min_interval_hours,
                        )
                    )

    summary = _queue_summary(items)
    filtered = [
        item
        for item in items
        if attention_value is None or item["attention_group"] == attention_value
        if monitoring_filter_matches(item["monitoring_plan"], monitoring_value)
    ]
    filtered.sort(key=_review_item_sort_key)
    rows = filtered[offset_value : offset_value + limit_value]
    warnings: list[str] = []
    if scan_truncated:
        warnings.append(
            f"符合基础筛选的项目共 {source_total} 个，本次只分析最近更新的前 "
            f"{PROJECT_SCAN_LIMIT} 个；请增加基础筛选条件。"
        )
    return {
        "rows": rows,
        "total": len(filtered),
        "limit": limit_value,
        "offset": offset_value,
        "marketplace": str(marketplace or "US").strip().upper(),
        "status": status or None,
        "keyword": " ".join(str(keyword or "").strip().split()) or None,
        "attention": attention_value,
        "monitoring": monitoring_value,
        "evaluated_on": evaluated_on.isoformat(),
        "generated_at": now_value.isoformat(timespec="seconds"),
        "summary": summary,
        "policy": {
            "read_only": True,
            "automatic_collection": False,
            "automatic_task_creation": False,
            "automatic_report_freeze": False,
            "automatic_schedule": False,
            "manual_observation_plans": True,
            "tracking_min_interval_hours": limits.tracking_min_interval_hours,
            "trend_independent_window_hours": TREND_INDEPENDENT_WINDOW_HOURS,
            "trend_minimum_points": TREND_MIN_POINTS,
            "trend_minimum_span_days": TREND_MIN_DAYS,
            "freshness_current_days": 7,
            "freshness_stale_days": 30,
            "next_review_meaning": "现有规则最早需要人工复核的日期，不代表后台自动调度。",
        },
        "attention_labels": ATTENTION_LABELS,
        "project_status_labels": PROJECT_STATUS_LABELS,
        "scanned_projects": len(items),
        "source_total": source_total,
        "scan_truncated": scan_truncated,
        "warnings": warnings,
    }


def assemble_research_review_item(
    project: dict[str, Any],
    report: dict[str, Any],
    *,
    versions: Iterable[dict[str, Any]] = (),
    monitoring_plan: dict[str, Any] | None = None,
    tasks_by_keyword: dict[tuple[str, str], list[KeywordTrackingTask | dict[str, Any]]] | None = None,
    now: datetime | None = None,
    min_interval_hours: int = 72,
) -> dict[str, Any]:
    """Pure queue-row assembly used by production and deterministic tests."""

    current_time = _effective_now(now, evaluated_on=parse_report_date(report.get("evaluated_on")))
    project_summary = dict(report.get("project") or project)
    project_summary["current_decision_report_version_id"] = project.get(
        "current_decision_report_version_id"
    )
    project_summary["current_decision_report_version_no"] = project.get(
        "current_decision_report_version_no"
    )
    project_id = int(project_summary.get("id") or project.get("id") or 0)
    marketplace = str(project_summary.get("marketplace") or project.get("marketplace") or "US").upper()
    project_status = str(project_summary.get("status") or project.get("status") or "")
    terminal = project_status in TERMINAL_STATUSES
    readiness = _readiness_summary(report.get("readiness") or {})
    freshness = _freshness_summary((report.get("evidence_health") or {}).get("freshness") or {})
    timeline = _timeline_summary((report.get("evidence_health") or {}).get("timeline") or {})
    tracking = _tracking_summary(
        report.get("assets", {}).get("keywords") or [],
        marketplace=marketplace,
        tasks_by_keyword=tasks_by_keyword or {},
        now=current_time,
        min_interval_hours=max(72, int(min_interval_hours or 72)),
    )
    version = _version_summary(
        project,
        report,
        versions=list(versions),
        terminal=terminal,
    )
    monitoring = summarize_observation_plan(
        monitoring_plan,
        evaluated_on=report.get("evaluated_on"),
        current_evidence_fingerprint=report.get("evidence_fingerprint"),
        current_report_fingerprint=report.get("report_fingerprint"),
    )
    signals = _build_signals(
        terminal=terminal,
        readiness=readiness,
        freshness=freshness,
        timeline=timeline,
        tracking=tracking,
        version=version,
        monitoring=monitoring,
    )
    primary = signals[0]
    attention_group = _attention_group(primary["code"])
    next_review_on, next_review_reason = _next_review(
        attention_group=attention_group,
        terminal=terminal,
        freshness=freshness,
        tracking=tracking,
        now=current_time,
    )
    recommended_keyword = tracking.get("recommended_keyword")
    return {
        "project": project_summary,
        "attention_group": attention_group,
        "attention_label": ATTENTION_LABELS[attention_group],
        "primary_status": primary,
        "signals": signals,
        "readiness": readiness,
        "freshness": freshness,
        "timeline": timeline,
        "tracking": tracking,
        "report_version": version,
        "monitoring_plan": monitoring,
        "next_review_on": next_review_on,
        "next_review_reason": next_review_reason,
        "actions": {
            "project_route": f"#/research-projects/{project_id}",
            "report_route": f"#/research-projects/{project_id}/report",
            "versions_route": f"#/research-projects/{project_id}/report-versions",
            "tracking_route": "#/tracking" if recommended_keyword else None,
            "monitoring_route": f"#/research-projects/{project_id}/observation-plan",
            "tracking_keyword": recommended_keyword,
            "write_required": False,
        },
    }


def compact_research_review_queue(page: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, evidence-bound representation for the in-app Agent."""

    compact_rows = []
    for item in page.get("rows") or []:
        project = item.get("project") or {}
        compact_rows.append(
            {
                "project_id": project.get("id"),
                "project_name": project.get("name"),
                "project_status": project.get("status"),
                "attention_group": item.get("attention_group"),
                "primary_status": item.get("primary_status"),
                "readiness": item.get("readiness"),
                "freshness": item.get("freshness"),
                "timeline": item.get("timeline"),
                "tracking": item.get("tracking"),
                "report_version": item.get("report_version"),
                "monitoring_plan": item.get("monitoring_plan"),
                "next_review_on": item.get("next_review_on"),
                "next_review_reason": item.get("next_review_reason"),
            }
        )
    return {
        "rows": compact_rows,
        "total": page.get("total"),
        "evaluated_on": page.get("evaluated_on"),
        "summary": page.get("summary"),
        "policy": page.get("policy"),
        "warnings": page.get("warnings") or [],
    }


def _load_project_rows(
    *,
    marketplace: str,
    status: str | None,
    keyword: str | None,
    client: MySQLClient,
) -> tuple[list[dict[str, Any]], int, bool]:
    rows: list[dict[str, Any]] = []
    source_total = 0
    offset = 0
    while len(rows) < PROJECT_SCAN_LIMIT:
        page = fetch_research_projects_page(
            limit=PROJECT_PAGE_SIZE,
            offset=offset,
            marketplace=marketplace,
            status=status,
            keyword=keyword,
            sort_by="updated_at",
            sort_dir="desc",
            client=client,
        )
        source_total = int(page.get("total") or 0)
        batch = list(page.get("rows") or [])
        rows.extend(batch[: PROJECT_SCAN_LIMIT - len(rows)])
        offset += len(batch)
        if not batch or offset >= source_total:
            break
    return rows, source_total, source_total > len(rows)


def _fetch_report_versions(cursor: Any, project_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not project_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(project_ids))
    cursor.execute(
        f"""
        SELECT
          id,
          project_id,
          version_no,
          freeze_kind,
          source_project_status,
          decision_status,
          evaluated_on,
          evidence_as_of,
          evidence_fingerprint,
          report_fingerprint,
          frozen_at,
          version_note
        FROM research_project_report_versions
        WHERE project_id IN ({placeholders})
        ORDER BY project_id, version_no DESC
        """,
        project_ids,
    )
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in cursor.fetchall():
        row = dict(raw)
        grouped[int(row["project_id"])].append(row)
    return dict(grouped)


def _group_tracking_tasks(
    tasks: Iterable[KeywordTrackingTask | dict[str, Any]],
) -> dict[tuple[str, str], list[KeywordTrackingTask | dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[KeywordTrackingTask | dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        marketplace = str(_task_value(task, "marketplace") or "US").upper()
        keyword = _normalize_keyword(_task_value(task, "keyword"))
        if keyword:
            grouped[(marketplace, keyword)].append(task)
    return dict(grouped)


def _tracking_summary(
    keywords: Iterable[dict[str, Any]],
    *,
    marketplace: str,
    tasks_by_keyword: dict[tuple[str, str], list[KeywordTrackingTask | dict[str, Any]]],
    now: datetime,
    min_interval_hours: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    counts = {
        "missing": 0,
        "active": 0,
        "due": 0,
        "paused": 0,
        "error": 0,
        "completed": 0,
        "target_reached": 0,
        "waiting": 0,
    }
    for keyword_row in keywords:
        role = str(keyword_row.get("role") or "").lower()
        if role not in RELEVANT_KEYWORD_ROLES:
            continue
        keyword = str(keyword_row.get("keyword") or "").strip()
        timepoint_count = int(keyword_row.get("timepoint_count") or 0)
        raw_timepoint_count = int(
            keyword_row.get("raw_timepoint_count")
            if keyword_row.get("raw_timepoint_count") is not None
            else timepoint_count
        )
        qualified_timepoint_count = int(
            keyword_row.get("qualified_timepoint_count")
            if keyword_row.get("qualified_timepoint_count") is not None
            else timepoint_count
        )
        candidates = tasks_by_keyword.get((marketplace, _normalize_keyword(keyword)), [])
        selected = _select_tracking_task(candidates)
        item = {
            "keyword": keyword,
            "role": role,
            "timepoint_count": timepoint_count,
            "raw_timepoint_count": raw_timepoint_count,
            "qualified_timepoint_count": qualified_timepoint_count,
            "invalid_rank_timepoint_count": int(
                keyword_row.get("invalid_rank_timepoint_count") or 0
            ),
            "near_duplicate_timepoint_count": int(
                keyword_row.get("near_duplicate_timepoint_count") or 0
            ),
            "timepoint_quality_label": keyword_row.get("timepoint_quality_label"),
            "timepoint_freshness_label": keyword_row.get("timepoint_freshness_label"),
            "first_snapshot_at": keyword_row.get("first_snapshot_at"),
            "latest_snapshot_at": keyword_row.get("latest_snapshot_at"),
            "task_id": None,
            "task_status": None,
            "task_state": "missing",
            "task_state_label": "未规划追踪",
            "current_snapshots": 0,
            "target_snapshots": None,
            "last_collected_at": None,
            "next_collectible_at": None,
            "error_message": None,
        }
        if selected is None:
            counts["missing"] += 1
            rows.append(item)
            continue
        task = _task_to_keyword_task(selected)
        task_status = str(task.status or "")
        item.update(
            {
                "task_id": task.id,
                "task_status": task_status,
                "current_snapshots": task.current_snapshots,
                "target_snapshots": task.target_snapshots,
                "last_collected_at": task.last_collected_at,
                "error_message": task.error_message,
            }
        )
        if task_status == "active":
            counts["active"] += 1
            due, _reason = is_tracking_task_due(
                task,
                now=now,
                min_interval_hours=min_interval_hours,
            )
            if task.current_snapshots >= task.target_snapshots:
                item["task_state"] = "target_reached"
                item["task_state_label"] = "已达目标，待人工检查状态"
                counts["target_reached"] += 1
            elif due:
                item["task_state"] = "due"
                item["task_state_label"] = "已到可采集时间"
                counts["due"] += 1
            else:
                item["task_state"] = "waiting"
                item["task_state_label"] = "等待安全间隔"
                counts["waiting"] += 1
            item["next_collectible_at"] = _next_collectible_at(task, min_interval_hours)
        elif task_status == "paused":
            item["task_state"] = "paused"
            item["task_state_label"] = "追踪已暂停"
            counts["paused"] += 1
        elif task_status == "error":
            item["task_state"] = "error"
            item["task_state_label"] = "追踪异常"
            counts["error"] += 1
        else:
            item["task_state"] = "completed"
            item["task_state_label"] = "追踪已完成"
            counts["completed"] += 1
        rows.append(item)

    recommended = next(
        (
            row["keyword"]
            for state in ("missing", "error", "paused", "completed", "due", "target_reached", "waiting")
            for row in rows
            if row["task_state"] == state and row["keyword"]
        ),
        None,
    )
    future_collectible = [
        _parse_datetime(row.get("next_collectible_at"))
        for row in rows
        if row.get("next_collectible_at")
    ]
    future_collectible = [value for value in future_collectible if value is not None]
    return {
        "relevant_keyword_count": len(rows),
        "tracked_keyword_count": sum(1 for row in rows if row["task_id"] is not None),
        "missing_keyword_count": counts["missing"],
        **counts,
        "recommended_keyword": recommended,
        "next_collectible_at": (
            min(future_collectible).isoformat(sep=" ", timespec="seconds")
            if future_collectible
            else None
        ),
        "keywords": rows,
    }


def _select_tracking_task(
    tasks: Iterable[KeywordTrackingTask | dict[str, Any]],
) -> KeywordTrackingTask | dict[str, Any] | None:
    values = list(tasks)
    if not values:
        return None

    def key(task: KeywordTrackingTask | dict[str, Any]) -> tuple[int, float, int]:
        status = str(_task_value(task, "status") or "")
        updated = _parse_datetime(_task_value(task, "updated_at"))
        timestamp = updated.timestamp() if updated else 0.0
        return (0 if status == "active" else 1, -timestamp, -int(_task_value(task, "id") or 0))

    return min(values, key=key)


def _version_summary(
    project: dict[str, Any],
    report: dict[str, Any],
    *,
    versions: list[dict[str, Any]],
    terminal: bool,
) -> dict[str, Any]:
    latest = versions[0] if versions else None
    current_version_id = int(project.get("current_decision_report_version_id") or 0)
    baseline = None
    baseline_kind = None
    if terminal:
        if current_version_id:
            baseline = next(
                (row for row in versions if int(row.get("id") or 0) == current_version_id),
                None,
            )
            baseline_kind = "terminal_decision" if baseline else None
    elif latest is not None:
        baseline = latest
        baseline_kind = "latest_frozen"
    evidence_changed = None
    report_changed = None
    time_only_change = False
    if baseline is not None:
        evidence_changed = (
            str(report.get("evidence_fingerprint") or "")
            != str(baseline.get("evidence_fingerprint") or "")
        )
        report_changed = (
            str(report.get("report_fingerprint") or "")
            != str(baseline.get("report_fingerprint") or "")
        )
        time_only_change = bool(not evidence_changed and report_changed)
    return {
        "version_count": len(versions),
        "latest": _compact_version(latest),
        "baseline": _compact_version(baseline),
        "baseline_kind": baseline_kind,
        "baseline_label": (
            "终态决策版本"
            if baseline_kind == "terminal_decision"
            else ("最近冻结版本" if baseline_kind else "尚无冻结版本")
        ),
        "evidence_changed": evidence_changed,
        "report_changed": report_changed,
        "time_only_change": time_only_change,
        "current_evidence_fingerprint": report.get("evidence_fingerprint"),
        "current_report_fingerprint": report.get("report_fingerprint"),
    }


def _build_signals(
    *,
    terminal: bool,
    readiness: dict[str, Any],
    freshness: dict[str, Any],
    timeline: dict[str, Any],
    tracking: dict[str, Any],
    version: dict[str, Any],
    monitoring: dict[str, Any],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    timeline_text = (
        f"{timeline['best_points']} 个合格点 / {timeline['best_span_days']} 天"
        + (
            f"（原始 {timeline['best_raw_points']} 点）"
            if timeline["best_raw_points"] != timeline["best_points"]
            else ""
        )
    )
    if terminal:
        if version.get("baseline") is None:
            return [
                _signal(
                    "terminal_baseline_missing",
                    "终态项目缺少决策基线",
                    "high",
                    "项目已批准或拒绝，但没有可核验的绑定决策版本，需要人工检查历史迁移或状态记录。",
                )
            ]
        if version.get("evidence_changed") is True:
            return [
                _signal(
                    "terminal_evidence_changed",
                    "终态项目发现新证据",
                    "high",
                    "当前证据指纹已不同于绑定的终态决策版本，需要人工判断是否退回复核。",
                )
            ]
        if monitoring.get("new_evidence_since_review") is True:
            return [
                _signal(
                    "monitoring_new_evidence",
                    "人工复核后出现新证据",
                    "high",
                    "当前证据指纹已不同于最近一次人工观察记录，需要重新查看报告。",
                )
            ]
        if monitoring.get("is_due"):
            return [
                _signal(
                    "monitoring_due",
                    "观察计划已到复核日",
                    "medium",
                    f"计划复核日为 {monitoring.get('next_review_on')}，是否继续观察仍由人工决定。",
                )
            ]
        return [
            _signal(
                "terminal_current",
                "终态证据未变化",
                "low",
                "当前证据与终态决策基线一致，无需自动重开项目。",
            )
        ]
    if monitoring.get("new_evidence_since_review") is True:
        signals.append(
            _signal(
                "monitoring_new_evidence",
                "人工复核后出现新证据",
                "high",
                "当前证据指纹已不同于最近一次人工观察记录，建议重新查看动态报告。",
            )
        )
    if version.get("evidence_changed") is True:
        signals.append(
            _signal(
                "evidence_changed",
                "冻结后出现新证据",
                "high",
                "当前证据指纹已不同于最近冻结版本，建议重新查看动态报告。",
            )
        )
    if freshness["status"] == "missing":
        signals.append(
            _signal("evidence_missing", "缺少时间证据", "high", "项目尚无可计算时效的商品、关键词或利基快照。")
        )
    if tracking["error"]:
        signals.append(
            _signal(
                "tracking_error",
                "核心词追踪异常",
                "high",
                f"{tracking['error']} 个核心/种子词追踪任务处于异常状态，需要人工检查。",
            )
        )
    if freshness["status"] == "stale":
        signals.append(
            _signal(
                "evidence_stale",
                "主要证据已过期",
                "high",
                f"主要来源超过 30 天；最近证据为 {freshness.get('latest_source_at') or '未知'}。",
            )
        )
    if version.get("baseline") is None and readiness["level"] in {"reviewable", "decision_ready"}:
        signals.append(
            _signal(
                "first_review",
                "首次人工复核待处理",
                "medium",
                f"项目已达到“{readiness['label']}”，但尚无冻结报告版本。",
            )
        )
    if tracking["paused"]:
        signals.append(
            _signal(
                "tracking_paused",
                "核心词追踪已暂停",
                "medium",
                f"{tracking['paused']} 个核心/种子词追踪任务已暂停，趋势证据不会继续积累。",
            )
        )
    if monitoring.get("is_due"):
        signals.append(
            _signal(
                "monitoring_due",
                "观察计划已到复核日",
                "medium",
                f"计划复核日为 {monitoring.get('next_review_on')}；本提醒不会自动采集或冻结报告。",
            )
        )
    if tracking["due"]:
        signals.append(
            _signal(
                "tracking_due",
                "核心词已到可采集时间",
                "medium",
                f"{tracking['due']} 个追踪任务已满足安全间隔；是否采集仍需用户显式确认。",
            )
        )
    if tracking["target_reached"]:
        signals.append(
            _signal(
                "tracking_target_reached",
                "追踪目标已达到",
                "medium",
                f"{tracking['target_reached']} 个 active 任务的实时快照数已达目标，可人工检查任务状态。",
            )
        )
    if not timeline["preliminary_ready"] and tracking["missing"]:
        signals.append(
            _signal(
                "trend_tracking_missing",
                "趋势未达标且核心词未规划追踪",
                "medium",
                (
                    f"决策相关序列仅 {timeline_text}；"
                    f"{tracking['missing']} 个核心/种子词没有追踪任务。"
                ),
            )
        )
    if not timeline["preliminary_ready"] and tracking["completed"] and not tracking["active"]:
        signals.append(
            _signal(
                "trend_tracking_finished",
                "追踪已结束但趋势仍不足",
                "medium",
                (
                    f"决策相关序列仅 {timeline_text}，"
                    "现有核心词任务均已结束。"
                ),
            )
        )
    if freshness["status"] == "mixed":
        signals.append(
            _signal(
                "freshness_mixed",
                "证据时效不一致",
                "medium",
                f"最旧来源约 {freshness.get('oldest_age_days') or 0} 天，复核时需区分不同观察窗口。",
            )
        )
    if (
        not timeline["preliminary_ready"]
        and tracking["waiting"]
        and not any(item["code"] in {"tracking_due", "tracking_error", "tracking_paused"} for item in signals)
    ):
        signals.append(
            _signal(
                "trend_waiting",
                "等待趋势证据积累",
                "low",
                (
                    f"决策相关序列为 {timeline_text}；"
                    "核心词追踪尚未到下一安全采集时间。"
                ),
            )
        )
    if not signals:
        if readiness["level"] in {"not_started", "evidence_building"}:
            signals.append(
                _signal("evidence_building", "证据建设中", "low", readiness.get("summary") or "继续按项目缺口补充证据。")
            )
        else:
            signals.append(
                _signal("review_current", "当前无需额外提醒", "low", "现有证据、追踪与冻结基线未触发新的复核规则。")
            )
    return sorted(signals, key=lambda item: _SIGNAL_PRIORITY.get(item["code"], 999))


def _next_review(
    *,
    attention_group: str,
    terminal: bool,
    freshness: dict[str, Any],
    tracking: dict[str, Any],
    now: datetime,
) -> tuple[str | None, str | None]:
    if attention_group == "action_required":
        return now.date().isoformat(), "已有规则触发人工处理，建议本次打开应用时复核。"
    if terminal:
        return None, "终态证据未变化；只有新证据进入后才重新提醒。"
    candidates: list[tuple[datetime, str]] = []
    next_collectible = _parse_datetime(tracking.get("next_collectible_at"))
    if next_collectible and next_collectible > now:
        candidates.append((next_collectible, "最早核心词追踪任务达到安全采集间隔。"))
    oldest_source = _parse_datetime(freshness.get("oldest_source_at"))
    if oldest_source and freshness.get("status") == "current":
        aging_at = datetime.combine((oldest_source + timedelta(days=8)).date(), time.min)
        if aging_at > now:
            candidates.append((aging_at, "最旧证据将离开 7 天新鲜窗口。"))
    if not candidates:
        return None, "现有证据无法推导下一复核日，需由人工工作节奏决定。"
    when, reason = min(candidates, key=lambda item: item[0])
    return when.date().isoformat(), reason


def _queue_summary(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    rows = list(items)
    return {
        "all": len(rows),
        "action_required": sum(row["attention_group"] == "action_required" for row in rows),
        "waiting": sum(row["attention_group"] == "waiting" for row in rows),
        "terminal": sum(row["attention_group"] == "terminal" for row in rows),
        "evidence_changed": sum(
            row["report_version"].get("evidence_changed") is True for row in rows
        ),
        "tracking_due": sum(int(row["tracking"].get("due") or 0) for row in rows),
        "tracking_error": sum(int(row["tracking"].get("error") or 0) for row in rows),
        "missing_tracking_plan": sum(
            int(row["tracking"].get("missing") or 0) for row in rows
        ),
        "monitoring_active": sum(
            row["monitoring_plan"].get("status") == "active" for row in rows
        ),
        "monitoring_due": sum(
            bool(row["monitoring_plan"].get("is_due")) for row in rows
        ),
        "monitoring_paused": sum(
            row["monitoring_plan"].get("state") == "paused" for row in rows
        ),
        "monitoring_unplanned": sum(
            row["monitoring_plan"].get("state") == "unplanned" for row in rows
        ),
    }


def _readiness_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "level": value.get("level") or "not_started",
        "label": value.get("label") or "尚未形成研究样本",
        "summary": value.get("summary"),
        "passed_count": int(value.get("passed_count") or 0),
        "total_count": int(value.get("total_count") or 0),
        "blocking_count": int(value.get("blocking_count") or 0),
    }


def _freshness_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": value.get("status") or "missing",
        "label": value.get("label") or "无可用时间来源",
        "latest_source_at": value.get("latest_source_at"),
        "oldest_source_at": value.get("oldest_source_at"),
        "latest_age_days": value.get("latest_age_days"),
        "oldest_age_days": value.get("oldest_age_days"),
        "source_count": int(value.get("source_count") or 0),
        "stale_source_count": int(value.get("stale_source_count") or 0),
    }


def _timeline_summary(value: dict[str, Any]) -> dict[str, Any]:
    points = int(value.get("best_points") or 0)
    raw_points = int(value.get("best_raw_points") or points)
    span = int(value.get("best_span_days") or 0)
    return {
        "best_source": value.get("best_source"),
        "best_source_label": value.get("best_source_label"),
        "best_name": value.get("best_name"),
        "best_points": points,
        "best_raw_points": raw_points,
        "best_excluded_points": int(value.get("best_excluded_points") or 0),
        "best_invalid_rank_points": int(value.get("best_invalid_rank_points") or 0),
        "best_near_duplicate_points": int(value.get("best_near_duplicate_points") or 0),
        "best_span_days": span,
        "best_first_snapshot_at": value.get("best_first_snapshot_at"),
        "best_latest_snapshot_at": value.get("best_latest_snapshot_at"),
        "best_quality_status": value.get("best_quality_status"),
        "best_quality_label": value.get("best_quality_label"),
        "best_quality_warnings": list(value.get("best_quality_warnings") or []),
        "best_freshness_status": value.get("best_freshness_status"),
        "best_freshness_label": value.get("best_freshness_label"),
        "best_latest_age_days": value.get("best_latest_age_days"),
        "preliminary_ready": bool(value.get("preliminary_ready")),
        "stable_ready": bool(value.get("stable_ready")),
        "missing_points": max(0, TREND_MIN_POINTS - points),
        "missing_span_days": max(0, TREND_MIN_DAYS - span),
        "context_only_ready": bool(value.get("context_only_ready")),
        "minimum_independent_window_hours": int(
            value.get("minimum_independent_window_hours") or TREND_INDEPENDENT_WINDOW_HOURS
        ),
        "rank_integrity_required": bool(value.get("rank_integrity_required", True)),
        "sequences": list(value.get("sequences") or []),
    }


def _compact_version(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        key: row.get(key)
        for key in (
            "id",
            "project_id",
            "version_no",
            "freeze_kind",
            "source_project_status",
            "decision_status",
            "evaluated_on",
            "evidence_as_of",
            "evidence_fingerprint",
            "report_fingerprint",
            "frozen_at",
            "version_note",
        )
    }


def _signal(code: str, label: str, severity: str, reason: str) -> dict[str, str]:
    return {"code": code, "label": label, "severity": severity, "reason": reason}


def _attention_group(primary_code: str) -> str:
    if primary_code in {"terminal_current"}:
        return "terminal"
    if primary_code in {"trend_waiting", "evidence_building", "review_current"}:
        return "waiting"
    return "action_required"


def _review_item_sort_key(item: dict[str, Any]) -> tuple[int, int, str, int]:
    attention_order = {"action_required": 0, "waiting": 1, "terminal": 2}
    next_review = item.get("next_review_on") or "9999-12-31"
    project = item.get("project") or {}
    return (
        attention_order.get(item.get("attention_group"), 9),
        _SIGNAL_PRIORITY.get((item.get("primary_status") or {}).get("code"), 999),
        next_review,
        -int(project.get("id") or 0),
    )


def _next_collectible_at(task: KeywordTrackingTask, min_interval_hours: int) -> str | None:
    last_collected = _parse_datetime(task.last_collected_at)
    if last_collected is None:
        return None
    return (last_collected + timedelta(hours=min_interval_hours)).isoformat(
        sep=" ", timespec="seconds"
    )


def _task_to_keyword_task(task: KeywordTrackingTask | dict[str, Any]) -> KeywordTrackingTask:
    if isinstance(task, KeywordTrackingTask):
        return task
    return KeywordTrackingTask(
        id=int(task.get("id") or 0),
        marketplace=str(task.get("marketplace") or "US"),
        keyword=str(task.get("keyword") or ""),
        target_snapshots=int(task.get("target_snapshots") or 0),
        status=str(task.get("status") or ""),
        pages_per_keyword=int(task.get("pages_per_keyword") or 1),
        last_collected_at=task.get("last_collected_at"),
        last_checked_at=task.get("last_checked_at"),
        achieved_snapshots=int(task.get("achieved_snapshots") or 0),
        current_snapshots=int(task.get("current_snapshots") or 0),
        error_message=task.get("error_message"),
        created_at=task.get("created_at"),
        updated_at=task.get("updated_at"),
    )


def _task_value(task: KeywordTrackingTask | dict[str, Any], key: str) -> Any:
    return task.get(key) if isinstance(task, dict) else getattr(task, key, None)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _effective_now(
    value: datetime | None,
    *,
    evaluated_on: date,
    historical: bool = False,
) -> datetime:
    if value is not None:
        parsed = _parse_datetime(value)
        if parsed is None:
            raise ResearchReviewQueueError("当前时间格式不正确")
        return parsed
    if historical:
        return datetime.combine(evaluated_on, time(hour=12))
    return datetime.now().replace(microsecond=0)


def _normalize_keyword(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _attention(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "all":
        return None
    if normalized not in ATTENTION_GROUPS:
        raise ResearchReviewQueueError("关注状态仅支持 action_required、waiting 或 terminal")
    return normalized


def _monitoring_filter(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "all":
        return None
    if normalized not in MONITORING_FILTERS:
        raise ResearchReviewQueueError(
            "观察计划筛选仅支持 active、due、paused 或 unplanned"
        )
    return normalized


def _bounded_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ResearchReviewQueueError(f"{label}必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchReviewQueueError(f"{label}必须是整数") from exc
    if not minimum <= parsed <= maximum:
        raise ResearchReviewQueueError(f"{label}必须在 {minimum}..{maximum} 之间")
    return parsed
