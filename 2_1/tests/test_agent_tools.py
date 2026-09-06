from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent_tools import (
    AgentToolExecutor,
    get_agent_tool_definitions,
    get_confirmation_tool_names,
    get_operation_tool_definitions,
    get_readonly_tool_definitions,
)


class FakeController:
    def get_top_recommendations(self, limit=50):
        return [{"asin": "B000000001", "score": 88.0, "limit": limit}]

    def get_product_pool(self, **kwargs):
        return [{"asin": "B000000002", "title": "Example", "filters": kwargs}]

    def get_product_history(self, asin):
        return {
            "product": {"asin": asin, "title": "Example"},
            "snapshots": [
                {"snapshot_at": "2026-06-01 10:00:00", "monthly_bought": 1000, "organic_rank": 30},
                {"snapshot_at": "2026-06-10 10:00:00", "monthly_bought": 1400, "organic_rank": 20},
            ],
        }

    def get_keyword_opportunities(self, **kwargs):
        return [
            {"keyword": "mini squishy", "opportunity_score": 72, "product_count": 12, "args": kwargs},
            {"keyword": "cow squishy", "opportunity_score": 68, "product_count": 8, "args": kwargs},
            {"keyword": "gift for man", "opportunity_score": 61, "product_count": 30, "args": kwargs},
            {"keyword": "man", "opportunity_score": 59, "product_count": 20, "args": kwargs},
        ]

    def get_review_insights(self, **kwargs):
        return [{"asin": "B000000003", "pain_points": ["packaging"], "args": kwargs}]

    def get_task_jobs(self, **kwargs):
        return [{"id": 1, "status": kwargs.get("status") or "done"}]

    def get_tracking_tasks(self, **kwargs):
        return [{"id": 7, "keyword": "squishy", "status": kwargs.get("status") or "active"}]


def test_readonly_tool_schema_has_twenty_one_tools() -> None:
    tools = get_readonly_tool_definitions()
    assert len(tools) == 21
    assert {tool.name for tool in tools} == {
        "query_app_overview",
        "query_recommendations",
        "query_products",
        "query_product_detail",
        "query_product_metrics",
        "query_detail_evidence_priorities",
        "query_product_trend",
        "query_keyword_opportunities",
        "query_keyword_groups",
        "query_keyword_ideas",
        "query_research_projects",
        "query_research_review_queue",
        "query_research_decision_report",
        "query_market_niches",
        "query_competitive_graph",
        "query_scoring_v2_replay",
        "query_scoring_v2_calibration",
        "query_review_insights",
        "query_tracking_tasks",
        "query_tracking_evidence",
        "query_tasks",
    }
    assert all(tool.requires_confirmation is False for tool in tools)
    detail_tool = next(tool for tool in tools if tool.name == "query_detail_evidence_priorities")
    assert detail_tool.parameters["properties"]["project_id"]["minimum"] == 1


def test_operation_tool_schema_requires_confirmation() -> None:
    tools = get_operation_tool_definitions()
    assert len(tools) == 4
    assert {tool.name for tool in tools} == {
        "open_amazon_page",
        "create_keyword_tracking",
        "set_keyword_tracking_status",
        "trigger_collection",
    }
    assert all(tool.requires_confirmation is True for tool in tools)
    assert get_confirmation_tool_names() == {tool.name for tool in tools}


def test_agent_tool_schema_combines_readonly_and_operations() -> None:
    tools = get_agent_tool_definitions()
    assert len(tools) == 25
    assert sum(1 for tool in tools if tool.requires_confirmation) == 4


def test_query_app_overview_returns_cross_module_snapshot() -> None:
    executor = AgentToolExecutor(controller=FakeController())
    result = executor.execute("query_app_overview", {"limit": 2})

    assert result.ok is True
    data = result.to_dict()["data"]
    assert data["推荐榜"]["ok"] is True
    assert data["关键词机会"]["ok"] is True
    assert data["关键词一级分组"]["data"][0]["primary"] == "squishy"
    assert data["追踪任务"]["data"][0]["id"] == 7
    assert "触发采集前" in data["使用建议"][-1]


