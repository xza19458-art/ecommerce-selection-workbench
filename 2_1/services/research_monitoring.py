"""User-controlled observation plans for research projects.

Plans persist a seller's review rhythm, but never schedule background work,
refresh tracking tasks, collect Amazon pages, freeze reports, or change project
status. Completing a review only records the fingerprints that the user saw and
advances the next manual review date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from services.research_decision_report import (
    build_research_decision_report_from_cursor,
    parse_report_date,
)
from services.research_workspace import PROJECT_STATUS_LABELS


PLAN_STATUSES = {"active", "paused"}
PLAN_STATUS_LABELS = {"active": "观察中", "paused": "已暂停"}
PLAN_STATE_LABELS = {
    "unplanned": "未制定计划",
    "paused": "计划已暂停",
    "scheduled": "按计划等待",
    "due": "今天应复核",
    "overdue": "已超过复核日",
}
MIN_CADENCE_DAYS = 3
MAX_CADENCE_DAYS = 180
DEFAULT_CADENCE_DAYS = 14
_HEX64_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


class ResearchMonitoringError(ValueError):
    """Raised when an observation-plan request is invalid or stale."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        code: str = "research_monitoring_invalid",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details


def get_research_observation_plan(
    project_id: int,
    *,
    as_of: date | str | None = None,
    client: MySQLClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one plan with the current read-only report context."""

    project_id_value = _positive_int(project_id, "研究项目 ID")
    evaluated_on = parse_report_date(as_of)
    now_value = _effective_now(now)
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            project = _fetch_project(cursor, project_id_value)
            plan = _fetch_plan(cursor, project_id_value)
            report = build_research_decision_report_from_cursor(
                cursor,
                project_id_value,
                evaluated_on=evaluated_on,
                generated_at=now_value,
            )
    return assemble_observation_plan_bundle(
        project,
        report,
        plan,
        evaluated_on=evaluated_on,
        generated_at=now_value,
    )


def save_research_observation_plan(
    project_id: int,
    *,
    status: str = "active",
    cadence_days: int = DEFAULT_CADENCE_DAYS,
    next_review_on: date | str,
    plan_note: str | None = None,
    client: MySQLClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create or update the latest operational plan for a project."""

    project_id_value = _positive_int(project_id, "研究项目 ID")
    status_value = _plan_status(status)
    cadence_value = _bounded_int(
        cadence_days,
        "复核周期",
        minimum=MIN_CADENCE_DAYS,
        maximum=MAX_CADENCE_DAYS,
    )
    review_date = _required_date(next_review_on, "下次复核日期")
    note = _optional_text(plan_note, maximum=2000)
    now_value = _effective_now(now)
    db = client or MySQLClient()
    created = False
    with db.connect() as conn:
        with conn.cursor() as cursor:
            project = _fetch_project(cursor, project_id_value, for_update=True)
            existing = _fetch_plan(cursor, project_id_value, for_update=True)
            created = existing is None
            cursor.execute(
                """
                INSERT INTO research_project_observation_plans (
                  project_id, status, cadence_days, next_review_on, plan_note
                )
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  status = VALUES(status),
                  cadence_days = VALUES(cadence_days),
                  next_review_on = VALUES(next_review_on),
                  plan_note = VALUES(plan_note),
                  updated_at = CURRENT_TIMESTAMP(6)
                """,
                (project_id_value, status_value, cadence_value, review_date, note),
            )
            plan = _fetch_plan(cursor, project_id_value)
            report = build_research_decision_report_from_cursor(
                cursor,
                project_id_value,
                evaluated_on=now_value.date(),
                generated_at=now_value,
            )
    result = assemble_observation_plan_bundle(
        project,
        report,
        plan,
        evaluated_on=now_value.date(),
        generated_at=now_value,
    )
    result["created"] = created
    return result


def complete_research_observation_review(
    project_id: int,
    *,
    confirmed: bool,
    evaluated_on: date | str,
    expected_report_fingerprint: str,
    review_note: str | None = None,
    client: MySQLClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record one explicit manual review and advance the plan once."""

    if not confirmed:
        raise ResearchMonitoringError("完成项目复核前必须由用户明确确认")
    project_id_value = _positive_int(project_id, "研究项目 ID")
    evaluated_date = parse_report_date(evaluated_on)
    expected_fingerprint = _fingerprint(expected_report_fingerprint)
    note = _optional_text(review_note, maximum=2000)
    now_value = _effective_now(now)
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            project = _fetch_project(cursor, project_id_value, for_update=True)
            plan = _fetch_plan(cursor, project_id_value, for_update=True)
            if plan is None:
                raise ResearchMonitoringError("请先保存项目观察计划，再完成本次复核")
            if str(plan.get("status") or "") != "active":
                raise ResearchMonitoringError("观察计划已暂停，请先恢复计划再完成复核")
            report = build_research_decision_report_from_cursor(
                cursor,
                project_id_value,
                evaluated_on=evaluated_date,
                generated_at=now_value,
            )
            current_fingerprint = _fingerprint(report.get("report_fingerprint"))
            if current_fingerprint != expected_fingerprint:
                raise ResearchMonitoringError(
                    "当前报告证据或口径已变化，请刷新观察计划后重新确认。",
                    status_code=409,
                    code="research_monitoring_stale_report",
                    details={
                        "expected_report_fingerprint": expected_fingerprint,
                        "current_report_fingerprint": current_fingerprint,
                        "current_evidence_fingerprint": report.get("evidence_fingerprint"),
                    },
                )
            cadence_days = _bounded_int(
                plan.get("cadence_days"),
                "复核周期",
                minimum=MIN_CADENCE_DAYS,
                maximum=MAX_CADENCE_DAYS,
            )
            next_review_on = now_value.date() + timedelta(days=cadence_days)
            cursor.execute(
                """
                UPDATE research_project_observation_plans
                SET last_reviewed_at = %s,
                    last_review_evaluated_on = %s,
                    last_review_evidence_fingerprint = %s,
                    last_review_report_fingerprint = %s,
                    last_review_note = %s,
                    next_review_on = %s,
                    updated_at = CURRENT_TIMESTAMP(6)
                WHERE project_id = %s
                """,
                (
                    now_value,
                    evaluated_date,
                    report.get("evidence_fingerprint"),
                    current_fingerprint,
                    note,
                    next_review_on,
                    project_id_value,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise ResearchMonitoringError("项目观察计划更新失败")
            plan = _fetch_plan(cursor, project_id_value)
    result = assemble_observation_plan_bundle(
        project,
        report,
        plan,
        evaluated_on=evaluated_date,
        generated_at=now_value,
    )
    result["review_completed"] = True
    return result


def fetch_observation_plans_for_projects(
    cursor: Any,
    project_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Bulk-load plan rows for review-queue assembly without extra connections."""

    ids = sorted({_positive_int(value, "研究项目 ID") for value in project_ids})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    cursor.execute(
        f"""
        SELECT *
        FROM research_project_observation_plans
        WHERE project_id IN ({placeholders})
        """,
        ids,
    )
    return {
        int(row["project_id"]): dict(row)
        for row in cursor.fetchall()
    }


def summarize_observation_plan(
    plan: dict[str, Any] | None,
    *,
    evaluated_on: date | str,
    current_evidence_fingerprint: str | None = None,
    current_report_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Return stable plan state labels used by API, queue, Web, and Agent."""

    evaluated_date = parse_report_date(evaluated_on)
    if not plan:
        return {
            "exists": False,
            "status": None,
            "status_label": "未制定",
            "state": "unplanned",
            "state_label": PLAN_STATE_LABELS["unplanned"],
            "cadence_days": None,
            "next_review_on": None,
            "days_until_review": None,
            "is_due": False,
            "last_reviewed_at": None,
            "last_review_evaluated_on": None,
            "last_review_evidence_fingerprint": None,
            "last_review_report_fingerprint": None,
            "last_review_note": None,
            "new_evidence_since_review": None,
            "report_changed_since_review": None,
            "plan_note": None,
            "created_at": None,
            "updated_at": None,
        }
    value = _serialize_plan(plan)
    status = _plan_status(value.get("status"))
    next_review_on = _required_date(value.get("next_review_on"), "下次复核日期")
    days_until = (next_review_on - evaluated_date).days
    if status == "paused":
        state = "paused"
    elif days_until < 0:
        state = "overdue"
    elif days_until == 0:
        state = "due"
    else:
        state = "scheduled"
    last_evidence = str(value.get("last_review_evidence_fingerprint") or "").upper() or None
    last_report = str(value.get("last_review_report_fingerprint") or "").upper() or None
    current_evidence = str(current_evidence_fingerprint or "").upper() or None
    current_report = str(current_report_fingerprint or "").upper() or None
    value.update(
        {
            "exists": True,
            "status": status,
            "status_label": PLAN_STATUS_LABELS[status],
            "state": state,
            "state_label": PLAN_STATE_LABELS[state],
            "cadence_days": int(value.get("cadence_days") or DEFAULT_CADENCE_DAYS),
            "next_review_on": next_review_on.isoformat(),
            "days_until_review": days_until,
            "is_due": status == "active" and days_until <= 0,
            "new_evidence_since_review": (
                current_evidence != last_evidence
                if current_evidence is not None and last_evidence is not None
                else None
            ),
            "report_changed_since_review": (
                current_report != last_report
                if current_report is not None and last_report is not None
                else None
            ),
        }
    )
    return value


def assemble_observation_plan_bundle(
    project: dict[str, Any],
    report: dict[str, Any],
    plan: dict[str, Any] | None,
    *,
    evaluated_on: date | str,
    generated_at: datetime,
) -> dict[str, Any]:
    """Build the public response without exposing the full decision report."""

    project_value = dict(project)
    project_value["status_label"] = PROJECT_STATUS_LABELS.get(
        str(project_value.get("status") or ""),
        str(project_value.get("status") or "未知"),
    )
    plan_value = summarize_observation_plan(
        plan,
        evaluated_on=evaluated_on,
        current_evidence_fingerprint=report.get("evidence_fingerprint"),
        current_report_fingerprint=report.get("report_fingerprint"),
    )
    readiness = report.get("readiness") or {}
    evidence_health = report.get("evidence_health") or {}
    freshness = evidence_health.get("freshness") or {}
    timeline = evidence_health.get("timeline") or {}
    project_id = int(project_value.get("id") or 0)
    return {
        "project": project_value,
        "plan": plan_value,
        "current_report": {
            "evaluated_on": report.get("evaluated_on"),
            "evidence_as_of": report.get("evidence_as_of"),
            "evidence_fingerprint": report.get("evidence_fingerprint"),
            "report_fingerprint": report.get("report_fingerprint"),
            "schema_version": report.get("schema_version"),
            "method_version": report.get("method_version"),
            "readiness": {
                "level": readiness.get("level"),
                "label": readiness.get("label"),
                "passed_count": int(readiness.get("passed_count") or 0),
                "total_count": int(readiness.get("total_count") or 0),
                "blocking_count": int(readiness.get("blocking_count") or 0),
            },
            "freshness": {
                "status": freshness.get("status"),
                "label": freshness.get("label"),
                "latest_source_at": freshness.get("latest_source_at"),
                "oldest_source_at": freshness.get("oldest_source_at"),
            },
            "timeline": {
                "best_name": timeline.get("best_name"),
                "best_points": int(timeline.get("best_points") or 0),
                "best_raw_points": int(
                    timeline.get("best_raw_points")
                    if timeline.get("best_raw_points") is not None
                    else timeline.get("best_points") or 0
                ),
                "best_span_days": int(timeline.get("best_span_days") or 0),
                "preliminary_ready": bool(timeline.get("preliminary_ready")),
                "stable_ready": bool(timeline.get("stable_ready")),
            },
        },
        "actions": {
            "project_route": f"#/research-projects/{project_id}",
            "report_route": f"#/research-projects/{project_id}/report",
            "versions_route": f"#/research-projects/{project_id}/report-versions",
            "tracking_route": "#/tracking",
        },
        "policy": {
            "manual_only": True,
            "background_scheduler": False,
            "automatic_collection": False,
            "automatic_browser_launch": False,
            "automatic_report_freeze": False,
            "automatic_project_status_change": False,
            "cadence_min_days": MIN_CADENCE_DAYS,
            "cadence_max_days": MAX_CADENCE_DAYS,
            "completion_effect": "仅记录本次人工复核指纹并顺延下次复核日。",
        },
        "generated_at": generated_at.isoformat(timespec="seconds"),
    }


def monitoring_filter_matches(plan: dict[str, Any], value: str | None) -> bool:
    """Apply the public queue filter to a summarized plan."""

    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "all":
        return True
    if normalized == "active":
        return plan.get("status") == "active"
    if normalized == "due":
        return bool(plan.get("is_due"))
    if normalized == "paused":
        return plan.get("state") == "paused"
    if normalized == "unplanned":
        return plan.get("state") == "unplanned"
    raise ResearchMonitoringError("观察计划筛选仅支持 active、due、paused 或 unplanned")


def _fetch_project(cursor: Any, project_id: int, *, for_update: bool = False) -> dict[str, Any]:
    cursor.execute(
        f"""
        SELECT
          p.id,
          p.marketplace,
          p.name,
          p.status,
          p.objective,
          p.strategy,
          p.current_decision_report_version_id,
          report.version_no AS current_decision_report_version_no,
          p.created_at,
          p.updated_at
        FROM research_projects p
        LEFT JOIN research_project_report_versions report
          ON report.id = p.current_decision_report_version_id
        WHERE p.id = %s
        {"FOR UPDATE" if for_update else ""}
        """,
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ResearchMonitoringError(f"研究项目不存在：{project_id}", status_code=404)
    return dict(row)


def _fetch_plan(cursor: Any, project_id: int, *, for_update: bool = False) -> dict[str, Any] | None:
    cursor.execute(
        f"""
        SELECT *
        FROM research_project_observation_plans
        WHERE project_id = %s
        {"FOR UPDATE" if for_update else ""}
        """,
        (project_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _serialize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    value = dict(plan)
    for key in ("next_review_on", "last_review_evaluated_on"):
        raw = value.get(key)
        if isinstance(raw, (date, datetime)):
            value[key] = raw.date().isoformat() if isinstance(raw, datetime) else raw.isoformat()
        elif raw is not None:
            value[key] = str(raw)
    for key in ("last_reviewed_at", "created_at", "updated_at"):
        raw = value.get(key)
        if isinstance(raw, datetime):
            value[key] = raw.isoformat(sep=" ", timespec="microseconds")
        elif raw is not None:
            value[key] = str(raw)
    for key in (
        "last_review_evidence_fingerprint",
        "last_review_report_fingerprint",
    ):
        raw = value.get(key)
        value[key] = str(raw).upper() if raw else None
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ResearchMonitoringError(f"{label}必须是正整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchMonitoringError(f"{label}必须是正整数") from exc
    if parsed < 1:
        raise ResearchMonitoringError(f"{label}必须是正整数")
    return parsed


def _bounded_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    parsed = _positive_int(value, label)
    if not minimum <= parsed <= maximum:
        raise ResearchMonitoringError(f"{label}必须在 {minimum}..{maximum} 天之间")
    return parsed


def _plan_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in PLAN_STATUSES:
        raise ResearchMonitoringError("观察计划状态仅支持 active 或 paused")
    return normalized


def _required_date(value: date | str | None, label: str) -> date:
    if value is None or value == "":
        raise ResearchMonitoringError(f"{label}不能为空")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ResearchMonitoringError(f"{label}格式应为 YYYY-MM-DD") from exc


def _optional_text(value: Any, *, maximum: int) -> str | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    if len(text_value) > maximum:
        raise ResearchMonitoringError(f"文本不能超过 {maximum} 个字符")
    return text_value


def _fingerprint(value: Any) -> str:
    text_value = str(value or "").strip().upper()
    if not _HEX64_RE.fullmatch(text_value):
        raise ResearchMonitoringError("报告指纹格式不正确")
    return text_value


def _effective_now(value: datetime | None) -> datetime:
    result = value or datetime.now()
    if result.tzinfo is not None:
        result = result.astimezone().replace(tzinfo=None)
    return result.replace(tzinfo=None)
