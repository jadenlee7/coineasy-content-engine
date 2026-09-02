from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import threading

import pytest

from core.agent_control.codex_gate import (
    squid_codex_gate_assignment_key,
    squid_codex_gate_work_key,
)


SCRIPT = Path(__file__).parents[1] / "scripts" / "probe_harmony_preview_concurrency.py"
SPEC = importlib.util.spec_from_file_location("harmony_preview_probe", SCRIPT)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def _read_only_pipe(payload: bytes) -> int:
    reader_fd, writer_fd = os.pipe()
    try:
        written = 0
        while written < len(payload):
            written += os.write(writer_fd, payload[written:])
    finally:
        os.close(writer_fd)
    return reader_fd


class FakeServerConcurrencyPsql:
    def __init__(self, concurrency: int) -> None:
        self.backend_concurrency_target = concurrency
        self.server_concurrency_evidence: dict[str, dict[str, object]] = {}
        self.concurrency = concurrency

    def json(self, sql: str) -> dict[str, object]:
        if "create unlogged table" in sql:
            return {
                "participants": 0,
                "released": False,
                "server_peak": 0,
            }
        return {
            "participants": self.concurrency,
            "released": True,
            "server_peak": self.concurrency,
        }

    def run_with_server_concurrency_gate(
        self,
        _gate: object,
        invoke: object,
    ) -> dict[str, object]:
        assert callable(invoke)
        return invoke()

    def run(self, sql: str) -> str:
        if "drop table if exists" in sql:
            return "dropped"
        if "set released = true" in sql:
            return "released"
        raise AssertionError("unexpected fake gate SQL")


def test_probe_accepts_only_local_hosts() -> None:
    assert PROBE._is_local_host("localhost")
    assert PROBE._is_local_host("127.0.0.1")
    assert PROBE._is_local_host("/private/tmp/harmony-pg")
    assert not PROBE._is_local_host("db.example.supabase.co")


