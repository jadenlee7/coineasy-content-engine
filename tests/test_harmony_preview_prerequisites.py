from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from test_harmony_preview_proof_runner import (
    CHILD_REF, DB_SECRET, JWT_SECRET, MANAGEMENT_TOKEN, POOLER_HOST,
    PUBLISHABLE, RUNNER, FakeRunner, _args, _assert_valid_receipt_digest,
    _clock, _fake_exact_checkout,
)

GROUPS = ("relations", "columns", "column_types", "keys", "routines", "roles")
UNTRUSTED = "UNTRUSTED_REMOTE_SCHEMA_TEXT_MUST_NOT_ESCAPE"


def _observations() -> dict[str, list[bool]]:
    return {
        "relations": [True] * len(RUNNER.SCHEMA_PREREQUISITE_RELATIONS),
        "columns": [True] * len(RUNNER.SCHEMA_PREREQUISITE_COLUMNS),
        "column_types": [True] * len(RUNNER.SCHEMA_PREREQUISITE_COLUMNS),
        "keys": [True] * len(RUNNER.SCHEMA_PREREQUISITE_KEYS),
        "routines": [True] * len(RUNNER.SCHEMA_PREREQUISITE_ROUTINES),
        "roles": [True] * len(RUNNER.SCHEMA_PREREQUISITE_ROLES),
    }


def _raw(payload: object | None = None) -> bytes:
    return json.dumps(
        _observations() if payload is None else payload, separators=(",", ":"),
    ).encode("utf-8")


@pytest.fixture(autouse=True)
def _isolated_fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV, MANAGEMENT_TOKEN)
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "unused-ambient-token")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unused-parent-secret")


class PrerequisiteRunner(FakeRunner):
    def __init__(self, response=None, *, query_error=None, **kwargs):
        super().__init__(**kwargs)
        self.schema_response = response
        self.schema_error = query_error
        self.schema_calls = []
        self.schema_environment_references = []

    def run_bytes(self, command, *, env=None, input_bytes=None, cwd=None,
                  timeout, code, pass_fds=()):
        default = super().run_bytes(
            command, env=env, input_bytes=input_bytes, cwd=cwd,
            timeout=timeout, code=code, pass_fds=pass_fds,
        )
        if code != "preview_schema_prerequisites":
            return default
        assert env is not None
        self.schema_environment_references.append(env)
        self.schema_calls.append({
            "command": list(command), "environment": dict(env),
            "input_bytes": input_bytes, "pass_fds": pass_fds, "timeout": timeout,
        })
        if self.schema_error is not None:
            raise self.schema_error
        return default if self.schema_response is None else self.schema_response


def _run_proof(tmp_path, monkeypatch, fake, *, transport="direct"):
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    args = _args(tmp_path)
    args.database_transport = transport
    proof = RUNNER.HarmonyPreviewProof(
        args, runner=fake, opener=fake.open_endpoint,
        sleeper=lambda _seconds: None, clock=_clock(),
    )
    receipt, exit_code = proof.run()
    return proof, receipt, exit_code


def _assert_clean_early_failure(proof, receipt, fake):
    assert receipt["ok"] is False
    assert receipt["database_connectivity_preflight"] == "passed"
    assert receipt["migration_completed_count"] == 0
    assert receipt["security_completed_count"] == 0
    assert receipt["sql_failure"] is None
    assert fake.events.count("preview_schema_prerequisites") == 1
    for event in ("preview_migration_apply", "preview_security_suite",
                  "direct_probe", "postgrest_probe"):
        assert event not in fake.events
    assert fake.events.count("branch_create") == 1
    assert fake.events.count("branch_delete") == 1
    assert receipt["cleanup"]["absence_confirmations"] == 3
    assert receipt["cleanup"]["watchdog_cancelled"] is True
    assert receipt["secret_cleanup_confirmed"] is True
    assert receipt["secrets_persisted"] is False
    assert proof.credentials is None
    assert all(not env for env in fake.schema_environment_references)
    assert proof.ssl_root_cert_owned_fds == {}
    assert proof.ssl_root_cert_master_fd == -1
    serialized = json.dumps(receipt, sort_keys=True)
    for value in (UNTRUSTED, DB_SECRET, JWT_SECRET, MANAGEMENT_TOKEN, PUBLISHABLE):
        assert value not in serialized
    _assert_valid_receipt_digest(receipt)


