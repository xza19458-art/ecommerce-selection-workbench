from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import app as api_app
from services.agent_chat import AgentChatService, AgentConversationStore, CONTEXT_SYSTEM_PREFIX, SYSTEM_PROMPT
from services.agent_tools import AgentToolExecutor, AgentToolResult, get_agent_tool_definitions
from services.llm_provider import LLMResponse, ToolCall


class FakeController:
    def __init__(self) -> None:
        self.opened_amazon = False

    def get_product_pool(self, **kwargs):
        return [{"asin": "B000000002", "title": "Example Product", "filters": kwargs}]

    def get_top_recommendations(self, limit=50):
        return []

    def get_product_history(self, asin):
        return {"product": {"asin": asin}, "snapshots": []}

    def get_keyword_opportunities(self, **kwargs):
        return []

    def get_review_insights(self, **kwargs):
        return []

    def get_task_jobs(self, **kwargs):
        return []

    def open_amazon_page(self):
        self.opened_amazon = True
        return {"状态": "已打开", "URL": "https://www.amazon.com/", "message": "ok"}


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name, tool_input):
        payload = dict(tool_input or {})
        self.calls.append((name, payload))
        return AgentToolResult(name=name, input=payload, ok=True, data={"handled": name}, message="")


class MockProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_tool_count = 0
        self.saw_tool_result = False

    def chat(self, messages, tools):
        self.calls += 1
        self.seen_tool_count = len(tools)
        if self.calls == 1:
            return LLMResponse(
                reply="I will query products first.",
                tool_calls=(ToolCall(id="call_1", name="query_products", input={"limit": 2}),),
                finished=False,
            )
        self.saw_tool_result = any(
            message.get("role") == "tool" and "Example Product" in str(message.get("content"))
            for message in messages
        )
        return LLMResponse(reply="Found one example product.", finished=True)


class ContextProvider:
    def __init__(self) -> None:
        self.messages_seen: list[list[dict]] = []

    def chat(self, messages, tools):
        self.messages_seen.append([dict(message) for message in messages])
        return LLMResponse(reply="ok", finished=True)


class OperationProvider:
    def __init__(self, tool_name="create_keyword_tracking", tool_input=None) -> None:
        self.calls = 0
        self.tool_name = tool_name
        self.tool_input = {"keyword": "wipes"} if tool_input is None else tool_input
        self.seen_tool_count = 0
        self.tool_payloads: list[dict] = []
        self.messages_seen: list[list[dict]] = []

    def chat(self, messages, tools):
        self.calls += 1
        self.messages_seen.append([dict(message) for message in messages])
        self.seen_tool_count = len(tools)
        if self.calls == 1:
            return LLMResponse(
                reply="I need confirmation before the operation.",
                tool_calls=(ToolCall(id="op_1", name=self.tool_name, input=self.tool_input),),
                finished=False,
            )
        self.tool_payloads = [
            json.loads(str(message.get("content")))
            for message in messages
            if message.get("role") == "tool"
        ]
        return LLMResponse(reply="Operation flow finished.", finished=True)


def test_conversation_store_evicts_oldest_conversation() -> None:
    store = AgentConversationStore(max_conversations=2)
    first = store.get_or_create("first")
    store.get_or_create("second")
    store.get_or_create("third")

    assert len(store) == 2
    assert "first" not in store._items
    assert first.conversation_id == "first"


def test_conversation_store_compacts_old_history_at_user_boundary() -> None:
    store = AgentConversationStore(max_messages=8)
    conversation = store.get_or_create("long")
    for index in range(20):
        role = "user" if index % 2 == 0 else "assistant"
        conversation.messages.append({"role": role, "content": str(index)})

    store.compact(conversation)

    body = [message for message in conversation.messages if message.get("role") != "system"]
    assert len(body) <= 8
    assert body[0]["role"] == "user"
    assert conversation.messages[0]["content"] == SYSTEM_PROMPT