def test_preview_probe_accepts_only_exact_child_database_transports() -> None:
    child = "vllwcbhqdojpjrssidcu"
    parent = "isuqcqwxpojgzevxfdwr"
    assert PROBE._validated_disposable_preview_ref(
        f"db.{child}.supabase.co",
        5432,
        child,
        parent,
        "direct",
        "postgres",
        "postgres",
    ) == child
    assert PROBE._validated_disposable_preview_ref(
        "aws-0-us-east-1.pooler.supabase.com",
        5432,
        child,
        parent,
        "supavisor-session",
        f"postgres.{child}",
        "postgres",
    ) == child
    assert PROBE._validated_disposable_preview_ref(
        "gcp-1-europe-west1.pooler.supabase.com",
        5432,
        child,
        parent,
        "supavisor-session",
        f"postgres.{child}",
        "postgres",
    ) == child
    for host, port, expected, parent_ref, transport, user, database in (
        (f"db.{parent}.supabase.co", 5432, parent, parent, "direct", "postgres", "postgres"),
        (f"db.{child}.supabase.co", 5432, parent, parent, "direct", "postgres", "postgres"),
        ("aws-0-us-east-1.pooler.supabase.com", 5432, child, parent, "direct", "postgres", "postgres"),
        (f"db.{child}.supabase.co", 6543, child, parent, "direct", "postgres", "postgres"),
        (f"db.{child}.supabase.co", 5432, child, parent, "direct", f"postgres.{child}", "postgres"),
        ("aws-0-us-east-1.pooler.supabase.com", 5432, child, parent, "supavisor-session", "postgres", "postgres"),
        ("green.pooler.supabase.com", 5432, child, parent, "supavisor-session", f"postgres.{child}", "postgres"),
        ("x.y.pooler.supabase.com", 5432, child, parent, "supavisor-session", f"postgres.{child}", "postgres"),
        ("aws-0-us-east-1.pooler.supabase.com,evil.example", 5432, child, parent, "supavisor-session", f"postgres.{child}", "postgres"),
        ("aws-0-us-east-1.pooler.supabase.com", 5432, child, parent, "supavisor-session", f"postgres.{parent}", "postgres"),
        ("aws-0-us-east-1.pooler.supabase.com", 5432, child, parent, "supavisor-session", f"postgres.{child}", "other"),
    ):
        try:
            PROBE._validated_disposable_preview_ref(
                host,
                port,
                expected,
                parent_ref,
                transport,
                user,
                database,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe Preview target was accepted")


def test_concurrency_probe_cli_requires_database_transport() -> None:
    required = [
        "--host", "localhost",
        "--user", "postgres",
        "--release-sha", "a" * 40,
        "--config-sha256", "b" * 64,
    ]
    with pytest.raises(SystemExit):
        PROBE.parse_args(required)
    assert PROBE.parse_args(
        [
            *required,
            "--database-transport", "direct",
            "--backend-concurrency-target", "64",
        ]
    ).database_transport == "direct"


def test_transport_specific_backend_capacity_reserve_is_fixed() -> None:
    assert PROBE._required_free_database_connections("direct", 64) == 72
    assert (
        PROBE._required_free_database_connections("supavisor-session", 2)
        == 4
    )
    assert (
        PROBE._required_free_database_connections("supavisor-session", 15)
        == 17
    )
    with pytest.raises(ValueError):
        PROBE._required_free_database_connections("direct", 63)


def test_server_gate_setup_shape_failure_drops_attempted_table() -> None:
    gate = PROBE._new_server_concurrency_gate("plan", 64, 15)

    class SetupMismatchPsql:
        dropped = False

        def json(self, _sql: str) -> dict[str, object]:
            return {
                "participants": 1,
                "released": False,
                "server_peak": 0,
            }

        def run(self, sql: str) -> str:
            assert gate.table_name in sql
            assert "drop table if exists" in sql
            self.dropped = True
            return "dropped"

    psql = SetupMismatchPsql()
    with pytest.raises(RuntimeError, match="gate setup failed"):
        PROBE._setup_server_concurrency_gate(psql, gate)
    assert psql.dropped is True


def test_server_gate_sql_records_real_backend_peak_before_rpc() -> None:
    gate = PROBE._new_server_concurrency_gate("plan", 64, 15)
    wrapped = gate.wrap("select rpc_result;")
    assert "pg_advisory_lock_shared" in wrapped
    assert "lock.mode = 'ShareLock'" in wrapped
    assert "observed.participant_count >= 15" in wrapped
    assert "server_peak = greatest(" in wrapped
    assert "pg_catalog.greatest" not in wrapped
    assert wrapped.index("pg_advisory_lock_shared") < wrapped.index(
        "select rpc_result;"
    )


def test_happy_path_identity_readback_is_canonical_distinct_and_db_bound() -> None:
    ids = {
        "workspace": "44444444-4444-4444-8444-444444444444",
        "round": "11111111-1111-4111-8111-111111111111",
        "plan": "22222222-2222-4222-8222-222222222222",
        "inbox": "33333333-3333-4333-8333-333333333333",
    }

    class FakePsql:
        sql = ""

        def json(self, sql: str) -> dict[str, object]:
            self.sql = sql
            return {
                "readback_rows": 1,
                "round_id": ids["round"],
                "plan_id": ids["plan"],
                "inbox_id": ids["inbox"],
            }

    psql = FakePsql()
    assert PROBE._verify_happy_path_identities(psql, ids) == {
        "round_id": ids["round"],
        "plan_id": ids["plan"],
        "inbox_id": ids["inbox"],
    }
    assert "agent_runtime.harmony_rounds" in psql.sql
    assert "agent_runtime.harmony_plans" in psql.sql
    assert "agent_runtime.harmony_operator_inbox" in psql.sql
    assert "agent_runtime.harmony_stage_receipts" in psql.sql
    assert "recap.stage = 'recap'" in psql.sql
    assert ids["round"] not in psql.sql
    assert ids["plan"] not in psql.sql
    assert ids["inbox"] not in psql.sql


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("round", "11111111-1111-1111-8111-111111111111"),
        ("plan", "22222222-2222-4222-8222-22222222222A"),
        ("inbox", "not-a-uuid"),
    ),
)
def test_happy_path_identity_readback_rejects_noncanonical_generated_ids(
    field: str,
    value: str,
) -> None:
    ids = {
        "workspace": "44444444-4444-4444-8444-444444444444",
        "round": "11111111-1111-4111-8111-111111111111",
        "plan": "22222222-2222-4222-8222-222222222222",
        "inbox": "33333333-3333-4333-8333-333333333333",
    }
    ids[field] = value

    class UnexpectedPsql:
        def json(self, _sql: str) -> dict[str, object]:
            raise AssertionError("invalid generated identities reached the database")

    with pytest.raises(RuntimeError, match="canonical lowercase UUID4"):
        PROBE._verify_happy_path_identities(UnexpectedPsql(), ids)


