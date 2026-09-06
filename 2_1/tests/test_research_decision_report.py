from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.routers.research_reports as report_router  # noqa: E402
from api.app import app  # noqa: E402
from services.research_decision_report import (  # noqa: E402
    REPORT_METHOD_VERSION,
    assemble_research_decision_report,
    compact_research_decision_report,
    export_research_decision_report,
    qualify_keyword_rank_timepoints,
)


client = TestClient(app)


def _detail(*, rich: bool = True) -> dict:
    products = []
    keywords = []
    niches = []
    notes = []
    if rich:
        products = [
            {
                "product_id": 10,
                "asin": "B0TEST0001",
                "title": "Candidate",
                "role": "candidate",
                "price": 22,
                "rating": 4.6,
                "review_count": 800,
                "monthly_bought": 1000,
                "snapshot_at": "2026-07-20 10:00:00",
                "evidence_status": "collected",
                "evidence_collected": 3,
                "evidence_total": 3,
            },
            {
                "product_id": 11,
                "asin": "B0TEST0002",
                "title": "Benchmark",
                "role": "benchmark",
                "price": 25,
                "rating": 4.7,
                "review_count": 5000,
                "monthly_bought": 5000,
                "snapshot_at": "2026-07-20 10:00:00",
                "evidence_status": "partial",
                "evidence_collected": 2,
                "evidence_total": 3,
            },
        ]
        keywords = [
            {
                "keyword_id": 20,
                "keyword": "squishy toy",
                "role": "core",
                "product_count": 100,
                "rank_snapshot_count": 600,
                "latest_snapshot_at": "2026-07-20 10:00:00",
                "tracking_status": "active",
            }
        ]
        niches = [
            {
                "niche_id": 30,
                "name": "Squishy Toys",
                "role": "primary",
                "status": "active",
            }
        ]
        notes = [
            {
                "id": 40,
                "note_type": "opportunity",
                "content": "现有产品尺寸偏大，考虑便携套装和更明确的减压使用场景。",
                "created_at": "2026-07-20 11:00:00",
                "updated_at": "2026-07-20 11:00:00",
            },
            {
                "id": 41,
                "note_type": "risk",
                "content": "头部评论门槛高，广告密度也高，不应直接复制现有款。",
                "created_at": "2026-07-20 11:10:00",
                "updated_at": "2026-07-20 11:10:00",
            },
        ]
    return {
        "project": {
            "id": 4,
            "marketplace": "US",
            "name": "减压玩具验证",
            "status": "manual_review" if rich else "idea",
            "status_label": "人工复核" if rich else "方向构想",
            "objective": "验证便携减压玩具在 20-30 美元价格带的差异化机会" if rich else None,
            "strategy": "差异化",
            "decision_summary": None,
            "created_at": "2026-07-13 10:00:00",
            "updated_at": "2026-07-20 11:10:00",
            "status_changed_at": "2026-07-20 11:10:00",
            "evidence_coverage": 88.0 if rich else 0.0,
        },
        "products": products,
        "keywords": keywords,
        "niches": niches,
        "notes": notes,
    }


def _supplement(*, rich: bool = True, short_timeline: bool = False) -> dict:
    if not rich:
        return {"products": [], "keywords": [], "niches": []}
    first = "2026-07-19 10:00:00" if short_timeline else "2026-06-10 10:00:00"
    points = 2 if short_timeline else 6
    return {
        "products": [
            {
                "product_id": 10,
                "latest_product_snapshot_id": 101,
                "timepoint_count": points,
                "first_snapshot_at": first,
                "latest_snapshot_at": "2026-07-20 10:00:00",
                "metric_input_count": 1,
                "latest_metric_period_end": "2026-07-19",
                "has_any_cost": 1,
                "has_complete_unit_cost": 1,
                "has_exact_conversion": 0,
                "has_official_input": 0,
            },
            {
                "product_id": 11,
                "latest_product_snapshot_id": 102,
                "timepoint_count": 2,
                "first_snapshot_at": "2026-07-19 10:00:00",
                "latest_snapshot_at": "2026-07-20 10:00:00",
                "metric_input_count": 0,
                "has_any_cost": 0,
                "has_complete_unit_cost": 0,
                "has_exact_conversion": 0,
                "has_official_input": 0,
            },
        ],
        "keywords": [
            {
                "keyword_id": 20,
                "timepoint_count": points,
                "first_snapshot_at": first,
                "latest_snapshot_at": "2026-07-20 10:00:00",
                "latest_batch_product_count": 100,
                "serp_timepoint_count": points,
                "latest_serp_at": "2026-07-20 10:00:00",
            }
        ],
        "niches": [
            {
                "niche_id": 30,
                "name": "Squishy Toys",
                "role": "primary",
                "status": "active",
                "snapshot_id": 301,
                "snapshot_at": "2026-07-20 10:30:00",
                "source_latest_at": "2026-07-20 10:00:00",
                "evidence_hash": "A" * 64,
                "evidence_level": "usable",
                "keyword_count": 3,
                "keyword_with_rank_count": 3,
                "rank_coverage": 1.0,
                "serp_coverage": 1.0,
                "serp_data_coverage": 0.9,
                "observed_product_count": 100,
                "product_snapshot_coverage": 0.9,
                "price_p25": 18,
                "price_median": 23,
                "price_p75": 29,
                "review_median": 2000,
                "monthly_bought_median": 500,
                "monthly_bought_total": 50_000,
                "monthly_bought_coverage": 0.8,
                "demand_cr3": 0.7,
                "ad_density": 0.4,
                "brand_product_cr3": 0.65,
                "timepoint_count": points,
                "first_snapshot_at": first,
                "latest_snapshot_at": "2026-07-20 10:30:00",
                "warnings": [],
            }
        ],
    }


