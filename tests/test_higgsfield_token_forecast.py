"""Pure forecast tests. No credential access, sockets or provider requests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import localcontext
import json

import pytest

from core.automation import higgsfield_token_forecast as module
from core.automation.higgsfield_banner import BannerReference, QualityBannerRequest, QualityBannerError

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
ACCOUNT = "a" * 64


def request():
    return QualityBannerRequest(
        operation_id="11111111-1111-4111-8111-111111111111",
        workspace_id="22222222-2222-4222-8222-222222222222", client_id="yellow",
        source_item_id="33333333-3333-4333-8333-333333333333",
        source_url="https://x.com/Yellow/status/123", source_sha256="b" * 64,
        source_published_at="2026-10-15T10:00:00Z", headline="공식 소식", subtitle="검수용 공지",
        design_feedback="넉넉한 행간", references=(
            BannerReference("official_logo", "https://assets.example.com/logo.png", "c" * 64),))


def proof(req=None, now=NOW, account=ACCOUNT):
    return module.forecast_proof(req or request(), provider_account_sha256=account, now=now)


def test_exact_arithmetic_and_sanitized_reproducible_receipt():
    with localcontext() as context:
        context.prec = 2
        result = proof()
    assert result.estimated_usd == "3.391488"
    module.validate_forecast_proof(result)
    data = json.loads(result.forecast_json)
    assert data["component_usd_micros"] == {
        "text_input": 81920, "image_input": 131072, "text_output": 81920, "image_output": 1966080}
    assert data["policy"]["provider_enforced_maximum"] is False
    assert data["policy"]["cache_discount_assumed"] is False
    assert data["policy"]["settings"]["quality"] == "max"
    assert "공식 소식" not in result.forecast_json
    assert "assets.example.com" not in result.forecast_json
    assert "https://x.com/" not in result.forecast_json
    assert data["request_sha256"] == request().digest()


@pytest.mark.parametrize("account", [None, "", "a" * 63, "A" * 64, "secret"])
def test_invalid_account_is_rejected(account):
    with pytest.raises(QualityBannerError, match="token_forecast_invalid"):
        proof(account=account)


@pytest.mark.parametrize("now", [NOW.replace(tzinfo=None), module.RATE_OBSERVED_AT - timedelta(microseconds=1),
                                module.RATE_REVIEW_DUE])
def test_rates_require_review_before_new_forecast(now):
    with pytest.raises(QualityBannerError):
        proof(now=now)


def test_expiry_does_not_exceed_pinned_rate_review_date():
    now = module.RATE_REVIEW_DUE - timedelta(minutes=1)
    req = replace(request(), source_published_at=(now - timedelta(hours=1)).isoformat())
    result = proof(req, now=now)
    assert result.expires_at == module.RATE_REVIEW_DUE


@pytest.mark.parametrize("change", [
    {"quality": "high"}, {"resolution": "2k"}, {"enhance_prompt": True},
    {"aspect_ratio": "16:9"}, {"moderation": "low"}, {"prompt": "x" * 16385},
    {"extra": "unsupported"},
])
def test_request_or_quality_contract_changes_need_new_policy(monkeypatch, change):
    original = module.generation_payload
    monkeypatch.setattr(module, "generation_payload", lambda req: {**original(req), **change})
    with pytest.raises(QualityBannerError, match="token_forecast_invalid"):
        proof()


def test_policy_change_cannot_keep_old_digest(monkeypatch):
    result = proof()
    original = module.policy_record
    def changed():
        record = original()
        record["rates_usd_per_million"]["image_output"] = 31
        return record
    monkeypatch.setattr(module, "policy_record", changed)
    with pytest.raises(QualityBannerError, match="token_forecast_invalid"):
        proof()
    with pytest.raises(QualityBannerError, match="token_forecast_invalid"):
        module.validate_forecast_proof(result)


@pytest.mark.parametrize("field,value", [("estimated_at", NOW.replace(tzinfo=None)),
    ("expires_at", NOW), ("estimated_usd", 3.391488), ("forecast_json", "[]"),
    ("policy_sha256", "d" * 64), ("kind", "authenticated_provider_estimate")])
def test_invalid_proof_fails_closed(field, value):
    with pytest.raises(QualityBannerError, match="token_forecast_invalid"):
        module.validate_forecast_proof(replace(proof(), **{field: value}))