def test_query_research_decision_report_is_readonly_and_compact(monkeypatch) -> None:
    from services import research_decision_report, research_detail_readiness

    calls = []
    readiness_calls = []

    def build(project_id, *, as_of=None):
        calls.append((project_id, as_of))
        return {
            "report_type": "research_project_decision_report",
            "schema_version": "1.0",
            "method_version": "research-decision-report-v1.0",
            "generated_at": "2026-07-24T18:00:00+08:00",
            "evaluated_on": "2026-07-24",
            "evidence_as_of": "2026-07-21 19:00:00",
            "evidence_fingerprint": "A" * 64,
            "report_fingerprint": "B" * 64,
            "project": {"id": project_id, "name": "减压玩具验证"},
            "readiness": {"level": "reviewable", "blocking_count": 2},
            "evidence_health": {"product_count": 3},
            "decision_axes": [{"key": "competition", "status": "risk"}],
            "supporting_arguments": [],
            "counter_arguments": [{"title": "竞争压力"}],
            "data_gaps": [{"key": "financial_inputs"}],
            "operational_due_diligence": [],
            "guardrails": {"human_decision_required": True},
            "assets": {"products": [{"asin": "B0TEST0001"}]},
        }

    def build_readiness(project_id):
        readiness_calls.append(project_id)
        return {
            "schema_version": "research-detail-readiness-v1",
            "method_version": "research-detail-readiness-v1",
            "generated_at": "2026-08-16 12:00:00",
            "project": {"id": project_id, "name": "减压玩具验证"},
            "summary": {
                "level": "action_required",
                "label": "存在可处理缺口",
                "product_total": 1,
                "ready_total": 0,
                "next_action": {"asin": "B0TEST0001", "code": "collect_gap"},
            },
            "rows": [
                {
                    "product_id": 1,
                    "asin": "B0TEST0001",
                    "title": "Example",
                    "role": "candidate",
                    "evidence_status": "partial",
                    "evidence_status_label": "字段不全",
                    "detail_collected_at": "2026-08-10 09:00:00",
                    "detail_age_days": 6,
                    "evidence_collected": 2,
                    "evidence_total": 3,
                    "missing_fields": [{"key": "date_first_available", "label": "首次可售日期"}],
                    "reasons": ["缺少首次可售日期"],
                    "has_local_evidence": True,
                    "local_evidence_status": "page_missing",
                    "recommended_action": {
                        "code": "page_missing",
                        "label": "页面未提供",
                        "kind": "hold",
                    },
                    "path": "C:/private/details/B0TEST0001.html",
                }
            ],
            "local_scan": {
                "checked": True,
                "complete": True,
                "file_count": 10,
                "file_limit": 100,
                "meaning": "已检查当前扫描范围。",
                "cache": {"private_path": "C:/private/cache.json"},
            },
            "policy": {
                "numeric_score": False,
                "changes_decision_gate": False,
                "changes_report_fingerprint": False,
            },
            "routes": {"product": "#/product/B0TEST0001"},
        }

    monkeypatch.setattr(research_decision_report, "build_research_decision_report", build)
    monkeypatch.setattr(
        research_detail_readiness,
        "get_research_project_detail_readiness",
        build_readiness,
    )
    executor = AgentToolExecutor(controller=FakeController())
    result = executor.execute(
        "query_research_decision_report",
        {"project_id": 4, "as_of": "2026-07-24"},
    )

    assert result.ok is True
    assert calls == [(4, "2026-07-24")]
    assert readiness_calls == [4]
    data = result.to_dict()["data"]
    assert data["readiness"]["level"] == "reviewable"
    assert data["guardrails"]["human_decision_required"] is True
    assert "assets" not in data
    assert data["detail_evidence_readiness"]["summary"]["level"] == "action_required"
    assert data["detail_evidence_readiness"]["policy"]["changes_decision_gate"] is False
    encoded = json.dumps(data["detail_evidence_readiness"], ensure_ascii=False)
    assert "C:/private" not in encoded
    assert '"path"' not in encoded
    assert "routes" not in data["detail_evidence_readiness"]