def test_agent_chat_executes_readonly_tool_then_returns_final_reply() -> None:
    provider = MockProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    result = service.chat(conversation_id=None, message="show products", confirm=None)

    assert provider.calls == 2
    assert provider.seen_tool_count == len(get_agent_tool_definitions())
    assert provider.saw_tool_result is True
    assert result["conversation_id"]
    assert result["reply"] == "Found one example product."
    assert result["pending_action"] is None
    assert result["tool_calls"][0]["name"] == "query_products"
    assert result["tool_calls"][0]["executed"] is True


def test_system_prompt_guides_overview_and_keyword_group_tools() -> None:
    assert "query_app_overview" in SYSTEM_PROMPT
    assert "query_detail_evidence_priorities" in SYSTEM_PROMPT
    assert "明确指定研究项目时必须传 project_id" in SYSTEM_PROMPT
    assert "页面未提供不等于 0" in SYSTEM_PROMPT
    assert "当前扫描范围未发现匹配证据" in SYSTEM_PROMPT
    assert "query_keyword_groups" in SYSTEM_PROMPT
    assert "query_keyword_ideas" in SYSTEM_PROMPT
    assert "query_research_review_queue" in SYSTEM_PROMPT
    assert "query_research_decision_report" in SYSTEM_PROMPT
    assert "detail_evidence_readiness" in SYSTEM_PROMPT
    assert "不改变决策门禁、综合评分或冻结报告指纹" in SYSTEM_PROMPT
    assert "query_tracking_evidence" in SYSTEM_PROMPT
    assert "支持、反对、数据缺口" in SYSTEM_PROMPT
    assert "Agent 不能保存或暂停观察计划" in SYSTEM_PROMPT
    assert "不代表后台自动调度" in SYSTEM_PROMPT
    assert "不是商品机会评分" in SYSTEM_PROMPT
    assert "一级关键词分组" in SYSTEM_PROMPT
    assert "当前应用上下文" in SYSTEM_PROMPT


def test_client_context_is_hidden_system_message_and_replaced() -> None:
    provider = ContextProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    first = service.chat(
        conversation_id=None,
        message="分析这个商品",
        confirm=None,
        client_context={"current_page": {"hash": "#/product/B0FIRST", "current_asin": "B0FIRST"}},
    )
    service.chat(
        conversation_id=first["conversation_id"],
        message="继续看当前关键词",
        confirm=None,
        client_context={"current_page": {"hash": "#/keywords", "keyword_filters": {"selected_keyword": "squishy"}}},
    )

    first_contexts = [
        message for message in provider.messages_seen[0]
        if str(message.get("content") or "").startswith(CONTEXT_SYSTEM_PREFIX)
    ]
    second_contexts = [
        message for message in provider.messages_seen[-1]
        if str(message.get("content") or "").startswith(CONTEXT_SYSTEM_PREFIX)
    ]
    assert len(first_contexts) == 1
    assert "B0FIRST" in first_contexts[0]["content"]
    assert len(second_contexts) == 1
    assert "squishy" in second_contexts[0]["content"]
    assert "B0FIRST" not in second_contexts[0]["content"]


def test_keyword_workshop_context_returns_safe_action_suggestions() -> None:
    provider = ContextProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    result = service.chat(
        conversation_id=None,
        message="这些候选下一步怎么处理",
        confirm=None,
        client_context={
            "current_page": {
                "hash": "#/keyword-workshop",
                "keyword_workshop_filters": {
                    "status": "candidate",
                    "selected_idea_ids": [11, "12", "bad", 11, 0],
                },
            }
        },
    )

    suggestions = result["action_suggestions"]
    assert [item["operation"] for item in suggestions] == ["promote", "create_tracking", "set_status"]
    assert suggestions[0]["ids"] == [11, 12]
    assert suggestions[0]["requires_confirmation"] is True
    assert suggestions[1]["target_snapshots"] == 3
    assert suggestions[2]["status"] == "ignored"


def test_product_pool_context_returns_navigation_suggestions() -> None:
    provider = ContextProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    result = service.chat(
        conversation_id=None,
        message="这几个商品怎么比较",
        confirm=None,
        client_context={
            "current_page": {
                "hash": "#/products",
                "product_filters": {
                    "selected_asins": ["B0ABCDEF12", "bad", "B0ABCDEF12", "B0ZZZZZZ99"],
                },
            }
        },
    )

    suggestions = result["action_suggestions"]
    assert [item["operation"] for item in suggestions] == ["compare", "view_detail"]
    assert suggestions[0]["asins"] == ["B0ABCDEF12", "B0ZZZZZZ99"]
    assert suggestions[0]["requires_confirmation"] is False
    assert suggestions[1]["asin"] == "B0ABCDEF12"


