from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.routers.research_monitoring as monitoring_router  # noqa: E402
from api.app import app  # noqa: E402
from services.research_monitoring import (  # noqa: E402
    assemble_observation_plan_bundle,
    monitoring_filter_matches,
    summarize_observation_plan,
)
from services.research_review_queue import assemble_research_review_item  # noqa: E402


EVIDENCE_A = "A" * 64
EVIDENCE_B = "B" * 64
REPORT_A = "C" * 64
REPORT_B = "D" * 64
api_client = TestClient(app)


def _plan(**patch) -> dict:
    value = {
        "project_id": 4,
        "status": "active",
        "cadence_days": 14,
        "next_review_on": date(2026, 8, 12),
        "plan_note": "核对核心词与供应链人工信息",
        "last_reviewed_at": datetime(2026, 8, 1, 9, 30),
        "last_review_evaluated_on": date(2026, 8, 1),
        "last_review_evidence_fingerprint": EVIDENCE_A,
        "last_review_report_fingerprint": REPORT_A,
        "last_review_note": "继续观察",
        "created_at": datetime(2026, 7, 29, 12),
        "updated_at": datetime(2026, 8, 1, 9, 30),
    }
    value.update(patch)
    return value


def _report(**patch) -> dict:
    value = {
        "evaluated_on": "2026-08-12",
        "evidence_as_of": "2026-08-07 23:00:00",
        "evidence_fingerprint": EVIDENCE_A,
        "report_fingerprint": REPORT_A,
        "schema_version": "1.0",
        "method_version": "1.3",
        "project": {
            "id": 4,
            "marketplace": "US",
            "name": "Squishy 研究",
            "status": "collecting",
            "status_label": "收集证据",
        },
        "readiness": {
            "level": "reviewable",
            "label": "可进入人工复核",
            "summary": "仍缺人工成本。",
            "passed_count": 9,
            "total_count": 10,
            "blocking_count": 1,
        },
        "evidence_health": {
            "freshness": {
                "status": "current",
                "label": "主要来源在 7 天内",
                "latest_source_at": "2026-08-07 23:00:00",
                "oldest_source_at": "2026-08-01 12:00:00",
            },
            "timeline": {
                "best_name": "slow rising squishy",
                "best_points": 5,
                "best_raw_points": 5,
                "best_span_days": 14,
                "preliminary_ready": True,
                "stable_ready": False,
            },
        },
        "assets": {"keywords": []},
    }
    value.update(patch)
    return value


def _project(**patch) -> dict:
    value = {
        "id": 4,
        "marketplace": "US",
        "name": "Squishy 研究",
        "status": "collecting",
        "objective": "验证需求与差异化空间",
        "strategy": "先观察，再人工核算成本",
        "current_decision_report_version_id": 2,
        "current_decision_report_version_no": 2,
    }
    value.update(patch)
    return value


def _version() -> dict:
    return {
        "id": 2,
        "project_id": 4,
        "version_no": 2,
        "freeze_kind": "manual",
        "source_project_status": "collecting",
        "decision_status": None,
        "evaluated_on": "2026-08-07",
        "evidence_as_of": "2026-08-07 23:00:00",
        "evidence_fingerprint": EVIDENCE_A,
        "report_fingerprint": REPORT_A,
        "frozen_at": "2026-08-08 09:00:00",
    }


def test_unplanned_summary_is_explicit_and_not_due() -> None:
    value = summarize_observation_plan(None, evaluated_on="2026-08-12")

    assert value["exists"] is False
    assert value["state"] == "unplanned"
    assert value["is_due"] is False
    assert monitoring_filter_matches(value, "unplanned") is True


def test_active_plan_reports_due_state_and_post_review_evidence_change() -> None:
    value = summarize_observation_plan(
        _plan(),
        evaluated_on="2026-08-12",
        current_evidence_fingerprint=EVIDENCE_B,
        current_report_fingerprint=REPORT_B,
    )

    assert value["state"] == "due"
    assert value["days_until_review"] == 0
    assert value["is_due"] is True
    assert value["new_evidence_since_review"] is True
    assert value["report_changed_since_review"] is True
    assert monitoring_filter_matches(value, "due") is True


def test_paused_plan_never_becomes_due_even_after_date() -> None:
    value = summarize_observation_plan(
        _plan(status="paused", next_review_on=date(2026, 8, 1)),
        evaluated_on="2026-08-12",
    )

    assert value["state"] == "paused"
    assert value["days_until_review"] == -11
    assert value["is_due"] is False
    assert monitoring_filter_matches(value, "paused") is True


