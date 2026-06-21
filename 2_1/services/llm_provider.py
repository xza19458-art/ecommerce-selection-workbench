"""Provider abstraction and local configuration for the in-app Agent.

The Agent keeps one neutral chat/tool contract internally. Provider adapters
translate that contract to OpenAI-compatible function calling or Anthropic
Claude native tool-use. API keys are only read from config/agent.json and are
never returned in public config payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "agent.json"
PROVIDER_OPTIONS = [
    {
        "value": "openai_compatible",
        "label": "OpenAI 兼容",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
    },
    {
        "value": "anthropic",
        "label": "Anthropic Claude",
        "default_base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-3-5-haiku-latest",
    },
]


class LLMProviderError(RuntimeError):
    """Raised when provider configuration or remote calls fail."""


@dataclass(frozen=True)
class ToolCall:
    """Provider-neutral tool call."""

    id: str
    name: str
    input: dict[str, Any]

    def to_openai_message_tool_call(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.input, ensure_ascii=False),
            },
        }


@dataclass(frozen=True)
class LLMResponse:
    """Provider-neutral chat response."""

    reply: str
    tool_calls: tuple[ToolCall, ...] = ()
    finished: bool = True


class LLMProvider(Protocol):
    """Minimal interface used by the Agent loop."""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        """Return a natural-language reply and optional normalized tool calls."""


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60
    supports_tool_calls: bool = True
    temperature: float = 0.2
    max_tokens: int = 2400

    @classmethod
    def from_file(cls, path: Path = CONFIG_PATH) -> "LLMProviderConfig":
        if not path.exists():
            raise LLMProviderError(
                "未配置 Agent 模型。请复制 2_1/config/agent.example.json 为 agent.json，"
                "或在 Web 的 AI 助手里填写模型配置。"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"Agent 模型配置 JSON 格式错误: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMProviderConfig":
        if not isinstance(data, dict):
            raise LLMProviderError("Agent 模型配置必须是 JSON object。")
        provider = str(data.get("provider") or "openai_compatible").strip()
        base_url = str(data.get("base_url") or "").strip()
        api_key = str(data.get("api_key") or "").strip()
        model = str(data.get("model") or "").strip()
        if provider not in _provider_values():
            raise LLMProviderError(f"Agent provider 不支持: {provider}")
        if provider == "anthropic" and not base_url:
            base_url = "https://api.anthropic.com/v1"
        if not base_url or not model:
            raise LLMProviderError("Agent 模型配置缺少 base_url 或 model。")
        if not api_key:
            raise LLMProviderError("Agent 模型配置缺少 api_key；本地模型可填写占位值，例如 ollama。")
        return cls(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=_to_int(data.get("timeout_seconds"), default=60, minimum=5, maximum=300),
            supports_tool_calls=_to_bool(data.get("supports_tool_calls", True)),
            temperature=_to_float(data.get("temperature"), default=0.2, minimum=0.0, maximum=2.0),
            max_tokens=_to_int(data.get("max_tokens"), default=2400, minimum=128, maximum=8192),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "supports_tool_calls": self.supports_tool_calls,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
        }


class OpenAICompatibleProvider:
    """OpenAI-compatible chat/completions adapter with graceful tool fallback."""

    def __init__(self, config: LLMProviderConfig) -> None:
        self.config = config

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        if tools and not self.config.supports_tool_calls:
            return LLMResponse(
                reply=(
                    "当前模型配置已关闭工具调用，无法自动查询系统数据。请换用支持 function calling "
                    "的 OpenAI 兼容模型，或在 agent.json 中开启 supports_tool_calls。"
                ),
                tool_calls=(),
                finished=True,
            )

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            payload["tools"] = [_to_openai_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"

        try:
            data = self._post_chat(payload)
        except LLMProviderError as exc:
            if tools and _looks_like_tool_calling_error(str(exc)):
                return LLMResponse(
                    reply=(
                        "当前 OpenAI 兼容接口似乎不支持 function calling，Agent 已停止自动工具查询。"
                        "请更换支持工具调用的模型，或把 supports_tool_calls 设为 false 后只用于普通对话。"
                    ),
                    tool_calls=(),
                    finished=True,
                )
            raise

        return _parse_openai_response(data)

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            _chat_completions_url(self.config.base_url),
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _safe_decode_http_error(exc)
            raise LLMProviderError(f"模型接口调用失败：HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise LLMProviderError(f"模型接口连接失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMProviderError("模型接口调用超时。") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("模型接口返回的 JSON 无法解析。") from exc


class AnthropicProvider:
    """Anthropic Messages API adapter with native tool-use support."""

    def __init__(self, config: LLMProviderConfig) -> None:
        self.config = config

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        if tools and not self.config.supports_tool_calls:
            return LLMResponse(
                reply="当前 Claude 配置已关闭工具调用，Agent 无法自动查询系统数据。",
                tool_calls=(),
                finished=True,
            )

        system, anthropic_messages = _to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": anthropic_messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_to_anthropic_tool(tool) for tool in tools]

        try:
            data = self._post_messages(payload)
        except LLMProviderError as exc:
            if tools and _looks_like_tool_calling_error(str(exc)):
                return LLMResponse(
                    reply=(
                        "当前 Claude 接口工具调用失败，Agent 已停止自动工具查询。"
                        "请检查模型是否支持 tool-use，或在配置中关闭工具调用。"
                    ),
                    tool_calls=(),
                    finished=True,
                )
            raise
        return _parse_anthropic_response(data)

    def _post_messages(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            _anthropic_messages_url(self.config.base_url),
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _safe_decode_http_error(exc)
            raise LLMProviderError(f"Claude 接口调用失败：HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise LLMProviderError(f"Claude 接口连接失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMProviderError("Claude 接口调用超时。") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Claude 接口返回的 JSON 无法解析。") from exc


def build_provider_from_config(path: Path = CONFIG_PATH) -> LLMProvider:
    config = LLMProviderConfig.from_file(path)
    return build_provider(config)


def build_provider(config: LLMProviderConfig) -> LLMProvider:
    if config.provider == "openai_compatible":
        return OpenAICompatibleProvider(config)
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    raise LLMProviderError(f"Agent provider 不支持: {config.provider}")


def get_public_agent_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    exists = path.exists()
    error = ""
    if exists:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            error = f"Agent 模型配置 JSON 格式错误: {exc}"
    public = _public_config_from_raw(raw, exists=exists, error=error)
    if exists and not error:
        try:
            config = LLMProviderConfig.from_dict(raw)
            public.update(_public_config_from_config(config, exists=True, valid=True, error=""))
        except LLMProviderError as exc:
            public["error"] = str(exc)
    return public


def save_agent_config(data: dict[str, Any], path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = resolve_agent_config(data, path=path, preserve_existing_key=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return get_public_agent_config(path)


def resolve_agent_config(
    data: dict[str, Any],
    *,
    path: Path = CONFIG_PATH,
    preserve_existing_key: bool = True,
) -> LLMProviderConfig:
    merged = _read_json_object(path) if path.exists() else {}
    for key, value in (data or {}).items():
        if key == "api_key" and preserve_existing_key and not str(value or "").strip():
            continue
        if value is not None:
            merged[key] = value
    return LLMProviderConfig.from_dict(merged)


def test_agent_provider_config(data: dict[str, Any], path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = resolve_agent_config(data, path=path, preserve_existing_key=True)
    provider = build_provider(config)
    response = provider.chat(
        [
            {"role": "system", "content": "你只用于本地 Agent 配置连通性测试。"},
            {"role": "user", "content": "请只回答 OK。"},
        ],
        [],
    )
    return {
        "provider": config.provider,
        "model": config.model,
        "reply": (response.reply or "").strip()[:200],
    }


def _public_config_from_config(
    config: LLMProviderConfig,
    *,
    exists: bool,
    valid: bool,
    error: str,
) -> dict[str, Any]:
    return {
        "exists": exists,
        "valid": valid,
        "configured": bool(config.api_key),
        "provider": config.provider,
        "provider_label": _provider_label(config.provider),
        "base_url": config.base_url,
        "model": config.model,
        "supports_tool_calls": config.supports_tool_calls,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "api_key_configured": bool(config.api_key),
        "api_key_preview": _mask_api_key(config.api_key),
        "providers": PROVIDER_OPTIONS,
        "error": error,
    }


def _public_config_from_raw(raw: dict[str, Any], *, exists: bool, error: str) -> dict[str, Any]:
    defaults = PROVIDER_OPTIONS[0]
    provider = str(raw.get("provider") or defaults["value"])
    base_url = str(raw.get("base_url") or defaults["default_base_url"])
    model = str(raw.get("model") or defaults["default_model"])
    api_key = str(raw.get("api_key") or "")
    return {
        "exists": exists,
        "valid": False,
        "configured": bool(api_key),
        "provider": provider,
        "provider_label": _provider_label(provider),
        "base_url": base_url,
        "model": model,
        "supports_tool_calls": _to_bool(raw.get("supports_tool_calls", True)),
        "temperature": _to_float(raw.get("temperature"), default=0.2, minimum=0.0, maximum=2.0),
        "max_tokens": _to_int(raw.get("max_tokens"), default=2400, minimum=128, maximum=8192),
        "timeout_seconds": _to_int(raw.get("timeout_seconds"), default=60, minimum=5, maximum=300),
        "api_key_configured": bool(api_key),
        "api_key_preview": _mask_api_key(api_key),
        "providers": PROVIDER_OPTIONS,
        "error": error or ("未配置 Agent 模型。" if not exists else ""),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMProviderError(f"Agent 模型配置 JSON 格式错误: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMProviderError("Agent 模型配置必须是 JSON object。")
    return data


def _provider_values() -> set[str]:
    return {str(item["value"]) for item in PROVIDER_OPTIONS}


def _provider_label(value: str) -> str:
    for item in PROVIDER_OPTIONS:
        if item["value"] == value:
            return str(item["label"])
    return value


def _mask_api_key(value: str) -> str:
    key = str(value or "")
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def _to_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": tool.get("parameters") or {"type": "object", "properties": {}},
    }


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            if content:
                system_parts.append(str(content))
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or ""),
                "content": str(content),
            }
            if _tool_result_is_error(content):
                block["is_error"] = True
            if (
                converted
                and converted[-1].get("role") == "user"
                and isinstance(converted[-1].get("content"), list)
            ):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        if role == "assistant":
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for raw_call in tool_calls:
                    call = _tool_call_from_openai_message(raw_call)
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.input,
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})
            else:
                converted.append({"role": "assistant", "content": str(content)})
            continue
        if role == "user":
            converted.append({"role": "user", "content": str(content)})
    return "\n\n".join(system_parts), converted


def _tool_call_from_openai_message(raw_call: dict[str, Any]) -> ToolCall:
    function = raw_call.get("function") or {}
    return ToolCall(
        id=str(raw_call.get("id") or f"tool_{uuid4().hex}"),
        name=str(function.get("name") or ""),
        input=_decode_tool_arguments(function.get("arguments")),
    )


def _tool_result_is_error(content: Any) -> bool:
    try:
        payload = json.loads(str(content))
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("ok") is False


def _anthropic_messages_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/messages"):
        return base
    return f"{base}/messages"


def _parse_anthropic_response(data: dict[str, Any]) -> LLMResponse:
    content = data.get("content") or []
    if not isinstance(content, list):
        raise LLMProviderError("Claude 接口返回的 content 格式无法解析。")
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        if block.get("type") == "tool_use":
            raw_input = block.get("input") or {}
            tool_calls.append(
                ToolCall(
                    id=str(block.get("id") or f"tool_{uuid4().hex}"),
                    name=str(block.get("name") or ""),
                    input=raw_input if isinstance(raw_input, dict) else {"value": raw_input},
                )
            )
    if not content:
        raise LLMProviderError("Claude 接口返回为空。")
    return LLMResponse(
        reply="".join(text_parts),
        tool_calls=tuple(tool_calls),
        finished=not tool_calls,
    )


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _safe_decode_http_error(exc: HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:1000]
    except Exception:
        return str(exc)


def _looks_like_tool_calling_error(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("tool", "function", "tool_choice", "function_call", "tool_use"))


def _to_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _to_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "否"}
    return bool(value)


def _decode_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {"_raw_arguments": str(value)}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _parse_openai_response(data: dict[str, Any]) -> LLMResponse:
    choices = data.get("choices") or []
    if not choices:
        raise LLMProviderError("模型接口返回为空。")
    message = choices[0].get("message") or {}
    reply = message.get("content") or ""
    tool_calls = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        tool_calls.append(
            ToolCall(
                id=str(raw.get("id") or f"tool_{uuid4().hex}"),
                name=str(function.get("name") or ""),
                input=_decode_tool_arguments(function.get("arguments")),
            )
        )

    # Older OpenAI-compatible servers may still emit a single function_call.
    function_call = message.get("function_call")
    if function_call and not tool_calls:
        tool_calls.append(
            ToolCall(
                id=f"tool_{uuid4().hex}",
                name=str(function_call.get("name") or ""),
                input=_decode_tool_arguments(function_call.get("arguments")),
            )
        )

    return LLMResponse(reply=reply, tool_calls=tuple(tool_calls), finished=not tool_calls)
