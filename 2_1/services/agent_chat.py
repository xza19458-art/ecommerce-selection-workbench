"""Conversation loop for the in-app Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from threading import RLock
from typing import Any
from uuid import uuid4

from core.controller import AppController
from services.agent_tools import AgentToolExecutor, get_agent_tool_schemas, get_confirmation_tool_names
from services.llm_provider import LLMProvider, LLMProviderError, ToolCall


SYSTEM_PROMPT = """你是本地亚马逊选品分析系统的内置 Agent。
你面向中文用户，必须用中文回答。
你可以调用只读工具查询推荐榜、商品池、商品详情、趋势、关键词机会、评论洞察、追踪任务和任务中心。
你也可以提出创建关键词追踪、修改追踪状态、触发采集，但这些操作必须先获得用户二次确认。
不要编造数据库里没有的数据；工具返回样本不足时要如实说明。
触发采集会联网打开浏览器访问 Amazon，必须提醒用户有采集边界和耗时风险。"""


@dataclass
class AgentConversation:
    conversation_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    lock: RLock = field(default_factory=RLock)
    pending_action: dict[str, Any] | None = None


class AgentConversationStore:
    """Small in-memory store; persistence is intentionally left out of M1."""

    def __init__(self) -> None:
        self._items: dict[str, AgentConversation] = {}
        self._lock = RLock()

    def get_or_create(self, conversation_id: str | None) -> AgentConversation:
        with self._lock:
            if conversation_id and conversation_id in self._items:
                return self._items[conversation_id]
            next_id = conversation_id or uuid4().hex
            conversation = AgentConversation(
                conversation_id=next_id,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}],
            )
            self._items[next_id] = conversation
            return conversation


class AgentChatService:
    """Runs the M1 read-only Agent loop."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        controller: AppController | None = None,
        store: AgentConversationStore | None = None,
        executor: AgentToolExecutor | None = None,
        max_tool_rounds: int = 4,
    ) -> None:
        self.provider = provider
        self.store = store or AgentConversationStore()
        self.tools = get_agent_tool_schemas()
        self.confirmation_tool_names = get_confirmation_tool_names()
        self.executor = executor or AgentToolExecutor(controller=controller)
        self.max_tool_rounds = max(1, max_tool_rounds)

    def chat(
        self,
        *,
        conversation_id: str | None,
        message: str | None,
        confirm: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation = self.store.get_or_create(conversation_id)
        with conversation.lock:
            return self._chat_locked(conversation, message=message, confirm=confirm)

    def _chat_locked(
        self,
        conversation: AgentConversation,
        *,
        message: str | None,
        confirm: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if confirm is not None:
            return self._handle_confirmation(conversation, confirm)

        if conversation.pending_action:
            reply = "请先确认或取消当前待执行操作。"
            return self._response(conversation, reply, [], pending_action=conversation.pending_action)

        text = (message or "").strip()
        if text:
            conversation.messages.append({"role": "user", "content": text})
        elif len(conversation.messages) <= 1:
            reply = "请告诉我你想分析的商品、关键词或选品问题。"
            conversation.messages.append({"role": "assistant", "content": reply})
            return self._response(conversation, reply, [], pending_action=None)

        executed_calls: list[dict[str, Any]] = []
        return self._run_provider_loop(conversation, executed_calls)

    def _handle_confirmation(
        self,
        conversation: AgentConversation,
        confirm: dict[str, Any],
    ) -> dict[str, Any]:
        pending = conversation.pending_action
        if not pending:
            reply = "当前没有等待确认的 Agent 操作。"
            conversation.messages.append({"role": "assistant", "content": reply})
            return self._response(conversation, reply, [], pending_action=None)

        tool_call_id = str(confirm.get("tool_call_id") or "")
        if tool_call_id != str(pending.get("tool_call_id") or ""):
            reply = "确认信息与当前待确认操作不匹配，已拒绝执行。"
            return self._response(conversation, reply, [], pending_action=pending)

        approved = bool(confirm.get("approved"))
        tool_name = str(pending.get("tool") or "")
        tool_input = dict(pending.get("input") or {})
        conversation.pending_action = None
        if approved:
            result = self.executor.execute(tool_name, tool_input)
            payload = result.to_dict()
            executed_calls = [
                {
                    "id": tool_call_id,
                    "name": tool_name,
                    "input": tool_input,
                    "executed": True,
                    "ok": result.ok,
                    "message": result.message,
                }
            ]
        else:
            payload = {
                "name": tool_name,
                "input": tool_input,
                "ok": False,
                "data": None,
                "message": "用户取消执行。",
            }
            executed_calls = [
                {
                    "id": tool_call_id,
                    "name": tool_name,
                    "input": tool_input,
                    "executed": False,
                    "ok": False,
                    "message": "用户取消执行。",
                }
            ]

        conversation.messages.append(_tool_result_message(tool_call_id, payload))
        return self._run_provider_loop(conversation, executed_calls)

    def _run_provider_loop(
        self,
        conversation: AgentConversation,
        executed_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        for _round in range(self.max_tool_rounds):
            provider_response = self.provider.chat(conversation.messages, self.tools)
            if not provider_response.tool_calls:
                reply = provider_response.reply or "我暂时没有得到可用回复。"
                conversation.messages.append({"role": "assistant", "content": reply})
                return self._response(conversation, reply, executed_calls, pending_action=None)

            pending_call = self._first_confirmation_call(provider_response.tool_calls)
            if pending_call:
                conversation.messages.append(_assistant_tool_call_message(provider_response.reply, (pending_call,)))
                pending_action = {
                    "tool": pending_call.name,
                    "input": pending_call.input,
                    "tool_call_id": pending_call.id,
                }
                conversation.pending_action = pending_action
                reply = provider_response.reply or _confirmation_reply(pending_call.name, pending_call.input)
                return self._response(conversation, reply, executed_calls, pending_action=pending_action)

            conversation.messages.append(_assistant_tool_call_message(provider_response.reply, provider_response.tool_calls))
            for tool_call in provider_response.tool_calls:
                result = self.executor.execute(tool_call.name, tool_call.input)
                payload = result.to_dict()
                executed_calls.append(
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": tool_call.input,
                        "executed": True,
                        "ok": result.ok,
                        "message": result.message,
                    }
                )
                conversation.messages.append(_tool_result_message(tool_call.id, payload))

        reply = "工具查询轮次已达上限，我已停止继续调用工具。请缩小问题范围后再试。"
        conversation.messages.append({"role": "assistant", "content": reply})
        return self._response(conversation, reply, executed_calls, pending_action=None)

    def _first_confirmation_call(self, tool_calls: tuple[ToolCall, ...]) -> ToolCall | None:
        for tool_call in tool_calls:
            if tool_call.name in self.confirmation_tool_names:
                return tool_call
        return None

    def _response(
        self,
        conversation: AgentConversation,
        reply: str,
        tool_calls: list[dict[str, Any]],
        *,
        pending_action: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "conversation_id": conversation.conversation_id,
            "reply": reply,
            "tool_calls": tool_calls,
            "pending_action": pending_action,
        }


def _assistant_tool_call_message(reply: str, tool_calls: tuple[ToolCall, ...]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": reply or "",
        "tool_calls": [call.to_openai_message_tool_call() for call in tool_calls],
    }


def _tool_result_message(tool_call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _confirmation_reply(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "create_keyword_tracking":
        return f"我准备创建关键词追踪任务：{tool_input.get('keyword') or '未指定关键词'}。是否执行？"
    if tool_name == "set_keyword_tracking_status":
        return f"我准备把追踪任务 {tool_input.get('task_id')} 改为 {tool_input.get('status')}。是否执行？"
    if tool_name == "trigger_collection":
        target = f"任务 {tool_input.get('task_id')}" if tool_input.get("task_id") else "所有到期任务"
        return f"我准备触发{target}的联网采集。该操作会打开浏览器访问 Amazon，是否执行？"
    return f"我准备执行 {tool_name}，是否确认？"


def error_response(message: str) -> dict[str, Any]:
    return {"conversation_id": None, "reply": message, "tool_calls": [], "pending_action": None}


def provider_error_message(exc: LLMProviderError) -> str:
    return str(exc)
