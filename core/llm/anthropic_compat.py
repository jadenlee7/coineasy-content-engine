"""Compatibility helpers for Anthropic Messages API model differences."""

from __future__ import annotations

import os
from typing import Any, Optional


# Opus 4.7 and later (Opus 4.8, Opus 5, Sonnet 5, Fable 5) reject the legacy
# sampling controls — sending ``temperature`` returns a 400.
_MODELS_WITH_DEPRECATED_TEMPERATURE = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
)

# On these models adaptive thinking runs whenever ``thinking`` is omitted.
# Every call in this repo is a deterministic JSON extraction under a hard
# timeout budget, so we keep the previous no-thinking latency/cost profile by
# default and let callers opt in per request (or globally via LLM_THINKING).
_MODELS_THINKING_BY_DEFAULT = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
)


def model_accepts_temperature(model: str) -> bool:
    """Return whether the model accepts the legacy ``temperature`` option."""
    normalized = model.strip().lower()
    return not normalized.startswith(_MODELS_WITH_DEPRECATED_TEMPERATURE)


def model_thinks_by_default(model: str) -> bool:
    """Return whether omitting ``thinking`` still runs adaptive thinking."""
    normalized = model.strip().lower()
    return normalized.startswith(_MODELS_THINKING_BY_DEFAULT)


def _default_thinking(model: str) -> Optional[dict[str, Any]]:
    """Resolve the thinking config to send when the caller passes none.

    LLM_THINKING=adaptive turns adaptive thinking back on globally; any other
    value (or unset) keeps thinking off on models that would otherwise think.
    """
    if not model_thinks_by_default(model):
        return None
    if os.environ.get("LLM_THINKING", "").strip().lower() == "adaptive":
        return {"type": "adaptive"}
    return {"type": "disabled"}


def first_text(response: Any) -> str:
    """Return the first text block of a response.

    ``content[0]`` is not safe on thinking-capable models: a thinking block can
    precede the answer, and indexing position 0 raises AttributeError. Scan for
    the first ``text`` block instead so the same code works on every model.
    """
    blocks = getattr(response, "content", None) or []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    # Blocks that don't declare a type (test doubles, older SDK shapes) still
    # carry .text. A thinking block exposes .thinking instead, so it is skipped
    # here too rather than being mistaken for the answer.
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    raise ValueError("LLM response contained no text block")


def create_message(
    client: Any,
    *,
    model: str,
    max_tokens: int,
    messages: list[dict[str, str]],
    system: Optional[str] = None,
    temperature: Optional[float] = None,
    timeout: Optional[float] = None,
    thinking: Optional[dict[str, Any]] = None,
) -> Any:
    """Create a message while omitting parameters rejected by newer models."""
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system is not None:
        request["system"] = system
    if temperature is not None and model_accepts_temperature(model):
        request["temperature"] = temperature
    resolved_thinking = thinking if thinking is not None else _default_thinking(model)
    if resolved_thinking is not None:
        request["thinking"] = resolved_thinking
    if timeout is not None:
        request["timeout"] = timeout
    return client.messages.create(**request)