def test_query_research_review_queue_is_readonly_and_compact(monkeypatch) -> None:
    from services import research_review_queue

    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return {
            "rows": [
                {
                    "project": {"id": 4, "name": "减压玩具验证", "status": "collecting"},
                    "attention_group": "action_required",
                    "primary_status": {"code": "trend_tracking_missing", "label": "趋势未达标"},
                    "readiness": {"level": "reviewable"},
                    "freshness": {"status": "current"},
                    "timeline": {"best_points": 2, "best_span_days": 1},
                    "tracking": {"missing": 1, "recommended_keyword": "squishy"},
                    "report_version": {"evidence_changed": False, "time_only_change": True},
                    "monitoring_plan": {"state": "due", "is_due": True},
                    "next_review_on": "2026-07-27",
                    "next_review_reason": "本次打开应用时复核。",
                }
            ],
            "total": 1,
            "evaluated_on": "2026-07-27",
            "summary": {"action_required": 1},
            "policy": {"read_only": True, "automatic_collection": False},
            "warnings": [],
        }

    monkeypatch.setattr(research_review_queue, "fetch_research_review_queue", fetch)
    executor = AgentToolExecutor(controller=FakeController())
    result = executor.execute(
        "query_research_review_queue",
        {
            "limit": 5,
            "attention": "action_required",
            "monitoring": "due",
            "as_of": "2026-07-27",
        },
    )

    assert result.ok is True
    assert calls == [
        {
            "limit": 5,
            "offset": 0,
            "marketplace": "US",
            "status": None,
            "keyword": None,
            "attention": "action_required",
            "monitoring": "due",
            "as_of": "2026-07-27",
        }
    ]
    data = result.to_dict()["data"]
    assert data["rows"][0]["tracking"]["recommended_keyword"] == "squishy"
    assert data["rows"][0]["monitoring_plan"]["is_due"] is True
    assert data["policy"]["automatic_collection"] is False


def test_query_tracking_evidence_is_readonly_compact_and_path_safe(monkeypatch) -> None:
    from services import tracking_evidence

    calls = []

    def build(task_id, *, movement_limit=20):
        calls.append((task_id, movement_limit))
        return {
            "schema_version": "tracking-evidence-v1.1",
            "generated_at": "2026-08-02 10:00:00",
            "task": {
                "id": task_id,
                "marketplace": "US",
                "keyword": "squishy",
                "status": "active",
                "current_snapshots": 4,
                "target_snapshots": 6,
                "pages_per_keyword": 1,
                "last_collected_at": "2026-07-31 22:00:00",
                "error_message": "C:/private/task-error.log",
                "progress_note": "原始进度不等于趋势合格点。",
            },
            "schedule": {
                "state": "waiting",
                "due": False,
                "next_collectible_at": "2026-08-03 22:00:00",
                "minimum_interval_hours": 72,
            },
            "trend_gate": {
                "raw_points": 4,
                "qualified_points": 4,
                "span_days": 11,
                "preliminary": {"ready": False, "remaining_days": 3},
                "stable": {"ready": False, "remaining_points": 2, "remaining_days": 19},
                "scope_note": "只使用合格独立时点。",
            },
            "timepoints": [
                {
                    "snapshot_at": "2026-07-31 22:00:00",
                    "qualification": "qualified",
                    "product_count": 46,
                }
            ],
            "adjacent_comparison": {
                "available": True,
                "retained_product_count": 34,
                "entered_product_count": 12,
                "exited_product_count": 12,
                "confidence_note": "未观察到不等于下架。",
                "entered": [{"asin": "B0ENTER001", "current_rank": 9, "product_url": "https://example.test"}],
                "exited": [],
                "movements": [],
            },
            "research_watch": {
                "project_count": 1,
                "product_count": 1,
                "projects": [{"project_id": 4, "name": "减压玩具验证"}],
                "products": [
                    {
                        "product_id": 1,
                        "asin": "B0TEST0001",
                        "projects": [{"role": "candidate", "role_label": "候选商品"}],
                        "current_observed": False,
                        "history": [],
                        "interpretation": "未观察到不等于下架。",
                        "detail_route": "#/product/B0TEST0001",
                    }
                ],
            },
            "local_evidence": {
                "manifests": [{"path": "C:/private/manifest.json", "readable": True}],
                "files": [
                    {
                        "path": "C:/private/squishy.html",
                        "name": "squishy.html",
                        "exists": True,
                        "sha256": "A" * 64,
                    }
                ],
                "jobs": [
                    {
                        "id": 158,
                        "status": "done",
                        "url": "local_html_import:C:/private/squishy.html",
                        "error_message": "C:/private/job-error.log",
                    }
                ],
                "note": "本地证据摘要。",
            },
            "boundaries": ["本批未观察到不等于下架。"],
        }

    monkeypatch.setattr(tracking_evidence, "build_tracking_task_evidence", build)
    result = AgentToolExecutor(controller=FakeController()).execute(
        "query_tracking_evidence",
        {"task_id": 15, "movement_limit": 3},
    )

    assert result.ok is True
    assert calls == [(15, 3)]
    data = result.to_dict()["data"]
    assert data["task"]["id"] == 15
    assert data["trend"]["qualified_points"] == 4
    assert data["adjacent_comparison"]["retained_product_count"] == 34
    assert data["local_evidence"]["existing_file_count"] == 1
    assert "path" not in data["local_evidence"]["files"][0]
    assert "url" not in data["local_evidence"]["jobs"][0]
    assert "error_message" not in data["local_evidence"]["jobs"][0]
    assert "product_url" not in data["adjacent_comparison"]["entered"][0]
    assert data["task"]["has_error"] is True
    assert data["local_evidence"]["jobs"][0]["has_error"] is True
    assert "private" not in str(data)
    assert data["policy"] == {
        "read_only": True,
        "refreshes_task_state": False,
        "automatic_collection": False,
        "writes_database": False,
    }


