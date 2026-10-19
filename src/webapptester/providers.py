"""Structured JSON model provider adapters.

The role layer only receives decoded JSON objects from this module.  The
providers intentionally use the Python standard library so transport failures
remain easy to classify and test without an SDK dependency.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Protocol


DEEPSEEK_MAX_TOKENS = 4096


class ModelOutputError(RuntimeError):
    """Raised when a provider cannot return a valid structured response."""


@dataclass(slots=True)
class TokenUsage:
    """Cumulative prompt and completion token counts."""

    prompt: int = 0
    completion: int = 0

    def __post_init__(self) -> None:
        for name in ("prompt", "completion"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} tokens must be a non-negative integer")

    def add(self, other: "TokenUsage") -> "TokenUsage":
        """Add ``other`` in place and return this usage object."""

        if not isinstance(other, TokenUsage):
            raise TypeError("usage can only be added to TokenUsage")
        self.prompt += other.prompt
        self.completion += other.completion
        return self

    def update(self, other: "TokenUsage") -> "TokenUsage":
        """Alias for :meth:`add`, useful to callers keeping cumulative usage."""

        return self.add(other)

    def __add__(self, other: object) -> "TokenUsage":
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(self.prompt + other.prompt, self.completion + other.completion)

    def __iadd__(self, other: "TokenUsage") -> "TokenUsage":
        return self.add(other)


class ModelProvider(Protocol):
    """Common interface consumed by Planner, Executor, and Verifier."""

    usage: TokenUsage

    def complete_json(self, system: str, user: str) -> dict[str, object]:
        ...


class _HTTPJSONProvider:
    """Shared request and response handling for provider adapters."""

    usage: TokenUsage

    def __init__(self) -> None:
        self.usage = TokenUsage()

    def _complete(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        content_path: tuple[str | int, ...],
        prompt_tokens_path: tuple[str | int, ...],
        completion_tokens_path: tuple[str | int, ...],
    ) -> dict[str, object]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw_response = response.read()
            decoded = json.loads(raw_response)
            content = _value_at(decoded, content_path)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("provider returned empty content")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise TypeError("provider content must decode to a JSON object")
            prompt = _optional_nonnegative_int(_value_at(decoded, prompt_tokens_path))
            completion = _optional_nonnegative_int(_value_at(decoded, completion_tokens_path))
        except ModelOutputError:
            raise
        except Exception as error:
            raise ModelOutputError("provider returned invalid JSON output") from error

        self.usage.add(TokenUsage(prompt=prompt, completion=completion))
        return result


def _value_at(value: object, path: tuple[str | int, ...]) -> object:
    current = value
    for key in path:
        if isinstance(current, dict) and isinstance(key, str):
            current = current[key]
        elif isinstance(current, list) and type(key) is int:
            current = current[key]
        else:
            location = ".".join(str(part) for part in path)
            raise TypeError(f"provider response missing {location}")
    return current


def _optional_nonnegative_int(value: object) -> int:
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ValueError("provider token count must be a non-negative integer")
    return value


class OllamaProvider(_HTTPJSONProvider):
    """Ollama ``/api/chat`` adapter using Ollama's JSON output mode."""

    def __init__(
        self,
        model: str = "qwen2.5:3b",
        url: str = "http://127.0.0.1:11434/api/chat",
    ) -> None:
        super().__init__()
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be nonempty")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be nonempty")
        self.model = model
        self.url = url

    def complete_json(
        self, system: str, user: str, *, schema: dict[str, object] | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "format": schema if schema is not None else "json",
        }
        return self._complete(
            url=self.url,
            headers={"Content-Type": "application/json"},
            payload=payload,
            content_path=("message", "content"),
            prompt_tokens_path=("prompt_eval_count",),
            completion_tokens_path=("eval_count",),
        )


class DeepSeekProvider(_HTTPJSONProvider):
    """DeepSeek Chat Completions adapter in JSON object mode."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-flash",
        url: str = "https://api.deepseek.com/chat/completions",
    ) -> None:
        super().__init__()
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be nonempty")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be nonempty")
        resolved_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        if not isinstance(resolved_key, str) or not resolved_key.strip():
            raise ValueError("DeepSeek API key is required")
        self.api_key = resolved_key
        self.model = model
        self.url = url

    def complete_json(self, system: str, user: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": DEEPSEEK_MAX_TOKENS,
        }
        return self._complete(
            url=self.url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            payload=payload,
            content_path=("choices", 0, "message", "content"),
            prompt_tokens_path=("usage", "prompt_tokens"),
            completion_tokens_path=("usage", "completion_tokens"),
        )


# Some callers prefer the provider name without the ``Provider`` suffix.
Ollama = OllamaProvider
DeepSeek = DeepSeekProvider


__all__ = [
    "DeepSeek",
    "DeepSeekProvider",
    "ModelOutputError",
    "ModelProvider",
    "Ollama",
    "OllamaProvider",
    "TokenUsage",
]