def _report(*, rich: bool = True, short_timeline: bool = False) -> dict:
    return assemble_research_decision_report(
        _detail(rich=rich),
        supplement=_supplement(rich=rich, short_timeline=short_timeline),
        evaluated_on="2026-07-24",
        generated_at=datetime.fromisoformat("2026-07-24T18:00:00+08:00"),
    )


def _rank_point(
    snapshot_at: str,
    *,
    rows: int = 142,
    distinct_ranks: int | None = None,
) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "row_count": rows,
        "product_count": rows,
        "organic_row_count": rows,
        "distinct_organic_rank_count": rows if distinct_ranks is None else distinct_ranks,
        "invalid_organic_rank_count": 0,
        "page_count": 3,
        "min_organic_rank": 1,
        "max_organic_rank": rows,
    }


def test_keyword_timepoint_quality_excludes_rank_reset_and_near_duplicate_batches() -> None:
    result = qualify_keyword_rank_timepoints(
        [
            _rank_point("2026-06-29 14:00:00", distinct_ranks=52),
            _rank_point("2026-06-29 16:00:00", distinct_ranks=54),
            _rank_point("2026-07-07 17:00:00", rows=140),
            _rank_point("2026-07-09 16:00:00"),
            _rank_point("2026-07-13 12:00:00"),
        ],
        raw_timepoint_count=5,
        raw_first_snapshot_at="2026-06-29 14:00:00",
        raw_latest_snapshot_at="2026-07-13 12:00:00",
        evaluated_on="2026-07-28",
    )

    assert result["raw_timepoint_count"] == 5
    assert result["raw_independent_window_count"] == 4
    assert result["qualified_timepoint_count"] == 3
    assert result["invalid_rank_timepoint_count"] == 2
    assert result["near_duplicate_timepoint_count"] == 1
    assert result["qualified_span_days"] == 5
    assert result["timepoint_quality_status"] == "partial"
    assert result["timepoint_freshness_status"] == "stale"
    assert len(result["timepoint_quality_warnings"]) == 3


def test_keyword_timepoint_quality_accepts_exact_24_hour_windows_and_full_day_span() -> None:
    result = qualify_keyword_rank_timepoints(
        [
            _rank_point("2026-07-01 10:00:00", rows=48),
            _rank_point("2026-07-02 10:00:00", rows=48),
            _rank_point("2026-07-15 10:00:00", rows=48),
        ],
        evaluated_on="2026-07-15",
    )

    assert result["qualified_timepoint_count"] == 3
    assert result["near_duplicate_timepoint_count"] == 0
    assert result["qualified_span_days"] == 14
    assert result["timepoint_quality_status"] == "qualified"


def test_keyword_timepoint_quality_keeps_aggregate_only_compatibility() -> None:
    result = qualify_keyword_rank_timepoints(
        [],
        raw_timepoint_count=3,
        raw_first_snapshot_at="2026-07-01 10:00:00",
        raw_latest_snapshot_at="2026-07-15 10:00:00",
        evaluated_on="2026-07-15",
    )

    assert result["qualified_timepoint_count"] == 3
    assert result["qualified_span_days"] == 14
    assert result["timepoint_quality_status"] == "aggregate_only"
    assert result["timepoint_quality_warnings"]