def test_prerequisite_observation_is_strict_and_immutable():
    payload = _observations()
    observation = RUNNER.parse_schema_prerequisites(_raw(payload))
    for group, values in payload.items():
        assert getattr(observation, group) == tuple(values)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            setattr(observation, group, ())
    payload["relations"][0] = False
    assert observation.relations[0] is True
    first = RUNNER.project_schema_prerequisites(observation)
    expected = json.dumps(first, sort_keys=True)
    first.clear()
    assert json.dumps(RUNNER.project_schema_prerequisites(observation), sort_keys=True) == expected


@pytest.mark.parametrize("group", GROUPS)
def test_false_prerequisite_is_retained_as_an_observation(group):
    payload = _observations()
    payload[group][0] = False
    if group == "columns":
        payload["column_types"][0] = False
    observation = RUNNER.parse_schema_prerequisites(_raw(payload))
    assert getattr(observation, group)[0] is False
    assert sum(not value for name in GROUPS for value in getattr(observation, name)) == (
        2 if group == "columns" else 1
    )
    assert "false" in json.dumps(RUNNER.project_schema_prerequisites(observation))


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("bad_value", (None, 0, 1, float("nan"), float("inf"), "true", UNTRUSTED, [], {}))
def test_prerequisite_parser_rejects_non_boolean_entries(group, bad_value):
    payload = _observations()
    payload[group][0] = bad_value
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.parse_schema_prerequisites(_raw(payload))
    assert caught.value.code == "preview_schema_prerequisites_invalid"
    assert UNTRUSTED not in str(caught.value)


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("change", ("missing", "short", "long", "null", "object"))
def test_prerequisite_parser_rejects_missing_or_wrong_sized_groups(group, change):
    payload = _observations()
    if change == "missing":
        del payload[group]
    elif change == "short":
        payload[group].pop()
    elif change == "long":
        payload[group].append(True)
    elif change == "null":
        payload[group] = None
    else:
        payload[group] = {UNTRUSTED: True}
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.parse_schema_prerequisites(_raw(payload))
    assert caught.value.code == "preview_schema_prerequisites_invalid"
    assert UNTRUSTED not in str(caught.value)


@pytest.mark.parametrize("group", GROUPS)
def test_duplicate_group_is_rejected_even_when_values_agree(group):
    payload = _observations()
    raw = _raw(payload)
    duplicate = b", " + _raw({group: payload[group]})[1:-1]
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.parse_schema_prerequisites(raw[:-1] + duplicate + b"}")
    assert caught.value.code == "preview_schema_prerequisites_invalid"


@pytest.mark.parametrize("raw", (b"", b"null", b"[]", b"true", b"0", b"{}", b"{", b"\xff", b"[" * 1100 + b"]" * 1100))
def test_prerequisite_parser_rejects_invalid_documents(raw):
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.parse_schema_prerequisites(raw)
    assert caught.value.code == "preview_schema_prerequisites_invalid"


def test_prerequisite_parser_rejects_extra_fields_and_trailing_documents():
    payload = _observations()
    payload[UNTRUSTED] = [True]
    for raw in (_raw(payload), _raw() + b"\n{}", _raw() + UNTRUSTED.encode()):
        with pytest.raises(RUNNER.ProofError) as caught:
            RUNNER.parse_schema_prerequisites(raw)
        assert caught.value.code == "preview_schema_prerequisites_invalid"
        assert UNTRUSTED not in str(caught.value)


def test_prerequisite_parser_caps_raw_bytes_before_parsing():
    raw = _raw()
    boundary = raw + b" " * (8192 - len(raw))
    assert RUNNER.parse_schema_prerequisites(boundary).relations == tuple(_observations()["relations"])
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.parse_schema_prerequisites(boundary + b" ")
    assert caught.value.code == "preview_schema_prerequisites_invalid"


