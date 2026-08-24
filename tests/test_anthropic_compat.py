import inspect
from types import SimpleNamespace

import pytest
from anthropic.resources.messages import Messages

from core.llm.anthropic_compat import (
    create_message,
    first_text,
    model_accepts_temperature,
    model_thinks_by_default,
)


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return {"ok": True}


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _call(model: str, *, timeout=None):
    client = _FakeClient()
    create_message(
        client,
        model=model,
        max_tokens=100,
        temperature=0.2,
        system="system",
        messages=[{"role": "user", "content": "hello"}],
        timeout=timeout,
    )
    return client.messages.kwargs


def test_installed_anthropic_sdk_accepts_thinking_configuration():
    """Keep the runtime SDK aligned with the Opus 5 compatibility request."""
    parameters = inspect.signature(Messages.create).parameters
    assert "thinking" in parameters
    assert "output_config" in parameters


def test_opus_4_8_omits_deprecated_temperature():
    kwargs = _call("claude-opus-4-8")
    assert "temperature" not in kwargs
    assert model_accepts_temperature("claude-opus-4-8-20260701") is False


def test_older_models_keep_configured_temperature():
    kwargs = _call("claude-sonnet-4-5-20250929")
    assert kwargs["temperature"] == 0.2


def test_per_request_timeout_is_forwarded_when_configured():
    kwargs = _call("claude-sonnet-4-5-20250929", timeout=8.0)

    assert kwargs["timeout"] == 8.0


def test_structured_output_config_is_forwarded_only_when_configured():
    client = _FakeClient()
    output_config = {
        "format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    }

    create_message(
        client,
        model="claude-sonnet-4-5-20250929",
        max_tokens=100,
        messages=[{"role": "user", "content": "hello"}],
        output_config=output_config,
    )

    assert client.messages.kwargs["output_config"] == output_config
    assert "output_config" not in _call("claude-sonnet-4-5-20250929")


def test_opus_5_omits_deprecated_temperature():
    kwargs = _call("claude-opus-5")

    assert "temperature" not in kwargs
    assert model_accepts_temperature("claude-opus-5") is False
    assert model_accepts_temperature("claude-sonnet-5") is False


def test_opus_5_disables_thinking_to_keep_the_previous_latency_profile():
    """Opus 5 thinks when `thinking` is omitted; these are timed JSON calls."""
    kwargs = _call("claude-opus-5")

    assert kwargs["thinking"] == {"type": "disabled"}
    assert model_thinks_by_default("claude-opus-5") is True


def test_older_models_send_no_thinking_field():
    kwargs = _call("claude-sonnet-4-5-20250929")

    assert "thinking" not in kwargs


def test_thinking_can_be_re_enabled_globally(monkeypatch):
    monkeypatch.setenv("LLM_THINKING", "adaptive")
    kwargs = _call("claude-opus-5")

    assert kwargs["thinking"] == {"type": "adaptive"}


def test_explicit_thinking_argument_wins_over_the_default():
    client = _FakeClient()
    create_message(
        client,
        model="claude-opus-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "adaptive"},
    )

    assert client.messages.kwargs["thinking"] == {"type": "adaptive"}


def test_system_prompt_is_omitted_when_not_supplied():
    client = _FakeClient()
    create_message(
        client,
        model="claude-opus-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "hello"}],
    )

    assert "system" not in client.messages.kwargs


def test_first_text_skips_a_leading_thinking_block():
    response = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="deliberating"),
        SimpleNamespace(type="text", text='{"ok": true}'),
    ])

    assert first_text(response) == '{"ok": true}'


def test_first_text_accepts_untyped_blocks():
    response = SimpleNamespace(content=[SimpleNamespace(text="plain")])

    assert first_text(response) == "plain"


def test_first_text_raises_when_no_text_block_exists():
    response = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="only")])

    with pytest.raises(ValueError):
        first_text(response)
