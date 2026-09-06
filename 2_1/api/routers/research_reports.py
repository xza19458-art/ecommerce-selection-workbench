"""Research-project decision report and deterministic export routes."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.contracts import ok
from repositories.research_reports import ResearchReportRepository


router = APIRouter(prefix="/api/research-projects", tags=["research-reports"])
_repository = ResearchReportRepository()


class ResearchReportFreezeIn(BaseModel):
    confirmed: bool = False
    evaluated_on: str
    expected_report_fingerprint: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=64)
    version_note: str | None = Field(default=None, max_length=2000)


def configure_repository(repository: ResearchReportRepository) -> None:
    global _repository
    _repository = repository


@router.get("/{project_id}/detail-readiness")
def research_project_detail_readiness(
    project_id: int,
    stale_days: int = 30,
    file_limit: int = 100,
) -> dict[str, Any]:
    return ok(
        _repository.get_detail_readiness(
            project_id,
            stale_days=stale_days,
            file_limit=file_limit,
        )
    )


@router.get("/{project_id}/decision-report")
def research_decision_report(
    project_id: int,
    as_of: str | None = None,
) -> dict[str, Any]:
    return ok(_repository.get_report(project_id, as_of=as_of))


@router.get("/{project_id}/decision-report/export")
def research_decision_report_export(
    project_id: int,
    format: Literal["json", "markdown"] = "json",
    as_of: str | None = None,
) -> Response:
    content, filename, media_type, report = _repository.export_report(
        project_id,
        format,
        as_of=as_of,
    )
    encoded = quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename={filename}; filename*=UTF-8''{encoded}",
            "X-Evidence-Fingerprint": str(report.get("evidence_fingerprint") or ""),
            "X-Report-Fingerprint": str(report.get("report_fingerprint") or ""),
            "Cache-Control": "no-store",
        },
    )


@router.post("/{project_id}/report-versions")
def research_report_version_freeze(
    project_id: int,
    body: ResearchReportFreezeIn,
) -> dict[str, Any]:
    return ok(
        _repository.freeze_version(
            project_id,
            confirmed=body.confirmed,
            evaluated_on=body.evaluated_on,
            expected_report_fingerprint=body.expected_report_fingerprint,
            idempotency_key=body.idempotency_key,
            version_note=body.version_note,
        )
    )


@router.get("/{project_id}/report-versions")
def research_report_versions(
    project_id: int,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return ok(
        _repository.list_versions(
            project_id,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/{project_id}/report-version-diff")
def research_report_version_diff(
    project_id: int,
    from_version: int,
    to_version: int,
) -> dict[str, Any]:
    return ok(
        _repository.compare_versions(
            project_id,
            from_version,
            to_version,
        )
    )


@router.get("/{project_id}/report-baseline-diff")
def research_report_baseline_diff(
    project_id: int,
    from_version: int | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    return ok(
        _repository.compare_current_to_baseline(
            project_id,
            from_version=from_version,
            as_of=as_of,
        )
    )


@router.get("/{project_id}/report-versions/{version_no}")
def research_report_version(
    project_id: int,
    version_no: int,
) -> dict[str, Any]:
    return ok(_repository.get_version(project_id, version_no))


@router.get("/{project_id}/report-versions/{version_no}/export")
def research_report_version_export(
    project_id: int,
    version_no: int,
    format: Literal["json", "markdown"] = "json",
) -> Response:
    content, filename, media_type, detail = _repository.export_version(
        project_id,
        version_no,
        format,
    )
    version = detail["version"]
    encoded = quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename={filename}; filename*=UTF-8''{encoded}",
            "X-Evidence-Fingerprint": str(version.get("evidence_fingerprint") or ""),
            "X-Report-Fingerprint": str(version.get("report_fingerprint") or ""),
            "X-Report-Version": str(version.get("version_no") or ""),
            "Cache-Control": "no-store",
        },
    )
