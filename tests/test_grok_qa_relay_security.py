from api.grok_qa_security import grok_qa_relay_token, has_grok_qa_relay_access


TOKEN = "grok-qa-relay-dedicated-token-20260813"


def test_relay_token_is_dedicated_and_exact():
    env = {"GROK_QA_RELAY_TOKEN": TOKEN, "API_SECRET": "another-admin-secret-value"}
    assert grok_qa_relay_token(env) == TOKEN
    assert has_grok_qa_relay_access(TOKEN, env) is True
    assert has_grok_qa_relay_access(TOKEN + "x", env) is False
    assert has_grok_qa_relay_access("x" * len(TOKEN), env) is False
    assert has_grok_qa_relay_access("한" * len(TOKEN), env) is False


def test_relay_token_rejects_missing_weak_or_reused_values():
    assert has_grok_qa_relay_access("", {}) is False
    assert has_grok_qa_relay_access("short", {"GROK_QA_RELAY_TOKEN": "short"}) is False
    for name in (
        "API_SECRET",
        "GROK_QA_CONNECTOR_TOKEN",
        "GROK_QA_DISPATCH_TOKEN",
        "PUBLICATION_WORKER_TOKEN",
        "SUPABASE_SERVICE_ROLE_KEY",
        "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
    ):
        env = {"GROK_QA_RELAY_TOKEN": TOKEN, name: TOKEN}
        assert has_grok_qa_relay_access(TOKEN, env) is False
