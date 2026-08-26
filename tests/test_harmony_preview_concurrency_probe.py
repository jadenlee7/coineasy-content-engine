from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import threading

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "probe_harmony_preview_concurrency.py"
SPEC = importlib.util.spec_from_file_location("harmony_preview_probe", SCRIPT)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def test_probe_accepts_only_local_hosts() -> None:
    assert PROBE._is_local_host("localhost")
    assert PROBE._is_local_host("127.0.0.1")
    assert PROBE._is_local_host("/private/tmp/harmony-pg")
    assert not PROBE._is_local_host("db.example.supabase.co")


def test_preview_probe_requires_a_direct_child_branch_host() -> None:
    child = "vllwcbhqdojpjrssidcu"
    parent = "isuqcqwxpojgzevxfdwr"
    assert PROBE._validated_disposable_preview_ref(
        f"db.{child}.supabase.co", 5432, child, parent
    ) == child
    for host, port, expected, parent_ref in (
        (f"db.{parent}.supabase.co", 5432, parent, parent),
        (f"db.{child}.supabase.co", 5432, parent, parent),
        ("aws-0-us-east-1.pooler.supabase.com", 5432, child, parent),
        (f"db.{child}.supabase.co", 6543, child, parent),
    ):
        try:
            PROBE._validated_disposable_preview_ref(
                host, port, expected, parent_ref
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe Preview target was accepted")


def test_remote_psql_requires_verify_full_and_explicit_trust(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    PROBE.Psql("psql", "localhost", 5432, "postgres", "postgres", 1)
    PROBE.Psql("psql", "/private/tmp/harmony-pg", 5432, "postgres", "postgres", 1)
    remote = "db.vllwcbhqdojpjrssidcu.supabase.co"
    with pytest.raises(ValueError, match="PGSSLMODE=verify-full"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)

    monkeypatch.setenv("PGSSLMODE", "verify-full")
    with pytest.raises(ValueError, match="explicit PGSSLROOTCERT"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)

    root_certificate = tmp_path / "root.crt"
    root_certificate.write_text("unit-test-root", encoding="utf-8")
    monkeypatch.setenv("PGSSLROOTCERT", str(root_certificate))
    psql = PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    assert psql.environment["PGSSLMODE"] == "verify-full"
    assert psql.environment["PGSSLROOTCERT"] == str(root_certificate)

    captured: dict[str, object] = {}

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(PROBE.subprocess, "run", fake_run)
    psql._execute("select 1")
    assert captured["env"] == psql.environment


def test_rpc_runs_as_exact_scoped_role() -> None:
    claims = {
        "role": "coineasy_harmony_connector",
        "client_id": "squid",
    }
    sql = PROBE._rpc_sql(claims, "'{}'::jsonb")
    assert "set local role coineasy_harmony_connector" in sql
    assert "request.jwt.claims" in sql
    assert "service_role" not in sql


def test_expected_error_mismatch_redacts_database_output(monkeypatch) -> None:
    psql = PROBE.Psql("psql", "localhost", 5432, "postgres", "postgres", 1)
    leaked = "signed.jwt.claims.must-never-leak"
    monkeypatch.setattr(
        psql,
        "_execute",
        lambda _sql: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=leaked
        ),
    )
    with pytest.raises(RuntimeError) as caught:
        psql.expect_error("select unsafe", "expected_error")
    assert leaked not in str(caught.value)
    assert "database_output=redacted" in str(caught.value)


def test_psql_run_and_json_never_raise_database_output(monkeypatch) -> None:
    psql = PROBE.Psql("psql", "localhost", 5432, "postgres", "postgres", 1)
    secret = "private-content-from-failing-row"
    monkeypatch.setattr(
        psql,
        "_execute",
        lambda _sql: subprocess.CompletedProcess(
            args=[], returncode=7, stdout=secret, stderr=secret
        ),
    )
    for invoke in (
        lambda: psql.run("select private_content"),
        lambda: psql.json("select private_content"),
    ):
        with pytest.raises(RuntimeError) as caught:
            invoke()
        assert secret not in str(caught.value)
        assert "database_output=redacted" in str(caught.value)

    monkeypatch.setattr(
        psql,
        "_execute",
        lambda _sql: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=secret, stderr=""
        ),
    )
    with pytest.raises(RuntimeError) as caught:
        psql.json("select malformed_private_content")
    assert secret not in str(caught.value)
    assert "psql_json_decode_failed" in str(caught.value)


