"""Read-only repository for research decision reports and exports."""

from __future__ import annotations

from datetime import date
from typing import Any

from services.research_decision_report import (
    build_research_decision_report,
    export_research_decision_report,
)
from services.research_report_versions import (
    compare_current_research_report_to_baseline,
    compare_research_report_versions,
    export_research_report_version,
    freeze_research_report_version,
    get_research_report_version,
    list_research_report_versions,
)
from services.research_detail_readiness import get_research_project_detail_readiness


class ResearchReportRepository:
    def get_detail_readiness(
        self,
        project_id: int,
        *,
        stale_days: int = 30,
        file_limit: int = 100,
    ) -> dict[str, Any]:
        return get_research_project_detail_readiness(
            project_id,
            stale_days=stale_days,
            file_limit=file_limit,
        )

    def get_report(self, project_id: int, *, as_of: date | str | None = None) -> dict[str, Any]:
        return build_research_decision_report(project_id, as_of=as_of)

    def export_report(
        self,
        project_id: int,
        export_format: str,
        *,
        as_of: date | str | None = None,
    ) -> tuple[bytes, str, str, dict[str, Any]]:
        report = self.get_report(project_id, as_of=as_of)
        content, filename, media_type = export_research_decision_report(report, export_format)
        return content, filename, media_type, report

    def freeze_version(
        self,
        project_id: int,
        *,
        confirmed: bool,
        evaluated_on: date | str,
        expected_report_fingerprint: str,
        idempotency_key: str,
        version_note: str | None = None,
    ) -> dict[str, Any]:
        return freeze_research_report_version(
            project_id,
            confirmed=confirmed,
            evaluated_on=evaluated_on,
            expected_report_fingerprint=expected_report_fingerprint,
            idempotency_key=idempotency_key,
            version_note=version_note,
        )

    def list_versions(
        self,
        project_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return list_research_report_versions(
            project_id,
            limit=limit,
            offset=offset,
        )

    def get_version(self, project_id: int, version_no: int) -> dict[str, Any]:
        detail = get_research_report_version(project_id, version_no)
        detail.pop("_report_markdown", None)
        return detail

    def export_version(
        self,
        project_id: int,
        version_no: int,
        export_format: str,
    ) -> tuple[bytes, str, str, dict[str, Any]]:
        return export_research_report_version(
            project_id,
            version_no,
            export_format,
        )

    def compare_versions(
        self,
        project_id: int,
        from_version: int,
        to_version: int,
    ) -> dict[str, Any]:
        return compare_research_report_versions(
            project_id,
            from_version,
            to_version,
        )

    def compare_current_to_baseline(
        self,
        project_id: int,
        *,
        from_version: int | None = None,
        as_of: date | str | None = None,
    ) -> dict[str, Any]:
        return compare_current_research_report_to_baseline(
            project_id,
            from_version=from_version,
            as_of=as_of,
        )
