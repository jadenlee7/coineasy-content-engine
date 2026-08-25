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


def _success(reused: bool) -> dict[str, object]:
    digest = "a" * 64
    return {
        "ok": True,
        "reused": reused,
        "signal": {"signal_id": "id", "payload_sha256": digest},
        "connector_receipt": {
            "receipt_id": "receipt",
            "payload_sha256": digest,
            "verification_reference_sha256": digest,
            "signal_payload_sha256": digest,
            "verification_method": "jwt",
            "side_effects_performed": False,
            "automatic_publication": False,
        },
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
    rows = PROBE._run_race(client, {"safe": True}, "signed.jwt.value")
    assert calls == 64
    assert sum(row["reused"] is False for row in rows) == 1
    assert sum(row["reused"] is True for row in rows) == 63


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
        "wrong_role",
        "future_jwt",
        "expired_jwt",
        "service_role",
        "tampered_payload",
    ):
        assert label in source
    assert "negative_row_delta" in source
    assert PROBE.EXPECTED_NEGATIVE_STATUSES == {400, 401, 403, 404}
    assert "CONCURRENCY = 64" in source
    assert "postgrest_commit_state_unknown_no_retry" in source
    assert "--jwt-secret" not in source
    assert "--publishable-key" not in source
    assert "provider_calls\": False" in source
    assert "publication_calls\": False" in source
    assert "automatic_publication\": False" in source