def test_query_detail_evidence_priorities_is_readonly_and_path_safe(monkeypatch) -> None:
    from services import detail_reparse

    calls = []

    def fake_list(**kwargs):
        calls.append(kwargs)
        return {
            "generated_at": "2026-08-13 10:00:00",
            "stale_days": 30,
            "summary": {"gap_total": 10},
            "gaps": {
                "priority_filter": "focus",
                "priority_filter_label": "项目重点（候选/对标）",
                "project_filter": {
                    "active": True,
                    "project_id": 4,
                    "project_name": "减压玩具验证",
                },
                "priority_summary": {"project_focus_total": 1},
                "priority_policy": {
                    "method_version": "detail-evidence-priority-v1.1",
                    "numeric_score": False,
                },
                "disposition_summary": {"scope": "current_page", "hold_total": 1},
                "disposition_policy": {
                    "method_version": "detail-evidence-disposition-v1",
                    "missing_is_zero": False,
                },
                "rows": [
                    {
                        "asin": "B0TEST0001",
                        "marketplace": "US",
                        "title": "Candidate",
                        "title_zh": None,
                        "evidence_status": "partial",
                        "reasons": ["缺商品类别"],
                        "detail_collected_at": "2026-08-10 09:00:00",
                        "detail_source_file": "C:/private/detail.html",
                        "local_file_path": "C:/private/local.html",
                        "last_seen_at": "2026-08-12 09:00:00",
                        "priority_tier": "project_candidate",
                        "priority_label": "项目候选",
                        "priority_reasons": ["进行中研究项目的候选商品"],
                        "recommended_action": {
                            "code": "page_missing",
                            "label": "页面未提供，保留缺失",
                            "kind": "hold",
                            "reason": "页面没有出现当前缺失字段，重复采集未必有效。",
                            "follow_up": "页面未提供不等于 0。",
                            "primary_command": "preview_local",
                            "accesses_amazon": False,
                            "writes_database": False,
                            "user_confirmation_required": False,
                            "automatic": False,
                            "local_evidence_checked": True,
                            "local_evidence_scan_complete": True,
                        },
                        "research_context": [
                            {
                                "project_id": 4,
                                "project_name": "减压玩具验证",
                                "project_status_label": "收集证据",
                                "role_label": "候选商品",
                                "plan_active": True,
                                "plan_due": False,
                                "next_review_on": "2026-08-20",
                            }
                        ],
                        "monitoring": {"active_plan_count": 1, "due_plan_count": 0},
                    }
                ],
            },
        }

    monkeypatch.setattr(detail_reparse, "list_detail_reparse_candidates", fake_list)
    result = AgentToolExecutor(controller=FakeController()).execute(
        "query_detail_evidence_priorities",
        {"limit": 5, "priority": "focus", "stale_days": 30, "project_id": 4},
    )

    assert result.ok is True
    assert calls == [
        {
            "limit": 5,
            "file_limit": detail_reparse.MAX_SCAN_FILES,
            "stale_days": 30,
            "priority": "focus",
            "project_id": 4,
        }
    ]
    data = result.to_dict()["data"]
    assert data["rows"][0]["priority_label"] == "项目候选"
    assert data["project_filter"]["project_id"] == 4
    assert data["rows"][0]["research_projects"][0]["role_label"] == "候选商品"
    assert data["rows"][0]["recommended_action"]["code"] == "page_missing"
    assert data["rows"][0]["recommended_action"]["requires_amazon"] is False
    assert data["rows"][0]["recommended_action"]["local_evidence_checked"] is True
    assert data["rows"][0]["recommended_action"]["local_evidence_scan_complete"] is True
    assert "primary_command" not in data["rows"][0]["recommended_action"]
    assert "detail_source_file" not in data["rows"][0]
    assert "local_file_path" not in data["rows"][0]
    assert "private" not in str(data)
    assert data["policy"]["read_only"] is True
    assert data["policy"]["writes_database"] is False
    assert data["policy"]["automatic_collection"] is False
    assert data["disposition_policy"]["missing_is_zero"] is False


