from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.routers.research_reviews as review_router  # noqa: E402
from api.app import app  # noqa: E402
from services.research_review_queue import assemble_research_review_item  # noqa: E402


FINGERPRINT_A = "A" * 64
FINGERPRINT_B = "B" * 64
REPORT_A = "C" * 64
REPORT_B = "D" * 64
api_client = TestClient(app)


def _project(**patch) -> dict:
    value = {
        "id": 4,
        "marketplace": "US",
        "name": "减压玩具验证",
        "status": "collecting",
        "current_decision_report_version_id": None,
        "current_decision_report_version_no": None,
    }
    value.update(patch)
    return value


def _report(**patch) -> dict:
    value = {
        "evaluated_on": "2026-07-27",
        "evidence_fingerprint": FINGERPRINT_A,
        "report_fingerprint": REPORT_B,
        "project": {
            "id": 4,
            "marketplace": "US",
            "name": "减压玩具验证",
            "status": "collecting",
            "status_label": "收集证据",
        },
        "readiness": {
            "level": "reviewable",
            "label": "可进入人工复核",
            "summary": "仍需补趋势。",
            "passed_count": 8,
            "total_count": 10,
            "blocking_count": 1,
        },
        "evidence_health": {
            "freshness": {
                "status": "current",
                "label": "主要来源在 7 天内",
                "latest_source_at": "2026-07-24 12:00:00",
                "oldest_source_at": "2026-07-23 12:00:00",
                "latest_age_days": 3,
                "oldest_age_days": 4,
                "source_count": 3,
                "stale_source_count": 0,
            },
            "timeline": {
                "best_source": "keyword",
                "best_source_label": "关键词排名",
                "best_name": "squishy",
                "best_points": 2,
                "best_span_days": 1,
                "preliminary_ready": False,
                "stable_ready": False,
                "context_only_ready": False,
            },
        },
        "assets": {
            "keywords": [
                {
                    "keyword": "squishy",
                    "role": "core",
                    "timepoint_count": 2,
                    "first_snapshot_at": "2026-07-20 12:00:00",
                    "latest_snapshot_at": "2026-07-21 12:00:00",
                }
            ]
        },
    }
    for key, item in patch.items():
        value[key] = item
    return value


def _version(**patch) -> dict:
    value = {
        "id": 10,
        "project_id": 4,
        "version_no": 2,
        "freeze_kind": "manual",
        "source_project_status": "collecting",
        "decision_status": None,
        "evaluated_on": "2026-07-26",
        "evidence_as_of": "2026-07-24 12:00:00",
        "evidence_fingerprint": FINGERPRINT_A,
        "report_fingerprint": REPORT_A,
        "frozen_at": "2026-07-26 18:00:00",
    }
    value.update(patch)
    return value


def _task(**patch) -> dict:
    value = {
        "id": 7,
        "marketplace": "US",
        "keyword": "squishy",
        "target_snapshots": 3,
        "status": "active",
        "pages_per_keyword": 2,
        "last_collected_at": "2026-07-26 12:00:00",
        "last_checked_at": "2026-07-26 12:05:00",
        "achieved_snapshots": 2,
        "current_snapshots": 2,
        "error_message": None,
        "created_at": "2026-07-20 12:00:00",
        "updated_at": "2026-07-26 12:05:00",
    }
    value.update(patch)
    return value


def _task_map(*tasks) -> dict:
    return {("US", "squishy"): list(tasks)}


def test_same_evidence_but_new_evaluation_date_is_not_new_evidence() -> None:
    item = assemble_research_review_item(
        _project(),
        _report(),
        versions=[_version()],
        tasks_by_keyword={},
        now=datetime(2026, 7, 27, 12),
    )

    assert item["report_version"]["evidence_changed"] is False
    assert item["report_version"]["report_changed"] is True
    assert item["report_version"]["time_only_change"] is True
    assert item["primary_status"]["code"] == "trend_tracking_missing"
    assert item["tracking"]["recommended_keyword"] == "squishy"
    assert item["attention_group"] == "action_required"


def test_changed_evidence_since_latest_freeze_requires_review() -> None:
    report = _report(evidence_fingerprint=FINGERPRINT_B)
    item = assemble_research_review_item(
        _project(),
        report,
        versions=[_version()],
        tasks_by_keyword=_task_map(_task()),
        now=datetime(2026, 7, 27, 12),
    )

    assert item["report_version"]["evidence_changed"] is True
    assert item["primary_status"]["code"] == "evidence_changed"
    assert item["attention_group"] == "action_required"
    assert item["next_review_on"] == "2026-07-27"


def test_terminal_project_uses_bound_decision_version_and_ignores_old_tracking_noise() -> None:
    project = _project(
        status="approved",
        current_decision_report_version_id=9,
        current_decision_report_version_no=1,
    )
    report = _report(
        project={
            **_report()["project"],
            "status": "approved",
            "status_label": "已批准",
        }
    )
    older_bound = _version(id=9, version_no=1)
    newer_ordinary = _version(
        id=10,
        version_no=2,
        evidence_fingerprint=FINGERPRINT_B,
    )
    item = assemble_research_review_item(
        project,
        report,
        versions=[newer_ordinary, older_bound],
        tasks_by_keyword=_task_map(
            _task(status="error", error_message="历史错误", updated_at="2026-07-27 09:00:00")
        ),
        now=datetime(2026, 7, 27, 12),
    )

    assert item["report_version"]["baseline"]["id"] == 9
    assert item["report_version"]["baseline_kind"] == "terminal_decision"
    assert item["primary_status"]["code"] == "terminal_current"
    assert item["attention_group"] == "terminal"
    assert item["next_review_on"] is None


