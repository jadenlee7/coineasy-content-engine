from core.llm.anthropic_compat import create_message, model_accepts_temperature


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return {"ok": True}


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _call(model: str):
    client = _FakeClient()
    create_message(
        client,
        model=model,
        max_tokens=100,
        temperature=0.2,
        system="system",
        messages=[{"role": "user", "content": "hello"}],
    )
    return client.messages.kwargs


def test_opus_4_8_omits_deprecated_temperature():
    kwargs = _call("claude-opus-4-8")
    assert "temperature" not in kwargs
    assert model_accepts_temperature("claude-opus-4-8-20260701") is False


def test_older_models_keep_configured_temperature():
    kwargs = _call("claude-sonnet-4-5-20250929")
    assert kwargs["temperature"] == 0.2