def test_tracking_context_returns_bounded_action_suggestions() -> None:
    provider = ContextProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    result = service.chat(
        conversation_id=None,
        message="这些追踪任务下一步怎么处理",
        confirm=None,
        client_context={
            "current_page": {
                "hash": "#/tracking",
                "tracking_filters": {
                    "selected_tasks": [
                        {"id": 7, "keyword": "stress ball", "status": "active"},
                        {"id": "8", "keyword": "squishy toy", "status": "paused"},
                        {"id": "bad", "status": "active"},
                        {"id": 7, "status": "active"},
                    ],
                },
            }
        },
    )

    suggestions = result["action_suggestions"]
    assert [item["operation"] for item in suggestions] == ["check", "set_status", "collect", "set_status"]
    assert suggestions[0]["task_ids"] == [7, 8]
    assert suggestions[0]["requires_confirmation"] is True
    assert "检查时间" in suggestions[0]["confirm_text"]
    assert suggestions[1]["status"] == "paused"
    assert suggestions[1]["task_ids"] == [7]
    assert suggestions[2]["task_id"] == 7
    assert suggestions[2]["requires_confirmation"] is True
    assert "联网" in suggestions[2]["risk"]
    assert suggestions[3]["status"] == "active"
    assert suggestions[3]["task_ids"] == [8]


def test_task_center_context_returns_diagnostic_navigation_suggestions() -> None:
    provider = ContextProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    result = service.chat(
        conversation_id=None,
        message="这些任务下一步怎么处理",
        confirm=None,
        client_context={
            "current_page": {
                "hash": "#/tasks",
                "task_filters": {
                    "selected_tasks": [
                        {
                            "row_id": "crawl-7",
                            "id": 7,
                            "type": "爬取",
                            "status": "异常停止",
                            "keyword": "stress ball",
                            "has_error": True,
                            "ingested_count": 0,
                        },
                        {
                            "row_id": "import-8",
                            "id": 8,
                            "type": "入库",
                            "status": "完成",
                            "keyword": "stress ball",
                            "has_error": False,
                            "ingested_count": 24,
                        },
                    ],
                },
            }
        },
    )

    suggestions = result["action_suggestions"]
    assert [item["operation"] for item in suggestions] == ["show_error", "navigate", "navigate", "navigate"]
    assert suggestions[0]["row_id"] == "crawl-7"
    assert suggestions[1]["route"] == "#/import"
    assert suggestions[2]["route"] == "#/crawl"
    assert suggestions[3]["route"] == "#/warehouse"
    assert all(item["requires_confirmation"] is False for item in suggestions)


def test_confirm_without_pending_action_returns_safe_message() -> None:
    provider = MockProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    result = service.chat(
        conversation_id=None,
        message=None,
        confirm={"tool_call_id": "x", "approved": True},
    )

    assert "Agent" in result["reply"]
    assert result["pending_action"] is None
    assert provider.calls == 0


def test_operation_tool_returns_pending_action_without_execution() -> None:
    provider = OperationProvider()
    executor = FakeExecutor()
    service = AgentChatService(
        provider,
        controller=FakeController(),
        store=AgentConversationStore(),
        executor=executor,
    )

    result = service.chat(conversation_id=None, message="create tracking", confirm=None)

    assert provider.calls == 1
    assert provider.seen_tool_count == len(get_agent_tool_definitions())
    assert executor.calls == []
    assert result["pending_action"] == {
        "tool": "create_keyword_tracking",
        "input": {"keyword": "wipes"},
        "tool_call_id": "op_1",
    }
    assert result["tool_calls"] == []


