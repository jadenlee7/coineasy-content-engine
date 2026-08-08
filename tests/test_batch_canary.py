from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.batch.canary import (
    CONFIG_SCHEMA,
    DISPATCH_SCHEMA,
    CanaryConfigApproval,
    CanaryDispatchApproval,
    canonical_json,
    canonical_sha256,
    config_subject,
    dispatch_subject,
)


START = datetime(2026, 7, 31, 15, tzinfo=timezone.utc)
END = START + timedelta(hours=48)


def _subject(**overrides):
    values = {
        "environment": "staging",
        "release_sha": "a" * 40,
        "supabase_url": "https://project-ref.supabase.co",
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "allowed_clients": frozenset({"origintrail"}),
        "daily_cap_usd": Decimal("0.05"),
        "max_claims": 1,
        "max_requests_per_batch": 1,
        "experiment_start_at": START,
        "experiment_end_at": END,
        "timezone_name": "Asia/Seoul",
    }
    values.update(overrides)
    return config_subject(**values)


def _config_receipt(subject_sha256: str, **overrides) -> str:
    values = {
        "version": CONFIG_SCHEMA,
        "approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "approved_by": "operator:test",
        "approved_at": "2026-07-31T15:00:00Z",
        "expires_at": "2026-07-31T17:00:00Z",
        "subject_sha256": subject_sha256,
    }
    values.update(overrides)
    return json.dumps(values)


def test_config_subject_is_deterministic_canonical_and_secret_free():
    first = _subject()
    second = _subject()

    assert canonical_json(first) == canonical_json(second)
    assert canonical_sha256(first) == canonical_sha256(second)
    encoded = canonical_json(first)
    assert "service_role" not in encoded
    assert "OPENAI" not in encoded
    assert "sk-" not in encoded
    assert first["authorized_provider_batches"] == 1
    assert first["daily_cap_usd"] == "0.05"


def test_config_subject_accepts_exact_production_shadow_environment():
    subject = _subject(environment="production")

    assert subject["environment"] == "production"
    assert subject["authorized_provider_batches"] == 1
    assert subject["automatic_external_effects"] is False


@pytest.mark.parametrize(
    "override, message",
    [
        ({"environment": "preview"}, "staging or production"),
        ({"release_sha": "b" * 39}, "40 lowercase hex"),
        ({"allowed_clients": frozenset({"origintrail", "yellow"})}, "only"),
        ({"daily_cap_usd": Decimal("0.50")}, "exactly 0.05"),
        ({"max_claims": 2}, "one claim"),
        ({"experiment_end_at": END + timedelta(days=1)}, "48 hours"),
        ({
            "experiment_start_at": START + timedelta(minutes=1),
            "experiment_end_at": END + timedelta(minutes=1),
        }, "exact KST midnights"),
    ],
)
def test_config_subject_rejects_any_expansion(override, message):
    with pytest.raises(ValueError, match=message):
        _subject(**override)


def test_config_receipt_is_short_lived_and_bound_to_subject():
    digest = canonical_sha256(_subject())
    approval = CanaryConfigApproval.from_json(
        _config_receipt(digest),
        expected_subject_sha256=digest,
    )

    assert approval.phase(datetime(2026, 7, 31, 14, tzinfo=timezone.utc)) == (
        "not_started"
    )
    assert approval.phase(datetime(2026, 7, 31, 16, tzinfo=timezone.utc)) == (
        "active"
    )
    assert approval.phase(datetime(2026, 7, 31, 17, tzinfo=timezone.utc)) == (
        "expired"
    )

    with pytest.raises(ValueError, match="does not match"):
        CanaryConfigApproval.from_json(
            _config_receipt("b" * 64),
            expected_subject_sha256=digest,
        )
    with pytest.raises(ValueError, match="at most 2 hours"):
        CanaryConfigApproval.from_json(
            _config_receipt(
                digest,
                expires_at="2026-07-31T17:00:01Z",
            ),
            expected_subject_sha256=digest,
        )


def test_dispatch_receipt_binds_exact_config_job_input_and_request():
    config_digest = canonical_sha256(_subject())
    approval_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    job_id = "11111111-1111-4111-8111-111111111111"
    input_sha256 = "c" * 64
    request_sha256 = "e" * 64
    subject = dispatch_subject(
        config_subject_sha256=config_digest,
        config_approval_id=approval_id,
        job_id=job_id,
        input_sha256=input_sha256,
        request_sha256=request_sha256,
    )
    receipt = json.dumps({
        "version": DISPATCH_SCHEMA,
        "dispatch_approval_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "approved_by": "operator:test",
        "approved_at": "2026-07-31T15:00:00Z",
        "expires_at": "2026-07-31T17:00:00Z",
        "job_id": job_id,
        "input_sha256": input_sha256,
        "request_sha256": request_sha256,
        "subject_sha256": canonical_sha256(subject),
    })

    parsed = CanaryDispatchApproval.from_json(
        receipt,
        config_subject_sha256=config_digest,
        config_approval_id=approval_id,
    )

    assert parsed.job_id == job_id
    assert parsed.input_sha256 == input_sha256
    assert parsed.request_sha256 == request_sha256
    assert subject["authorized_provider_batches"] == 1
    assert subject["authorized_total_usd"] == "0.05"

    with pytest.raises(ValueError, match="does not match"):
        CanaryDispatchApproval.from_json(
            receipt,
            config_subject_sha256="d" * 64,
            config_approval_id=approval_id,
        )

    tampered = json.loads(receipt)
    tampered["request_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="does not match"):
        CanaryDispatchApproval.from_json(
            json.dumps(tampered),
            config_subject_sha256=config_digest,
            config_approval_id=approval_id,
        )

    missing_request = json.loads(receipt)
    del missing_request["request_sha256"]
    with pytest.raises(ValueError, match="fields are invalid"):
        CanaryDispatchApproval.from_json(
            json.dumps(missing_request),
            config_subject_sha256=config_digest,
            config_approval_id=approval_id,
        )