def test_terminal_project_with_changed_evidence_is_promoted_to_action_required() -> None:
    project = _project(status="rejected", current_decision_report_version_id=9)
    report = _report(
        evidence_fingerprint=FINGERPRINT_B,
        project={**_report()["project"], "status": "rejected", "status_label": "已拒绝"},
    )
    item = assemble_research_review_item(
        project,
        report,
        versions=[_version(id=9, version_no=1)],
        tasks_by_keyword={},
        now=datetime(2026, 7, 27, 12),
    )

    assert item["primary_status"]["code"] == "terminal_evidence_changed"
    assert item["attention_group"] == "action_required"


def test_terminal_project_without_bound_version_requires_manual_audit() -> None:
    project = _project(status="approved", current_decision_report_version_id=None)
    report = _report(
        project={**_report()["project"], "status": "approved", "status_label": "已批准"},
    )
    item = assemble_research_review_item(
        project,
        report,
        versions=[_version()],
        tasks_by_keyword={},
        now=datetime(2026, 7, 27, 12),
    )

    assert item["report_version"]["latest"]["version_no"] == 2
    assert item["report_version"]["baseline"] is None
    assert item["primary_status"]["code"] == "terminal_baseline_missing"
    assert item["attention_group"] == "action_required"


def test_due_tracking_task_is_read_only_action_signal() -> None:
    item = assemble_research_review_item(
        _project(),
        _report(),
        versions=[_version()],
        tasks_by_keyword=_task_map(_task(last_collected_at="2026-07-20 12:00:00")),
        now=datetime(2026, 7, 27, 12),
        min_interval_hours=72,
    )

    assert item["tracking"]["due"] == 1
    assert item["tracking"]["keywords"][0]["task_state"] == "due"
    assert item["primary_status"]["code"] == "tracking_due"
    assert item["actions"]["write_required"] is False


def test_waiting_task_derives_next_review_without_claiming_scheduling() -> None:
    item = assemble_research_review_item(
        _project(),
        _report(),
        versions=[_version()],
        tasks_by_keyword=_task_map(_task(last_collected_at="2026-07-26 12:00:00")),
        now=datetime(2026, 7, 27, 12),
        min_interval_hours=72,
    )

    assert item["primary_status"]["code"] == "trend_waiting"
    assert item["attention_group"] == "waiting"
    assert item["next_review_on"] == "2026-07-29"
    assert "安全采集间隔" in item["next_review_reason"]


def test_queue_preserves_raw_and_qualified_trend_counts() -> None:
    report = _report()
    report["evidence_health"]["timeline"].update(
        {
            "best_points": 3,
            "best_raw_points": 5,
            "best_excluded_points": 2,
            "best_invalid_rank_points": 2,
            "best_near_duplicate_points": 1,
            "best_span_days": 5,
            "best_quality_status": "partial",
            "best_quality_label": "部分时间点可用",
            "best_quality_warnings": ["2 个批次的自然排名不完整。"],
        }
    )

    item = assemble_research_review_item(
        _project(),
        report,
        versions=[_version()],
        tasks_by_keyword={},
        now=datetime(2026, 7, 27, 12),
    )

    assert item["timeline"]["best_points"] == 3
    assert item["timeline"]["best_raw_points"] == 5
    assert item["timeline"]["best_excluded_points"] == 2
    assert item["timeline"]["best_invalid_rank_points"] == 2
    assert item["timeline"]["best_near_duplicate_points"] == 1
    assert item["timeline"]["best_quality_status"] == "partial"
    assert item["timeline"]["best_quality_warnings"] == ["2 个批次的自然排名不完整。"]


def test_active_task_wins_over_historical_error_for_same_keyword() -> None:
    item = assemble_research_review_item(
        _project(),
        _report(),
        versions=[_version()],
        tasks_by_keyword=_task_map(
            _task(id=6, status="error", updated_at="2026-07-27 11:00:00"),
            _task(id=7, status="active", updated_at="2026-07-26 12:00:00"),
        ),
        now=datetime(2026, 7, 27, 12),
    )

    assert item["tracking"]["keywords"][0]["task_id"] == 7
    assert item["tracking"]["error"] == 0
    assert item["tracking"]["waiting"] == 1


def test_review_queue_api_uses_standard_envelope_and_remains_get_only(monkeypatch) -> None:
    calls = []

    class FakeRepository:
        def get_queue(self, **kwargs):
            calls.append(kwargs)
            return {
                "rows": [{"project": {"id": 4, "name": "减压玩具验证"}}],
                "total": 1,
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
                "summary": {"action_required": 1},
                "policy": {"read_only": True},
            }

    monkeypatch.setattr(review_router, "_repository", FakeRepository())
    response = api_client.get(
        "/api/research-review-queue",
        params={
            "limit": 10,
            "offset": 0,
            "marketplace": "US",
            "status": "collecting",
            "keyword": "squishy",
            "attention": "action_required",
            "monitoring": "due",
            "as_of": "2026-07-27",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["policy"]["read_only"] is True
    assert calls == [
        {
            "limit": 10,
            "offset": 0,
            "marketplace": "US",
            "status": "collecting",
            "keyword": "squishy",
            "attention": "action_required",
            "monitoring": "due",
            "as_of": "2026-07-27",
        }
    ]
    methods = app.openapi()["paths"]["/api/research-review-queue"]
    assert set(methods) == {"get"}


def test_review_queue_api_rejects_unknown_attention_group() -> None:
    response = api_client.get(
        "/api/research-review-queue",
        params={"attention": "automatic_collection"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_error"
