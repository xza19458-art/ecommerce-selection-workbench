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
        return [{"keyword": "test", "args": kwargs}]

    def get_review_insights(self, **kwargs):
        return [{"asin": "B000000003", "pain_points": ["packaging"], "args": kwargs}]

    def get_task_jobs(self, **kwargs):
        return [{"id": 1, "status": kwargs.get("status") or "done"}]


def test_readonly_tool_schema_has_eight_tools() -> None:
    tools = get_readonly_tool_definitions()
    assert len(tools) == 8
    assert {tool.name for tool in tools} == {
        "query_recommendations",
        "query_products",
        "query_product_detail",
        "query_product_trend",
        "query_keyword_opportunities",
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
    assert len(tools) == 12
    assert sum(1 for tool in tools if tool.requires_confirmation) == 4


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
        test_readonly_tool_schema_has_eight_tools,
        test_operation_tool_schema_requires_confirmation,
        test_agent_tool_schema_combines_readonly_and_operations,
        test_query_products_calls_controller_without_business_logic,
        test_query_product_trend_returns_json_safe_dataclass,
        test_unknown_tool_is_structured_error,
        test_trigger_collection_ignores_model_execute_flag,
    ]
    for test in tests:
        test()
    print(f"agent_tools tests passed: {len(tests)}/{len(tests)}")