@pytest.mark.parametrize("transport", ("direct", "supavisor-session"))
def test_schema_preflight_runs_once_between_connectivity_and_first_migration(
    transport, tmp_path, monkeypatch,
):
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    monkeypatch.setenv("PGSERVICE", "ambient-route-bypass")
    fake = PrerequisiteRunner()
    proof, receipt, exit_code = _run_proof(tmp_path, monkeypatch, fake, transport=transport)
    assert exit_code == 0
    assert fake.events.count("preview_schema_prerequisites") == 1
    assert fake.events.index("preview_database_connectivity") < fake.events.index(
        "preview_schema_prerequisites",
    ) < fake.events.index("preview_migration_apply")
    assert receipt["database_schema_prerequisites"] == {
        "status": "passed",
        "query_sha256": hashlib.sha256(RUNNER.build_schema_prerequisite_sql()).hexdigest(),
        "checks": RUNNER.project_schema_prerequisites(RUNNER.parse_schema_prerequisites(_raw())),
    }
    assert receipt["migration_completed_count"] == 9
    assert receipt["security_completed_count"] == 3
    call = fake.schema_calls[0]
    command, env, inherited = call["command"], call["environment"], call["pass_fds"]
    assert command[0] == "psql"
    assert command[-2:] == ["-f", "-"]
    assert "-X" in command
    assert "ON_ERROR_STOP=1" in command
    assert call["input_bytes"] == RUNNER.build_schema_prerequisite_sql()
    assert len(inherited) == 1
    assert env["PGSSLROOTCERT"] == f"/dev/fd/{inherited[0]}"
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGPASSWORD"] == DB_SECRET
    for key in ("PGHOSTADDR", "PGSERVICE", "SUPABASE_ACCESS_TOKEN",
                "SUPABASE_JWT_SECRET", RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV):
        assert key not in env
    assert command[command.index("-h") + 1] == (
        f"db.{CHILD_REF}.supabase.co" if transport == "direct" else POOLER_HOST
    )
    assert command[command.index("-U") + 1] == (
        "postgres" if transport == "direct" else f"postgres.{CHILD_REF}"
    )
    assert command[command.index("-p") + 1] == "5432"
    assert all(not env for env in fake.schema_environment_references)
    assert proof.credentials is None
    assert proof.ssl_root_cert_owned_fds == {}
    assert proof.ssl_root_cert_master_fd == -1
    combined = json.dumps(receipt) + " ".join(command) + call["input_bytes"].decode()
    for value in (DB_SECRET, JWT_SECRET, MANAGEMENT_TOKEN, PUBLISHABLE):
        assert value not in combined
    _assert_valid_receipt_digest(receipt)


@pytest.mark.parametrize("group", GROUPS)
def test_missing_prerequisite_cleans_child_without_applying_any_migration(group, tmp_path, monkeypatch):
    payload = _observations()
    payload[group][0] = False
    if group == "columns":
        payload["column_types"][0] = False
    fake = PrerequisiteRunner(_raw(payload))
    proof, receipt, exit_code = _run_proof(tmp_path, monkeypatch, fake)
    assert exit_code == 1
    assert receipt["failure_code"] == "preview_schema_prerequisites_not_met"
    assert receipt["database_schema_prerequisites"] == {
        "status": "failed",
        "query_sha256": hashlib.sha256(RUNNER.build_schema_prerequisite_sql()).hexdigest(),
        "checks": RUNNER.project_schema_prerequisites(RUNNER.parse_schema_prerequisites(_raw(payload))),
    }
    _assert_clean_early_failure(proof, receipt, fake)


@pytest.mark.parametrize("kind", ("unknown_field", "malformed", "oversized", "duplicate"))
def test_invalid_schema_response_cleans_child_and_never_echoes_raw_output(kind, tmp_path, monkeypatch, capsys):
    payload = _observations()
    if kind == "unknown_field":
        payload[UNTRUSTED] = [True]
        raw = _raw(payload)
    elif kind == "malformed":
        raw = (UNTRUSTED + DB_SECRET + JWT_SECRET).encode()
    elif kind == "oversized":
        raw = _raw() + (UNTRUSTED * 1000).encode()
    else:
        raw = _raw()[:-1] + b',"relations":[]}'
    fake = PrerequisiteRunner(raw)
    proof, receipt, exit_code = _run_proof(tmp_path, monkeypatch, fake)
    assert exit_code == 1
    assert receipt["failure_code"] == "preview_schema_prerequisites_invalid"
    assert receipt["database_schema_prerequisites"]["status"] == "failed"
    assert receipt["database_schema_prerequisites"]["checks"] is None
    _assert_clean_early_failure(proof, receipt, fake)
    captured = capsys.readouterr()
    for value in (UNTRUSTED, DB_SECRET, JWT_SECRET):
        assert value not in captured.out + captured.err


def test_connectivity_failure_never_runs_schema_preflight(tmp_path, monkeypatch):
    fake = PrerequisiteRunner(connectivity_failure=True)
    _proof, receipt, exit_code = _run_proof(tmp_path, monkeypatch, fake)
    assert exit_code == 1
    assert receipt["failure_code"] == "preview_database_connectivity_failed"
    assert receipt["database_schema_prerequisites"]["status"] == "not_started"
    assert receipt["database_schema_prerequisites"]["checks"] is None
    assert receipt["database_schema_prerequisites"]["query_sha256"] == hashlib.sha256(
        RUNNER.build_schema_prerequisite_sql(),
    ).hexdigest()
    assert fake.schema_calls == []
    assert "preview_schema_prerequisites" not in fake.events
    assert receipt["migration_completed_count"] == 0
    assert receipt["cleanup"]["absence_confirmations"] == 3


