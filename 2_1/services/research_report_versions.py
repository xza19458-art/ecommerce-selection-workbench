"""Immutable research-report versions and deterministic structured diffs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from services.research_decision_report import (
    build_research_decision_report_from_cursor,
    parse_report_date,
    render_research_decision_markdown,
)
from services.research_workspace import (
    PROJECT_STATUS_LABELS,
    TERMINAL_STATUSES,
    get_research_project,
    validate_project_transition,
)


VERSION_EXPORT_TYPE = "research_project_report_version"
DIFF_SCHEMA_VERSION = "research-report-diff-v1"
LIVE_DIFF_SCHEMA_VERSION = "research-report-live-baseline-v1"
FREEZE_KINDS = {"manual", "decision"}
_HEX64_RE = re.compile(r"^[0-9A-F]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


class ResearchReportVersionError(ValueError):
    """Raised when an immutable report version request is invalid."""

    status_code = 422
    code = "research_report_version_invalid"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ResearchReportVersionConflict(ResearchReportVersionError):
    """Raised when a freeze request no longer matches current evidence."""

    status_code = 409
    code = "research_report_version_conflict"


class ResearchReportVersionIntegrityError(ResearchReportVersionError):
    """Raised when stored immutable content no longer matches its hash."""

    status_code = 409
    code = "research_report_version_integrity"


def canonical_report_json(report: dict[str, Any]) -> bytes:
    """Serialize one report deterministically for immutable content hashing."""
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest().upper()


def freeze_research_report_version(
    project_id: int,
    *,
    confirmed: bool,
    evaluated_on: date | str,
    expected_report_fingerprint: str,
    idempotency_key: str,
    version_note: str | None = None,
    client: MySQLClient | None = None,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze a user-confirmed, non-terminal report version."""
    return _freeze_version(
        project_id,
        freeze_kind="manual",
        confirmed=confirmed,
        evaluated_on=evaluated_on,
        expected_report_fingerprint=expected_report_fingerprint,
        idempotency_key=idempotency_key,
        version_note=version_note,
        decision_status=None,
        decision_summary=None,
        client=client,
        frozen_at=frozen_at,
    )


