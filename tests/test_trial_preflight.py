"""Netlify config/upstream auth proof never becomes an intake/generation path."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from core.automation.trial_controller import TrialControllerBlocked, TrialGenerationPreflight

NOW = datetime(2026, 10, 14, 13, 4, 51, tzinfo=timezone.utc)
RELEASE, PLAN, TRIAL = "a" * 40, "b" * 64, "11111111-1111-4111-8111-111111111111"


def fixture(*, mutation=None, status=200, response=None, omit=None):
    calls, guards = [], []
    payload = {"schema_version": "ybo-trial-preflight@1", "ready": True,
        "netlify_release_sha": RELEASE, "trial_id": TRIAL, "plan_sha256": PLAN,
        "generation_gateway_enabled": True, "broad_public_flags_off": True,
        "upstream_api_auth_verified": True,
        "observed_at": NOW.isoformat()}
    payload.update(mutation or {})
    if omit is not None:
        payload.pop(omit)

    def handle(req):
        calls.append(req)
        assert req.method == "GET" and not req.content
        assert str(req.url) == "https://coineasy-newscard.netlify.app/api/ybo-trial-preflight"
        assert req.headers["x-studio-automation-key"] == "fixture-existing-automation-token"
        assert req.headers["x-studio-expected-release-sha"] == RELEASE
        assert req.headers["x-ybo-trial-id"] == TRIAL and req.headers["x-ybo-trial-plan-sha256"] == PLAN
        return response(req) if response else httpx.Response(status, json=payload)

    def guard():
        guards.append(True)
        return NOW

    studio = SimpleNamespace(base_url="https://coineasy-newscard.netlify.app",
        automation_token="fixture-existing-automation-token", transport=httpx.MockTransport(handle))
    gate = SimpleNamespace(plan=SimpleNamespace(payload={"release_sha": RELEASE}, trial_id=TRIAL, sha256=PLAN))
    return TrialGenerationPreflight(studio, gate, guard), calls, guards


def test_exact_preflight_only_get_and_fresh_bounded_response():
    preflight, calls, guards = fixture()
    assert asyncio.run(preflight.verify()) is None
    assert len(calls) == 1 and len(guards) == 2


def test_older_config_only_receipt_cannot_admit_work():
    preflight, calls, _ = fixture(omit="upstream_api_auth_verified")
    with pytest.raises(TrialControllerBlocked):
        asyncio.run(preflight.verify())
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", [
    {"ready": False}, {"ready": 1}, {"generation_gateway_enabled": False},
    {"broad_public_flags_off": False}, {"broad_public_flags_off": "true"},
    {"upstream_api_auth_verified": False}, {"upstream_api_auth_verified": "true"},
    {"netlify_release_sha": "c" * 40}, {"trial_id": "22222222-1111-4111-8111-111111111111"},
    {"plan_sha256": "c" * 64}, {"schema_version": "unknown"}, {"extra": "forbidden"},
    {"observed_at": NOW.replace(tzinfo=None).isoformat()},
    {"observed_at": (NOW - timedelta(seconds=6)).isoformat()},
    {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
])
def test_bad_preflight_receipt_never_retries_or_continues(mutation):
    preflight, calls, _ = fixture(mutation=mutation)
    with pytest.raises(TrialControllerBlocked, match="^trial_generation_preflight_blocked$"):
        asyncio.run(preflight.verify())
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 409, 500, 503])
def test_missing_or_off_config_failure_is_not_retried(status):
    preflight, calls, _ = fixture(status=status)
    with pytest.raises(TrialControllerBlocked): asyncio.run(preflight.verify())
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["redirect", "oversize", "malformed", "timeout", "content-type"])
def test_unknown_or_unsafe_response_held_once_with_no_secret_reflection(kind):
    def response(req):
        if kind == "timeout": raise httpx.ReadTimeout("secret upstream body", request=req)
        if kind == "redirect": return httpx.Response(302, headers={"location": "https://forbidden.invalid"})
        if kind == "oversize": return httpx.Response(200, content=b"x" * 4097, headers={"content-type": "application/json"})
        if kind == "malformed": return httpx.Response(200, content=b"secret upstream body", headers={"content-type": "application/json"})
        return httpx.Response(200, content=b"secret upstream body", headers={"content-type": "text/html"})
    preflight, calls, _ = fixture(response=response)
    with pytest.raises(TrialControllerBlocked, match="^trial_generation_preflight_blocked$"):
        asyncio.run(preflight.verify())
    assert len(calls) == 1
