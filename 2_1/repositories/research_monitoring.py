"""Repository facade for user-controlled research observation plans."""

from __future__ import annotations

from datetime import date
from typing import Any

from services.research_monitoring import (
    complete_research_observation_review,
    get_research_observation_plan,
    save_research_observation_plan,
)


class ResearchMonitoringRepository:
    def get_plan(
        self,
        project_id: int,
        *,
        as_of: date | str | None = None,
    ) -> dict[str, Any]:
        return get_research_observation_plan(project_id, as_of=as_of)

    def save_plan(
        self,
        project_id: int,
        *,
        status: str,
        cadence_days: int,
        next_review_on: date | str,
        plan_note: str | None = None,
    ) -> dict[str, Any]:
        return save_research_observation_plan(
            project_id,
            status=status,
            cadence_days=cadence_days,
            next_review_on=next_review_on,
            plan_note=plan_note,
        )

    def complete_review(
        self,
        project_id: int,
        *,
        confirmed: bool,
        evaluated_on: date | str,
        expected_report_fingerprint: str,
        review_note: str | None = None,
    ) -> dict[str, Any]:
        return complete_research_observation_review(
            project_id,
            confirmed=confirmed,
            evaluated_on=evaluated_on,
            expected_report_fingerprint=expected_report_fingerprint,
            review_note=review_note,
        )