def freeze_research_project_decision(
    project_id: int,
    status: str,
    *,
    decision_summary: str | None,
    confirmed: bool,
    evaluated_on: date | str,
    expected_report_fingerprint: str,
    idempotency_key: str,
    version_note: str | None = None,
    client: MySQLClient | None = None,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    """Atomically freeze the evidence basis and move a project to a terminal state."""
    target = str(status or "").strip().lower()
    if target not in TERMINAL_STATUSES:
        raise ResearchReportVersionError("决策报告版本只绑定批准或拒绝状态")
    summary = _required_text(decision_summary, "人工结论", minimum=10, maximum=10_000)
    result = _freeze_version(
        project_id,
        freeze_kind="decision",
        confirmed=confirmed,
        evaluated_on=evaluated_on,
        expected_report_fingerprint=expected_report_fingerprint,
        idempotency_key=idempotency_key,
        version_note=version_note,
        decision_status=target,
        decision_summary=summary,
        client=client,
        frozen_at=frozen_at,
    )
    db = client or MySQLClient()
    bundle = get_research_project(_positive_int(project_id, "研究项目 ID"), client=db)
    bundle["frozen_version"] = result["version"]
    bundle["idempotent_replay"] = result["idempotent_replay"]
    return bundle


def list_research_report_versions(
    project_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    project_id_value = _positive_int(project_id, "研究项目 ID")
    limit_value = _bounded_int(limit, "每页数量", minimum=1, maximum=100)
    offset_value = _bounded_int(offset, "偏移量", minimum=0, maximum=1_000_000)
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            _require_project_exists(cursor, project_id_value)
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM research_project_report_versions
                WHERE project_id = %s
                """,
                (project_id_value,),
            )
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                """
                SELECT
                  id,
                  project_id,
                  version_no,
                  freeze_kind,
                  source_project_status,
                  decision_status,
                  decision_summary,
                  evaluated_on,
                  schema_version,
                  method_version,
                  evidence_as_of,
                  evidence_fingerprint,
                  report_fingerprint,
                  report_json_sha256,
                  report_markdown_sha256,
                  version_note,
                  frozen_at,
                  JSON_UNQUOTE(JSON_EXTRACT(report_json, '$.readiness.level')) AS readiness_level,
                  JSON_UNQUOTE(JSON_EXTRACT(report_json, '$.readiness.label')) AS readiness_label,
                  CAST(JSON_UNQUOTE(JSON_EXTRACT(report_json, '$.readiness.passed_count')) AS UNSIGNED)
                    AS readiness_passed_count,
                  CAST(JSON_UNQUOTE(JSON_EXTRACT(report_json, '$.readiness.total_count')) AS UNSIGNED)
                    AS readiness_total_count,
                  CAST(JSON_UNQUOTE(JSON_EXTRACT(report_json, '$.readiness.blocking_count')) AS UNSIGNED)
                    AS readiness_blocking_count
                FROM research_project_report_versions
                WHERE project_id = %s
                ORDER BY version_no DESC
                LIMIT %s OFFSET %s
                """,
                (project_id_value, limit_value, offset_value),
            )
            rows = [_version_summary(row) for row in cursor.fetchall()]
    return {
        "rows": rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "project_id": project_id_value,
    }


def get_research_report_version(
    project_id: int,
    version_no: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    project_id_value = _positive_int(project_id, "研究项目 ID")
    version_no_value = _positive_int(version_no, "报告版本号")
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            row = _fetch_version_row(cursor, project_id_value, version_no=version_no_value)
    return _version_detail(row)


def export_research_report_version(
    project_id: int,
    version_no: int,
    export_format: str,
    *,
    client: MySQLClient | None = None,
) -> tuple[bytes, str, str, dict[str, Any]]:
    detail = get_research_report_version(project_id, version_no, client=client)
    version = detail["version"]
    fmt = str(export_format or "").strip().lower()
    stem = (
        f"research_project_{int(version['project_id'])}"
        f"_v{int(version['version_no'])}"
        f"_{str(version['evaluated_on']).replace('-', '')}"
        f"_{str(version['report_fingerprint'])[:12]}"
    )
    if fmt == "json":
        envelope = {
            "artifact_type": VERSION_EXPORT_TYPE,
            "version": version,
            "report": detail["report"],
        }
        content = json.dumps(
            envelope,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        return content, f"{stem}.json", "application/json; charset=utf-8", detail
    if fmt in {"markdown", "md"}:
        content = detail["_report_markdown"].encode("utf-8")
        return content, f"{stem}.md", "text/markdown; charset=utf-8", detail
    raise ResearchReportVersionError("导出格式仅支持 json 或 markdown")


def compare_research_report_versions(
    project_id: int,
    from_version: int,
    to_version: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    first = get_research_report_version(project_id, from_version, client=client)
    second = get_research_report_version(project_id, to_version, client=client)
    return build_research_report_diff(first, second)


def compare_current_research_report_to_baseline(
    project_id: int,
    *,
    from_version: int | None = None,
    as_of: date | str | None = None,
    client: MySQLClient | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Compare current evidence with an explicit or policy-selected frozen baseline."""
    project_id_value = _positive_int(project_id, "研究项目 ID")
    from_version_value = (
        _positive_int(from_version, "基准报告版本号")
        if from_version is not None
        else None
    )
    evaluated_on = parse_report_date(as_of)
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  status,
                  current_decision_report_version_id
                FROM research_projects
                WHERE id = %s
                LIMIT 1
                """,
                (project_id_value,),
            )
            project = cursor.fetchone()
            if not project:
                raise ResearchReportVersionError(f"研究项目不存在：{project_id_value}")

            report = build_research_decision_report_from_cursor(
                cursor,
                project_id_value,
                evaluated_on=evaluated_on,
                generated_at=generated_at,
            )
            project_status = str(project.get("status") or "")
            baseline_row: dict[str, Any] | None = None
            baseline_kind = "none"
            baseline_label = "尚无冻结版本"

            if from_version_value is not None:
                baseline_row = _fetch_version_row(
                    cursor,
                    project_id_value,
                    version_no=from_version_value,
                )
                baseline_kind = "explicit"
                baseline_label = "指定冻结版本"
            elif project_status in TERMINAL_STATUSES:
                current_version_id = int(
                    project.get("current_decision_report_version_id") or 0
                )
                if current_version_id:
                    baseline_row = _fetch_version_row(
                        cursor,
                        project_id_value,
                        version_id=current_version_id,
                    )
                    baseline_kind = "terminal_decision"
                    baseline_label = "终态决策版本"
                else:
                    baseline_kind = "terminal_missing"
                    baseline_label = "终态未绑定决策版本"
            else:
                cursor.execute(
                    """
                    SELECT version_no
                    FROM research_project_report_versions
                    WHERE project_id = %s
                    ORDER BY version_no DESC
                    LIMIT 1
                    """,
                    (project_id_value,),
                )
                latest = cursor.fetchone()
                if latest:
                    baseline_row = _fetch_version_row(
                        cursor,
                        project_id_value,
                        version_no=int(latest["version_no"]),
                    )
                    baseline_kind = "latest_frozen"
                    baseline_label = "最近冻结版本"

    current = _dynamic_report_detail(report, project_status=project_status)
    baseline = _version_detail(baseline_row) if baseline_row else None
    diff = build_research_report_diff(baseline, current) if baseline else None
    return {
        "comparison_type": LIVE_DIFF_SCHEMA_VERSION,
        "project_id": project_id_value,
        "has_baseline": baseline is not None,
        "baseline_kind": baseline_kind,
        "baseline_label": baseline_label,
        "baseline": baseline["version"] if baseline else None,
        "current": current["version"],
        "current_report": report,
        "diff": diff,
        "message": (
            "已按冻结基线比较当前动态报告。"
            if baseline
            else "当前没有可比较的冻结基线；动态报告仍可正常查看。"
        ),
    }


def build_research_report_diff(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    """Compare immutable reports by stable business identities, never by Markdown text."""
    before_version = dict(first.get("version") or {})
    after_version = dict(second.get("version") or {})
    before = dict(first.get("report") or {})
    after = dict(second.get("report") or {})
    if not before_version or not after_version or not before or not after:
        raise ResearchReportVersionError("报告版本数据不完整，无法比较")
    if int(before_version.get("project_id") or 0) != int(after_version.get("project_id") or 0):
        raise ResearchReportVersionError("只能比较同一研究项目的报告版本")

    changes: list[dict[str, Any]] = []
    method_compatible = (
        before.get("schema_version") == after.get("schema_version")
        and before.get("method_version") == after.get("method_version")
    )
    evidence_same = before.get("evidence_fingerprint") == after.get("evidence_fingerprint")
    report_same = before.get("report_fingerprint") == after.get("report_fingerprint")
    decision_same = all(
        before_version.get(key) == after_version.get(key)
        for key in ("decision_status", "decision_summary")
    )

    _append_field_changes(
        changes,
        "版本元数据",
        before_version,
        after_version,
        (
            ("freeze_kind", "冻结类型"),
            ("source_project_status", "冻结前阶段"),
            ("decision_status", "决策结果"),
            ("decision_summary", "人工结论"),
            ("evaluated_on", "评估日期"),
            ("version_note", "版本说明"),
        ),
    )
    _append_field_changes(
        changes,
        "方法身份",
        before,
        after,
        (
            ("schema_version", "报告结构版本"),
            ("method_version", "报告方法版本"),
            ("evidence_fingerprint", "证据指纹"),
            ("report_fingerprint", "报告指纹"),
            ("evidence_as_of", "证据截至"),
        ),
    )
    _append_field_changes(
        changes,
        "决策就绪度",
        before.get("readiness") or {},
        after.get("readiness") or {},
        (
            ("level", "就绪级别"),
            ("passed_count", "门禁通过数"),
            ("blocking_count", "阻断数"),
            ("summary", "就绪说明"),
        ),
    )

    _append_keyed_changes(
        changes,
        "决策门禁",
        (before.get("readiness") or {}).get("gates") or [],
        (after.get("readiness") or {}).get("gates") or [],
        key_name="key",
        label_name="label",
        fields=("passed", "severity", "critical_for_final_decision", "detail", "action"),
    )
    _append_axis_changes(
        changes,
        before.get("decision_axes") or [],
        after.get("decision_axes") or [],
    )
    _append_keyed_changes(
        changes,
        "数据缺口",
        before.get("data_gaps") or [],
        after.get("data_gaps") or [],
        key_name="key",
        label_name="title",
        fields=("severity", "reason", "next_action", "route", "requires_user_action"),
    )
    _append_asset_changes(changes, before.get("assets") or {}, after.get("assets") or {})
    _append_source_changes(
        changes,
        before.get("source_manifest") or {},
        after.get("source_manifest") or {},
    )
    _append_human_text_changes(changes, before, after)

    if report_same and decision_same:
        comparison_scope = "no_material_change"
        scope_label = "无实质变化"
    elif report_same:
        comparison_scope = "decision_changed"
        scope_label = "证据未变，人工决策已变化"
    elif not method_compatible:
        comparison_scope = "method_changed"
        scope_label = "报告口径已变化"
    elif evidence_same and before.get("evaluated_on") != after.get("evaluated_on"):
        comparison_scope = "time_passage"
        scope_label = "仅评估时间推进"
    else:
        comparison_scope = "evidence_changed"
        scope_label = "证据或人工判断已变化"

    category_counts: dict[str, int] = {}
    for change in changes:
        category = str(change.get("category") or "其他")
        category_counts[category] = category_counts.get(category, 0) + 1
    material_count = sum(
        count
        for category, count in category_counts.items()
        if category not in {"版本元数据", "方法身份"}
    )
    return {
        "diff_type": DIFF_SCHEMA_VERSION,
        "project_id": int(before_version["project_id"]),
        "from_version": before_version,
        "to_version": after_version,
        "comparison_scope": comparison_scope,
        "comparison_scope_label": scope_label,
        "method_compatible": method_compatible,
        "business_interpretation_allowed": method_compatible,
        "evidence_fingerprint_changed": not evidence_same,
        "report_fingerprint_changed": not report_same,
        "summary": {
            "change_count": len(changes),
            "material_change_count": material_count,
            "category_counts": category_counts,
        },
        "changes": changes,
        "warning": (
            ""
            if method_compatible
            else "两份报告的方法或结构版本不同；数值只作原值/新值对照，不能直接解释为市场变化。"
        ),
    }


def _freeze_version(
    project_id: int,
    *,
    freeze_kind: str,
    confirmed: bool,
    evaluated_on: date | str,
    expected_report_fingerprint: str,
    idempotency_key: str,
    version_note: str | None,
    decision_status: str | None,
    decision_summary: str | None,
    client: MySQLClient | None,
    frozen_at: datetime | None,
) -> dict[str, Any]:
    if not confirmed:
        raise ResearchReportVersionError("冻结报告前必须由用户明确确认")
    if freeze_kind not in FREEZE_KINDS:
        raise ResearchReportVersionError("不支持的报告冻结类型")
    project_id_value = _positive_int(project_id, "研究项目 ID")
    if evaluated_on is None or not str(evaluated_on).strip():
        raise ResearchReportVersionError("冻结报告必须指定评估日期")
    evaluated_date = parse_report_date(evaluated_on)
    expected_fingerprint = _fingerprint_value(expected_report_fingerprint)
    idempotency_value = _idempotency_value(idempotency_key)
    note = _optional_text(version_note, maximum=2000)
    now = _aware_datetime(frozen_at)
    db_timestamp = now.replace(tzinfo=None)
    db = client or MySQLClient()

    with db.connect() as conn:
        with conn.cursor() as cursor:
            replay = _find_version_by_idempotency(
                cursor,
                project_id_value,
                idempotency_value,
            )
            if replay:
                _assert_idempotent_match(
                    replay,
                    freeze_kind=freeze_kind,
                    evaluated_on=evaluated_date,
                    expected_report_fingerprint=expected_fingerprint,
                    version_note=note,
                    decision_status=decision_status,
                    decision_summary=decision_summary,
                )
                return {
                    "version": _version_summary(replay),
                    "idempotent_replay": True,
                }

            cursor.execute(
                """
                SELECT
                  id,
                  marketplace,
                  status,
                  current_decision_report_version_id
                FROM research_projects
                WHERE id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (project_id_value,),
            )
            project = cursor.fetchone()
            if not project:
                raise ResearchReportVersionError(f"研究项目不存在：{project_id_value}")

            replay = _find_version_by_idempotency(
                cursor,
                project_id_value,
                idempotency_value,
            )
            if replay:
                _assert_idempotent_match(
                    replay,
                    freeze_kind=freeze_kind,
                    evaluated_on=evaluated_date,
                    expected_report_fingerprint=expected_fingerprint,
                    version_note=note,
                    decision_status=decision_status,
                    decision_summary=decision_summary,
                )
                return {
                    "version": _version_summary(replay),
                    "idempotent_replay": True,
                }

            source_status = str(project.get("status") or "")
            if freeze_kind == "decision":
                validate_project_transition(source_status, str(decision_status or ""))

            report = build_research_decision_report_from_cursor(
                cursor,
                project_id_value,
                evaluated_on=evaluated_date,
                generated_at=now,
            )
            current_fingerprint = _fingerprint_value(report.get("report_fingerprint"))
            if current_fingerprint != expected_fingerprint:
                raise ResearchReportVersionConflict(
                    "当前证据已变化，请刷新决策报告后重新确认。",
                    details={
                        "expected_report_fingerprint": expected_fingerprint,
                        "current_report_fingerprint": current_fingerprint,
                        "evidence_fingerprint": report.get("evidence_fingerprint"),
                    },
                )

            cursor.execute(
                """
                SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version
                FROM research_project_report_versions
                WHERE project_id = %s
                """,
                (project_id_value,),
            )
            version_no = int((cursor.fetchone() or {}).get("next_version") or 1)
            report_json_bytes = canonical_report_json(report)
            metadata = {
                "project_id": project_id_value,
                "version_no": version_no,
                "freeze_kind": freeze_kind,
                "source_project_status": source_status,
                "decision_status": decision_status,
                "decision_summary": decision_summary,
                "evaluated_on": evaluated_date.isoformat(),
                "schema_version": report.get("schema_version"),
                "method_version": report.get("method_version"),
                "evidence_as_of": report.get("evidence_as_of"),
                "evidence_fingerprint": report.get("evidence_fingerprint"),
                "report_fingerprint": current_fingerprint,
                "version_note": note,
                "frozen_at": now.isoformat(timespec="microseconds"),
            }
            markdown = render_frozen_report_markdown(report, metadata)
            markdown_bytes = markdown.encode("utf-8")
            cursor.execute(
                """
                INSERT INTO research_project_report_versions (
                  project_id,
                  version_no,
                  freeze_kind,
                  source_project_status,
                  decision_status,
                  decision_summary,
                  evaluated_on,
                  schema_version,
                  method_version,
                  evidence_as_of,
                  evidence_fingerprint,
                  report_fingerprint,
                  report_json,
                  report_json_sha256,
                  report_markdown,
                  report_markdown_sha256,
                  version_note,
                  idempotency_key,
                  frozen_at
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    project_id_value,
                    version_no,
                    freeze_kind,
                    source_status,
                    decision_status,
                    decision_summary,
                    evaluated_date,
                    report.get("schema_version"),
                    report.get("method_version"),
                    report.get("evidence_as_of"),
                    report.get("evidence_fingerprint"),
                    current_fingerprint,
                    report_json_bytes.decode("utf-8"),
                    content_sha256(report_json_bytes),
                    markdown,
                    content_sha256(markdown_bytes),
                    note,
                    idempotency_value,
                    db_timestamp,
                ),
            )
            version_id = int(cursor.lastrowid)
            if freeze_kind == "decision":
                cursor.execute(
                    """
                    UPDATE research_projects
                    SET status = %s,
                        decision_summary = %s,
                        decided_at = %s,
                        status_changed_at = %s,
                        current_decision_report_version_id = %s
                    WHERE id = %s
                    """,
                    (
                        decision_status,
                        decision_summary,
                        db_timestamp,
                        db_timestamp,
                        version_id,
                        project_id_value,
                    ),
                )
            row = _fetch_version_row(cursor, project_id_value, version_id=version_id)
    return {"version": _version_summary(row), "idempotent_replay": False}


def render_frozen_report_markdown(
    report: dict[str, Any],
    version: dict[str, Any],
) -> str:
    decision_status = str(version.get("decision_status") or "")
    decision_label = PROJECT_STATUS_LABELS.get(decision_status, decision_status or "--")
    kind_label = "决策版本" if version.get("freeze_kind") == "decision" else "普通冻结版本"
    lines = [
        f"# 决策报告冻结版本 #{version.get('version_no')}",
        "",
        f"- 版本类型：{kind_label}",
        f"- 冻结前阶段：{PROJECT_STATUS_LABELS.get(str(version.get('source_project_status') or ''), version.get('source_project_status') or '--')}",
        f"- 决策结果：{decision_label if decision_status else '--'}",
        f"- 评估日期：{version.get('evaluated_on') or '--'}",
        f"- 冻结时间：{version.get('frozen_at') or '--'}",
        f"- 方法版本：`{version.get('method_version') or '--'}`",
        f"- 证据指纹：`{version.get('evidence_fingerprint') or '--'}`",
        f"- 报告指纹：`{version.get('report_fingerprint') or '--'}`",
    ]
    if version.get("decision_summary"):
        lines.extend(["", "## 人工决策结论", "", str(version["decision_summary"])])
    if version.get("version_note"):
        lines.extend(["", "## 版本说明", "", str(version["version_note"])])
    lines.extend(["", "---", "", render_research_decision_markdown(report).rstrip(), ""])
    return "\n".join(lines)


def _fetch_version_row(
    cursor: Any,
    project_id: int,
    *,
    version_no: int | None = None,
    version_id: int | None = None,
) -> dict[str, Any]:
    if version_no is None and version_id is None:
        raise ResearchReportVersionError("缺少报告版本标识")
    where = "version_no = %s" if version_no is not None else "id = %s"
    value = version_no if version_no is not None else version_id
    cursor.execute(
        f"""
        SELECT
          id,
          project_id,
          version_no,
          freeze_kind,
          source_project_status,
          decision_status,
          decision_summary,
          evaluated_on,
          schema_version,
          method_version,
          evidence_as_of,
          evidence_fingerprint,
          report_fingerprint,
          report_json,
          report_json_sha256,
          report_markdown,
          report_markdown_sha256,
          version_note,
          idempotency_key,
          frozen_at
        FROM research_project_report_versions
        WHERE project_id = %s AND {where}
        LIMIT 1
        """,
        (project_id, value),
    )
    row = cursor.fetchone()
    if not row:
        raise ResearchReportVersionError(f"报告版本不存在：{value}")
    return row


def _find_version_by_idempotency(
    cursor: Any,
    project_id: int,
    idempotency_key: str,
) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT
          id,
          project_id,
          version_no,
          freeze_kind,
          source_project_status,
          decision_status,
          decision_summary,
          evaluated_on,
          schema_version,
          method_version,
          evidence_as_of,
          evidence_fingerprint,
          report_fingerprint,
          report_json,
          report_json_sha256,
          report_markdown,
          report_markdown_sha256,
          version_note,
          idempotency_key,
          frozen_at
        FROM research_project_report_versions
        WHERE project_id = %s AND idempotency_key = %s
        LIMIT 1
        """,
        (project_id, idempotency_key),
    )
    return cursor.fetchone()


def _assert_idempotent_match(
    row: dict[str, Any],
    *,
    freeze_kind: str,
    evaluated_on: date,
    expected_report_fingerprint: str,
    version_note: str | None,
    decision_status: str | None,
    decision_summary: str | None,
) -> None:
    actual = {
        "freeze_kind": str(row.get("freeze_kind") or ""),
        "evaluated_on": _date_text(row.get("evaluated_on")),
        "report_fingerprint": str(row.get("report_fingerprint") or "").upper(),
        "version_note": _none_or_text(row.get("version_note")),
        "decision_status": _none_or_text(row.get("decision_status")),
        "decision_summary": _none_or_text(row.get("decision_summary")),
    }
    expected = {
        "freeze_kind": freeze_kind,
        "evaluated_on": evaluated_on.isoformat(),
        "report_fingerprint": expected_report_fingerprint,
        "version_note": version_note,
        "decision_status": decision_status,
        "decision_summary": decision_summary,
    }
    if actual != expected:
        raise ResearchReportVersionConflict(
            "该幂等键已用于另一项报告冻结请求，请刷新页面后重试。",
            details={"existing_version_no": int(row.get("version_no") or 0)},
        )


def _version_detail(row: dict[str, Any]) -> dict[str, Any]:
    report = _decode_report(row.get("report_json"))
    expected_json_hash = str(row.get("report_json_sha256") or "").upper()
    actual_json_hash = content_sha256(canonical_report_json(report))
    markdown = str(row.get("report_markdown") or "")
    expected_markdown_hash = str(row.get("report_markdown_sha256") or "").upper()
    actual_markdown_hash = content_sha256(markdown.encode("utf-8"))
    if actual_json_hash != expected_json_hash or actual_markdown_hash != expected_markdown_hash:
        raise ResearchReportVersionIntegrityError(
            "历史报告版本完整性校验失败，已停止展示。",
            details={
                "version_no": int(row.get("version_no") or 0),
                "report_json_valid": actual_json_hash == expected_json_hash,
                "report_markdown_valid": actual_markdown_hash == expected_markdown_hash,
            },
        )
    return {
        "version": _version_summary(row),
        "report": report,
        "integrity": {
            "valid": True,
            "report_json_sha256": actual_json_hash,
            "report_markdown_sha256": actual_markdown_hash,
        },
        "_report_markdown": markdown,
    }


def _dynamic_report_detail(
    report: dict[str, Any],
    *,
    project_status: str,
) -> dict[str, Any]:
    project = dict(report.get("project") or {})
    readiness = dict(report.get("readiness") or {})
    project_id = _positive_int(project.get("id"), "研究项目 ID")
    status = str(project_status or project.get("status") or "")
    return {
        "version": {
            "id": None,
            "project_id": project_id,
            "version_no": None,
            "freeze_kind": "dynamic",
            "source_project_status": status,
            "decision_status": status if status in TERMINAL_STATUSES else None,
            "decision_summary": project.get("decision_summary"),
            "evaluated_on": report.get("evaluated_on"),
            "schema_version": report.get("schema_version"),
            "method_version": report.get("method_version"),
            "evidence_as_of": report.get("evidence_as_of"),
            "evidence_fingerprint": report.get("evidence_fingerprint"),
            "report_fingerprint": report.get("report_fingerprint"),
            "report_json_sha256": None,
            "report_markdown_sha256": None,
            "version_note": None,
            "frozen_at": None,
            "readiness_level": readiness.get("level"),
            "readiness_label": readiness.get("label"),
            "readiness_passed_count": readiness.get("passed_count"),
            "readiness_total_count": readiness.get("total_count"),
            "readiness_blocking_count": readiness.get("blocking_count"),
        },
        "report": report,
    }


def _version_summary(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: _json_value(row.get(key))
        for key in (
            "id",
            "project_id",
            "version_no",
            "freeze_kind",
            "source_project_status",
            "decision_status",
            "decision_summary",
            "evaluated_on",
            "schema_version",
            "method_version",
            "evidence_as_of",
            "evidence_fingerprint",
            "report_fingerprint",
            "report_json_sha256",
            "report_markdown_sha256",
            "version_note",
            "frozen_at",
            "readiness_level",
            "readiness_label",
            "readiness_passed_count",
            "readiness_total_count",
            "readiness_blocking_count",
        )
    }
    if row.get("report_json") is not None and result.get("readiness_level") is None:
        readiness = (_decode_report(row.get("report_json")).get("readiness") or {})
        result["readiness_level"] = readiness.get("level")
        result["readiness_label"] = readiness.get("label")
        result["readiness_passed_count"] = readiness.get("passed_count")
        result["readiness_total_count"] = readiness.get("total_count")
        result["readiness_blocking_count"] = readiness.get("blocking_count")
    for key in (
        "id",
        "project_id",
        "version_no",
        "readiness_passed_count",
        "readiness_total_count",
        "readiness_blocking_count",
    ):
        if result.get(key) is not None:
            result[key] = int(result[key])
    return result


def _append_field_changes(
    changes: list[dict[str, Any]],
    category: str,
    before: dict[str, Any],
    after: dict[str, Any],
    fields: Iterable[tuple[str, str]],
    *,
    item_key: str = "",
    item_label: str = "",
) -> None:
    for field, label in fields:
        old = before.get(field)
        new = after.get(field)
        if old == new:
            continue
        change = {
            "category": category,
            "item_key": item_key,
            "item_label": item_label,
            "field": field,
            "label": label,
            "change_type": "changed",
            "from": _json_value(old),
            "to": _json_value(new),
        }
        delta = _numeric_delta(old, new)
        if delta is not None:
            change["delta"] = delta
        changes.append(change)


def _append_keyed_changes(
    changes: list[dict[str, Any]],
    category: str,
    before_rows: Iterable[dict[str, Any]],
    after_rows: Iterable[dict[str, Any]],
    *,
    key_name: str,
    label_name: str,
    fields: Iterable[str],
) -> None:
    old_map = {str(row.get(key_name)): row for row in before_rows if row.get(key_name) is not None}
    new_map = {str(row.get(key_name)): row for row in after_rows if row.get(key_name) is not None}
    for key in sorted(old_map.keys() - new_map.keys()):
        row = old_map[key]
        changes.append(
            _collection_change(category, key, str(row.get(label_name) or key), "removed", row, None)
        )
    for key in sorted(new_map.keys() - old_map.keys()):
        row = new_map[key]
        changes.append(
            _collection_change(category, key, str(row.get(label_name) or key), "added", None, row)
        )
    field_pairs = tuple((field, field) for field in fields)
    for key in sorted(old_map.keys() & new_map.keys()):
        label = str(new_map[key].get(label_name) or old_map[key].get(label_name) or key)
        _append_field_changes(
            changes,
            category,
            old_map[key],
            new_map[key],
            field_pairs,
            item_key=key,
            item_label=label,
        )


def _append_axis_changes(
    changes: list[dict[str, Any]],
    before_rows: Iterable[dict[str, Any]],
    after_rows: Iterable[dict[str, Any]],
) -> None:
    old_map = {str(row.get("key")): row for row in before_rows if row.get("key")}
    new_map = {str(row.get("key")): row for row in after_rows if row.get("key")}
    for key in sorted(old_map.keys() | new_map.keys()):
        old = old_map.get(key)
        new = new_map.get(key)
        label = str((new or old or {}).get("label") or key)
        if old is None or new is None:
            changes.append(
                _collection_change(
                    "判断轴线",
                    key,
                    label,
                    "added" if old is None else "removed",
                    old,
                    new,
                )
            )
            continue
        _append_field_changes(
            changes,
            "判断轴线",
            old,
            new,
            (
                ("status", "状态"),
                ("confidence", "置信度"),
                ("summary", "判断说明"),
                ("caveats", "限制条件"),
            ),
            item_key=key,
            item_label=label,
        )
        _append_keyed_changes(
            changes,
            f"轴线事实 · {label}",
            old.get("facts") or [],
            new.get("facts") or [],
            key_name="key",
            label_name="label",
            fields=("value", "display", "source_type", "source_id", "source_at"),
        )


def _append_asset_changes(
    changes: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    specs = (
        (
            "资产 · 商品",
            "products",
            "asin",
            "title",
            (
                "role",
                "price",
                "rating",
                "review_count",
                "monthly_bought",
                "latest_snapshot_at",
                "timepoint_count",
                "evidence_status",
                "has_complete_unit_cost",
            ),
        ),
        (
            "资产 · 关键词",
            "keywords",
            "keyword_id",
            "keyword",
            (
                "role",
                "latest_batch_product_count",
                "timepoint_count",
                "latest_snapshot_at",
                "tracking_status",
            ),
        ),
        (
            "资产 · 利基",
            "niches",
            "niche_id",
            "name",
            (
                "role",
                "snapshot_id",
                "snapshot_at",
                "evidence_hash",
                "rank_source_alignment",
                "observed_product_count",
            ),
        ),
        (
            "资产 · 人工记录",
            "notes",
            "id",
            "note_type",
            ("note_type", "content", "updated_at"),
        ),
    )
    for category, collection, key, label, fields in specs:
        _append_keyed_changes(
            changes,
            category,
            before.get(collection) or [],
            after.get(collection) or [],
            key_name=key,
            label_name=label,
            fields=fields,
        )


def _append_source_changes(
    changes: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    specs = (
        ("products", "product_id", "asin"),
        ("keywords", "keyword_id", "keyword"),
        ("niches", "niche_id", "name"),
        ("notes", "id", "note_type"),
    )
    for collection, key, label in specs:
        _append_keyed_changes(
            changes,
            f"来源清单 · {collection}",
            before.get(collection) or [],
            after.get(collection) or [],
            key_name=key,
            label_name=label,
            fields=tuple(
                sorted(
                    {
                        item_key
                        for row in [
                            *(before.get(collection) or []),
                            *(after.get(collection) or []),
                        ]
                        for item_key in row
                        if item_key not in {key, label}
                    }
                )
            ),
        )


def _append_human_text_changes(
    changes: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    _append_field_changes(
        changes,
        "人工文本",
        before.get("project") or {},
        after.get("project") or {},
        (
            ("objective", "研究目标"),
            ("strategy", "研究策略"),
            ("decision_summary", "阶段/终态结论"),
        ),
        item_key="project",
        item_label="研究项目",
    )


def _collection_change(
    category: str,
    key: str,
    label: str,
    change_type: str,
    before: Any,
    after: Any,
) -> dict[str, Any]:
    return {
        "category": category,
        "item_key": key,
        "item_label": label,
        "field": "",
        "label": label,
        "change_type": change_type,
        "from": _json_value(before),
        "to": _json_value(after),
    }


def _numeric_delta(before: Any, after: Any) -> float | int | None:
    if isinstance(before, bool) or isinstance(after, bool):
        return None
    if not isinstance(before, (int, float, Decimal)) or not isinstance(after, (int, float, Decimal)):
        return None
    delta = float(after) - float(before)
    return int(delta) if delta.is_integer() else round(delta, 6)


def _decode_report(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        report = value
    else:
        try:
            report = json.loads(str(value or ""))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResearchReportVersionIntegrityError("历史报告 JSON 无法解析") from exc
    if not isinstance(report, dict):
        raise ResearchReportVersionIntegrityError("历史报告 JSON 结构不正确")
    return report


def _require_project_exists(cursor: Any, project_id: int) -> None:
    cursor.execute("SELECT id FROM research_projects WHERE id = %s LIMIT 1", (project_id,))
    if not cursor.fetchone():
        raise ResearchReportVersionError(f"研究项目不存在：{project_id}")


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchReportVersionError(f"{label} 必须是正整数") from exc
    if number < 1:
        raise ResearchReportVersionError(f"{label} 必须是正整数")
    return number


def _bounded_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchReportVersionError(f"{label} 格式不正确") from exc
    if number < minimum or number > maximum:
        raise ResearchReportVersionError(f"{label} 必须在 {minimum} 到 {maximum} 之间")
    return number


def _required_text(
    value: Any,
    label: str,
    *,
    minimum: int = 1,
    maximum: int,
) -> str:
    text = str(value or "").strip()
    if len(text) < minimum:
        raise ResearchReportVersionError(f"{label} 不少于 {minimum} 个字符")
    if len(text) > maximum:
        raise ResearchReportVersionError(f"{label} 不能超过 {maximum} 个字符")
    return text


def _optional_text(value: Any, *, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise ResearchReportVersionError(f"版本说明不能超过 {maximum} 个字符")
    return text


def _fingerprint_value(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not _HEX64_RE.fullmatch(text):
        raise ResearchReportVersionError("报告指纹格式不正确")
    return text


def _idempotency_value(value: Any) -> str:
    text = str(value or "").strip()
    if not _IDEMPOTENCY_RE.fullmatch(text):
        raise ResearchReportVersionError("幂等键格式不正确")
    return text


def _aware_datetime(value: datetime | None) -> datetime:
    result = value or datetime.now().astimezone()
    if result.tzinfo is None:
        result = result.astimezone()
    return result


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value or "")


def _none_or_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
