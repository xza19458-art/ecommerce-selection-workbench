"""User-controlled research observation plan routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.contracts import ok
from repositories.research_monitoring import ResearchMonitoringRepository


router = APIRouter(prefix="/api/research-projects", tags=["research-monitoring"])
_repository = ResearchMonitoringRepository()


class ResearchObservationPlanIn(BaseModel):
    status: Literal["active", "paused"] = "active"
    cadence_days: int = Field(default=14, ge=3, le=180)
    next_review_on: str
    plan_note: str | None = Field(default=None, max_length=2000)


class ResearchObservationReviewIn(BaseModel):
    confirmed: bool = False
    evaluated_on: str
    expected_report_fingerprint: str = Field(min_length=64, max_length=64)
    review_note: str | None = Field(default=None, max_length=2000)


def configure_repository(repository: ResearchMonitoringRepository) -> None:
    global _repository
    _repository = repository


@router.get("/{project_id}/observation-plan")
def research_observation_plan(
    project_id: int,
    as_of: str | None = None,
) -> dict[str, Any]:
    return ok(_repository.get_plan(project_id, as_of=as_of))


@router.put("/{project_id}/observation-plan")
def research_observation_plan_save(
    project_id: int,
    body: ResearchObservationPlanIn,
) -> dict[str, Any]:
    return ok(_repository.save_plan(project_id, **body.model_dump()))


@router.post("/{project_id}/observation-plan/review")
def research_observation_plan_review(
    project_id: int,
    body: ResearchObservationReviewIn,
) -> dict[str, Any]:
    return ok(_repository.complete_review(project_id, **body.model_dump()))