def test_schema_query_failure_cleans_child_and_suppresses_error_details(tmp_path, monkeypatch):
    error = RUNNER.CommandError("preview_schema_prerequisites_failed", ambiguous=True)
    error.args = (UNTRUSTED + DB_SECRET + JWT_SECRET,)
    fake = PrerequisiteRunner(query_error=error)
    proof, receipt, exit_code = _run_proof(tmp_path, monkeypatch, fake)
    assert exit_code == 1
    assert receipt["failure_code"] == "preview_schema_prerequisites_failed"
    assert receipt["database_schema_prerequisites"]["status"] == "failed"
    assert receipt["database_schema_prerequisites"]["checks"] is None
    _assert_clean_early_failure(proof, receipt, fake)


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("malformation", ("mutable", "short", "long", "integer", "untrusted"))
def test_manual_observation_constructor_rejects_malformed_fields(group, malformation):
    values = {key: tuple(items) for key, items in _observations().items()}
    original = values[group]
    if malformation == "mutable":
        values[group] = list(original)
    elif malformation == "short":
        values[group] = original[:-1]
    elif malformation == "long":
        values[group] = (*original, True)
    else:
        values[group] = (1 if malformation == "integer" else UNTRUSTED, *original[1:])
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.SchemaPrerequisiteObservation(**values)
    assert caught.value.code == "preview_schema_prerequisites_invalid"
    assert UNTRUSTED not in str(caught.value)


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("malformation", ("mutable", "short", "long", "integer", "untrusted"))
def test_projector_revalidates_forcibly_tampered_frozen_observation(group, malformation):
    observation = RUNNER.parse_schema_prerequisites(_raw())
    original = getattr(observation, group)
    if malformation == "mutable":
        invalid = list(original)
    elif malformation == "short":
        invalid = original[:-1]
    elif malformation == "long":
        invalid = (*original, True)
    else:
        invalid = (1 if malformation == "integer" else UNTRUSTED, *original[1:])
    object.__setattr__(observation, group, invalid)
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.project_schema_prerequisites(observation)
    assert caught.value.code == "preview_schema_prerequisites_invalid"
    assert UNTRUSTED not in str(caught.value)


def test_absent_column_cannot_claim_matching_type_at_any_validation_boundary():
    payload = _observations()
    payload["columns"][0] = False
    values = {key: tuple(items) for key, items in payload.items()}
    observation = RUNNER.parse_schema_prerequisites(_raw())
    object.__setattr__(observation, "columns", values["columns"])
    for action in (
        lambda: RUNNER.parse_schema_prerequisites(_raw(payload)),
        lambda: RUNNER.SchemaPrerequisiteObservation(**values),
        lambda: RUNNER.project_schema_prerequisites(observation),
    ):
        with pytest.raises(RUNNER.ProofError) as caught:
            action()
        assert caught.value.code == "preview_schema_prerequisites_invalid"


def test_projector_rebuilds_static_labels_and_fresh_nested_objects():
    observation = RUNNER.parse_schema_prerequisites(_raw())
    checks = RUNNER.project_schema_prerequisites(observation)
    assert set(checks) == {"relations", "columns", "keys", "routines", "roles"}
    assert checks["relations"][0] == {"relation": "public.workspace_clients", "present": True}
    assert checks["columns"][0] == {
        "relation": "public.workspace_clients", "column": "workspace_id",
        "expected_type": "uuid", "present": True, "type_matches": True,
    }
    assert checks["keys"][0] == {
        "relation": "public.workspace_clients", "columns": ["workspace_id", "client_id"],
        "usable": True,
    }
    assert checks["routines"][0] == {"signature": "auth.uid()", "present": True}
    assert checks["roles"][0] == {"role": "anon", "present": True}
    expected = json.dumps(checks, sort_keys=True)
    checks["keys"][0]["columns"].append(UNTRUSTED)
    for rows in checks.values():
        rows[0]["arbitrary_remote_name"] = UNTRUSTED
    assert json.dumps(RUNNER.project_schema_prerequisites(observation), sort_keys=True) == expected


@pytest.mark.parametrize("imposter", (None, {}, (), UNTRUSTED))
def test_projector_rejects_non_observation_objects(imposter):
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.project_schema_prerequisites(imposter)
    assert caught.value.code == "preview_schema_prerequisites_invalid"
    assert UNTRUSTED not in str(caught.value)