def test_query_keyword_groups_groups_by_tail_and_shared_words() -> None:
    executor = AgentToolExecutor(controller=FakeController())

    tail = executor.execute("query_keyword_groups", {"mode": "tail", "max_groups": 3})
    shared = executor.execute("query_keyword_groups", {"mode": "shared", "max_groups": 3})

    assert tail.ok is True
    tail_groups = tail.to_dict()["data"]["groups"]
    squishy = next(group for group in tail_groups if group["primary"] == "squishy")
    assert squishy["keyword_count"] == 2
    assert squishy["avg_opportunity_score"] == 70.0
    assert squishy["level"] == "蓝海赛道"

    assert shared.ok is True
    shared_groups = shared.to_dict()["data"]["groups"]
    assert any(group["primary"] == "squishy" for group in shared_groups)


def test_query_products_calls_controller_without_business_logic() -> None:
    executor = AgentToolExecutor(controller=FakeController())
    result = executor.execute(
        "query_products",
        {
            "limit": 2,
            "keyword": "wipes",
            "keyword_exact": True,
            "keyword_scope": "observed",
            "min_score": 70,
            "max_score": 95,
            "min_rating": 4.2,
            "max_reviews": 500,
            "min_bought": 100,
            "max_rank": 50,
            "deal_status": "regular",
            "size_status": "known",
        },
    )

    assert result.ok is True
    data = result.to_dict()["data"]
    assert data[0]["filters"]["limit"] == 2
    assert data[0]["filters"]["keyword"] == "wipes"
    assert data[0]["filters"]["keyword_exact"] is True
    assert data[0]["filters"]["keyword_scope"] == "observed"
    assert data[0]["filters"]["min_score"] == 70.0
    assert data[0]["filters"]["max_score"] == 95.0
    assert data[0]["filters"]["min_rating"] == 4.2
    assert data[0]["filters"]["max_reviews"] == 500
    assert data[0]["filters"]["min_bought"] == 100
    assert data[0]["filters"]["max_rank"] == 50
    assert data[0]["filters"]["deal_status"] == "regular"
    assert data[0]["filters"]["size_status"] == "known"


def test_query_product_trend_returns_json_safe_dataclass() -> None:
    executor = AgentToolExecutor(controller=FakeController())
    result = executor.execute("query_product_trend", {"asin": "B000000002"})

    assert result.ok is True
    data = result.to_dict()["data"]
    assert data["sample_size"] == 2
    assert data["growth_score"] > 50.0


def test_query_scoring_v2_replay_returns_compact_readonly_evidence() -> None:
    from services import scoring_v2

    old_fetch = scoring_v2.fetch_scoring_v2_replay

    def fake_fetch(**kwargs):
        return {
            "rows": [
                {
                    "asin": "B000000009",
                    "title": "Example",
                    "keyword": "squishy",
                    "snapshot_at": "2026-07-13 12:00:00",
                    "opportunity_score": 72,
                    "risk_score": 61,
                    "confidence_score": 80,
                    "confidence_level": "高",
                    "recommendation_label": "仅适合作为对标",
                    "recommendation_reason": "高机会与高风险并存",
                    "supporting_reasons": ["需求强"],
                    "opposing_reasons": ["评论壁垒高"],
                    "opportunity_components": [{"key": "demand"}],
                    "trend": {"sample_size": 5, "span_days": 14, "confidence": "中", "growth_score": 60},
                    "source_identity": {
                        "model_version": "selection-score-v2-shadow-2026.07",
                        "strategy": kwargs["strategy"],
                        "rank_snapshot_id": 9,
                        "product_snapshot_id": 10,
                    },
                }
            ],
            "total": 1,
            "summary": {"context_count": 1},
            "strategy": {"code": kwargs["strategy"]},
            "model": {"version": "selection-score-v2-shadow-2026.07", "status": "shadow_readonly", "boundaries": ["只读"]},
        }

    scoring_v2.fetch_scoring_v2_replay = fake_fetch
    try:
        result = AgentToolExecutor(controller=FakeController()).execute(
            "query_scoring_v2_replay",
            {"strategy": "differentiation", "limit": 3},
        )
    finally:
        scoring_v2.fetch_scoring_v2_replay = old_fetch

    assert result.ok is True
    data = result.to_dict()["data"]
    assert data["rows"][0]["source"]["strategy"] == "differentiation"
    assert "opportunity_components" not in data["rows"][0]
    assert data["model"]["status"] == "shadow_readonly"