def test_happy_path_identity_readback_rejects_duplicates_and_db_tampering() -> None:
    ids = {
        "workspace": "44444444-4444-4444-8444-444444444444",
        "round": "11111111-1111-4111-8111-111111111111",
        "plan": "22222222-2222-4222-8222-222222222222",
        "inbox": "33333333-3333-4333-8333-333333333333",
    }

    duplicate_ids = {**ids, "plan": ids["round"]}

    class UnexpectedPsql:
        def json(self, _sql: str) -> dict[str, object]:
            raise AssertionError("duplicate generated identities reached the database")

    with pytest.raises(RuntimeError, match="were not distinct"):
        PROBE._verify_happy_path_identities(UnexpectedPsql(), duplicate_ids)

    class TamperedPsql:
        def json(self, _sql: str) -> dict[str, object]:
            return {
                "readback_rows": 1,
                "round_id": ids["round"],
                "plan_id": "55555555-5555-4555-8555-555555555555",
                "inbox_id": ids["inbox"],
            }

    with pytest.raises(RuntimeError, match="persisted happy-path identity"):
        PROBE._verify_happy_path_identities(TamperedPsql(), ids)


def test_remote_psql_requires_verify_full_and_explicit_trust(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    monkeypatch.delenv(PROBE.SUPABASE_CA_FD_ENV, raising=False)
    PROBE.Psql("psql", "localhost", 5432, "postgres", "postgres", 1)
    PROBE.Psql("psql", "/private/tmp/harmony-pg", 5432, "postgres", "postgres", 1)
    remote = "db.vllwcbhqdojpjrssidcu.supabase.co"
    with pytest.raises(ValueError, match="PGSSLMODE=verify-full"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)

    monkeypatch.setenv("PGSSLMODE", "verify-full")
    with pytest.raises(ValueError, match="PGGSSENCMODE=disable"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    monkeypatch.setenv("PGGSSENCMODE", "disable")
    with pytest.raises(ValueError, match="PGSSLCERTMODE=disable"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    monkeypatch.setenv("PGSSLCERTMODE", "disable")
    with pytest.raises(ValueError, match="in-memory password"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    monkeypatch.setenv("PGPASSWORD", "preview-password")
    with pytest.raises(ValueError, match="inherited unlinked Supabase CA fd"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)

    monkeypatch.setenv("PGSSLROOTCERT", "system")
    with pytest.raises(ValueError, match="inherited unlinked Supabase CA fd"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    monkeypatch.delenv("PGSSLROOTCERT")

    exact_ca = (
        SCRIPT.parents[1] / "certs/supabase-prod-ca-2021.crt"
    ).read_bytes()
    linked_ca = tmp_path / "linked-root.crt"
    linked_ca.write_bytes(exact_ca)
    linked_ca.chmod(0o600)
    linked_fd = os.open(linked_ca, os.O_RDONLY)
    monkeypatch.setenv(PROBE.SUPABASE_CA_FD_ENV, str(linked_fd))
    with pytest.raises(ValueError, match="inherited unlinked Supabase CA fd"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    with pytest.raises(OSError):
        os.fstat(linked_fd)

    wrong_digest_fd = _read_only_pipe(b"wrong digest")
    monkeypatch.setenv(PROBE.SUPABASE_CA_FD_ENV, str(wrong_digest_fd))
    with pytest.raises(ValueError, match="inherited unlinked Supabase CA fd"):
        PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    with pytest.raises(OSError):
        os.fstat(wrong_digest_fd)

    exact_fd = _read_only_pipe(exact_ca)
    monkeypatch.setenv(PROBE.SUPABASE_CA_FD_ENV, str(exact_fd))
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    monkeypatch.setenv("PGSERVICE", "ambient-route-bypass")
    monkeypatch.setenv("PGSSLKEY", "/tmp/ambient-client.key")
    psql = PROBE.Psql("psql", remote, 5432, "postgres", "postgres", 1)
    with pytest.raises(OSError):
        os.fstat(exact_fd)

    assert psql.environment["PGSSLMODE"] == "verify-full"
    assert psql.environment["PGGSSENCMODE"] == "disable"
    assert psql.environment["PGSSLCERTMODE"] == "disable"
    assert "PGSSLROOTCERT" not in psql.environment
    assert "PGHOSTADDR" not in psql.environment
    assert "PGSERVICE" not in psql.environment
    assert "PGSSLKEY" not in psql.environment
    assert PROBE.SUPABASE_CA_FD_ENV not in psql.environment

    captured: dict[str, object] = {}

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        inherited_fds = kwargs["pass_fds"]
        assert isinstance(inherited_fds, tuple) and len(inherited_fds) == 1
        certificate_fd = inherited_fds[0]
        assert kwargs["env"]["PGSSLROOTCERT"] == f"/dev/fd/{certificate_fd}"
        certificate_stat = os.fstat(certificate_fd)
        assert certificate_stat.st_nlink == 0
        assert os.read(certificate_fd, 4097) == exact_ca
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(PROBE.subprocess, "run", fake_run)
    psql._execute("select 1")
    assert captured["close_fds"] is True


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
    assert '"harmony-preview-concurrency-proof@5"' in source
    assert '"identities": identities' in source
    assert '"plan": plan_race' in source
    for stage in ("private_content", "operator_inbox", "recap"):
        assert f'operation_races[stage] = stage_race' in source
        assert f'"{stage}"' in source
    for operation in ("prepare", "claim", "start", "submit", "verify"):
        assert f'codex_qa_races["{operation}"]' in source
    for rpc in (
        "prepare_preview_harmony_squid_codex_qa",
        "claim_preview_harmony_squid_codex_qa",
        "start_preview_harmony_squid_codex_qa_attempt",
        "submit_preview_harmony_squid_codex_qa_result",
        "verify_preview_harmony_squid_codex_qa_result",
    ):
        assert rpc in source
    assert 'operation_races["independent_qa"] = codex_qa_races["verify"]' in source
    assert '"codex_transitions": 11' in source
    assert '"codex_reconciliations": 1' in source
    assert '"codex_stage_links": 1' in source
    assert '"codex_result_not_current_race"' in source
    assert '"action": "result_not_current"' in source
    assert '"transition_from": "result_submitted"' in source
    assert '"transition_to": "blocked"' in source
    assert '"verifications": 0' in source
    assert '"wrong_principal_attempts": 5' in source
    assert '"wrong_principal_preemption_rows"' in source
    assert (
        source.index('claimed, codex_qa_races["claim"]')
        < source.index("reconciliation_before_wrong_actor")
        < source.index('started, codex_qa_races["start"]')
    )
    assert '"operator_inbox_stage4_delta"' in source
    assert '"recap_operator_inbox_delta"' in source
    assert "plan_expression(uid())" in source
    assert "_stage_expression(ids, stage, uid(), inbox_id)" in source
    assert '"verdict": "passed"' not in source
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


def test_revoked_currentness_uses_durable_codex_gate() -> None:
    ids = {
        "workspace": "11111111-1111-4111-8111-111111111111",
        "round": "22222222-2222-4222-8222-222222222222",
        "plan": "33333333-3333-4333-8333-333333333333",
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
    prepare = PROBE._codex_prepare_expression(ids)
    verify = PROBE._codex_verify_expression(ids, "d" * 64)
    assert claims["role"] == "coineasy_harmony_qa"
    assert claims["capability"] == "harmony_independent_qa"
    assert "prepare_preview_harmony_squid_codex_qa" in prepare
    assert prepare.endswith(", 0::bigint)")
    assert "verify_preview_harmony_squid_codex_qa_result" in verify
    assert "'" + "d" * 64 + "'" in verify
    source = SCRIPT.read_text()
    assert "denial_qa_claims,\n            _codex_prepare_expression(denial_ids)" in source
    assert "_codex_verify_expression(ids, codex_work_key)" in source
    assert '"harmony_preview_codex_gate_not_current"' in source


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

    psql = FakeServerConcurrencyPsql(PROBE.CONCURRENCY)
    row, counts = PROBE._race_exactly_once(psql, "plan", invoke)
    assert row["ok"] is True
    assert counts == {"new": 1, "reused": 3}


def test_codex_gate_race_helpers_prove_idempotence_and_single_execution(
    monkeypatch,
) -> None:
    monkeypatch.setattr(PROBE, "CONCURRENCY", 4)
    lock = threading.Lock()
    calls = 0

    def idempotent(_: int) -> dict[str, object]:
        nonlocal calls
        with lock:
            reused = calls > 0
            calls += 1
        return {"reused": reused, "work_key": "a" * 64, "request_key": "b" * 64}

    psql = FakeServerConcurrencyPsql(PROBE.CONCURRENCY)
    row, counts = PROBE._race_codex_idempotent(
        psql, "prepare", idempotent, ("work_key", "request_key")
    )
    assert row["work_key"] == "a" * 64
    assert counts == {"new": 1, "reused": 3}

    calls = 0

    def claim(_: int) -> dict[str, object]:
        nonlocal calls
        with lock:
            won = calls == 0
            calls += 1
        if not won:
            return {"claimed": False}
        return {
            "claimed": True,
            "work_key": "a" * 64,
            "request_key": "b" * 64,
            "claim_fence_sha256": "c" * 64,
        }

    winner, claim_counts = PROBE._race_codex_claim(psql, claim)
    assert winner["claim_fence_sha256"] == "c" * 64
    assert claim_counts == {"claimed": 1, "not_claimed": 3}

    calls = 0

    def start(_: int) -> dict[str, object]:
        nonlocal calls
        with lock:
            authorized = calls == 0
            calls += 1
        return {
            "execute_authorized": authorized,
            "reused": not authorized,
            "work_key": "a" * 64,
            "attempt_fence_sha256": "d" * 64,
        }

    attempt, start_counts = PROBE._race_codex_start(psql, start)
    assert attempt["execute_authorized"] is True
    assert start_counts == {"authorized": 1, "replay_non_authorizing": 3}


def test_stale_codex_result_reconciliation_race_is_exactly_once(
    monkeypatch,
) -> None:
    monkeypatch.setattr(PROBE, "CONCURRENCY", 4)
    lock = threading.Lock()
    calls = 0
    work_key = "a" * 64

    def reconcile(_: int) -> dict[str, object]:
        nonlocal calls
        with lock:
            won = calls == 0
            calls += 1
        if won:
            return {
                "blocked": True,
                "outcome_unknown": False,
                "pending": False,
                "reconciled": True,
                "status": "blocked",
                "work_key": work_key,
            }
        return {
            "blocked": False,
            "outcome_unknown": False,
            "pending": False,
            "reconciled": False,
            "work_key": None,
        }

    psql = FakeServerConcurrencyPsql(PROBE.CONCURRENCY)
    winner, counts = PROBE._race_codex_reconciliation(
        psql, reconcile,
        expected_work_key=work_key,
    )
    assert winner["status"] == "blocked"
    assert counts == {"reconciled": 1, "no_op": 3}


def test_codex_gate_expressions_match_eight_argument_submit_contract() -> None:
    ids = {
        "workspace": "11111111-1111-4111-8111-111111111111",
        "round": "22222222-2222-4222-8222-222222222222",
        "plan": "33333333-3333-4333-8333-333333333333",
    }
    criteria = {
        "automatic_publication_off": True,
        "factual_binding": True,
        "no_external_calls": True,
        "output_contract_valid": True,
        "private_boundary_preserved": True,
        "source_lineage_complete": True,
    }
    assert PROBE._codex_prepare_expression(ids).endswith(", 0::bigint)")
    assert PROBE._codex_claim_expression(ids).endswith(", 900)")
    assert "start_preview_harmony_squid_codex_qa_attempt" in (
        PROBE._codex_start_expression(ids, "a" * 64, "b" * 64)
    )
    submit = PROBE._codex_submit_result_expression(
        ids, "a" * 64, "b" * 64, criteria,
        qa_output_sha256="c" * 64, verdict="pass", finding_codes=[],
    )
    assert "submit_preview_harmony_squid_codex_qa_result" in submit
    assert "array[]::text[]" in submit
    assert '"automatic_publication_off":true' in submit
    assert "actual_cost" not in submit
    assert "verify_preview_harmony_squid_codex_qa_result" in (
        PROBE._codex_verify_expression(ids, "a" * 64)
    )


def test_probe_codex_identity_matches_offline_runner_exactly() -> None:
    lineage = {
        "workspace_id": "11111111-1111-4111-8111-111111111111",
        "client_id": "squid",
        "round_id": "22222222-2222-4222-8222-222222222222",
        "plan_id": "33333333-3333-4333-8333-333333333333",
        "plan_receipt_sha256": "1" * 64,
        "private_content_receipt_sha256": "2" * 64,
        "private_content_output_sha256": "3" * 64,
        "official_content_version_id": (
            "44444444-4444-4444-8444-444444444444"
        ),
        "official_source_item_id": "55555555-5555-4555-8555-555555555555",
        "official_source_binding_sha256": "6" * 64,
        "content_snapshot_sha256": "7" * 64,
        "signal_input_set_sha256": "8" * 64,
        "signal_manifest_sha256": "8" * 64,
        "signal_producer_principal_ids": [
            "66666666-6666-4666-8666-666666666666",
            "77777777-7777-4777-8777-777777777777",
            "88888888-8888-4888-8888-888888888888",
            "99999999-9999-4999-8999-999999999999",
        ],
    }
    offline_request = {
        "workspace_id": lineage["workspace_id"],
        "client_id": "squid",
        "round_id": lineage["round_id"],
        "plan_id": lineage["plan_id"],
        "plan_receipt": {"receipt_sha256": lineage["plan_receipt_sha256"]},
        "private_content_receipt": {
            "receipt_sha256": lineage["private_content_receipt_sha256"],
            "output_sha256": lineage["private_content_output_sha256"],
        },
        "source_lineage": lineage,
    }
    expected_work_key = squid_codex_gate_work_key(offline_request)
    assert PROBE._codex_work_key_from_lineage(lineage) == expected_work_key

    binding_sha256 = "a" * 64
    assert PROBE._codex_assignment_key(
        expected_work_key, binding_sha256
    ) == squid_codex_gate_assignment_key({
        "work_key": expected_work_key,
        "reviewer_binding": {"binding_sha256": binding_sha256},
    })


def test_probe_executes_missing_claim_negatives_before_prepare() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '("capability", "jti", "max_cost_microusd")' in source
    assert "incomplete_claims.pop(missing_claim)" in source
    assert '"harmony_preview_codex_qa_scope_invalid"' in source
    assert "canonical_identity.get(\"assignment_key\")" in source


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

    psql = FakeServerConcurrencyPsql(PROBE.CONCURRENCY)
    row, counts = PROBE._race_qa_denial(psql, invoke)
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
