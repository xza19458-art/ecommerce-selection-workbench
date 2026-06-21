from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import app as api_app
from services.agent_chat import AgentChatService, AgentConversationStore
from services.agent_tools import AgentToolResult
from services.llm_provider import LLMResponse, ToolCall


class FakeController:
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


class OperationProvider:
    def __init__(self, tool_name="create_keyword_tracking", tool_input=None) -> None:
        self.calls = 0
        self.tool_name = tool_name
        self.tool_input = tool_input or {"keyword": "wipes"}
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


def test_agent_chat_executes_readonly_tool_then_returns_final_reply() -> None:
    provider = MockProvider()
    service = AgentChatService(provider, controller=FakeController(), store=AgentConversationStore())

    result = service.chat(conversation_id=None, message="show products", confirm=None)

    assert provider.calls == 2
    assert provider.seen_tool_count == 11
    assert provider.saw_tool_result is True
    assert result["conversation_id"]
    assert result["reply"] == "Found one example product."
    assert result["pending_action"] is None
    assert result["tool_calls"][0]["name"] == "query_products"
    assert result["tool_calls"][0]["executed"] is True


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
    assert provider.seen_tool_count == 11
    assert executor.calls == []
    assert result["pending_action"] == {
        "tool": "create_keyword_tracking",
        "input": {"keyword": "wipes"},
        "tool_call_id": "op_1",
    }
    assert result["tool_calls"] == []


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
        response = api_app.agent_chat(api_app.AgentChatIn(message="query products"))
    finally:
        api_app._build_agent_provider = old_builder
        api_app._agent_store = old_store
        api_app._controller = old_controller

    assert response["ok"] is True
    assert response["data"]["reply"] == "Found one example product."
    assert response["data"]["tool_calls"][0]["ok"] is True


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
        test_confirm_without_pending_action_returns_safe_message,
        test_operation_tool_returns_pending_action_without_execution,
        test_normal_message_is_blocked_while_action_is_pending,
        test_confirm_approved_executes_pending_action_and_continues,
        test_confirm_cancel_does_not_execute_pending_action,
        test_api_agent_chat_uses_mock_provider_without_real_key,
        test_api_agent_config_endpoints_are_thin_wrappers,
    ]
    for test in tests:
        test()
    print(f"agent_chat tests passed: {len(tests)}/{len(tests)}")
