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
你可以调用只读工具查询应用全局概览、推荐榜、商品池、商品详情、趋势、关键词机会、一级关键词分组、关键词创意候选池、评论洞察、追踪任务和任务中心。
你也可以提出预开启 Amazon 页面、创建关键词追踪、修改追踪状态、触发采集，但这些操作必须先获得用户二次确认。
你会收到一段“当前应用上下文”，里面可能包含用户当前/最近所在页面、ASIN、关键词、筛选条件和选择项；用户说“这个商品/当前页面/刚才那个关键词”时，优先结合该上下文理解。
用户问“现在系统情况、下一步、有哪些机会、帮我整体分析”时，优先调用 query_app_overview，再按需要追加明细工具。
用户问“赛道、一级关键词、中心词、关键词分组”时，优先调用 query_keyword_groups，而不是只列单个关键词。
用户问“关键词创意、候选词、种子词扩展”时，优先调用 query_keyword_ideas。
不要编造数据库里没有的数据；工具返回样本不足时要如实说明。
评论洞察只代表已导入或已解析的评论证据；如果没有评论 HTML/CSV/JSON 数据，必须说明数据不足。
monthly_bought/近期购买量可能为空，空值不等于 0，分析时要说明缺失风险。
触发采集会联网打开浏览器访问 Amazon，必须提醒用户有采集边界、账号/页面状态和耗时风险。
建议用户先预开启 Amazon 页面并手动处理地址、登录或验证码等前置状态，再触发采集；采集流程会尽量复用该共享浏览器会话。"""

CONTEXT_SYSTEM_PREFIX = "【当前应用上下文】"


@dataclass
class AgentConversation:
    conversation_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    lock: RLock = field(default_factory=RLock)
    pending_action: dict[str, Any] | None = None
    client_context: dict[str, Any] = field(default_factory=dict)


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
        client_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation = self.store.get_or_create(conversation_id)
        with conversation.lock:
            return self._chat_locked(
                conversation,
                message=message,
                confirm=confirm,
                client_context=client_context,
            )

    def _chat_locked(
        self,
        conversation: AgentConversation,
        *,
        message: str | None,
        confirm: dict[str, Any] | None,
        client_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self._upsert_client_context(conversation, client_context)

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

    def _upsert_client_context(
        self,
        conversation: AgentConversation,
        client_context: dict[str, Any] | None,
    ) -> None:
        if client_context is None:
            return
        cleaned_context = _compact_context(client_context)
        conversation.client_context = cleaned_context if isinstance(cleaned_context, dict) else {}
        conversation.messages = [
            message for message in conversation.messages
            if not _is_client_context_message(message)
        ]
        context_text = _format_client_context(conversation.client_context)
        if not context_text:
            return
        insert_at = 1 if conversation.messages and conversation.messages[0].get("role") == "system" else 0
        conversation.messages.insert(
            insert_at,
            {
                "role": "system",
                "content": (
                    f"{CONTEXT_SYSTEM_PREFIX}\n"
                    "这是前端传入的轻量页面状态，不是数据库事实；涉及真实指标仍需调用工具核验。\n"
                    f"{context_text}"
                ),
            },
        )

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
            "action_suggestions": _build_action_suggestions(
                conversation.client_context,
                pending_action=pending_action,
            ),
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
    if tool_name == "open_amazon_page":
        return "我准备预开启 Amazon 页面，供你手动处理地址、登录或验证码等前置状态。是否执行？"
    if tool_name == "create_keyword_tracking":
        return f"我准备创建关键词追踪任务：{tool_input.get('keyword') or '未指定关键词'}。是否执行？"
    if tool_name == "set_keyword_tracking_status":
        return f"我准备把追踪任务 {tool_input.get('task_id')} 改为 {tool_input.get('status')}。是否执行？"
    if tool_name == "trigger_collection":
        target = f"任务 {tool_input.get('task_id')}" if tool_input.get("task_id") else "所有到期任务"
        return f"我准备触发{target}的联网采集。该操作会打开浏览器访问 Amazon，是否执行？"
    return f"我准备执行 {tool_name}，是否确认？"


def build_agent_action_suggestions(
    client_context: dict[str, Any] | None,
    *,
    pending_action: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cleaned_context = _compact_context(client_context or {})
    if not isinstance(cleaned_context, dict):
        return []
    return _build_action_suggestions(cleaned_context, pending_action=pending_action)


def _build_action_suggestions(
    client_context: dict[str, Any],
    *,
    pending_action: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if pending_action:
        return []
    page = _active_business_page(client_context)
    hash_value = str(page.get("hash") or "")
    if hash_value == "#/keyword-workshop":
        return _keyword_workshop_suggestions(page)
    if hash_value == "#/products":
        return _product_pool_suggestions(page)
    if hash_value == "#/tracking":
        return _tracking_suggestions(page)
    if hash_value == "#/tasks":
        return _task_center_suggestions(page)
    return []


def _keyword_workshop_suggestions(page: dict[str, Any]) -> list[dict[str, Any]]:
    filters = page.get("keyword_workshop_filters")
    if not isinstance(filters, dict):
        return []
    ids = _positive_int_list(filters.get("selected_idea_ids"), limit=50)
    if not ids:
        return []

    count = len(ids)
    status = str(filters.get("status") or "candidate")
    suggestions = [
        {
            "id": "keyword_ideas_promote_selected",
            "kind": "keyword_workshop",
            "operation": "promote",
            "label": "加入关键词库",
            "description": f"将已选 {count} 个候选词写入关键词库；重复关键词会复用已有记录。",
            "confirm_text": f"确认将已选 {count} 个候选词加入关键词库？",
            "ids": ids,
            "marketplace": "US",
            "requires_confirmation": True,
            "risk": "写入 MySQL，不联网。",
        },
        {
            "id": "keyword_ideas_create_tracking_selected",
            "kind": "keyword_workshop",
            "operation": "create_tracking",
            "label": "创建追踪",
            "description": f"为已选 {count} 个候选词创建关键词追踪任务；不会立即采集。",
            "confirm_text": f"确认为已选 {count} 个候选词创建追踪任务？",
            "ids": ids,
            "marketplace": "US",
            "target_snapshots": 3,
            "requires_confirmation": True,
            "risk": "写入 MySQL；后续采集仍需单独确认。",
        },
    ]
    if status == "ignored":
        suggestions.append(
            {
                "id": "keyword_ideas_restore_selected",
                "kind": "keyword_workshop",
                "operation": "set_status",
                "status": "candidate",
                "label": "恢复候选",
                "description": f"把已选 {count} 个已忽略候选恢复为候选状态。",
                "confirm_text": f"确认恢复已选 {count} 个候选词？",
                "ids": ids,
                "marketplace": "US",
                "requires_confirmation": True,
                "risk": "仅更新候选状态，不联网。",
            }
        )
    else:
        suggestions.append(
            {
                "id": "keyword_ideas_ignore_selected",
                "kind": "keyword_workshop",
                "operation": "set_status",
                "status": "ignored",
                "label": "忽略候选",
                "description": f"把已选 {count} 个候选词标记为已忽略，后续可恢复。",
                "confirm_text": f"确认忽略已选 {count} 个候选词？",
                "ids": ids,
                "marketplace": "US",
                "requires_confirmation": True,
                "risk": "仅更新候选状态，不联网。",
            }
        )
    return suggestions


def _product_pool_suggestions(page: dict[str, Any]) -> list[dict[str, Any]]:
    filters = page.get("product_filters")
    if not isinstance(filters, dict):
        return []
    asins = _asin_list(filters.get("selected_asins"), limit=5)
    if not asins:
        return []
    suggestions = [
        {
            "id": "product_detail_first_selected",
            "kind": "product_pool",
            "operation": "view_detail",
            "label": "查看首个商品",
            "description": f"打开已选 ASIN {asins[0]} 的商品详情与趋势页。",
            "asin": asins[0],
            "requires_confirmation": False,
            "risk": "仅前端跳转，不写库、不联网。",
        }
    ]
    if len(asins) >= 2:
        suggestions.insert(
            0,
            {
                "id": "product_compare_selected",
                "kind": "product_pool",
                "operation": "compare",
                "label": "对比选中商品",
                "description": f"打开已选 {len(asins)} 个商品的对比页，适合快速看价格、评分、评论和需求差异。",
                "asins": asins,
                "requires_confirmation": False,
                "risk": "仅前端跳转，不写库、不联网。",
            },
        )
    return suggestions


def _tracking_suggestions(page: dict[str, Any]) -> list[dict[str, Any]]:
    filters = page.get("tracking_filters")
    if not isinstance(filters, dict):
        return []
    tasks = _tracking_task_list(filters.get("selected_tasks"), limit=5)
    if not tasks:
        tasks = [{"id": task_id, "status": ""} for task_id in _positive_int_list(filters.get("selected_task_ids"), limit=5)]
    if not tasks:
        return []

    task_ids = [item["id"] for item in tasks]
    active_ids = [item["id"] for item in tasks if item.get("status") == "active"]
    resumable_ids = [item["id"] for item in tasks if item.get("status") in {"paused", "error"}]
    suggestions: list[dict[str, Any]] = [
        {
            "id": "tracking_check_selected",
            "kind": "tracking",
            "operation": "check",
            "label": "检查选中任务",
            "description": f"对已选 {len(task_ids)} 个追踪任务做 dry-run 检查，刷新进度和检查时间，不联网采集。",
            "confirm_text": f"确认检查 {len(task_ids)} 个追踪任务？会刷新进度和最近检查时间。",
            "task_ids": task_ids,
            "requires_confirmation": True,
            "risk": "会更新任务进度和最近检查时间；不联网采集。",
        }
    ]
    if active_ids:
        suggestions.append(
            {
                "id": "tracking_pause_selected",
                "kind": "tracking",
                "operation": "set_status",
                "status": "paused",
                "label": "暂停选中任务",
                "description": f"暂停 {len(active_ids)} 个 active 追踪任务，后续不会自动进入采集。",
                "confirm_text": f"确认暂停 {len(active_ids)} 个 active 追踪任务？",
                "task_ids": active_ids,
                "requires_confirmation": True,
                "risk": "写入 MySQL，不联网。",
            }
        )
        first_active = active_ids[0]
        suggestions.append(
            {
                "id": "tracking_collect_first_active",
                "kind": "tracking",
                "operation": "collect",
                "label": "执行首个采集",
                "description": f"对任务 #{first_active} 执行一次关键词追踪采集；若未到期或被拦会停止/跳过。",
                "confirm_text": f"确认对追踪任务 #{first_active} 执行联网采集？",
                "task_id": first_active,
                "requires_confirmation": True,
                "risk": "会联网打开浏览器访问 Amazon，受 72h 边界和被拦即停约束。",
            }
        )
    if resumable_ids:
        suggestions.append(
            {
                "id": "tracking_resume_selected",
                "kind": "tracking",
                "operation": "set_status",
                "status": "active",
                "label": "恢复选中任务",
                "description": f"恢复 {len(resumable_ids)} 个 paused/error 追踪任务为 active。",
                "confirm_text": f"确认恢复 {len(resumable_ids)} 个追踪任务？",
                "task_ids": resumable_ids,
                "requires_confirmation": True,
                "risk": "写入 MySQL，不会立即联网采集。",
            }
        )
    return suggestions


def _task_center_suggestions(page: dict[str, Any]) -> list[dict[str, Any]]:
    filters = page.get("task_filters")
    if not isinstance(filters, dict):
        return []
    tasks = _task_center_task_list(filters.get("selected_tasks"), limit=5)
    if not tasks:
        return []

    suggestions: list[dict[str, Any]] = []
    error_task = next((item for item in tasks if item.get("has_error")), None)
    if error_task:
        suggestions.append(
            {
                "id": "task_show_first_error",
                "kind": "task_center",
                "operation": "show_error",
                "label": "查看首条错误",
                "description": f"打开任务 #{error_task.get('id') or error_task.get('row_id')} 的错误日志，先确认失败原因。",
                "row_id": error_task["row_id"],
                "requires_confirmation": False,
                "risk": "仅前端打开日志，不写库、不联网。",
            }
        )

    crawl_tasks = [item for item in tasks if item.get("type") == "爬取"]
    if crawl_tasks:
        suggestions.append(
            {
                "id": "task_go_html_import",
                "kind": "task_center",
                "operation": "navigate",
                "label": "去 HTML 入库",
                "description": f"选中 {len(crawl_tasks)} 条爬取任务，可到本地 HTML 入库页预览并写入有效页面。",
                "route": "#/import",
                "requires_confirmation": False,
                "risk": "仅跳转页面；真正入库仍需在入库页确认。",
            }
        )

    failed_crawl = [item for item in crawl_tasks if _looks_bad_status(item.get("status")) or item.get("has_error")]
    if failed_crawl:
        suggestions.append(
            {
                "id": "task_go_manual_crawl",
                "kind": "task_center",
                "operation": "navigate",
                "label": "去手动采集",
                "description": "选中任务包含失败或异常爬取记录，建议回到手动采集页处理地址、登录、验证码或关键词参数。",
                "route": "#/crawl",
                "requires_confirmation": False,
                "risk": "仅跳转页面；不会自动采集。",
            }
        )

    successful_import = [
        item for item in tasks
        if item.get("type") == "入库" and not _looks_bad_status(item.get("status")) and int(item.get("ingested_count") or 0) > 0
    ]
    if successful_import:
        suggestions.append(
            {
                "id": "task_go_warehouse_sync",
                "kind": "task_center",
                "operation": "navigate",
                "label": "去仓库同步",
                "description": f"选中 {len(successful_import)} 条成功入库任务，可同步 DuckDB/Parquet 分析副本。",
                "route": "#/warehouse",
                "requires_confirmation": False,
                "risk": "仅跳转页面；同步仍需在仓库页确认。",
            }
        )

    return suggestions[:4]


def _active_business_page(client_context: dict[str, Any]) -> dict[str, Any]:
    current = client_context.get("current_page") if isinstance(client_context, dict) else None
    if isinstance(current, dict) and str(current.get("hash") or "") != "#/agent":
        return current
    recent = client_context.get("recent_business_page") if isinstance(client_context, dict) else None
    return recent if isinstance(recent, dict) else (current if isinstance(current, dict) else {})


def _positive_int_list(value: Any, *, limit: int) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number <= 0 or number in seen:
            continue
        result.append(number)
        seen.add(number)
        if len(result) >= limit:
            break
    return result


def _asin_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip().upper()
        if not text or text in seen:
            continue
        if not (len(text) == 10 and text.startswith("B") and text.isalnum()):
            continue
        result.append(text)
        seen.add(text)
        if len(result) >= limit:
            break
    return result


def _tracking_task_list(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            task_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if task_id <= 0 or task_id in seen:
            continue
        result.append(
            {
                "id": task_id,
                "keyword": str(item.get("keyword") or "").strip()[:120],
                "status": str(item.get("status") or "").strip().lower(),
            }
        )
        seen.add(task_id)
        if len(result) >= limit:
            break
    return result


def _task_center_task_list(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        row_id = str(item.get("row_id") or item.get("id") or "").strip()
        if not row_id or row_id in seen:
            continue
        result.append(
            {
                "row_id": row_id[:80],
                "id": _optional_int(item.get("id")),
                "type": str(item.get("type") or "").strip()[:40],
                "status": str(item.get("status") or "").strip()[:80],
                "keyword": str(item.get("keyword") or "").strip()[:120],
                "has_error": bool(item.get("has_error")),
                "ingested_count": _optional_int(item.get("ingested_count")) or 0,
            }
        )
        seen.add(row_id)
        if len(result) >= limit:
            break
    return result


def _looks_bad_status(status: Any) -> bool:
    text = str(status or "").strip().lower()
    return bool(text and any(token in text for token in ("失败", "异常", "停止", "blocked", "fail", "error")))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def error_response(message: str) -> dict[str, Any]:
    return {
        "conversation_id": None,
        "reply": message,
        "tool_calls": [],
        "pending_action": None,
        "action_suggestions": [],
    }


def provider_error_message(exc: LLMProviderError) -> str:
    return str(exc)


def _is_client_context_message(message: dict[str, Any]) -> bool:
    return (
        message.get("role") == "system"
        and str(message.get("content") or "").startswith(CONTEXT_SYSTEM_PREFIX)
    )


def _format_client_context(client_context: dict[str, Any]) -> str:
    cleaned = _compact_context(client_context)
    if not cleaned:
        return ""
    text = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
    if len(text) > 2400:
        text = text[:2400] + "...(已截断)"
    return text


def _compact_context(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text[:300] + ("..." if len(text) > 300 else "")
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 24:
                result["..."] = f"还有 {len(value) - index} 项"
                break
            key_text = str(key).strip()[:60]
            if not key_text:
                continue
            compacted = _compact_context(item, depth=depth + 1)
            if compacted in ({}, [], ""):
                continue
            result[key_text] = compacted
        return result
    if isinstance(value, (list, tuple)):
        items = [_compact_context(item, depth=depth + 1) for item in value[:12]]
        return [item for item in items if item not in ({}, [], "")]
    return str(value)[:200]