def test_query_scoring_v2_calibration_returns_audit_not_manual_labels() -> None:
    from services import scoring_v2

    old_fetch = scoring_v2.fetch_scoring_v2_calibration

    def fake_fetch(**kwargs):
        return {
            "rows": [
                {
                    "sample_key": "model:balanced:7:9",
                    "sample_bucket_label": "可进入观察池 · 高置信",
                    "asin": "B000000009",
                    "title": "Example",
                    "keyword": "squishy",
                    "opportunity_score": 72,
                    "risk_score": 45,
                    "confidence_score": 80,
                    "recommendation_label": "可进入观察池",
                    "strategy_outcomes": {"balanced": {"recommendation_code": "observe"}},
                    "calibration_flags": [
                        {"code": "threshold_boundary", "label": "接近建议阈值", "reason": "需要人工复核"}
                    ],
                    "source_identity": {
                        "model_version": "selection-score-v2-shadow-2026.07",
                        "rank_snapshot_id": 7,
                        "product_snapshot_id": 9,
                    },
                }
            ],
            "summary": {"sample_count": 1},
            "strategy": {"code": kwargs["strategy"]},
            "calibration": {"status": "shadow_readonly_manual_review", "writes_database": False},
            "model": {"version": "selection-score-v2-shadow-2026.07", "status": "shadow_readonly", "boundaries": ["只读"]},
        }

    scoring_v2.fetch_scoring_v2_calibration = fake_fetch
    try:
        result = AgentToolExecutor(controller=FakeController()).execute(
            "query_scoring_v2_calibration",
            {"strategy": "balanced", "sample_per_bucket": 1},
        )
    finally:
        scoring_v2.fetch_scoring_v2_calibration = old_fetch

    assert result.ok is True
    data = result.to_dict()["data"]
    assert data["rows"][0]["audit_flags"][0]["code"] == "threshold_boundary"
    assert data["calibration"]["writes_database"] is False
    assert "judgment" not in data["rows"][0]


def test_unknown_tool_is_structured_error() -> None:
    executor = AgentToolExecutor(controller=FakeController())
    result = executor.execute("delete_tracking_task", {"task_id": 1})

    assert result.ok is False
    assert result.to_dict()["message"]


def test_trigger_collection_ignores_model_execute_flag() -> None:
    from services import keyword_tracking_scheduler

    captured = {}
    old_runner = keyword_tracking_scheduler.run_keyword_tracking_scheduler

    def fake_runner(*, execute, task_id=None, controller=None):
        captured["execute"] = execute
        captured["task_id"] = task_id
        captured["controller"] = controller
        return {"executed": execute, "task_id": task_id}

    keyword_tracking_scheduler.run_keyword_tracking_scheduler = fake_runner
    try:
        executor = AgentToolExecutor(controller=FakeController())
        result = executor.execute("trigger_collection", {"task_id": 7, "execute": False})
    finally:
        keyword_tracking_scheduler.run_keyword_tracking_scheduler = old_runner

    assert result.ok is True
    assert captured["execute"] is True
    assert captured["task_id"] == 7
    assert captured["controller"] is executor.controller


if __name__ == "__main__":
    tests = [
        test_readonly_tool_schema_has_twenty_one_tools,
        test_operation_tool_schema_requires_confirmation,
        test_agent_tool_schema_combines_readonly_and_operations,
        test_query_app_overview_returns_cross_module_snapshot,
        test_query_keyword_groups_groups_by_tail_and_shared_words,
        test_query_products_calls_controller_without_business_logic,
        test_query_product_trend_returns_json_safe_dataclass,
        test_query_detail_evidence_priorities_is_readonly_and_path_safe,
        test_query_scoring_v2_replay_returns_compact_readonly_evidence,
        test_query_scoring_v2_calibration_returns_audit_not_manual_labels,
        test_unknown_tool_is_structured_error,
        test_trigger_collection_ignores_model_execute_flag,
    ]
    for test in tests:
        test()
    print(f"agent_tools tests passed: {len(tests)}/{len(tests)}")