def test_open_amazon_page_is_confirmation_operation() -> None:
    provider = OperationProvider(tool_name="open_amazon_page", tool_input={})
    executor = FakeExecutor()
    service = AgentChatService(
        provider,
        controller=FakeController(),
        store=AgentConversationStore(),
        executor=executor,
    )

    result = service.chat(conversation_id=None, message="先打开 Amazon", confirm=None)

    assert provider.calls == 1
    assert executor.calls == []
    assert result["pending_action"] == {
        "tool": "open_amazon_page",
        "input": {},
        "tool_call_id": "op_1",
    }


def test_open_amazon_page_tool_uses_shared_controller() -> None:
    controller = FakeController()
    executor = AgentToolExecutor(controller=controller)

    result = executor.execute("open_amazon_page", {})

    assert result.ok is True
    assert controller.opened_amazon is True
    assert result.data["URL"] == "https://www.amazon.com/"


def test_trigger_collection_reuses_shared_controller() -> None:
    import services.keyword_tracking_scheduler as scheduler

    controller = FakeController()
    seen: dict = {}

    def fake_run_keyword_tracking_scheduler(**kwargs):
        seen.update(kwargs)
        return {"status": "完成", "message": "ok"}

    old_runner = scheduler.run_keyword_tracking_scheduler
    try:
        scheduler.run_keyword_tracking_scheduler = fake_run_keyword_tracking_scheduler
        executor = AgentToolExecutor(controller=controller)
        result = executor.execute("trigger_collection", {"task_id": 7})
    finally:
        scheduler.run_keyword_tracking_scheduler = old_runner

    assert result.ok is True
    assert seen["execute"] is True
    assert seen["task_id"] == 7
    assert seen["controller"] is controller


def test_normal_message_is_blocked_while_action_is_pending() -> None:
    provider = OperationProvider()
    executor = FakeExecutor()
    service = AgentChatService(
        provider,
        controller=FakeController(),
        store=AgentConversationStore(),
        executor=executor,
    )

    first = service.chat(conversation_id=None, message="create tracking", confirm=None)
    blocked = service.chat(conversation_id=first["conversation_id"], message="continue", confirm=None)
    confirmed = service.chat(
        conversation_id=first["conversation_id"],
        message=None,
        confirm={"tool_call_id": "op_1", "approved": True},
    )

    assert provider.calls == 2
    assert executor.calls == [("create_keyword_tracking", {"keyword": "wipes"})]
    assert blocked["pending_action"] == first["pending_action"]
    assert confirmed["pending_action"] is None
    sent_history = provider.messages_seen[-1]
    tool_call_index = next(index for index, item in enumerate(sent_history) if item.get("tool_calls"))
    assert sent_history[tool_call_index + 1]["role"] == "tool"


def test_confirm_approved_executes_pending_action_and_continues() -> None:
    provider = OperationProvider()
    executor = FakeExecutor()
    service = AgentChatService(
        provider,
        controller=FakeController(),
        store=AgentConversationStore(),
        executor=executor,
    )

    first = service.chat(conversation_id=None, message="create tracking", confirm=None)
    second = service.chat(
        conversation_id=first["conversation_id"],
        message=None,
        confirm={"tool_call_id": "op_1", "approved": True},
    )

    assert provider.calls == 2
    assert executor.calls == [("create_keyword_tracking", {"keyword": "wipes"})]
    assert second["reply"] == "Operation flow finished."
    assert second["pending_action"] is None
    assert second["tool_calls"][0]["executed"] is True
    assert second["tool_calls"][0]["ok"] is True
    assert provider.tool_payloads[-1]["ok"] is True


def test_confirm_cancel_does_not_execute_pending_action() -> None:
    provider = OperationProvider()
    executor = FakeExecutor()
    service = AgentChatService(
        provider,
        controller=FakeController(),
        store=AgentConversationStore(),
        executor=executor,
    )

    first = service.chat(conversation_id=None, message="create tracking", confirm=None)
    second = service.chat(
        conversation_id=first["conversation_id"],
        message=None,
        confirm={"tool_call_id": "op_1", "approved": False},
    )

    assert provider.calls == 2
    assert executor.calls == []
    assert second["pending_action"] is None
    assert second["tool_calls"][0]["executed"] is False
    assert second["tool_calls"][0]["ok"] is False
    assert provider.tool_payloads[-1]["ok"] is False


