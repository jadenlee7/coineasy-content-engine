from __future__ import annotations

import pytest

from core.automation.settings import AUTOMATION_CLIENTS, AutomationSettings


SIGNALS_URL = (
    "https://jlxbywqofrltyttklcqy.supabase.co"
    "/functions/v1/content-signals-api"
)


def _env(**overrides):
    values = {
        "SUPABASE_URL": "https://project-ref.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "s" * 64,
        "CONTENT_STUDIO_WORKSPACE_ID": "00000000-0000-4000-8000-000000000001",
        "X_BEARER_TOKEN": "x" * 32,
        "STUDIO_BASE_URL": "https://coineasy-newscard.netlify.app",
        "STUDIO_AUTOMATION_TOKEN": "a" * 64,
    }
    values.update(overrides)
    return values


def test_automation_settings_are_review_first_by_default():
    settings = AutomationSettings.from_env(_env())

    assert settings.lookback_hours == 30
    assert settings.allowed_clients == AUTOMATION_CLIENTS
    assert settings.daily_draft_limit == 4
    assert settings.enable_tutorials is False
    assert settings.timezone == "Asia/Seoul"
    assert settings.easyfarm_content_signals_url is None
    assert settings.easyfarm_content_signals_token is None
    assert settings.easyfarm_content_signals_window_days == 7
    assert settings.daily_review_mode is False
    assert settings.daily_review_start_kst == "09:00"


def test_daily_review_intake_requires_explicit_opt_in_and_accepts_kst_time():
    settings = AutomationSettings.from_env(_env(
        AUTOMATION_DAILY_REVIEW_MODE="true",
        AUTOMATION_DAILY_REVIEW_START_KST="10:30",
    ))
    assert settings.daily_review_mode is True
    assert settings.daily_review_start_kst == "10:30"


@pytest.mark.parametrize("value", ["9:00", "24:00", "09:60", "09:00Z", "", None])
def test_daily_review_time_is_strict_even_for_direct_settings(value):
    with pytest.raises(ValueError, match="HH:MM"):
        AutomationSettings(
            "", "", "", "", "", "", daily_review_start_kst=value,
        )


@pytest.mark.parametrize("value", ["true", 1, None])
def test_daily_review_direct_settings_cannot_use_truthy_non_booleans(value):
    with pytest.raises(ValueError, match="boolean"):
        AutomationSettings("", "", "", "", "", "", daily_review_mode=value)


def test_automation_settings_scope_clients_in_canonical_order():
    settings = AutomationSettings.from_env(_env(
        AUTOMATION_ALLOWED_CLIENTS="babylon,origintrail",
    ))

    assert settings.allowed_clients == ("origintrail", "babylon")


def test_automation_settings_enable_the_exact_easyfarm_signals_endpoint():
    settings = AutomationSettings.from_env(_env(
        EASYFARM_CONTENT_SIGNALS_URL=SIGNALS_URL,
        EASYFARM_CONTENT_SIGNALS_TOKEN="e" * 64,
        EASYFARM_CONTENT_SIGNALS_WINDOW_DAYS="14",
    ))

    assert settings.easyfarm_content_signals_url == SIGNALS_URL
    assert settings.easyfarm_content_signals_token == "e" * 64
    assert settings.easyfarm_content_signals_window_days == 14


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"SUPABASE_URL": "https://supabase.co.evil.test"}, "allowlist"),
        ({"SUPABASE_SERVICE_ROLE_KEY": "short"}, "required"),
        ({"CONTENT_STUDIO_WORKSPACE_ID": "not-a-uuid"}, "UUID"),
        ({"AUTOMATION_TIMEZONE": "UTC"}, "Asia/Seoul"),
        ({"AUTOMATION_DAILY_DRAFT_LIMIT": "5"}, "between 1 and 4"),
        ({"AUTOMATION_ALLOWED_CLIENTS": ""}, "unique nonempty subset"),
        (
            {"AUTOMATION_ALLOWED_CLIENTS": "origintrail,origintrail"},
            "unique nonempty subset",
        ),
        (
            {"AUTOMATION_ALLOWED_CLIENTS": "origintrail,unknown"},
            "unique nonempty subset",
        ),
        ({"AUTOMATION_ENABLE_TUTORIALS": "maybe"}, "boolean"),
        ({"AUTOMATION_ENABLE_TUTORIALS": "true"}, "must remain false"),
        ({"AUTOMATION_DAILY_REVIEW_MODE": "maybe"}, "boolean"),
        ({"AUTOMATION_DAILY_REVIEW_START_KST": "25:00"}, "HH:MM"),
        ({"STUDIO_AUTOMATION_TOKEN": "a" * 513}, "at most 512"),
        (
            {"EASYFARM_CONTENT_SIGNALS_URL": SIGNALS_URL},
            "must be configured together",
        ),
        (
            {"EASYFARM_CONTENT_SIGNALS_TOKEN": "e" * 64},
            "must be configured together",
        ),
        (
            {
                "EASYFARM_CONTENT_SIGNALS_URL": (
                    "https://evil.test/functions/v1/content-signals-api"
                ),
                "EASYFARM_CONTENT_SIGNALS_TOKEN": "e" * 64,
            },
            "allowlist",
        ),
        (
            {
                "EASYFARM_CONTENT_SIGNALS_URL": SIGNALS_URL + "?redirect=1",
                "EASYFARM_CONTENT_SIGNALS_TOKEN": "e" * 64,
            },
            "allowlist",
        ),
        (
            {
                "EASYFARM_CONTENT_SIGNALS_URL": SIGNALS_URL,
                "EASYFARM_CONTENT_SIGNALS_TOKEN": "short",
            },
            "32 to 512",
        ),
        (
            {"EASYFARM_CONTENT_SIGNALS_WINDOW_DAYS": "32"},
            "between 1 and 31",
        ),
    ],
)
def test_automation_settings_fail_closed(overrides, message):
    with pytest.raises(ValueError, match=message):
        AutomationSettings.from_env(_env(**overrides))
