from __future__ import annotations

import pytest

from core.grok_qa.settings import GrokQaSettings, grok_qa_dispatch_enabled


KEY = "xai-" + "a" * 40
CANARY_VERSION = "22222222-2222-4222-8222-222222222222"


def env(**overrides) -> dict[str, str]:
    values = {
        "GROK_QA_DISPATCH_ENABLED": "true",
        "XAI_API_KEY": KEY,
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "GROK_QA_EXPECTED_ENVIRONMENT": "production",
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "GROK_QA_RELEASE_SHA": "a" * 40,
    }
    values.update(overrides)
    return values


def test_dispatch_gate_is_literal_and_default_off():
    assert grok_qa_dispatch_enabled({}) is False
    assert grok_qa_dispatch_enabled(env()) is True
    with pytest.raises(ValueError, match="literal true or false"):
        grok_qa_dispatch_enabled({"GROK_QA_DISPATCH_ENABLED": "1"})


def test_settings_pin_model_and_bound_tools_cost_and_clients():
    settings = GrokQaSettings.from_env(env(
        GROK_QA_ALLOWED_CLIENTS="squid,yellow",
        GROK_QA_MAX_TURNS="2",
        GROK_QA_X_SEARCH_WINDOW_DAYS="1",
        GROK_QA_MAX_COST_IN_USD_TICKS="250000000",
    ))
    assert settings.model == "grok-4.5"
    assert settings.allowed_clients == ("squid", "yellow")
    assert settings.max_turns == 2
    assert settings.max_cost_in_usd_ticks == 250_000_000
    with pytest.raises(ValueError, match="must be grok-4.5"):
        GrokQaSettings.from_env(env(GROK_QA_MODEL="grok-beta"))
    with pytest.raises(ValueError, match="between 1 and 3"):
        GrokQaSettings.from_env(env(GROK_QA_MAX_TURNS="20"))


@pytest.mark.parametrize("name", [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "API_SECRET",
    "API_KEY_ADMIN",
    "STUDIO_ACCESS_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_PUBLISHABLE_KEY",
    "GROK_QA_CONNECTOR_TOKEN",
    "GROK_QA_RELAY_TOKEN",
    "PUBLICATION_WORKER_TOKEN",
    "TELEGRAM_CONTENT_OPS_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "X_BEARER_TOKEN",
    "TYPEFULLY_API_KEY",
    "DATABASE_URL",
    "GITHUB_TOKEN",
    "NETLIFY_AUTH_TOKEN",
    "RAILWAY_TOKEN",
])
@pytest.mark.parametrize("value", [KEY, "different-high-authority-secret"])
def test_worker_rejects_any_operational_credential_presence(
    name: str,
    value: str,
):
    with pytest.raises(ValueError, match="must not receive"):
        GrokQaSettings.from_env(env(**{name: value}))


def test_xai_key_and_dispatch_token_must_be_distinct():
    with pytest.raises(ValueError, match="dedicated secret"):
        GrokQaSettings.from_env(env(GROK_QA_DISPATCH_TOKEN=KEY))


def test_validation_mode_parses_everything_while_gate_is_off():
    settings = GrokQaSettings.from_env_for_validation(env(
        GROK_QA_DISPATCH_ENABLED="false"
    ))
    assert settings.xai_api_key == KEY
    with pytest.raises(ValueError, match="must be true"):
        GrokQaSettings.from_env(env(GROK_QA_DISPATCH_ENABLED="false"))


def test_environment_and_release_fences_are_exact_even_in_validation_mode():
    with pytest.raises(ValueError, match="exact production"):
        GrokQaSettings.from_env_for_validation(env(
            GROK_QA_DISPATCH_ENABLED="false",
            RAILWAY_ENVIRONMENT_NAME="staging",
        ))
    with pytest.raises(ValueError, match="release SHA fence"):
        GrokQaSettings.from_env_for_validation(env(
            GROK_QA_DISPATCH_ENABLED="false",
            GROK_QA_RELEASE_SHA="b" * 40,
        ))


def test_canary_is_literal_exact_version_scoping_and_default_off():
    normal = GrokQaSettings.from_env(env())
    assert normal.canary_mode is False
    assert normal.canary_content_version_id is None
    assert normal.active_canary_content_version_id is None

    canary = GrokQaSettings.from_env(env(
        GROK_QA_CANARY_MODE="true",
        GROK_QA_CANARY_CONTENT_VERSION_ID=CANARY_VERSION,
    ))
    assert canary.canary_mode is True
    assert canary.canary_content_version_id == CANARY_VERSION
    assert canary.active_canary_content_version_id == CANARY_VERSION

    with pytest.raises(ValueError, match="literal true or false"):
        GrokQaSettings.from_env(env(GROK_QA_CANARY_MODE="1"))
    with pytest.raises(ValueError, match="required in canary mode"):
        GrokQaSettings.from_env(env(GROK_QA_CANARY_MODE="true"))
    with pytest.raises(ValueError, match="canonical UUID"):
        GrokQaSettings.from_env(env(
            GROK_QA_CANARY_MODE="true",
            GROK_QA_CANARY_CONTENT_VERSION_ID="not-a-version-id",
        ))


def test_canary_target_is_rejected_when_literal_gate_is_false():
    with pytest.raises(ValueError, match="must be empty unless"):
        GrokQaSettings.from_env(env(
            GROK_QA_CANARY_MODE="false",
            GROK_QA_CANARY_CONTENT_VERSION_ID=CANARY_VERSION,
        ))
