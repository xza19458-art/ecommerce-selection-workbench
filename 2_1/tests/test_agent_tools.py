from __future__ import annotations

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


def test_readonly_tool_schema_has_eleven_tools() -> None:
    tools = get_readonly_tool_definitions()
    assert len(tools) == 11
    assert {tool.name for tool in tools} == {
        "query_app_overview",
        "query_recommendations",
        "query_products",
        "query_product_detail",
        "query_product_trend",
        "query_keyword_opportunities",
        "query_keyword_groups",
        "query_keyword_ideas",
        "query_review_insights",
        "query_tracking_tasks",
        "query_tasks",
    }
    assert all(tool.requires_confirmation is False for tool in tools)


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
    assert len(tools) == 15
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
    result = executor.execute("query_products", {"limit": 2, "keyword": "wipes", "min_score": 70})

    assert result.ok is True
    data = result.to_dict()["data"]
    assert data[0]["filters"]["limit"] == 2
    assert data[0]["filters"]["keyword"] == "wipes"
    assert data[0]["filters"]["min_score"] == 70.0


def test_query_product_trend_returns_json_safe_dataclass() -> None:
    executor = AgentToolExecutor(controller=FakeController())
    result = executor.execute("query_product_trend", {"asin": "B000000002"})

    assert result.ok is True
    data = result.to_dict()["data"]
    assert data["sample_size"] == 2
    assert data["growth_score"] > 50.0


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
        test_readonly_tool_schema_has_eleven_tools,
        test_operation_tool_schema_requires_confirmation,
        test_agent_tool_schema_combines_readonly_and_operations,
        test_query_app_overview_returns_cross_module_snapshot,
        test_query_keyword_groups_groups_by_tail_and_shared_words,
        test_query_products_calls_controller_without_business_logic,
        test_query_product_trend_returns_json_safe_dataclass,
        test_unknown_tool_is_structured_error,
        test_trigger_collection_ignores_model_execute_flag,
    ]
    for test in tests:
        test()
    print(f"agent_tools tests passed: {len(tests)}/{len(tests)}")