def test_connector_attestation_claims_are_complete_and_nonce_bound() -> None:
    before = int(datetime.now(UTC).timestamp())
    nonce = "11111111-1111-4111-8111-111111111111"
    claims = PROBE._claims(
        workspace_id="22222222-2222-4222-8222-222222222222",
        branch_ref="vllwcbhqdojpjrssidcu",
        role="coineasy_harmony_connector",
        capability="harmony_submit_quiz_bot",
        principal_id="33333333-3333-4333-8333-333333333333",
        release_sha="a" * 40,
        config_sha256="b" * 64,
        connector_id="squid_quiz_probe",
        attestation_registration_id="44444444-4444-4444-8444-444444444444",
        attestation_key_id="harmony-preview-unit-key-1",
        request_sha256="c" * 64,
        request_nonce=nonce,
    )
    assert claims["jti"] == nonce
    assert claims["request_nonce"] == nonce
    assert claims["attestation_registration_id"] == (
        "44444444-4444-4444-8444-444444444444"
    )
    assert claims["attestation_key_id"] == "harmony-preview-unit-key-1"
    assert claims["request_sha256"] == "c" * 64
    assert before <= int(claims["iat"]) <= int(datetime.now(UTC).timestamp())

    with pytest.raises(ValueError, match="must be supplied together"):
        PROBE._claims(
            workspace_id="22222222-2222-4222-8222-222222222222",
            branch_ref="vllwcbhqdojpjrssidcu",
            role="coineasy_harmony_connector",
            capability="harmony_submit_quiz_bot",
            principal_id="33333333-3333-4333-8333-333333333333",
            release_sha="a" * 40,
            config_sha256="b" * 64,
            attestation_registration_id=(
                "44444444-4444-4444-8444-444444444444"
            ),
        )


def test_plan_probe_rejects_missing_epoch_before_any_plan_row() -> None:
    source = SCRIPT.read_text()
    assert 'missing_epoch_plan_claims.pop("iat")' in source
    assert 'missing_epoch_plan_claims.pop("exp")' in source
    assert "harmony_preview_plan_scope_invalid" in source
    assert "invalid plan claims preempted the fixed specialist" in source


def test_plan_probe_rejects_missing_subject_before_any_plan_row() -> None:
    source = SCRIPT.read_text()
    assert 'missing_subject_plan_claims.pop("sub")' in source
    assert "harmony_preview_plan_scope_invalid" in source
    assert "invalid plan claims preempted the fixed specialist" in source


def test_connector_request_digest_has_a_fixed_cross_language_vector() -> None:
    vector = PROBE.CONNECTOR_REQUEST_DIGEST_VECTOR
    digest = PROBE._connector_request_sha256(
        workspace_id=vector["workspace_id"],
        client_id=vector["client_id"],
        registration_id=vector["registration_id"],
        connector_receipt_id=vector["connector_receipt_id"],
        signal=vector["signal"],
    )
    assert digest == PROBE.CONNECTOR_REQUEST_DIGEST_VECTOR_SHA256
    assert digest == "cfdf90b7d13d375ab4db44d32ab3fd115f5c830ddf99d526251d1c642add9bb9"
    payload = PROBE._connector_request_payload(
        workspace_id=vector["workspace_id"],
        client_id=vector["client_id"],
        registration_id=vector["registration_id"],
        connector_receipt_id=vector["connector_receipt_id"],
        signal=vector["signal"],
    )
    assert payload["signal_payload_sha256"] == (
        "a908c2820db28b21f5ef4caf467c3d0eef274b96a5e35082d021834624b2e8c6"
    )
    assert payload["domain"] == "coineasy:harmony:preview:connector-request:v1"
    assert payload["rpc"] == (
        "public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)"
    )


def test_connector_request_digest_recomputes_payload_and_detects_drift() -> None:
    vector = PROBE.CONNECTOR_REQUEST_DIGEST_VECTOR
    drifted_signal = {
        **vector["signal"],
        "attempts": 65,
    }
    with pytest.raises(ValueError, match="independent canonical hash"):
        PROBE._connector_request_sha256(
            workspace_id=vector["workspace_id"],
            client_id=vector["client_id"],
            registration_id=vector["registration_id"],
            connector_receipt_id=vector["connector_receipt_id"],
            signal=drifted_signal,
        )
    drifted_signal.pop("payload_sha256")
    drifted = PROBE._connector_request_sha256(
        workspace_id=vector["workspace_id"],
        client_id=vector["client_id"],
        registration_id=vector["registration_id"],
        connector_receipt_id=vector["connector_receipt_id"],
        signal=drifted_signal,
    )
    assert drifted != PROBE.CONNECTOR_REQUEST_DIGEST_VECTOR_SHA256


