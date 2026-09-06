"""Repository facade for the read-only research review queue."""

from __future__ import annotations

from datetime import date
from typing import Any

from services.research_review_queue import fetch_research_review_queue


class ResearchReviewRepository:
    def get_queue(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        marketplace: str = "US",
        status: str | None = None,
        keyword: str | None = None,
        attention: str | None = None,
        monitoring: str | None = None,
        as_of: date | str | None = None,
    ) -> dict[str, Any]:
        return fetch_research_review_queue(
            limit=limit,
            offset=offset,
            marketplace=marketplace,
            status=status,
            keyword=keyword,
            attention=attention,
            monitoring=monitoring,
            as_of=as_of,
        )
