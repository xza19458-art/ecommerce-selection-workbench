from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import app
from services.keyword_tracking import KeywordTrackingTask
from services.tracking_evidence import (
    build_adjacent_batch_comparison,
    build_tracking_schedule,
    build_trend_gate,
    classify_tracking_timepoints,
    research_watch_interpretation,
)
import services.tracking_evidence as tracking_evidence


def _task(**overrides) -> KeywordTrackingTask:
    values = {
        "id": 15,
        "marketplace": "US",
        "keyword": "squishy",
        "target_snapshots": 6,
        "status": "active",
        "pages_per_keyword": 1,
        "last_collected_at": "2026-07-31 22:00:00",
        "last_checked_at": "2026-07-31 22:44:16",
        "achieved_snapshots": 4,
        "current_snapshots": 4,
        "error_message": None,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return KeywordTrackingTask(**values)


def _timepoint(snapshot_at: str, *, products: int, ranks: int | None = None) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "row_count": products,
        "product_count": products,
        "organic_row_count": products,
        "distinct_organic_rank_count": products if ranks is None else ranks,
        "invalid_organic_rank_count": 0,
        "page_count": 1,
    }


def _observation(snapshot_at: str, product_id: int, rank: int, asin: str) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "product_id": product_id,
        "organic_rank": rank,
        "is_sponsored": 0,
        "marketplace": "US",
        "asin": asin,
        "title": asin,
        "title_zh": None,
        "brand": None,
        "product_url": f"https://www.amazon.com/dp/{asin}",
    }


def test_timepoint_classification_reuses_integrity_and_24_hour_rule() -> None:
    rows = classify_tracking_timepoints(
        [
            _timepoint("2026-07-20 00:00:00", products=10),
            _timepoint("2026-07-20 12:00:00", products=12),
            _timepoint("2026-07-21 18:00:00", products=11),
            _timepoint("2026-07-24 18:00:00", products=10, ranks=9),
        ]
    )

    assert [row["qualification"] for row in rows] == [
        "near_duplicate",
        "qualified",
        "qualified",
        "invalid",
    ]
    assert rows[-1]["rank_integrity"] is False
    assert "未计入趋势" in rows[-1]["exclusion_reason"]


def test_adjacent_comparison_uses_batch_sets_and_rank_direction() -> None:
    previous = "2026-07-27 22:00:00"
    current = "2026-07-31 22:00:00"
    observations = [
        _observation(previous, 1, 10, "B000000001"),
        _observation(previous, 2, 20, "B000000002"),
        _observation(previous, 3, 30, "B000000003"),
        _observation(current, 1, 8, "B000000001"),
        _observation(current, 2, 25, "B000000002"),
        _observation(current, 4, 12, "B000000004"),
    ]

    result = build_adjacent_batch_comparison(observations)

    assert result["retained_product_count"] == 2
    assert result["entered_product_count"] == 1
    assert result["exited_product_count"] == 1
    assert result["improved_count"] == 1
    assert result["worsened_count"] == 1
    assert result["average_rank_delta"] == 1.5
    assert result["entered"][0]["asin"] == "B000000004"
    assert result["exited"][0]["asin"] == "B000000003"
    assert "未观察到不等于下架" in result["confidence_note"]


def test_trend_gate_and_schedule_keep_distinct_thresholds(monkeypatch) -> None:
    monkeypatch.setattr(
        tracking_evidence,
        "get_collection_limits",
        lambda: type("Limits", (), {"tracking_min_interval_hours": 72})(),
    )
    gate = build_trend_gate(
        {
            "raw_timepoint_count": 4,
            "qualified_timepoint_count": 4,
            "qualified_span_days": 11,
        }
    )
    schedule = build_tracking_schedule(_task(), now=datetime(2026, 7, 31, 23, 0, 0))

    assert gate["preliminary"]["ready"] is False
    assert gate["preliminary"]["remaining_points"] == 0
    assert gate["preliminary"]["remaining_days"] == 3
    assert gate["stable"]["remaining_points"] == 2
    assert gate["stable"]["remaining_days"] == 19
    assert schedule["due"] is False
    assert schedule["next_collectible_at"] == "2026-08-03 22:00:00"


def test_research_watch_absence_does_not_claim_delisting() -> None:
    message = research_watch_interpretation(
        {
            "current_observed": False,
            "previous_observed": False,
            "observed_timepoint_count": 2,
            "absence_streak": 2,
        },
        total_points=4,
    )

    assert "最近 2 个批次未观察到" in message
    assert "不等于下架" in message


def test_tracking_evidence_api_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        tracking_evidence,
        "build_tracking_task_evidence",
        lambda task_id: {
            "schema_version": "tracking-evidence-v1.1",
            "task": {"id": task_id, "keyword": "squishy"},
            "timepoints": [],
        },
    )
    client = TestClient(app)

    response = client.get("/api/tracking/tasks/15/evidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["task"] == {"id": 15, "keyword": "squishy"}
