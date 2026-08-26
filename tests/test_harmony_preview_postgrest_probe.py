from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import inspect
import threading
from urllib import error

import pytest


SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "probe_harmony_preview_postgrest.py"
)
SPEC = importlib.util.spec_from_file_location(
    "harmony_preview_postgrest_probe", SCRIPT
)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def _decode(segment: str) -> dict[str, object]:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def test_project_url_is_exact_child_and_never_parent() -> None:
    child = "vllwcbhqdojpjrssidcu"
    parent = "isuqcqwxpojgzevxfdwr"
    assert PROBE._validated_project_url(
        f"https://{child}.supabase.co", child, parent
    ) == f"https://{child}.supabase.co"
    for unsafe in (
        f"http://{child}.supabase.co",
        f"https://{parent}.supabase.co",
        f"https://{child}.supabase.co/rest/v1",
        f"https://{child}.supabase.co?token=unsafe",
        f"https://user:password@{child}.supabase.co",
    ):
        with pytest.raises(ValueError):
            PROBE._validated_project_url(unsafe, child, parent)


def test_hs256_jwt_is_short_lived_claim_envelope_signed_in_memory() -> None:
    secret = "preview-only-secret-which-is-longer-than-thirty-two-bytes"
    claims = {
        "iss": "supabase",
        "aud": "authenticated",
        "role": "coineasy_harmony_connector",
        "exp": 200,
        "iat": 100,
        "automatic_publication": False,
    }
    token = PROBE._mint_hs256_jwt(claims, secret)
    header_segment, claims_segment, signature = token.split(".")
    assert _decode(header_segment) == {"alg": "HS256", "typ": "JWT"}
    assert _decode(claims_segment) == claims
    expected = hmac.new(
        secret.encode(),
        f"{header_segment}.{claims_segment}".encode(),
        hashlib.sha256,
    ).digest()
    padded = signature + "=" * (-len(signature) % 4)
    assert hmac.compare_digest(base64.urlsafe_b64decode(padded), expected)
    assert secret not in token


def test_http_secrets_are_fixed_env_only_and_scrubbed_from_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publishable = "sb_publishable_preview_key_for_unit_test"
    secret = "legacy-preview-secret-that-is-never-printed-or-written"
    monkeypatch.setenv(PROBE.PUBLISHABLE_KEY_ENV, publishable)
    monkeypatch.setenv(PROBE.LEGACY_JWT_SECRET_ENV, secret)
    assert PROBE._load_http_secrets() == (publishable, secret)
    assert PROBE.PUBLISHABLE_KEY_ENV not in os.environ
    assert PROBE.LEGACY_JWT_SECRET_ENV not in os.environ