def test_api_agent_chat_uses_mock_provider_without_real_key() -> None:
    provider = MockProvider()
    old_builder = api_app._build_agent_provider
    old_store = api_app._agent_store
    old_controller = api_app._controller
    try:
        api_app._build_agent_provider = lambda: provider
        api_app._agent_store = AgentConversationStore()
        api_app._controller = FakeController()
        response = api_app.agent_chat(
            api_app.AgentChatIn(
                message="query products",
                client_context={"current_page": {"hash": "#/products"}},
            )
        )
    finally:
        api_app._build_agent_provider = old_builder
        api_app._agent_store = old_store
        api_app._controller = old_controller

    assert response["ok"] is True
    assert response["data"]["reply"] == "Found one example product."
    assert response["data"]["tool_calls"][0]["ok"] is True


def test_api_agent_suggestions_does_not_require_model_config() -> None:
    response = api_app.agent_suggestions(
        api_app.AgentSuggestionsIn(
            client_context={
                "current_page": {
                    "hash": "#/products",
                    "product_filters": {
                        "selected_asins": ["B0ABCDEF12", "B0ZZZZZZ99"],
                    },
                }
            }
        )
    )

    assert response["ok"] is True
    suggestions = response["data"]["action_suggestions"]
    assert suggestions[0]["operation"] == "compare"
    assert suggestions[0]["asins"] == ["B0ABCDEF12", "B0ZZZZZZ99"]


def test_api_agent_config_endpoints_are_thin_wrappers() -> None:
    old_get = api_app.get_public_agent_config
    old_save = api_app.save_agent_config
    old_test = api_app.test_agent_provider_config
    try:
        api_app.get_public_agent_config = lambda: {"provider": "openai_compatible", "api_key_configured": False}
        api_app.save_agent_config = lambda data: {"provider": data["provider"], "model": data["model"]}
        api_app.test_agent_provider_config = lambda data: {"provider": data["provider"], "reply": "OK"}

        current = api_app.agent_config_get()
        saved = api_app.agent_config_save(
            api_app.AgentConfigIn(
                provider="anthropic",
                base_url="https://api.anthropic.com/v1",
                api_key=None,
                model="claude-test",
            )
        )
        tested = api_app.agent_config_test(
            api_app.AgentConfigIn(
                provider="anthropic",
                base_url="https://api.anthropic.com/v1",
                api_key=None,
                model="claude-test",
            )
        )
    finally:
        api_app.get_public_agent_config = old_get
        api_app.save_agent_config = old_save
        api_app.test_agent_provider_config = old_test

    assert current["ok"] is True
    assert current["data"]["api_key_configured"] is False
    assert saved["data"] == {"provider": "anthropic", "model": "claude-test"}
    assert tested["data"] == {"provider": "anthropic", "reply": "OK"}


if __name__ == "__main__":
    tests = [
        test_agent_chat_executes_readonly_tool_then_returns_final_reply,
        test_system_prompt_guides_overview_and_keyword_group_tools,
        test_client_context_is_hidden_system_message_and_replaced,
        test_keyword_workshop_context_returns_safe_action_suggestions,
        test_product_pool_context_returns_navigation_suggestions,
        test_tracking_context_returns_bounded_action_suggestions,
        test_task_center_context_returns_diagnostic_navigation_suggestions,
        test_confirm_without_pending_action_returns_safe_message,
        test_operation_tool_returns_pending_action_without_execution,
        test_open_amazon_page_is_confirmation_operation,
        test_open_amazon_page_tool_uses_shared_controller,
        test_trigger_collection_reuses_shared_controller,
        test_normal_message_is_blocked_while_action_is_pending,
        test_confirm_approved_executes_pending_action_and_continues,
        test_confirm_cancel_does_not_execute_pending_action,
        test_api_agent_chat_uses_mock_provider_without_real_key,
        test_api_agent_suggestions_does_not_require_model_config,
        test_api_agent_config_endpoints_are_thin_wrappers,
    ]
    for test in tests:
        test()
    print(f"agent_chat tests passed: {len(tests)}/{len(tests)}")
