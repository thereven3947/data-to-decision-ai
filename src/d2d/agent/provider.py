"""LLM providers. Default: Ollama on the Mac mini (native /api/chat, tools, thinking off)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from d2d.config import load_toml


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str
    timeout_s: float
    num_ctx: int
    temperature: float
    max_tool_calls: int


def load_llm(path: Path) -> LLMConfig:
    raw = load_toml(path)["llm"]
    return LLMConfig(
        provider=str(raw.get("provider", "ollama")),
        base_url=os.environ.get("D2D_OLLAMA_URL") or str(raw["base_url"]),
        model=os.environ.get("D2D_LLM_MODEL") or str(raw["model"]),
        timeout_s=float(raw.get("timeout_s", 120)),
        num_ctx=int(raw.get("num_ctx", 8192)),
        temperature=float(raw.get("temperature", 0.2)),
        max_tool_calls=int(raw.get("max_tool_calls", 6)),
    )


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


class ProviderError(RuntimeError):
    """The LLM could not be reached or returned something unusable."""


class Provider(Protocol):
    model_tag: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply: ...


class OllamaProvider:
    def __init__(self, cfg: LLMConfig, client: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.model_tag = f"ollama:{cfg.model}"
        self._client = client or httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_s)

    def available(self) -> bool:
        try:
            tags = self._client.get("/api/tags", timeout=3).json()
        except (httpx.HTTPError, ValueError):
            return False
        return any(m.get("name") == self.cfg.model for m in tags.get("models", []))

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "think": False,
            "options": {"temperature": self.cfg.temperature, "num_ctx": self.cfg.num_ctx},
        }
        try:
            resp = self._client.post("/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"ollama: {exc}") from exc
        msg = data.get("message") or {}
        calls = [
            ToolCall(c["function"]["name"], dict(c["function"].get("arguments") or {}))
            for c in msg.get("tool_calls") or []
        ]
        return Reply(
            content=str(msg.get("content") or ""),
            tool_calls=calls,
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
        )
