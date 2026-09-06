"""Read-only research review queue routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from api.contracts import ok
from repositories.research_reviews import ResearchReviewRepository


router = APIRouter(prefix="/api/research-review-queue", tags=["research-review-queue"])
_repository = ResearchReviewRepository()


def configure_repository(repository: ResearchReviewRepository) -> None:
    global _repository
    _repository = repository


@router.get("")
def research_review_queue(
    limit: int = 25,
    offset: int = 0,
    marketplace: str = "US",
    status: Literal[
        "idea",
        "collecting",
        "validating",
        "candidate",
        "manual_review",
        "approved",
        "rejected",
    ]
    | None = None,
    keyword: str | None = None,
    attention: Literal["action_required", "waiting", "terminal"] | None = None,
    monitoring: Literal["active", "due", "paused", "unplanned"] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    return ok(
        _repository.get_queue(
            limit=limit,
            offset=offset,
            marketplace=marketplace,
            status=status,
            keyword=keyword,
            attention=attention,
            monitoring=monitoring,
            as_of=as_of,
        )
    )