def test_bundle_exposes_only_manual_policy_and_compact_report_context() -> None:
    bundle = assemble_observation_plan_bundle(
        _project(),
        _report(),
        _plan(),
        evaluated_on="2026-08-12",
        generated_at=datetime(2026, 8, 12, 10),
    )

    assert bundle["current_report"]["readiness"]["passed_count"] == 9
    assert bundle["current_report"]["timeline"]["best_points"] == 5
    assert bundle["policy"]["manual_only"] is True
    assert bundle["policy"]["background_scheduler"] is False
    assert bundle["policy"]["automatic_collection"] is False
    assert "assets" not in bundle["current_report"]


def test_due_plan_is_a_readonly_queue_signal() -> None:
    item = assemble_research_review_item(
        _project(),
        _report(),
        versions=[_version()],
        monitoring_plan=_plan(),
        tasks_by_keyword={},
        now=datetime(2026, 8, 12, 10),
    )

    assert item["monitoring_plan"]["is_due"] is True
    assert item["primary_status"]["code"] == "monitoring_due"
    assert item["attention_group"] == "action_required"
    assert item["actions"]["write_required"] is False
    assert item["actions"]["monitoring_route"].endswith("/observation-plan")


def test_new_evidence_since_manual_review_has_priority() -> None:
    report = _report(evidence_fingerprint=EVIDENCE_B, report_fingerprint=REPORT_B)
    item = assemble_research_review_item(
        _project(),
        report,
        versions=[_version()],
        monitoring_plan=_plan(next_review_on=date(2026, 8, 20)),
        tasks_by_keyword={},
        now=datetime(2026, 8, 12, 10),
    )

    assert item["monitoring_plan"]["new_evidence_since_review"] is True
    assert item["primary_status"]["code"] == "monitoring_new_evidence"


def test_observation_plan_api_contract(monkeypatch) -> None:
    calls = []

    class FakeRepository:
        def get_plan(self, project_id, *, as_of=None):
            calls.append(("get", project_id, as_of))
            return {"project": {"id": project_id}, "plan": {"exists": False}}

        def save_plan(self, project_id, **kwargs):
            calls.append(("save", project_id, kwargs))
            return {"project": {"id": project_id}, "plan": {"exists": True}}

        def complete_review(self, project_id, **kwargs):
            calls.append(("review", project_id, kwargs))
            return {"project": {"id": project_id}, "review_completed": True}

    monkeypatch.setattr(monitoring_router, "_repository", FakeRepository())

    response = api_client.get(
        "/api/research-projects/4/observation-plan",
        params={"as_of": "2026-08-12"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["plan"]["exists"] is False

    response = api_client.put(
        "/api/research-projects/4/observation-plan",
        json={
            "status": "active",
            "cadence_days": 14,
            "next_review_on": "2026-08-26",
            "plan_note": "继续人工观察",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["plan"]["exists"] is True

    response = api_client.post(
        "/api/research-projects/4/observation-plan/review",
        json={
            "confirmed": True,
            "evaluated_on": "2026-08-12",
            "expected_report_fingerprint": REPORT_A,
            "review_note": "本次无异常",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["review_completed"] is True
    assert calls == [
        ("get", 4, "2026-08-12"),
        (
            "save",
            4,
            {
                "status": "active",
                "cadence_days": 14,
                "next_review_on": "2026-08-26",
                "plan_note": "继续人工观察",
            },
        ),
        (
            "review",
            4,
            {
                "confirmed": True,
                "evaluated_on": "2026-08-12",
                "expected_report_fingerprint": REPORT_A,
                "review_note": "本次无异常",
            },
        ),
    ]


def test_observation_plan_migration_has_one_plan_per_project_and_down_script() -> None:
    migration = (
        ROOT / "database" / "migrations" / "20260812_research_observation_plans_v1.sql"
    ).read_text(encoding="utf-8")
    rollback = (
        ROOT / "database" / "migrations" / "20260812_research_observation_plans_v1.down.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS research_project_observation_plans" in migration
    assert "PRIMARY KEY (project_id)" in migration
    assert "CHECK (cadence_days BETWEEN 3 AND 180)" in migration
    assert "FOREIGN KEY (project_id) REFERENCES research_projects(id)" in migration
    assert "DROP TABLE IF EXISTS research_project_observation_plans" in rollback
