from __future__ import annotations

import pytest

from core.automation.settings import AutomationSettings


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
    assert settings.daily_draft_limit == 4
    assert settings.enable_tutorials is False
    assert settings.timezone == "Asia/Seoul"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"SUPABASE_URL": "https://supabase.co.evil.test"}, "allowlist"),
        ({"SUPABASE_SERVICE_ROLE_KEY": "short"}, "required"),
        ({"CONTENT_STUDIO_WORKSPACE_ID": "not-a-uuid"}, "UUID"),
        ({"AUTOMATION_TIMEZONE": "UTC"}, "Asia/Seoul"),
        ({"AUTOMATION_DAILY_DRAFT_LIMIT": "5"}, "between 1 and 4"),
        ({"AUTOMATION_ENABLE_TUTORIALS": "maybe"}, "boolean"),
        ({"AUTOMATION_ENABLE_TUTORIALS": "true"}, "must remain false"),
        ({"STUDIO_AUTOMATION_TOKEN": "a" * 513}, "at most 512"),
    ],
)
def test_automation_settings_fail_closed(overrides, message):
    with pytest.raises(ValueError, match=message):
        AutomationSettings.from_env(_env(**overrides))
