from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.llm_provider import (
    AnthropicProvider,
    LLMProviderConfig,
    OpenAICompatibleProvider,
    ToolCall,
    build_provider,
    get_public_agent_config,
    save_agent_config,
    _parse_anthropic_response,
    _parse_openai_response,
    _to_anthropic_messages,
)


def test_config_parses_string_false_for_tool_support() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "agent.json"
        path.write_text(
            json.dumps(
                {
                    "provider": "openai_compatible",
                    "base_url": "http://localhost:11434/v1",
                    "api_key": "ollama",
                    "model": "local-model",
                    "supports_tool_calls": "false",
                }
            ),
            encoding="utf-8",
        )
        config = LLMProviderConfig.from_file(path)

    assert config.supports_tool_calls is False


def test_openai_tool_call_response_is_normalized() -> None:
    response = _parse_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "我先查询。",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "query_products",
                                    "arguments": "{\"limit\": 2}",
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )

    assert response.finished is False
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "query_products"
    assert response.tool_calls[0].input == {"limit": 2}


def test_build_provider_supports_openai_and_anthropic() -> None:
    openai = LLMProviderConfig.from_dict(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
            "model": "local-model",
        }
    )
    anthropic = LLMProviderConfig.from_dict(
        {
            "provider": "anthropic",
            "api_key": "sk-ant-test",
            "model": "claude-test",
        }
    )

    assert isinstance(build_provider(openai), OpenAICompatibleProvider)
    assert isinstance(build_provider(anthropic), AnthropicProvider)
    assert anthropic.base_url == "https://api.anthropic.com/v1"


def test_anthropic_tool_use_response_is_normalized() -> None:
    response = _parse_anthropic_response(
        {
            "content": [
                {"type": "text", "text": "我先查询。"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "query_products",
                    "input": {"limit": 2},
                },
            ],
            "stop_reason": "tool_use",
        }
    )

    assert response.finished is False
    assert response.reply == "我先查询。"
    assert response.tool_calls[0].id == "toolu_1"
    assert response.tool_calls[0].name == "query_products"
    assert response.tool_calls[0].input == {"limit": 2}


def test_openai_style_history_converts_to_anthropic_messages() -> None:
    call = ToolCall(id="call_1", name="query_products", input={"limit": 2})
    system, messages = _to_anthropic_messages(
        [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "show products"},
            {
                "role": "assistant",
                "content": "I will query.",
                "tool_calls": [call.to_openai_message_tool_call()],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "{\"ok\": false}"},
        ]
    )

    assert system == "system prompt"
    assert messages[0] == {"role": "user", "content": "show products"}
    assert messages[1]["content"][1]["type"] == "tool_use"
    assert messages[1]["content"][1]["input"] == {"limit": 2}
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["is_error"] is True


def test_agent_config_masks_key_and_preserves_existing_key() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "agent.json"
        saved = save_agent_config(
            {
                "provider": "openai_compatible",
                "base_url": "http://localhost:11434/v1",
                "api_key": "secret-token-1234",
                "model": "local-model",
            },
            path=path,
        )
        saved_again = save_agent_config(
            {
                "provider": "openai_compatible",
                "base_url": "http://localhost:11434/v1",
                "api_key": "",
                "model": "new-model",
            },
            path=path,
        )
        raw = json.loads(path.read_text(encoding="utf-8"))
        public = get_public_agent_config(path)

    assert "api_key" not in saved
    assert saved["api_key_configured"] is True
    assert saved_again["model"] == "new-model"
    assert raw["api_key"] == "secret-token-1234"
    assert public["api_key_preview"].startswith("secr")
    assert "secret-token-1234" not in json.dumps(public, ensure_ascii=False)


if __name__ == "__main__":
    tests = [
        test_config_parses_string_false_for_tool_support,
        test_openai_tool_call_response_is_normalized,
        test_build_provider_supports_openai_and_anthropic,
        test_anthropic_tool_use_response_is_normalized,
        test_openai_style_history_converts_to_anthropic_messages,
        test_agent_config_masks_key_and_preserves_existing_key,
    ]
    for test in tests:
        test()
    print(f"llm_provider tests passed: {len(tests)}/{len(tests)}")