def _legacy_project_key(role: str) -> str:
    def encode(value: object) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(value, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()

    return ".".join((
        encode({"alg": "HS256", "typ": "JWT"}),
        encode({"iss": "supabase", "role": role}),
        encode(b"unit-test-signature".decode()),
    ))


def test_apikey_rejects_secret_and_service_role_credentials() -> None:
    PROBE._validate_publishable_key("sb_publishable_preview_key")
    PROBE._validate_publishable_key(_legacy_project_key("anon"))
    for unsafe in (
        "sb_secret_preview_key_must_be_rejected",
        _legacy_project_key("service_role"),
        "not-a-project-key",
    ):
        with pytest.raises(ValueError):
            PROBE._validate_publishable_key(unsafe)


def test_credentials_are_scrubbed_before_psql_or_any_subprocess() -> None:
    source = inspect.getsource(PROBE.run_probe)
    assert source.index("_load_http_secrets()") < source.index("BASE.Psql(")


class _FakeResponse:
    status = 200

    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def read(self, _limit: int) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


EXPECTED_CONNECTOR_RECEIPT_ID = "22222222-2222-4222-8222-222222222222"
EXPECTED_SIGNAL = {
    "schema_version": "agent-harmony-signal@1",
    "signal_id": "11111111-1111-4111-8111-111111111111",
    "source_event_id": "33333333-3333-4333-8333-333333333333",
    "producer_principal_id": "44444444-4444-4444-8444-444444444444",
}
EXPECTED_SIGNAL["payload_sha256"] = PROBE._json_sha256(EXPECTED_SIGNAL)
EXPECTED_REGISTRATION = {
    "registration_id": "55555555-5555-4555-8555-555555555555",
    "attestation_key_id": "harmony-preview-test-key",
    "connector_id": "squid_quiz_signed_jwt_positive",
    "principal_id": EXPECTED_SIGNAL["producer_principal_id"],
    "release_sha": "a" * 40,
    "config_sha256": "b" * 64,
}
EXPECTED_CLAIMS = {
    "capability": "harmony_submit_quiz_bot",
    "jti": "66666666-6666-4666-8666-666666666666",
    "request_sha256": "c" * 64,
    "role": "coineasy_harmony_connector",
}


def _rehash_payload(value: dict[str, object]) -> None:
    value["payload_sha256"] = PROBE._json_sha256({
        key: item for key, item in value.items() if key != "payload_sha256"
    })


def _success(reused: bool) -> dict[str, object]:
    token_claims_sha256 = PROBE._json_sha256(EXPECTED_CLAIMS)
    connector_receipt = {
        "receipt_id": EXPECTED_CONNECTOR_RECEIPT_ID,
        "signal_id": EXPECTED_SIGNAL["signal_id"],
        "source_event_id": EXPECTED_SIGNAL["source_event_id"],
        "producer_principal_id": EXPECTED_REGISTRATION["principal_id"],
        "producer_release_sha": EXPECTED_REGISTRATION["release_sha"],
        "config_sha256": EXPECTED_REGISTRATION["config_sha256"],
        "connector_id": EXPECTED_REGISTRATION["connector_id"],
        "capability": EXPECTED_CLAIMS["capability"],
        "verification_reference_sha256": token_claims_sha256,
        "signal_payload_sha256": EXPECTED_SIGNAL["payload_sha256"],
        "verification_method": "jwt",
        "side_effects_performed": False,
        "automatic_publication": False,
    }
    _rehash_payload(connector_receipt)
    request_receipt = {
        "request_receipt_id": "77777777-7777-4777-8777-777777777777",
        "registration_id": EXPECTED_REGISTRATION["registration_id"],
        "registration_sha256": "d" * 64,
        "attestation_key_id": EXPECTED_REGISTRATION["attestation_key_id"],
        "request_nonce": EXPECTED_CLAIMS["jti"],
        "request_sha256": EXPECTED_CLAIMS["request_sha256"],
        "token_claims_sha256": token_claims_sha256,
        "signal_id": EXPECTED_SIGNAL["signal_id"],
        "signal_payload_sha256": EXPECTED_SIGNAL["payload_sha256"],
        "connector_receipt_id": EXPECTED_CONNECTOR_RECEIPT_ID,
        "connector_receipt_sha256": connector_receipt["payload_sha256"],
        "raw_content_included": False,
        "external_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }
    _rehash_payload(request_receipt)
    return {
        "ok": True,
        "reused": reused,
        "signal": dict(EXPECTED_SIGNAL),
        "connector_receipt": connector_receipt,
        "connector_request_receipt": request_receipt,
        "external_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }


def test_64_way_http_race_accepts_one_new_and_63_reused() -> None:
    lock = threading.Lock()
    calls = 0

    def opener(_req: object, *, timeout: float):
        nonlocal calls
        assert timeout == 3
        with lock:
            calls += 1
            index = calls
        return _FakeResponse(_success(reused=index != 1))

    client = PROBE.PostgrestClient(
        "https://vllwcbhqdojpjrssidcu.supabase.co",
        "sb_publishable_test",
        3,
        opener=opener,
    )
    rows = PROBE._run_race(
        client,
        {"safe": True},
        "signed.jwt.value",
        expected_signal=EXPECTED_SIGNAL,
        expected_connector_receipt_id=EXPECTED_CONNECTOR_RECEIPT_ID,
        expected_registration=EXPECTED_REGISTRATION,
        expected_claims=EXPECTED_CLAIMS,
    )
    assert calls == 64
    assert sum(row["reused"] is False for row in rows) == 1
    assert sum(row["reused"] is True for row in rows) == 63


def _validate_success_fixture(value: dict[str, object]) -> dict[str, object]:
    return PROBE._validate_success(
        value,
        expected_signal=EXPECTED_SIGNAL,
        expected_connector_receipt_id=EXPECTED_CONNECTOR_RECEIPT_ID,
        expected_registration=EXPECTED_REGISTRATION,
        expected_claims=EXPECTED_CLAIMS,
    )


def test_success_rejects_self_consistent_but_unexpected_token_claim_hash() -> None:
    value = json.loads(json.dumps(_success(reused=False)))
    receipt = value["connector_receipt"]
    request_receipt = value["connector_request_receipt"]
    assert isinstance(receipt, dict)
    assert isinstance(request_receipt, dict)
    receipt["verification_reference_sha256"] = "e" * 64
    _rehash_payload(receipt)
    request_receipt["token_claims_sha256"] = "e" * 64
    request_receipt["connector_receipt_sha256"] = receipt["payload_sha256"]
    _rehash_payload(request_receipt)

    with pytest.raises(
        RuntimeError,
        match="postgrest_connector_expected_binding_invalid",
    ):
        _validate_success_fixture(value)


def test_success_rejects_self_consistent_but_unexpected_signal_identity() -> None:
    value = json.loads(json.dumps(_success(reused=False)))
    signal = value["signal"]
    receipt = value["connector_receipt"]
    request_receipt = value["connector_request_receipt"]
    assert isinstance(signal, dict)
    assert isinstance(receipt, dict)
    assert isinstance(request_receipt, dict)
    signal["signal_id"] = "88888888-8888-4888-8888-888888888888"
    _rehash_payload(signal)
    receipt["signal_id"] = signal["signal_id"]
    receipt["signal_payload_sha256"] = signal["payload_sha256"]
    _rehash_payload(receipt)
    request_receipt["signal_id"] = signal["signal_id"]
    request_receipt["signal_payload_sha256"] = signal["payload_sha256"]
    request_receipt["connector_receipt_sha256"] = receipt["payload_sha256"]
    _rehash_payload(request_receipt)

    with pytest.raises(
        RuntimeError,
        match="postgrest_signal_expected_binding_invalid",
    ):
        _validate_success_fixture(value)


def test_success_rejects_self_consistent_but_unexpected_connector_identity() -> None:
    value = json.loads(json.dumps(_success(reused=False)))
    receipt = value["connector_receipt"]
    request_receipt = value["connector_request_receipt"]
    assert isinstance(receipt, dict)
    assert isinstance(request_receipt, dict)
    receipt["receipt_id"] = "99999999-9999-4999-8999-999999999999"
    _rehash_payload(receipt)
    request_receipt["connector_receipt_id"] = receipt["receipt_id"]
    request_receipt["connector_receipt_sha256"] = receipt["payload_sha256"]
    _rehash_payload(request_receipt)

    with pytest.raises(
        RuntimeError,
        match="postgrest_connector_expected_binding_invalid",
    ):
        _validate_success_fixture(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_sha256", "e" * 64),
        ("connector_receipt_id", "99999999-9999-4999-8999-999999999999"),
        ("registration_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ],
)
def test_success_rejects_self_consistent_but_unexpected_request_binding(
    field: str,
    value: str,
) -> None:
    response = json.loads(json.dumps(_success(reused=False)))
    request_receipt = response["connector_request_receipt"]
    assert isinstance(request_receipt, dict)
    request_receipt[field] = value
    _rehash_payload(request_receipt)

    with pytest.raises(
        RuntimeError,
        match="postgrest_connector_request_expected_binding_invalid",
    ):
        _validate_success_fixture(response)


@pytest.mark.parametrize("label", sorted(PROBE.NEGATIVE_EXPECTATIONS))
def test_negative_response_requires_exact_status_code_and_message(
    label: str,
) -> None:
    status, code, message = PROBE.NEGATIVE_EXPECTATIONS[label]
    assert PROBE._validate_negative_response(
        label,
        status,
        {"code": code, "message": message},
    ) == {"status": status, "code": code, "message": message}
    for wrong in (
        (status + 1, code, message),
        (status, "P0000", message),
        (status, code, message + "_unrelated"),
    ):
        with pytest.raises(
            RuntimeError,
            match="negative gate returned a different typed error",
        ):
            PROBE._validate_negative_response(
                label,
                wrong[0],
                {"code": wrong[1], "message": wrong[2]},
            )


def test_transport_ambiguity_is_not_retried_or_leaked() -> None:
    calls = 0

    def opener(_req: object, *, timeout: float):
        nonlocal calls
        calls += 1
        raise error.URLError("test transport failure")

    client = PROBE.PostgrestClient(
        "https://vllwcbhqdojpjrssidcu.supabase.co",
        "publishable-value-must-not-appear",
        2,
        opener=opener,
    )
    with pytest.raises(
        PROBE.CommitStateUnknown,
        match="postgrest_commit_state_unknown_no_retry",
    ) as caught:
        client.post({"payload": "safe"}, "bearer-value-must-not-appear")
    assert calls == 1
    assert "publishable-value" not in str(caught.value)
    assert "bearer-value" not in str(caught.value)


def test_probe_contract_has_negative_matrix_and_no_secret_cli_args() -> None:
    source = SCRIPT.read_text()
    for label in (
        "wrong_client",
        "wrong_workspace",
        "wrong_lane",
        "missing_capability",
        "wrong_role",
        "future_jwt",
        "expired_jwt",
        "extreme_past_iat",
        "service_role",
        "tampered_payload",
        "changed_digest",
        "same_nonce_changed_claims",
        "new_nonce_same_digest",
        "revoked_registration",
    ):
        assert label in source
    assert "negative_row_delta" in source
    assert set(PROBE.NEGATIVE_EXPECTATIONS) == {
        "wrong_client",
        "wrong_workspace",
        "wrong_lane",
        "missing_capability",
        "wrong_role",
        "future_jwt",
        "expired_jwt",
        "extreme_past_iat",
        "service_role",
        "wrong_ref",
        "tampered_payload",
        "changed_digest",
        "same_nonce_changed_claims",
        "new_nonce_same_digest",
        "revoked_registration",
    }
    assert "EXPECTED_NEGATIVE_STATUSES" not in source
    assert "CONCURRENCY = 64" in source
    assert "postgrest_commit_state_unknown_no_retry" in source
    assert "--jwt-secret" not in source
    assert "--publishable-key" not in source
    assert "provider_calls\": False" in source
    assert "publication_calls\": False" in source
    assert "automatic_publication\": False" in source
    assert "harmony_preview_connector_registrations" in source
    assert "harmony_preview_connector_registration_revocations" in source
    assert "harmony_preview_connector_request_receipts" in source
    assert "connector_request_receipt_delta" in source
    assert "connector_request_nonce_equals_jti" in source
    assert '"connector_registration_rows": 1' in source