def test_report_uses_qualified_keyword_points_instead_of_raw_count() -> None:
    detail = _detail()
    detail["keywords"].append(
        {
            "keyword_id": 21,
            "keyword": "slow rising squishy",
            "role": "core",
            "product_count": 142,
            "rank_snapshot_count": 708,
            "latest_snapshot_at": "2026-07-13 12:00:00",
            "tracking_status": "completed",
        }
    )
    supplement = _supplement(short_timeline=True)
    supplement["keywords"].append(
        {
            "keyword_id": 21,
            "timepoint_count": 5,
            "first_snapshot_at": "2026-06-29 14:00:00",
            "latest_snapshot_at": "2026-07-13 12:00:00",
            "latest_batch_product_count": 142,
            "_rank_timepoints": [
                _rank_point("2026-06-29 14:00:00", distinct_ranks=52),
                _rank_point("2026-06-29 16:00:00", distinct_ranks=54),
                _rank_point("2026-07-07 17:00:00", rows=140),
                _rank_point("2026-07-09 16:00:00"),
                _rank_point("2026-07-13 12:00:00"),
            ],
        }
    )
    report = assemble_research_decision_report(
        detail,
        supplement=supplement,
        evaluated_on="2026-07-28",
    )
    timeline = report["evidence_health"]["timeline"]
    slow_rising = next(
        row for row in timeline["sequences"] if row["name"] == "slow rising squishy"
    )

    assert slow_rising["raw_points"] == 5
    assert slow_rising["points"] == 3
    assert slow_rising["span_days"] == 5
    assert timeline["preliminary_ready"] is False
    assert "原始 5 个" in next(
        gate["detail"] for gate in report["readiness"]["gates"] if gate["key"] == "trend_evidence"
    )


def test_empty_project_lists_real_gaps_without_fake_support() -> None:
    report = _report(rich=False)

    assert report["readiness"]["level"] == "not_started"
    assert report["readiness"]["passed_count"] == 0
    assert report["supporting_arguments"] == []
    assert report["decision_axes"][0]["status"] == "insufficient"
    assert {row["key"] for row in report["data_gaps"]} >= {
        "objective",
        "candidate_product",
        "keyword_scope",
        "market_snapshot",
        "financial_inputs",
    }
    assert report["evidence_health"]["timeline"]["best_source"] is None
    assert report["evidence_health"]["timeline"]["best_source_label"] is None
    assert report["evidence_health"]["timeline"]["best_points"] == 0
    assert report["guardrails"]["human_decision_required"] is True


def test_complete_sample_is_review_ready_but_high_competition_remains_visible() -> None:
    report = _report()
    axes = {row["key"]: row for row in report["decision_axes"]}

    assert report["readiness"]["level"] == "decision_ready"
    assert report["readiness"]["passed_count"] == 10
    assert axes["demand"]["status"] == "supporting"
    assert axes["competition"]["status"] == "risk"
    assert "同质化" in axes["competition"]["summary"]
    assert axes["differentiation"]["status"] == "supporting"
    assert axes["trend"]["status"] == "supporting"
    assert axes["finance"]["status"] == "supporting"
    assert any(row["title"] == "竞争压力" for row in report["counter_arguments"])
    assert any(row["source"] == "项目记录 #41" for row in report["counter_arguments"])


def test_two_timepoints_do_not_form_a_trend_conclusion() -> None:
    report = _report(short_timeline=True)
    axes = {row["key"]: row for row in report["decision_axes"]}
    gates = {row["key"]: row for row in report["readiness"]["gates"]}

    assert axes["trend"]["status"] == "insufficient"
    assert report["evidence_health"]["timeline"]["best_points"] == 2
    assert report["evidence_health"]["timeline"]["best_span_days"] == 1
    assert gates["trend_evidence"]["passed"] is False


def test_benchmark_history_is_context_not_candidate_trend_evidence() -> None:
    supplement = _supplement(short_timeline=True)
    supplement["products"][1].update(
        {
            "timepoint_count": 7,
            "first_snapshot_at": "2026-06-01 10:00:00",
            "latest_snapshot_at": "2026-07-20 10:00:00",
        }
    )
    report = assemble_research_decision_report(
        _detail(),
        supplement=supplement,
        evaluated_on="2026-07-24",
    )
    axes = {row["key"]: row for row in report["decision_axes"]}
    gates = {row["key"]: row for row in report["readiness"]["gates"]}
    timeline = report["evidence_health"]["timeline"]

    assert gates["trend_evidence"]["passed"] is False
    assert axes["trend"]["status"] == "insufficient"
    assert "对标/参考资产" in axes["trend"]["summary"]
    assert timeline["best_points"] == 2
    assert timeline["context_best_name"] == "B0TEST0002"
    assert timeline["context_best_points"] == 7
    assert timeline["context_only_ready"] is True


