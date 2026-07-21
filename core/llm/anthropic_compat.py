"""Compatibility helpers for Anthropic Messages API model differences."""

from __future__ import annotations

from typing import Any, Optional


_MODELS_WITH_DEPRECATED_TEMPERATURE = ("claude-opus-4-8",)


def model_accepts_temperature(model: str) -> bool:
    """Return whether the model accepts the legacy ``temperature`` option."""
    normalized = model.strip().lower()
    return not normalized.startswith(_MODELS_WITH_DEPRECATED_TEMPERATURE)


def create_message(
    client: Any,
    *,
    model: str,
    max_tokens: int,
    system: str,
    messages: list[dict[str, str]],
    temperature: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Create a message while omitting parameters rejected by newer models."""
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if temperature is not None and model_accepts_temperature(model):
        request["temperature"] = temperature
    if timeout is not None:
        request["timeout"] = timeout
    return client.messages.create(**request)