def test_database_digest_is_only_an_afterward_equality_assertion() -> None:
    class FakePsql:
        sql = ""

        def run(self, sql: str) -> str:
            self.sql = sql
            return PROBE.CONNECTOR_REQUEST_DIGEST_VECTOR_SHA256

    psql = FakePsql()
    vector = PROBE.CONNECTOR_REQUEST_DIGEST_VECTOR
    expected = PROBE._connector_request_sha256(
        workspace_id=vector["workspace_id"],
        client_id=vector["client_id"],
        registration_id=vector["registration_id"],
        connector_receipt_id=vector["connector_receipt_id"],
        signal=vector["signal"],
    )
    result = PROBE._assert_connector_request_sha256_matches_database(
        psql,
        expected_sha256=expected,
        workspace_id=vector["workspace_id"],
        client_id=vector["client_id"],
        registration_id=vector["registration_id"],
        connector_receipt_id=vector["connector_receipt_id"],
        signal=vector["signal"],
    )
    assert result is None
    assert "private.harmony_preview_connector_request_sha256(" in psql.sql
    assert "'squid'" in psql.sql
    assert "22222222-2222-4222-8222-222222222222" in psql.sql
    assert "33333333-3333-4333-8333-333333333333" in psql.sql


def test_probe_contract_is_closed_and_fail_closed() -> None:
    source = SCRIPT.read_text()
    assert 'topic = "official_update"' in source
    assert "CONCURRENCY = 64" in source
    assert "new_count, reused_count" in source
    assert '"signals": 1' in source
    assert '"connector_receipts": 1' in source
    assert '"request_receipts": 1' in source
    assert "side_effect_baseline_unchanged" in source
    assert "confirm_disposable_local" in source
    assert "confirm_disposable_preview" in source
    assert "expected_branch_ref" in source
    assert "parent_project_ref" in source
    assert "release_sha" in source
    assert "config_sha256" in source
    assert "command_timeout_seconds" in source
    assert "fence_ttl_minutes" in source
    assert "max_connections" in source
    assert "psql_timeout_commit_state_unknown_no_retry" in source
    assert "refusing to run the disposable Preview probe against Production" in source
    assert "_seed_specialists_sql" in source
    assert "harmony_preview_squid_specialist_bindings" in source
    assert "harmony_preview_connector_registrations" in source
    assert "harmony_preview_connector_registration_revocations" in source
    assert "harmony_preview_connector_request_receipts" in source
    assert "harmony_preview_qa_denial_receipts" in source
    assert "connector_request_receipt" in source
    assert "same_nonce_changed_claims_rejected" in source
    assert "new_nonce_same_digest_rejected" in source
    assert "record_preview_harmony_squid_qa_denial" in source
    assert '"qa_denial_race": qa_denial_race' in source
    assert '"passed_qa_stages": 0' in source
    assert '"qa_denial_receipts": 1' in source
    assert "harmony_preview_qa_output_already_denied" in source
    assert '"before": True' in source
    assert '"after": False' in source
    assert "_race_exactly_once" in source
    assert '"harmony-preview-concurrency-proof@2"' in source
    assert '"plan": plan_race' in source
    for stage in (
        "private_content",
        "independent_qa",
        "operator_inbox",
        "recap",
    ):
        assert f'operation_races[stage] = stage_race' in source
        assert f'"{stage}"' in source
    assert '"wrong_principal_attempts": 5' in source
    assert '"wrong_principal_preemption_rows"' in source
    assert '"operator_inbox_stage4_delta"' in source
    assert '"recap_operator_inbox_delta"' in source
    assert "plan_expression(uid())" in source
    assert "_stage_expression(ids, stage, uid(), inbox_id, qa_evidence)" in source
    assert "import requests" not in source
    assert "urllib.request" not in source


def test_fixed_specialist_seed_has_five_distinct_stage_owners() -> None:
    ids = {"workspace": "11111111-1111-4111-8111-111111111111"}
    stages = (
        "plan",
        "private_content",
        "independent_qa",
        "operator_inbox",
        "recap",
    )
    principals = {
        stage: f"00000000-0000-4000-8000-{index:012d}"
        for index, stage in enumerate(stages, start=1)
    }
    sql = PROBE._seed_specialists_sql(
        ids,
        principals,
        "vllwcbhqdojpjrssidcu",
        "a" * 40,
        "b" * 64,
        "2026-08-25T18:49:07Z",
    )
    assert "private.harmony_preview_squid_specialist_bindings" in sql
    assert "'specialists', pg_catalog.count(*)" in sql
    assert "'distinct_principals'" in sql
    for stage, principal in principals.items():
        assert f"'{stage}'" in sql
        assert f"'{principal}'::uuid" in sql


