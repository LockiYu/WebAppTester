import json
from unittest.mock import patch

import pytest

from webapptester.providers import (
    DeepSeekProvider,
    ModelOutputError,
    OllamaProvider,
    TokenUsage,
)
from webapptester.deepseek_budget import DeepSeekBudget, DeepSeekBudgetError


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_ollama_sends_json_chat_request_and_tracks_usage() -> None:
    response = _Response(
        {
            "message": {"content": '{"checkpoints": []}'},
            "prompt_eval_count": 7,
            "eval_count": 3,
        }
    )
    with patch("urllib.request.urlopen", return_value=response) as opened:
        provider = OllamaProvider(model="local-model")
        assert provider.complete_json("system", "user") == {"checkpoints": []}

    request = opened.call_args.args[0]
    payload = json.loads(request.data)
    assert request.get_method() == "POST"
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert payload["model"] == "local-model"
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["think"] is False
    assert opened.call_args.kwargs == {"timeout": 120}
    assert provider.usage == TokenUsage(prompt=7, completion=3)


def test_ollama_passes_explicit_json_schema_to_api() -> None:
    response = _Response({
        "message": {"content": '{"action": {"kind": "stop"}}'},
        "prompt_eval_count": 0,
        "eval_count": 0,
    })
    schema = {
        "type": "object",
        "properties": {"action": {"type": "object"}},
        "required": ["action"],
    }
    with patch("urllib.request.urlopen", return_value=response) as opened:
        OllamaProvider(model="local-model").complete_json("system", "user", schema=schema)

    payload = json.loads(opened.call_args.args[0].data)
    assert payload["format"] == schema


def test_deepseek_sends_json_mode_and_bearer_key() -> None:
    response = _Response(
        {
            "choices": [{"message": {"content": '{"action": {"kind": "stop"}}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        }
    )
    with patch("urllib.request.urlopen", return_value=response) as opened:
        provider = DeepSeekProvider(api_key="secret")
        assert provider.complete_json("system", "user") == {
            "action": {"kind": "stop"}
        }

    request = opened.call_args.args[0]
    payload = json.loads(request.data)
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    assert payload["model"] == "deepseek-flash"
    assert payload["stream"] is False
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 4096
    assert request.get_header("Authorization") == "Bearer secret"
    assert provider.usage == TokenUsage(prompt=11, completion=5)


def test_deepseek_preserves_explicit_model_override() -> None:
    response = _Response(
        {
            "choices": [{"message": {"content": '{"action": {"kind": "stop"}}'}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
    )
    with patch("urllib.request.urlopen", return_value=response) as opened:
        provider = DeepSeekProvider(api_key="secret", model="custom-model")
        provider.complete_json("system", "user")

    payload = json.loads(opened.call_args.args[0].data)
    assert payload["model"] == "custom-model"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 4096


def test_deepseek_counts_billed_tokens_even_when_content_is_invalid() -> None:
    response = _Response({
        "choices": [{"message": {"content": "not json"}}],
        "usage": {"prompt_tokens": 123, "completion_tokens": 45},
    })
    provider = DeepSeekProvider(api_key="secret")

    with patch("urllib.request.urlopen", return_value=response):
        with pytest.raises(ModelOutputError):
            provider.complete_json("system", "user")

    assert provider.usage == TokenUsage(prompt=123, completion=45)


def test_deepseek_provider_uses_shared_budget_before_post(tmp_path) -> None:
    response = _Response({
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    })
    ledger = tmp_path / "deepseek-budget.json"
    provider = DeepSeekProvider(api_key="secret", budget=DeepSeekBudget(ledger))
    with patch("urllib.request.urlopen", return_value=response) as opened:
        assert provider.complete_json("system", "user") == {"ok": True}
    assert opened.call_count == 1
    assert json.loads(ledger.read_text())["reserved_or_spent_cny"] == "0.01"

    ledger.write_text(json.dumps({
        "version": 1, "limit_cny": "20.00", "reserved_or_spent_cny": "20.00",
    }), encoding="utf-8")
    with patch("urllib.request.urlopen") as opened:
        with pytest.raises(DeepSeekBudgetError):
            provider.complete_json("system", "user")
    opened.assert_not_called()


def test_provider_rejects_empty_content_and_chains_cause() -> None:
    response = _Response({"message": {"content": ""}})
    with patch("urllib.request.urlopen", return_value=response):
        with pytest.raises(ModelOutputError) as raised:
            OllamaProvider().complete_json("system", "user")
    assert raised.value.__cause__ is not None


def test_token_usage_additive_update() -> None:
    usage = TokenUsage(prompt=2, completion=4)
    assert usage + TokenUsage(prompt=3, completion=1) == TokenUsage(5, 5)
    assert usage.add(TokenUsage(prompt=3, completion=1)) == TokenUsage(5, 5)