def test_mixed_period_keyword_batches_lower_confidence_without_changing_metrics() -> None:
    supplement = _supplement()
    supplement["niches"][0].update(
        {
            "rank_source_first_at": "2026-07-13 10:00:00",
            "rank_source_latest_at": "2026-07-21 10:00:00",
            "rank_source_span_hours": 192.0,
            "rank_source_span_days": 8.0,
            "rank_source_alignment": "mixed_period",
        }
    )
    report = assemble_research_decision_report(
        _detail(),
        supplement=supplement,
        evaluated_on="2026-07-24",
    )
    axes = {row["key"]: row for row in report["decision_axes"]}
    niche = report["assets"]["niches"][0]

    assert axes["demand"]["facts"][1]["value"] == 50_000
    assert axes["demand"]["confidence"] == "medium"
    assert axes["competition"]["confidence"] == "medium"
    assert axes["evidence_quality"]["status"] == "mixed"
    assert "混合时点" in axes["evidence_quality"]["summary"]
    assert any("8.0 天" in item for item in axes["evidence_quality"]["caveats"])
    assert niche["rank_source_alignment"] == "mixed_period"
    assert niche["rank_source_span_hours"] == 192.0


def test_missing_cost_never_becomes_financial_feasibility() -> None:
    supplement = _supplement()
    supplement["products"][0]["has_any_cost"] = 0
    supplement["products"][0]["has_complete_unit_cost"] = 0
    report = assemble_research_decision_report(
        _detail(),
        supplement=supplement,
        evaluated_on="2026-07-24",
    )
    finance = next(row for row in report["decision_axes"] if row["key"] == "finance")
    cost_gap = next(row for row in report["data_gaps"] if row["key"] == "financial_inputs")

    assert finance["status"] == "insufficient"
    assert cost_gap["severity"] == "blocker"


def test_fingerprints_are_reproducible_and_note_content_is_evidence() -> None:
    first = _report()
    second = assemble_research_decision_report(
        _detail(),
        supplement=_supplement(),
        evaluated_on="2026-07-24",
        generated_at=datetime.fromisoformat("2026-07-24T22:00:00+08:00"),
    )
    changed_detail = deepcopy(_detail())
    changed_detail["notes"][0]["content"] += " 新增包装差异。"
    changed = assemble_research_decision_report(
        changed_detail,
        supplement=_supplement(),
        evaluated_on="2026-07-24",
    )

    assert first["generated_at"] != second["generated_at"]
    assert first["evidence_fingerprint"] == second["evidence_fingerprint"]
    assert first["report_fingerprint"] == second["report_fingerprint"]
    assert changed["evidence_fingerprint"] != first["evidence_fingerprint"]
    assert changed["report_fingerprint"] != first["report_fingerprint"]


def test_json_markdown_and_agent_compact_exports_keep_audit_identity() -> None:
    report = _report()
    json_bytes, json_name, json_type = export_research_decision_report(report, "json")
    md_bytes, md_name, md_type = export_research_decision_report(report, "markdown")
    compact = compact_research_decision_report(report)

    decoded = json.loads(json_bytes.decode("utf-8"))
    markdown = md_bytes.decode("utf-8")
    assert decoded["report_fingerprint"] == report["report_fingerprint"]
    assert json_name.endswith(".json") and "application/json" in json_type
    assert md_name.endswith(".md") and "text/markdown" in md_type
    assert "## 反对理由与风险" in markdown
    assert report["evidence_fingerprint"] in markdown
    assert "assets" not in compact
    assert compact["method_version"] == REPORT_METHOD_VERSION


def test_report_routes_keep_envelope_and_download_headers(monkeypatch) -> None:
    report = _report()

    class FakeRepository:
        def get_detail_readiness(self, project_id, *, stale_days=30, file_limit=100):
            assert (project_id, stale_days, file_limit) == (4, 45, 25)
            return {
                "project": {"id": project_id},
                "summary": {"level": "page_limited", "hold_total": 3},
                "policy": {"numeric_score": False, "changes_decision_gate": False},
            }

        def get_report(self, project_id, *, as_of=None):
            assert project_id == 4
            assert as_of == "2026-07-24"
            return report

        def export_report(self, project_id, export_format, *, as_of=None):
            assert (project_id, export_format, as_of) == (4, "markdown", "2026-07-24")
            content, filename, media_type = export_research_decision_report(report, export_format)
            return content, filename, media_type, report

    monkeypatch.setattr(report_router, "_repository", FakeRepository())
    readiness = client.get(
        "/api/research-projects/4/detail-readiness",
        params={"stale_days": 45, "file_limit": 25},
    )
    read = client.get("/api/research-projects/4/decision-report", params={"as_of": "2026-07-24"})
    exported = client.get(
        "/api/research-projects/4/decision-report/export",
        params={"format": "markdown", "as_of": "2026-07-24"},
    )

    assert readiness.status_code == 200
    assert readiness.json()["data"]["summary"]["level"] == "page_limited"
    assert readiness.json()["data"]["policy"]["changes_decision_gate"] is False
    assert read.status_code == 200
    assert read.json()["data"]["readiness"]["level"] == "decision_ready"
    assert exported.status_code == 200
    assert exported.headers["x-report-fingerprint"] == report["report_fingerprint"]
    assert "attachment;" in exported.headers["content-disposition"]
    assert exported.text.startswith("# 减压玩具验证")