def test_connector_registration_seed_is_lane_exact_and_nonsecret() -> None:
    workspace_id = "11111111-1111-4111-8111-111111111111"
    registrations = [
        {
            "lane": "quiz_bot",
            "capability": "harmony_submit_quiz_bot",
            "connector_id": "squid_quiz_probe",
            "principal_id": "22222222-2222-4222-8222-222222222222",
            "registration_id": "33333333-3333-4333-8333-333333333333",
            "attestation_key_id": "harmony-preview-quiz-key-1",
            "release_sha": "a" * 40,
            "config_sha256": "b" * 64,
        }
    ]
    sql = PROBE._seed_connector_registrations_sql(
        workspace_id=workspace_id,
        branch_ref="vllwcbhqdojpjrssidcu",
        registrations=registrations,
        expires_at="2026-08-26T20:00:00Z",
    )
    assert "private.harmony_preview_connector_registrations" in sql
    assert "attestation_key_id" in sql
    assert "harmony-preview-quiz-key-1" in sql
    assert "secret" not in sql.lower()
    assert "registration.expires_at <= fence.expires_at" in sql
    assert "created_at" not in sql.split(") values", 1)[0]

    revoke_sql = PROBE._revoke_connector_registration_sql(
        workspace_id=workspace_id,
        registration_id="33333333-3333-4333-8333-333333333333",
        revocation_id="44444444-4444-4444-8444-444444444444",
    )
    assert "harmony_preview_connector_registration_revocations" in revoke_sql
    assert "registration.registration_sha256" in revoke_sql
    assert "connector_disabled" in revoke_sql


def test_revocation_lock_winner_race_is_two_connection_and_redacted() -> None:
    class FakePsql:
        timeout_seconds = 1.0

        def __init__(self, loser_error: str) -> None:
            self.loser_error = loser_error
            self.sql: list[str] = []

        def run(self, sql: str) -> str:
            assert "pg_catalog.pg_stat_activity" in sql
            assert "pg_catalog.strpos(activity.query," in sql
            assert "activity.pid <> pg_catalog.pg_backend_pid()" in sql
            assert "position(" not in sql
            return "1"

        def _execute(
            self, sql: str, *, timeout_seconds: float | None = None
        ) -> subprocess.CompletedProcess[str]:
            assert timeout_seconds == 10.0
            self.sql.append(sql)
            if "pg_catalog.pg_sleep(2)" in sql:
                return subprocess.CompletedProcess([], 0, "", "")
            return subprocess.CompletedProcess([], 1, "", self.loser_error)

    loser_sql = "begin;\nselect ('{}'::jsonb)::text;\ncommit;"
    psql = FakePsql("harmony_preview_plan_input_not_current")
    result = PROBE._revocation_lock_winner_race(
        psql,
        revocation_sql="insert into private.revocations values (1);",
        loser_sql=loser_sql,
        expected_loser_error="harmony_preview_plan_input_not_current",
    )
    assert result == {
        "connections": 2,
        "revocation_lock_acquired_first": True,
        "typed_loser_waited_on_lock": True,
        "typed_loser_rejected_after_recheck": True,
    }
    assert len(psql.sql) == 2
    assert "harmony_revocation_winner_" in psql.sql[0]
    assert "harmony_revocation_loser_" in psql.sql[1]

    secret = "signed.claims.must-not-leak"
    with pytest.raises(RuntimeError) as caught:
        PROBE._revocation_lock_winner_race(
            FakePsql(secret),
            revocation_sql="insert into private.revocations values (1);",
            loser_sql=loser_sql,
            expected_loser_error="harmony_preview_plan_input_not_current",
        )
    assert secret not in str(caught.value)
    assert "database_output=redacted" in str(caught.value)


def test_operator_and_recap_bind_the_same_existing_inbox() -> None:
    ids = {
        "workspace": "11111111-1111-4111-8111-111111111111",
        "round": "22222222-2222-4222-8222-222222222222",
        "plan": "33333333-3333-4333-8333-333333333333",
    }
    inbox_id = "44444444-4444-4444-8444-444444444444"
    operator = PROBE._stage_expression(
        ids,
        "operator_inbox",
        "55555555-5555-4555-8555-555555555555",
        inbox_id,
    )
    recap = PROBE._stage_expression(
        ids,
        "recap",
        "66666666-6666-4666-8666-666666666666",
        inbox_id,
    )
    private_content = PROBE._stage_expression(
        ids,
        "private_content",
        "77777777-7777-4777-8777-777777777777",
    )
    assert f"'{inbox_id}'::uuid" in operator
    assert f"'{inbox_id}'::uuid" in recap
    assert "null::uuid" in private_content


