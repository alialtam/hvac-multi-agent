"""Small chat client with tool calling, used by the triage agent.

OpenAI and Ollama both speak the OpenAI chat format (Ollama at <OLLAMA_URL>/v1),
so one class covers both. When Ahmed's LLM layer (`app/llm`) is merged, the
triage agent can use it instead: anything with the same `chat()` method works.

    client = make_client("openai")          # None if the provider is "rules" or has no key
    reply = client.chat(messages, tools)     # {"content", "tool_calls", "usage", "duration_ms"}
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol

from ..config import env

DEFAULT_TIMEOUT_S = 25.0


class ChatClient(Protocol):
    provider: str
    model: str

    def chat(self, messages: list[dict], tools: list[dict], tool_choice: Any = "auto") -> dict: ...


class OpenAICompatibleChat:
    """OpenAI (cloud) or Ollama (local) through the official `openai` package."""

    def __init__(self, provider: str, model: str, api_key: str, base_url: str | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        from openai import OpenAI   # imported here so the rules-only mode needs no extra package

        self.provider, self.model = provider, model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=1)

    def chat(self, messages: list[dict], tools: list[dict], tool_choice: Any = "auto") -> dict:
        t0 = time.perf_counter()
        extra = {} if self.model.startswith(("o1", "o3", "o4", "gpt-5")) else {"temperature": 0}  # reasoning models reject it
        r = self._client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                 tool_choice=tool_choice, **extra)
        msg = r.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
                 for c in (msg.tool_calls or [])]
        usage = {"prompt_tokens": getattr(r.usage, "prompt_tokens", 0) or 0,
                 "completion_tokens": getattr(r.usage, "completion_tokens", 0) or 0} if r.usage else {}
        return {"content": msg.content, "tool_calls": calls, "usage": usage,
                "duration_ms": round((time.perf_counter() - t0) * 1000)}


def make_client(provider: str) -> ChatClient | None:
    """Client for "openai" / "ollama", or None for "rules" (or when OpenAI has no key)."""
    provider = (provider or "rules").lower()
    if provider == "openai":
        key = env("OPENAI_API_KEY")
        if not key:
            return None
        return OpenAICompatibleChat("openai", env("OPENAI_MODEL", "gpt-4o-mini"), key)
    if provider == "ollama":
        url = env("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        return OpenAICompatibleChat("ollama", env("OLLAMA_MODEL", "qwen2.5:1.5b"), "ollama",
                                    base_url=f"{url}/v1", timeout_s=60.0)
    return None


def assistant_message(reply: dict) -> dict:
    """Turn a reply back into the message the API expects in the history."""
    m: dict = {"role": "assistant", "content": reply.get("content") or ""}
    if reply.get("tool_calls"):
        m["tool_calls"] = [{"id": c["id"], "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"]}}
                           for c in reply["tool_calls"]]
    return m


def parse_args(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}