def test_revoked_currentness_stage_negative_has_valid_next_qa_preconditions() -> None:
    ids = {
        "workspace": "11111111-1111-4111-8111-111111111111",
        "round": "22222222-2222-4222-8222-222222222222",
        "plan": "33333333-3333-4333-8333-333333333333",
    }
    output_sha256 = "d" * 64
    evidence = {
        "schema_version": "harmony-independent-qa-evidence@1",
        "reviewed_output_sha256": output_sha256,
        "criteria": {
            "automatic_publication": False,
            "factual_binding": True,
            "no_external_calls": True,
            "private_only": True,
        },
        "findings": [],
        "verdict": "passed",
        "verifier_version": "harmony-deterministic-qa@1",
    }
    claims = PROBE._claims(
        workspace_id=ids["workspace"],
        branch_ref="vllwcbhqdojpjrssidcu",
        role="coineasy_harmony_qa",
        capability="harmony_independent_qa",
        principal_id="44444444-4444-4444-8444-444444444444",
        release_sha="a" * 40,
        config_sha256="b" * 64,
    )
    expression = PROBE._stage_expression(
        ids,
        "independent_qa",
        "55555555-5555-4555-8555-555555555555",
        qa_evidence=evidence,
    )
    assert claims["role"] == "coineasy_harmony_qa"
    assert claims["capability"] == "harmony_independent_qa"
    assert "'independent_qa'" in expression
    assert "null::uuid" in expression
    assert '"reviewed_output_sha256":"' + output_sha256 + '"' in expression
    assert '"verdict":"passed"' in expression
    source = SCRIPT.read_text()
    assert "denial_qa_claims,\n            _stage_expression(" in source
    assert 'denial_ids,\n                "independent_qa"' in source
    assert '"harmony_preview_stage_input_expired_or_tampered"' in source


def test_exactly_once_race_accepts_fresh_transport_ids(monkeypatch) -> None:
    monkeypatch.setattr(PROBE, "CONCURRENCY", 4)
    lock = threading.Lock()
    committed = False

    def invoke(_: int) -> dict[str, object]:
        nonlocal committed
        with lock:
            reused = committed
            committed = True
        return {
            "ok": True,
            "reused": reused,
            "stage_receipt": {
                "receipt_id": "11111111-1111-4111-8111-111111111111",
                "receipt_sha256": "b" * 64,
                "operation_key_sha256": "a" * 64,
            },
            "external_calls": False,
            "provider_calls": False,
            "publication_calls": False,
            "automatic_publication": False,
        }

    row, counts = PROBE._race_exactly_once("plan", invoke)
    assert row["ok"] is True
    assert counts == {"new": 1, "reused": 3}


def test_qa_denial_race_is_exactly_once_and_has_no_downstream_effects(
    monkeypatch,
) -> None:
    monkeypatch.setattr(PROBE, "CONCURRENCY", 4)
    lock = threading.Lock()
    committed = False
    receipt = {
        "denial_receipt_id": "11111111-1111-4111-8111-111111111111",
        "payload_sha256": "a" * 64,
    }

    def invoke(_: int) -> dict[str, object]:
        nonlocal committed
        with lock:
            reused = committed
            committed = True
        return {
            "ok": False,
            "denied": True,
            "reused": reused,
            "qa_denial_receipt": receipt,
            "external_calls": False,
            "provider_calls": False,
            "publication_calls": False,
            "automatic_publication": False,
        }

    row, counts = PROBE._race_qa_denial(invoke)
    assert row["denied"] is True
    assert counts == {"new": 1, "reused": 3}

    expression = PROBE._qa_denial_expression(
        {
            "workspace": "22222222-2222-4222-8222-222222222222",
            "round": "33333333-3333-4333-8333-333333333333",
            "plan": "44444444-4444-4444-8444-444444444444",
        },
        "55555555-5555-4555-8555-555555555555",
        {
            "schema_version": "harmony-independent-qa-evidence@1",
            "verdict": "failed",
        },
    )
    assert "public.record_preview_harmony_squid_qa_denial(" in expression
    assert "55555555-5555-4555-8555-555555555555" in expression
    assert '"verdict":"failed"' in expression
