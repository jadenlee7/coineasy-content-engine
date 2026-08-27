from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/run_harmony_preview_proof.py"
SPEC = importlib.util.spec_from_file_location("harmony_preview_proof_runner", SCRIPT)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


PARENT_REF = "isuqcqwxpojgzevxfdwr"
CHILD_REF = "vllwcbhqdojpjrssidcu"
RELEASE_SHA = "a" * 40
CONFIG_PAYLOAD = b'{"client_id":"squid","environment":"preview"}\n'
PROBE_PAYLOAD = b"# immutable exact-head probe snapshot\n"
CONFIG_SHA = hashlib.sha256(CONFIG_PAYLOAD).hexdigest()
DB_SECRET = "db-secret-must-never-appear"
JWT_SECRET = "legacy-jwt-secret-longer-than-thirty-two-bytes"
PUBLISHABLE = "sb_publishable_secret_must_never_appear"
MANAGEMENT_TOKEN = "sbp_scoped_management_token_must_never_appear"
SQL_PAYLOAD = b"-- immutable exact-head sql\n"
SQL_SHA256 = hashlib.sha256(SQL_PAYLOAD).hexdigest()


def _support_payload(relative: Path) -> bytes:
    if relative == RUNNER.CONFIG_PATH:
        return CONFIG_PAYLOAD
    if relative in RUNNER.PROBE_PATHS:
        return PROBE_PAYLOAD
    return SQL_PAYLOAD


def _support_manifest() -> dict[str, str]:
    return {
        str(relative): hashlib.sha256(_support_payload(relative)).hexdigest()
        for relative in RUNNER.SUPPORT_PATHS
    }


@pytest.fixture(autouse=True)
def _scoped_management_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV, MANAGEMENT_TOKEN)
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "unsafe-ambient-default-token")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unsafe-stale-parent-secret")


def _args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=str(tmp_path),
        parent_project_ref=PARENT_REF,
        release_sha=RELEASE_SHA,
        supabase="supabase",
        psql="psql",
        branch_ready_timeout_seconds=30,
        supabase_read_timeout_seconds=7,
        supabase_mutation_timeout_seconds=11,
        schema_ready_timeout_seconds=30,
        migration_timeout_seconds=30,
        probe_timeout_seconds=30,
        cleanup_timeout_seconds=30,
        poll_interval_seconds=0.01,
        fence_ttl_minutes=105,
    )


def _assert_no_live_group_members(pgid: int, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    state = RUNNER.PROCESS_GROUP_UNKNOWN
    while time.monotonic() < deadline:
        state = RUNNER.ProcessRunner._process_group_state(pgid)
        if state in {
            RUNNER.PROCESS_GROUP_ABSENT,
            RUNNER.PROCESS_GROUP_DEAD_ONLY,
        }:
            return
        time.sleep(0.05)
    pytest.fail(f"process group {pgid} remained non-quiescent: {state}")


def test_extract_branches_prefers_project_health_and_preserves_lifecycle() -> None:
    rows = RUNNER.extract_branches(
        [
            {
                "id": "branch-id-1",
                "name": "preview",
                "project_ref": CHILD_REF,
                "status": "RUNNING_MIGRATIONS",
                "preview_project_status": "ACTIVE_HEALTHY",
                "is_default": False,
            }
        ]
    )

    assert len(rows) == 1
    assert rows[0].status == "ACTIVE_HEALTHY"
    assert rows[0].migration_status == "RUNNING_MIGRATIONS"


def test_extract_branches_accepts_legacy_health_status_shape() -> None:
    rows = RUNNER.extract_branches(
        [
            {
                "id": "branch-id-1",
                "name": "preview",
                "project_ref": CHILD_REF,
                "status": "ACTIVE_HEALTHY",
                "is_default": False,
            }
        ]
    )

    assert len(rows) == 1
    assert rows[0].status == "ACTIVE_HEALTHY"
    assert rows[0].migration_status == ""


@pytest.mark.parametrize(
    ("project_status", "lifecycle_status", "expected"),
    (
        ("ACTIVE_HEALTHY", "RUNNING_MIGRATIONS", "waiting"),
        ("ACTIVE_HEALTHY", "MIGRATIONS_PASSED", "ready"),
        ("ACTIVE_HEALTHY", "FUNCTIONS_DEPLOYED", "ready"),
        ("ACTIVE_HEALTHY", "MIGRATIONS_FAILED", "failed"),
        ("INIT_FAILED", "CREATING_PROJECT", "failed"),
        ("ACTIVE_HEALTHY", "UNRECOGNIZED", "invalid"),
    ),
)
def test_preview_branch_readiness_requires_terminal_server_workflow(
    project_status: str,
    lifecycle_status: str,
    expected: str,
) -> None:
    branch = RUNNER.BranchIdentity(
        branch_id="branch-id-1",
        ref=CHILD_REF,
        name="preview",
        status=project_status,
        migration_status=lifecycle_status,
    )

    assert RUNNER.preview_branch_readiness(branch) == expected


class FakeWatchdog:
    def __init__(
        self,
        *,
        fail_cancel: bool = False,
        unsafe_ack: bool = False,
    ) -> None:
        self.terminated = False
        self.waited = False
        self.fail_cancel = fail_cancel
        self.unsafe_ack = unsafe_ack
        self.returncode: int | None = None
        self.pid = 424242
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def attach(
        self,
        child_fd: int,
        *,
        root: str,
        nonce: str,
    ) -> None:
        self._socket = socket.socket(fileno=os.dup(child_fd))

        def serve() -> None:
            assert self._socket is not None
            payload = b""
            try:
                while b"\n" not in payload:
                    chunk = self._socket.recv(RUNNER.WATCHDOG_MESSAGE_MAX_BYTES)
                    if not chunk:
                        # A retained fake owns its root just like the real
                        # natural-deadline watchdog, but it need not wait 110m.
                        import shutil

                        shutil.rmtree(root, ignore_errors=True)
                        self.returncode = 0
                        return
                    payload += chunk
                message = json.loads(payload.split(b"\n", 1)[0])
                self.terminated = True
                if self.fail_cancel:
                    self.returncode = 1
                    return
                import shutil

                if not self.unsafe_ack:
                    shutil.rmtree(root)
                ready = {
                    "schema": RUNNER.WATCHDOG_PROTOCOL_SCHEMA,
                    "type": "clean_ready",
                    "nonce": nonce,
                    "status": (
                        "cancel_unsafe" if self.unsafe_ack else "cancel_clean"
                    ),
                    "active_cli_pgid": None,
                    "last_cli_pgid": None,
                    "root_absent": not os.path.lexists(root),
                }
                assert message == {
                    "schema": RUNNER.WATCHDOG_PROTOCOL_SCHEMA,
                    "type": "cancel",
                    "nonce": nonce,
                }
                self._socket.sendall(
                    json.dumps(
                        ready, separators=(",", ":"), sort_keys=True
                    ).encode("ascii")
                    + b"\n"
                )
                if self.unsafe_ack:
                    self.returncode = 3
                    return
                ack = b""
                while b"\n" not in ack:
                    chunk = self._socket.recv(
                        RUNNER.WATCHDOG_MESSAGE_MAX_BYTES
                    )
                    if not chunk:
                        self.returncode = 1
                        return
                    ack += chunk
                accepted = json.loads(ack.split(b"\n", 1)[0])
                self.returncode = 0 if accepted == {
                    "schema": RUNNER.WATCHDOG_PROTOCOL_SCHEMA,
                    "type": "ack_accepted",
                    "nonce": nonce,
                } else 1
            finally:
                self._socket.close()

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()

    def terminate(self) -> None:
        if self.fail_cancel:
            raise RuntimeError("synthetic watchdog cancellation failure")
        self.terminated = True
        self.returncode = -signal.SIGTERM
        if self._socket is not None:
            self._socket.close()

    def wait(self, timeout: float) -> int:
        assert self._thread is not None
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired(["fake-watchdog"], timeout)
        self.waited = True
        assert self.returncode is not None
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


class FakeRunner:
    def __init__(
        self,
        *,
        direct_failure: bool = False,
        create_ambiguous: bool = False,
        delete_ambiguous: bool = False,
        timeout_code: str | None = None,
        watchdog_cancel_failure: bool = False,
        watchdog_unsafe_ack: bool = False,
        persistent: bool | None = False,
        with_data: bool | None = False,
        compute_size: str | None = "small",
    ) -> None:
        self.events: list[str] = []
        self.commands: list[list[str]] = []
        self.environments: list[dict[str, str]] = []
        self.working_directories: list[str | None] = []
        self.timeouts: list[tuple[str, float]] = []
        self.json_inputs: list[tuple[str, bytes | None]] = []
        self.branch_name = ""
        self.list_calls = 0
        self.direct_failure = direct_failure
        self.create_ambiguous = create_ambiguous
        self.delete_ambiguous = delete_ambiguous
        self.timeout_code = timeout_code
        self.timeout_raised = False
        self.watchdog = FakeWatchdog(
            fail_cancel=watchdog_cancel_failure,
            unsafe_ack=watchdog_unsafe_ack,
        )
        self.persistent = persistent
        self.with_data = with_data
        self.compute_size = compute_size

    def run_bytes(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        input_bytes: bytes | None = None,
        cwd: str | None = None,
        timeout: float,
        code: str,
    ) -> bytes:
        assert input_bytes is None
        assert code in {"migration_snapshot", "proof_support_snapshot"}
        self.commands.append(list(command))
        self.environments.append(dict(env or {}))
        self.working_directories.append(cwd)
        self.timeouts.append((code, timeout))
        self.events.append(code)
        if code == "migration_snapshot":
            return SQL_PAYLOAD
        relative = Path(command[-1].split(":", 1)[1])
        return _support_payload(relative)

    def run_json(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        input_bytes: bytes | None = None,
        cwd: str | None = None,
        timeout: float,
        code: str,
        before_spawn: object | None = None,
    ) -> object:
        if before_spawn is not None:
            assert callable(before_spawn)
            before_spawn()
        self.commands.append(list(command))
        self.environments.append(dict(env or {}))
        self.working_directories.append(cwd)
        self.timeouts.append((code, timeout))
        self.json_inputs.append((code, input_bytes))
        if code == self.timeout_code and not self.timeout_raised:
            self.timeout_raised = True
            raise RUNNER.CommandError(f"{code}_timeout", ambiguous=True)
        if "branches" in command and "create" in command:
            self.events.append("branch_create")
            self.branch_name = command[command.index("create") + 1]
            if self.create_ambiguous:
                raise RUNNER.CommandError("supabase_branch_create_timeout", ambiguous=True)
            return {
                "id": "branch-id-1",
                "name": self.branch_name,
                "project_ref": CHILD_REF,
                "status": "CREATING_PROJECT",
                "preview_project_status": "COMING_UP",
                "is_default": False,
                "persistent": self.persistent,
                "with_data": self.with_data,
            }
        if "branches" in command and "list" in command:
            self.list_calls += 1
            self.events.append(f"branch_list_{self.list_calls}")
            default = {
                "id": "main-id",
                "name": "main",
                "project_ref": PARENT_REF,
                "status": "FUNCTIONS_DEPLOYED",
                "preview_project_status": "ACTIVE_HEALTHY",
                "is_default": True,
            }
            if self.list_calls == 1:
                return [default]
            if self.list_calls in {2, 3}:
                return [
                    default,
                    {
                        "id": "branch-id-1",
                        "name": self.branch_name,
                        "project_ref": CHILD_REF,
                        "status": "FUNCTIONS_DEPLOYED",
                        "preview_project_status": (
                            "COMING_UP" if self.list_calls == 2 else "ACTIVE_HEALTHY"
                        ),
                        "is_default": False,
                        "persistent": self.persistent,
                        "with_data": self.with_data,
                    },
                ]
            return [default]
        if "projects" in command and "list" in command:
            self.events.append("project_list")
            database = (
                {}
                if self.compute_size is None
                else {"infra_compute_size": self.compute_size}
            )
            return [{"ref": CHILD_REF, "databases": [database]}]
        if "branches" in command and "get" in command:
            self.events.append("branch_get_secrets")
            return {
                "envs": {
                    "POSTGRES_URL_NON_POOLING": (
                        "postgresql://postgres:"
                        + DB_SECRET
                        + f"@db.{CHILD_REF}.supabase.co:5432/postgres"
                    ),
                    "SUPABASE_URL": f"https://{CHILD_REF}.supabase.co",
                    "SUPABASE_PUBLISHABLE_KEY": PUBLISHABLE,
                    "SUPABASE_JWT_SECRET": JWT_SECRET,
                    "SUPABASE_SERVICE_ROLE_KEY": "must-be-ignored",
                }
            }
        if "branches" in command and "delete" in command:
            self.events.append("branch_delete")
            if self.delete_ambiguous:
                raise RUNNER.CommandError("supabase_branch_delete_timeout", ambiguous=True)
            return {"deleted": True}
        if code == "direct_database_probe":
            assert command[:3] == [sys.executable, "-I", "-"]
            assert input_bytes == PROBE_PAYLOAD
            self.events.append("direct_probe")
            if self.direct_failure:
                raise RUNNER.CommandError("direct_database_probe_failed", ambiguous=True)
            return {
                "ok": True,
                "schema_version": "harmony-preview-concurrency-proof@3",
                "release_sha": RELEASE_SHA,
                "config_sha256": CONFIG_SHA,
                "connections": 64,
                "new": 1,
                "reused": 63,
                "side_effect_baseline_unchanged": True,
                "automatic_publication": False,
                "external_calls": False,
                "provider_calls": False,
                "publication_calls": False,
                "unexpected_secret": JWT_SECRET,
            }
        if code == "signed_postgrest_probe":
            assert command[:3] == [sys.executable, "-I", "-"]
            assert input_bytes == RUNNER.build_postgrest_probe_bundle(
                PROBE_PAYLOAD, PROBE_PAYLOAD
            )
            self.events.append("postgrest_probe")
            return {
                "ok": True,
                "schema_version": "harmony-preview-postgrest-proof@2",
                "branch_ref": CHILD_REF,
                "release_sha": RELEASE_SHA,
                "config_sha256": CONFIG_SHA,
                "connections": 64,
                "new": 1,
                "reused": 63,
                "side_effect_baseline_unchanged": True,
                "automatic_publication": False,
                "external_calls": False,
                "provider_calls": False,
                "buzz_calls": False,
                "approval_decisions": False,
                "publication_calls": False,
                "unexpected_secret": JWT_SECRET,
            }
        raise AssertionError(f"unexpected JSON command: {command}")

    def run_quiet(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None,
        input_bytes: bytes | None = None,
        cwd: str | None = None,
        timeout: float,
        code: str,
    ) -> None:
        assert input_bytes == SQL_PAYLOAD
        self.commands.append(list(command))
        self.environments.append(dict(env or {}))
        self.working_directories.append(cwd)
        self.timeouts.append((code, timeout))
        self.events.append(code)

    def popen(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        pass_fds: tuple[int, ...] = (),
    ) -> FakeWatchdog:
        self.commands.append(list(command))
        self.environments.append(dict(env))
        self.working_directories.append(None)
        self.events.append("watchdog_armed")
        assert len(pass_fds) == 1
        self.watchdog.attach(
            pass_fds[0],
            root=env["HARMONY_WATCHDOG_CONTROL_DIR"],
            nonce=env["HARMONY_WATCHDOG_NONCE"],
        )
        return self.watchdog

    def confirm_external_process_group_absent(
        self,
        pgid: int,
        *,
        code: str,
    ) -> None:
        assert pgid == self.watchdog.pid
        assert code == "cleanup_watchdog_parent_fence"
        self.events.append("watchdog_group_reaped")

    def terminate_process_group(
        self,
        process: FakeWatchdog,
        *,
        code: str,
        term_grace_seconds: float = RUNNER.PROCESS_GROUP_TERM_GRACE_SECONDS,
    ) -> None:
        assert code == "cleanup_watchdog_cancel"
        assert term_grace_seconds == RUNNER.WATCHDOG_CANCEL_GRACE_SECONDS
        process.terminate()
        process.wait(timeout=5)
        self.events.append("watchdog_group_reaped")


class OpenApiResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return json.dumps({"paths": {RUNNER.POSTGREST_RPC_PATH: {}}}).encode()


def _fake_exact_checkout(
    *_args: object,
) -> tuple[dict[str, str], dict[str, str]]:
    return ({name: SQL_SHA256 for name in RUNNER.MIGRATIONS}, _support_manifest())


def _clock() -> callable:
    value = 0.0

    def now() -> float:
        nonlocal value
        value += 0.1
        return value

    return now


def test_one_shot_order_secret_hygiene_and_final_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner()
    proof = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    )
    receipt, exit_code = proof.run()

    assert exit_code == 0
    assert receipt["ok"] is True
    assert receipt["parent_project_ref"] == PARENT_REF
    assert receipt["parent_child_fence"] is True
    assert receipt["migration_count"] == 9
    assert receipt["branch"] == {
        "ref": CHILD_REF,
        "name": fake.branch_name,
        "size": "small",
        "persistent": False,
        "with_data": False,
    }
    assert receipt["cleanup"]["absence_confirmations"] == 3
    assert receipt["cleanup"]["watchdog_cancelled"] is True
    assert receipt["cleanup"]["branch_create_mutation_invoked"] is True
    assert proof.branch_create_mutation_invoked is True
    assert fake.watchdog.terminated and fake.watchdog.waited
    assert fake.events.index("watchdog_armed") < fake.events.index("branch_create")
    assert fake.events.index("watchdog_armed") < fake.events.index("branch_get_secrets")
    assert fake.events.index("watchdog_armed") < fake.events.index("branch_list_2")
    assert fake.events.index("direct_probe") < fake.events.index("postgrest_probe")
    assert fake.events.count("postgrest_probe") == 1
    assert fake.events.count("branch_create") == 1
    assert fake.events.count("branch_delete") == 1
    assert fake.events.count("preview_migration_apply") == 9
    assert fake.events.count("preview_security_suite") == 3
    assert fake.events.count("migration_snapshot") == 9
    assert fake.events.count("proof_support_snapshot") == len(RUNNER.SUPPORT_PATHS)
    assert fake.events[-5:] == [
        "branch_delete",
        "branch_list_4",
        "branch_list_5",
        "branch_list_6",
        "watchdog_group_reaped",
    ]

    create = next(command for command in fake.commands if "create" in command)
    assert "--size" in create and create[create.index("--size") + 1] == "small"
    assert "--persistent" not in create
    assert "--with-data" not in create
    assert PARENT_REF in create
    assert "--profile" not in create
    snapshot_commands = [command for command in fake.commands if "show" in command]
    assert len(snapshot_commands) == 9 + len(RUNNER.SUPPORT_PATHS)
    assert all(
        any(item.startswith(f"{RELEASE_SHA}:") for item in command)
        for command in snapshot_commands
    )
    psql_commands = [command for command in fake.commands if command[0] == "psql"]
    assert len(psql_commands) == 12
    assert all(command[-2:] == ["-f", "-"] for command in psql_commands)
    projects_list = next(
        command
        for command in fake.commands
        if "projects" in command and "list" in command
    )
    assert projects_list[-2:] == ["--output-format", "json"]
    branch_get = next(
        command
        for command in fake.commands
        if "branches" in command and "get" in command
    )
    assert branch_get[-2:] == ["--output-format", "json"]
    probe_commands = [
        command
        for command in fake.commands
        if command[:3] == [sys.executable, "-I", "-"]
    ]
    assert len(probe_commands) == 2
    assert [
        (code, payload)
        for code, payload in fake.json_inputs
        if code in {"direct_database_probe", "signed_postgrest_probe"}
    ] == [
        ("direct_database_probe", PROBE_PAYLOAD),
        (
            "signed_postgrest_probe",
            RUNNER.build_postgrest_probe_bundle(PROBE_PAYLOAD, PROBE_PAYLOAD),
        ),
    ]
    assert receipt["proof_artifact_sha256"] == _support_manifest()
    assert receipt["secret_cleanup_confirmed"] is True
    assert receipt["secrets_persisted"] is False
    serialized = json.dumps(receipt, sort_keys=True)
    for secret in (
        DB_SECRET,
        JWT_SECRET,
        PUBLISHABLE,
        MANAGEMENT_TOKEN,
        "must-be-ignored",
    ):
        assert secret not in serialized

    cli_envs = [
        env
        for command, env in zip(fake.commands, fake.environments)
        if command and command[0] == "supabase"
    ]
    for env in cli_envs:
        assert env["SUPABASE_ACCESS_TOKEN"] == MANAGEMENT_TOKEN
        assert RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV not in env
        assert Path(env["HOME"]).name.startswith("harmony-supabase-home-")
        assert env["HOME"] != os.environ.get("HOME")
        assert env["XDG_CONFIG_HOME"] == str(Path(env["HOME"]) / ".config")
        assert "PGPASSWORD" not in env
        assert "SUPABASE_JWT_SECRET" not in env
        assert "SUPABASE_SERVICE_ROLE_KEY" not in env

    for command, cwd in zip(fake.commands, fake.working_directories):
        if command and command[0] == "supabase":
            assert cwd == cli_envs[0]["HOME"]

    watchdog_index = fake.events.index("watchdog_armed")
    assert fake.environments[watchdog_index]["SUPABASE_ACCESS_TOKEN"] == MANAGEMENT_TOKEN
    watchdog_home = Path(fake.environments[watchdog_index]["HOME"])
    assert watchdog_home.name == "home"
    assert watchdog_home.parent.name.startswith("harmony-watchdog-control-")
    assert str(watchdog_home) != cli_envs[0]["HOME"]
    assert fake.environments[watchdog_index][
        "HARMONY_WATCHDOG_CONTROL_DIR"
    ] == str(watchdog_home.parent)
    assert not watchdog_home.parent.exists()
    watchdog_command = " ".join(fake.commands[watchdog_index])
    assert MANAGEMENT_TOKEN not in watchdog_command
    assert "timeout=11" in watchdog_command
    assert "while time.time() < hard_stop" in watchdog_command
    assert "target_observed" in watchdog_command
    assert "absence_confirmations >= 3" in watchdog_command
    assert '"type": "clean_ready"' in watchdog_command
    assert '"ack_accepted"' in watchdog_command
    assert watchdog_command.count("os.killpg(pid, signal.SIGTERM)") == 1
    assert watchdog_command.count("os.killpg(pid, signal.SIGKILL)") == 1
    assert "def process_group_state(pgid):" in watchdog_command
    assert "pending_failure = sys.exc_info()[1]" in watchdog_command
    assert "if cancel_requested or not watchdog_safe:" in watchdog_command
    assert 'WatchdogFenceError("process_group_fence_failed")' in watchdog_command
    assert 'WatchdogFenceError("initial_signal_unmask_failed")' in watchdog_command
    assert watchdog_command.index("threading.Thread(target=control_worker") < (
        watchdog_command.index("signal.SIG_UNBLOCK")
    )
    assert watchdog_command.index("signal.SIG_UNBLOCK") < watchdog_command.index(
        "time.sleep(max(0.0, deadline - time.time()))"
    )
    assert "and matches is None" not in watchdog_command
    for command, env in zip(fake.commands, fake.environments):
        is_management = (command and command[0] == "supabase") or (
            command and command[0] == sys.executable and "time.sleep" in " ".join(command)
        )
        if not is_management:
            assert "SUPABASE_ACCESS_TOKEN" not in env
            assert RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV not in env
    assert RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV not in os.environ
    assert "SUPABASE_ACCESS_TOKEN" not in os.environ
    assert "SUPABASE_JWT_SECRET" not in os.environ
    assert not Path(cli_envs[0]["HOME"]).exists()

    for code, timeout in fake.timeouts:
        if code in {
            "supabase_branch_list",
            "supabase_branch_get",
            "supabase_project_list",
        }:
            assert timeout == 7
        if code in {"supabase_branch_create", "supabase_branch_delete"}:
            assert timeout == 11


def test_watchdog_spawn_signal_is_deferred_until_parent_tracks_child(
    tmp_path: Path,
) -> None:
    class InterruptAfterSpawnRunner(FakeRunner):
        def popen(
            self,
            command: list[str],
            *,
            env: dict[str, str],
            pass_fds: tuple[int, ...] = (),
        ) -> FakeWatchdog:
            child = super().popen(command, env=env, pass_fds=pass_fds)
            os.kill(os.getpid(), signal.SIGTERM)
            return child

    fake = InterruptAfterSpawnRunner()
    proof = RUNNER.HarmonyPreviewProof(_args(tmp_path), runner=fake)
    proof.management_token = MANAGEMENT_TOKEN
    management_home = tmp_path / "management-home"
    management_home.mkdir()
    proof.management_home = str(management_home)
    branch_name = "hc-proof-aaaaaaaaaaaa-20260828000000-bbbbbbbbbbbb"
    observed: list[tuple[bool, bool, str]] = []
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(_signum: int, _frame: object) -> None:
        observed.append((
            proof.watchdog is fake.watchdog,
            proof.watchdog_control_socket is not None,
            proof.watchdog_spawn_state,
        ))
        raise RUNNER.ProofError("synthetic_watchdog_spawn_interrupt")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        with pytest.raises(
            RUNNER.ProofError,
            match="synthetic_watchdog_spawn_interrupt",
        ):
            proof._arm_watchdog(branch_name)
        assert observed == [(True, True, "tracked")]
        watchdog_root = proof.watchdog_control_dir
        assert os.path.lexists(watchdog_root)
        proof._cancel_watchdog()
        assert proof.watchdog_spawn_state == "released"
        assert proof.watchdog is None
        assert not os.path.lexists(watchdog_root)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if fake.watchdog.poll() is None:
            fake.watchdog.terminate()


def test_mutation_before_spawn_signal_is_deferred_until_child_is_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RUNNER.ProcessRunner()
    mutation_invoked = False
    child_spawned = False
    observed: list[tuple[bool, bool]] = []
    real_popen = RUNNER.subprocess.Popen
    previous_handler = signal.getsignal(signal.SIGTERM)

    def tracking_popen(*args: object, **kwargs: object) -> object:
        nonlocal child_spawned
        child = real_popen(*args, **kwargs)
        child_spawned = True
        return child

    def before_spawn() -> None:
        nonlocal mutation_invoked
        mutation_invoked = True
        os.kill(os.getpid(), signal.SIGTERM)

    def interrupted(_signum: int, _frame: object) -> None:
        observed.append((mutation_invoked, child_spawned))
        raise RUNNER.ProofError("synthetic_mutation_handoff_interrupt")

    monkeypatch.setattr(RUNNER.subprocess, "Popen", tracking_popen)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with pytest.raises(
            RUNNER.ProofError,
            match="synthetic_mutation_handoff_interrupt",
        ):
            runner.run_bytes(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                timeout=5,
                code="mutation_handoff",
                before_spawn=before_spawn,
            )
        assert observed == [(True, True)]
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def test_run_interrupt_after_watchdog_arm_before_create_cancels_and_reaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class InterruptAfterSpawnRunner(FakeRunner):
        def popen(
            self,
            command: list[str],
            *,
            env: dict[str, str],
            pass_fds: tuple[int, ...] = (),
        ) -> FakeWatchdog:
            child = super().popen(command, env=env, pass_fds=pass_fds)
            os.kill(os.getpid(), signal.SIGTERM)
            return child

    fake = InterruptAfterSpawnRunner()
    proof = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    )
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(_signum: int, _frame: object) -> None:
        raise RUNNER.ProofError("synthetic_pre_create_interrupt")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        receipt, exit_code = proof.run()
        assert exit_code == 1
        assert receipt["failure_code"] == "synthetic_pre_create_interrupt"
        assert "branch_create" not in fake.events
        assert proof.branch_create_mutation_invoked is False
        assert receipt["cleanup"]["branch_create_mutation_invoked"] is False
        assert receipt["cleanup"]["create_not_invoked_absence_confirmed"] is True
        assert receipt["cleanup"]["absence_confirmations"] == 3
        assert receipt["cleanup"]["delete_requested"] is False
        assert receipt["cleanup"]["delete_target_count"] == 0
        assert receipt["cleanup"]["watchdog_cancelled"] is True
        assert receipt["cleanup"]["watchdog_secret_released"] is True
        assert receipt["secret_cleanup_confirmed"] is True
        assert receipt["secrets_persisted"] is False
        assert proof.watchdog_spawn_state == "released"
        assert proof.watchdog is None
        assert fake.watchdog.waited is True
        assert "watchdog_group_reaped" in fake.events
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if fake.watchdog.poll() is None:
            fake.watchdog.terminate()


def test_watchdog_spawn_mask_failure_never_calls_popen_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner()
    proof = RUNNER.HarmonyPreviewProof(_args(tmp_path), runner=fake)
    proof.management_token = MANAGEMENT_TOKEN
    management_home = tmp_path / "management-home"
    management_home.mkdir()
    proof.management_home = str(management_home)

    def fail_mask(*, code: str) -> set[signal.Signals]:
        assert code == "cleanup_watchdog_spawn"
        raise RUNNER.ProofError("cleanup_watchdog_spawn_signal_mask_failed")

    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "_block_interrupt_signals",
        staticmethod(fail_mask),
    )
    with pytest.raises(
        RUNNER.ProofError,
        match="cleanup_watchdog_spawn_signal_mask_failed",
    ):
        proof._arm_watchdog(
            "hc-proof-aaaaaaaaaaaa-20260828000000-bbbbbbbbbbbb"
        )
    assert "watchdog_armed" not in fake.events
    assert proof.watchdog is None
    assert proof.watchdog_spawn_state == "spawning"
    assert proof.watchdog_control_dir == ""
    assert proof.watchdog_spawn_state not in {"never_started", "released"}


def test_direct_probe_failure_never_runs_postgrest_and_still_deletes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner(direct_failure=True)
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "direct_database_probe_failed"
    assert "postgrest_probe" not in fake.events
    assert "branch_delete" in fake.events
    assert receipt["cleanup"]["absence_confirmations"] == 3
    assert receipt["same_child_repair_attempts"] == 0
    assert receipt["replacement_branch_attempts"] == 0


def test_exact_checkout_change_after_branch_creation_fails_before_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def changing_checkout(
        *_args: object,
    ) -> tuple[dict[str, str], dict[str, str]]:
        nonlocal calls
        calls += 1
        support_manifest = _support_manifest()
        if calls != 1:
            support_manifest[str(RUNNER.CONFIG_PATH)] = "d" * 64
        return ({name: SQL_SHA256 for name in RUNNER.MIGRATIONS}, support_manifest)

    monkeypatch.setattr(RUNNER, "verify_exact_checkout", changing_checkout)
    fake = FakeRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "exact_checkout_changed_during_preview"
    assert "preview_migration_apply" not in fake.events
    assert "direct_probe" not in fake.events
    assert "branch_delete" in fake.events
    assert receipt["cleanup"]["absence_confirmations"] == 3


def test_exact_commit_sql_snapshot_digest_mismatch_fails_before_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class TamperedSnapshotRunner(FakeRunner):
        def run_bytes(self, command: list[str], **kwargs: object) -> bytes:
            value = super().run_bytes(command, **kwargs)
            if kwargs.get("code") == "migration_snapshot":
                return b"-- changed after manifest\n"
            return value

    fake = TamperedSnapshotRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "migration_snapshot_digest_mismatch"
    assert "branch_create" not in fake.events


def test_exact_commit_probe_snapshot_digest_mismatch_fails_before_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class TamperedProbeSnapshotRunner(FakeRunner):
        def run_bytes(self, command: list[str], **kwargs: object) -> bytes:
            value = super().run_bytes(command, **kwargs)
            if (
                kwargs.get("code") == "proof_support_snapshot"
                and str(RUNNER.PROBE_PATHS[0]) in command[-1]
            ):
                return b"# changed probe after manifest\n"
            return value

    fake = TamperedProbeSnapshotRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "proof_support_snapshot_digest_mismatch"
    assert "branch_create" not in fake.events


def test_postgrest_bundle_imports_exact_base_without_workspace_sibling(
    tmp_path: Path,
) -> None:
    repo_root = SCRIPT.parents[1]
    concurrency_payload = (repo_root / RUNNER.PROBE_PATHS[0]).read_bytes()
    postgrest_payload = (repo_root / RUNNER.PROBE_PATHS[1]).read_bytes()
    bundle = RUNNER.build_postgrest_probe_bundle(
        concurrency_payload, postgrest_payload
    )

    output = RUNNER.ProcessRunner().run_bytes(
        [sys.executable, "-I", "-", "--help"],
        input_bytes=bundle,
        cwd=tmp_path,
        timeout=15,
        code="postgrest_bundle_import_smoke",
    )

    assert b"usage:" in output
    assert not (tmp_path / RUNNER.PROBE_PATHS[0].name).exists()


def test_tampered_probe_receipt_fails_exact_fence_and_never_runs_postgrest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class TamperedRunner(FakeRunner):
        def run_json(self, command: list[str], **kwargs: object) -> object:
            value = super().run_json(command, **kwargs)
            if kwargs.get("code") == "direct_database_probe":
                assert isinstance(value, dict)
                value["release_sha"] = "d" * 40
            return value

    fake = TamperedRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "probe_receipt_exact_fence_invalid"
    assert "postgrest_probe" not in fake.events
    assert receipt["cleanup"]["absence_confirmations"] == 3


def test_missing_scoped_management_token_fails_before_any_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV)
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "supabase_management_token_missing"
    assert fake.commands == []


def test_ambiguous_create_reconciles_only_for_cleanup_and_never_proves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner(create_ambiguous=True)
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "branch_create_commit_state_unknown"
    assert receipt["cleanup"]["create_response_ambiguous"] is True
    assert (
        receipt["cleanup"]["create_failure_code"]
        == "supabase_branch_create_timeout"
    )
    assert "watchdog_armed" in fake.events
    assert "branch_get_secrets" not in fake.events
    assert "preview_migration_apply" not in fake.events
    assert "branch_delete" in fake.events
    assert receipt["cleanup"]["absence_confirmations"] == 3


def test_ambiguous_create_with_unavailable_readback_keeps_prearmed_watchdog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class UnreadableAfterCreateRunner(FakeRunner):
        def run_json(self, command: list[str], **kwargs: object) -> object:
            if (
                "branches" in command
                and "list" in command
                and self.list_calls >= 1
            ):
                super().run_json(command, **kwargs)
                raise RUNNER.CommandError(
                    "supabase_branch_list_timeout", ambiguous=True
                )
            return super().run_json(command, **kwargs)

    fake = UnreadableAfterCreateRunner(create_ambiguous=True)
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "supabase_branch_list_timeout"
    assert receipt["cleanup_failure_code"] == "supabase_branch_list_timeout"
    assert fake.events.index("watchdog_armed") < fake.events.index("branch_create")
    assert receipt["cleanup"]["watchdog_armed"] is True
    assert receipt["cleanup"]["watchdog_absolute_deadline"] is True
    assert receipt["cleanup"]["watchdog_cancelled"] is False
    assert fake.watchdog.terminated is False
    assert "branch_delete" not in fake.events
    assert "preview_migration_apply" not in fake.events


def test_unidentified_child_retains_absolute_watchdog_for_late_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class NameOnlyCreateRunner(FakeRunner):
        def run_json(self, command: list[str], **kwargs: object) -> object:
            value = super().run_json(command, **kwargs)
            if "branches" in command and "create" in command:
                return {"status": "COMING_UP"}
            if "branches" in command and "list" in command:
                assert isinstance(value, list)
                return [value[0]]
            return value

    args = _args(tmp_path)
    args.branch_ready_timeout_seconds = 0
    fake = NameOnlyCreateRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        args,
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == "branch_create_succeeded_child_not_observed"
    assert receipt["branch"] is None
    assert receipt["cleanup"]["branch_name"] == fake.branch_name
    assert receipt["cleanup"]["delete_target_count"] == 0
    assert receipt["cleanup"]["absence_confirmations"] == 0
    assert receipt["cleanup"]["late_visibility_watchdog_retained"] is True
    assert receipt["cleanup"]["watchdog_cancelled"] is False
    assert receipt["cleanup"]["watchdog_secret_released"] is False
    assert receipt["secret_cleanup_confirmed"] is False
    assert receipt["secrets_persisted"] is None
    assert fake.watchdog.terminated is False
    assert "branch_delete" not in fake.events


def test_failed_branch_lifecycle_blocks_even_when_project_health_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class FailedLifecycleRunner(FakeRunner):
        def run_json(self, command: list[str], **kwargs: object) -> object:
            value = super().run_json(command, **kwargs)
            if "branches" in command and "list" in command:
                assert isinstance(value, list)
                for row in value:
                    if isinstance(row, dict) and row.get("project_ref") == CHILD_REF:
                        row["status"] = "MIGRATIONS_FAILED"
                        row["preview_project_status"] = "ACTIVE_HEALTHY"
            return value

    fake = FailedLifecycleRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()

    assert exit_code == 1
    assert receipt["failure_code"] == "preview_child_failed_readiness"
    assert "project_list" not in fake.events
    assert "branch_get_secrets" not in fake.events
    assert "preview_migration_apply" not in fake.events
    assert fake.events.count("branch_delete") == 1
    assert receipt["cleanup"]["absence_confirmations"] == 3


@pytest.mark.parametrize(
    ("persistent", "with_data", "compute_size", "expected_failure"),
    (
        (True, False, "small", "preview_child_persistent_readback_invalid"),
        (None, False, "small", "preview_child_persistent_readback_invalid"),
        (False, True, "small", "preview_child_with_data_readback_invalid"),
        (False, None, "small", "preview_child_with_data_readback_invalid"),
        (False, False, "medium", "preview_child_compute_size_not_small"),
        (
            False,
            False,
            None,
            "preview_child_compute_size_readback_invalid",
        ),
    ),
)
def test_branch_shape_is_server_read_back_and_fails_closed(
    persistent: bool | None,
    with_data: bool | None,
    compute_size: str | None,
    expected_failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner(
        persistent=persistent,
        with_data=with_data,
        compute_size=compute_size,
    )
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == expected_failure
    assert receipt["branch"]["size"] is None
    assert receipt["branch"]["persistent"] is None
    assert receipt["branch"]["with_data"] is None
    assert "branch_get_secrets" not in fake.events
    assert "preview_migration_apply" not in fake.events
    assert fake.events.count("branch_delete") == 1
    assert receipt["cleanup"]["absence_confirmations"] == 3


def test_ambiguous_delete_is_resolved_by_three_read_only_absence_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner(delete_ambiguous=True)
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 0
    assert receipt["cleanup"]["delete_response_ambiguous"] is True
    assert receipt["cleanup"]["delete_failure_code"] == "supabase_branch_delete_timeout"
    assert receipt["cleanup"]["absence_confirmations"] == 3
    assert fake.events.count("branch_delete") == 1


def test_watchdog_cancel_failure_is_a_cleanup_failure_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner(watchdog_cancel_failure=True)
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["cleanup_failure_code"] == "cleanup_watchdog_cancel_failed"
    assert receipt["cleanup"]["absence_confirmations"] == 3
    assert receipt["cleanup"]["watchdog_cancelled"] is False


def test_watchdog_unsafe_ack_cannot_claim_secret_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner(watchdog_unsafe_ack=True)
    proof = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    )
    receipt, exit_code = proof.run()
    try:
        assert exit_code == 1
        assert receipt["cleanup_failure_code"] == "cleanup_watchdog_cancel_failed"
        assert receipt["cleanup"]["watchdog_cancelled"] is False
        assert receipt["cleanup"]["watchdog_secret_released"] is False
        assert receipt["secret_cleanup_confirmed"] is False
        assert receipt["secrets_persisted"] is None
    finally:
        proof._close_watchdog_control_socket()
        proof._clear_watchdog_control_dir()


def test_management_home_cleanup_failure_makes_secret_persistence_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    real_rmtree = RUNNER.shutil.rmtree

    def fail_management_home(path: object, *args: object, **kwargs: object) -> None:
        if Path(path).name.startswith("harmony-supabase-home-"):
            raise OSError("synthetic cleanup failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(RUNNER.shutil, "rmtree", fail_management_home)
    fake = FakeRunner()
    proof = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    )
    receipt, exit_code = proof.run()

    assert exit_code == 1
    assert receipt["cleanup_failure_code"] == (
        "supabase_management_home_cleanup_failed"
    )
    assert receipt["secret_cleanup_confirmed"] is False
    assert receipt["secrets_persisted"] is None
    assert proof.management_home
    assert Path(proof.management_home).exists()

    monkeypatch.setattr(RUNNER.shutil, "rmtree", real_rmtree)
    proof._clear_management_home()
    assert proof.management_home == ""
    assert proof.management_home_cleanup_confirmed is True


def test_empty_branch_list_cannot_be_misread_as_deletion_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)

    class EmptyCleanupListRunner(FakeRunner):
        def run_json(self, command: list[str], **kwargs: object) -> object:
            value = super().run_json(command, **kwargs)
            if (
                "branches" in command
                and "list" in command
                and "branch_delete" in self.events
            ):
                return []
            return value

    fake = EmptyCleanupListRunner()
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert (
        receipt["cleanup_failure_code"]
        == "supabase_branch_list_parent_fence_missing"
    )
    assert receipt["cleanup"]["absence_confirmations"] == 0
    assert receipt["cleanup"]["watchdog_cancelled"] is False


def test_branch_credentials_parse_flexible_env_shape_and_fence_child() -> None:
    value = {
        "nested": {
            "POSTGRES_URL_NON_POOLING": (
                f"postgresql://postgres:{DB_SECRET}@db.{CHILD_REF}.supabase.co:5432/postgres"
            ),
            "SUPABASE_URL": f"https://{CHILD_REF}.supabase.co",
            "SUPABASE_ANON_KEY": PUBLISHABLE,
            "SUPABASE_JWT_SECRET": JWT_SECRET,
        }
    }
    credentials = RUNNER.extract_branch_credentials(value, CHILD_REF)
    assert credentials.host == f"db.{CHILD_REF}.supabase.co"
    assert credentials.password == DB_SECRET
    assert credentials.publishable_key == PUBLISHABLE
    credentials.scrub()
    assert credentials.password == ""
    assert credentials.publishable_key == ""
    assert credentials.jwt_secret == ""

    with pytest.raises(RUNNER.ProofError, match="branch_direct_database_fence_mismatch"):
        RUNNER.extract_branch_credentials(value, "z" * 20)


def test_branch_credentials_parse_labeled_cli_secret_json_without_service_key() -> None:
    value = {
        "secrets": [
            {
                "name": "POSTGRES_URL_NON_POOLING",
                "value": (
                    f"postgresql://postgres:{DB_SECRET}"
                    f"@db.{CHILD_REF}.supabase.co:5432/postgres"
                ),
            },
            {"name": "SUPABASE_URL", "value": f"https://{CHILD_REF}.supabase.co"},
            {"label": "JWT Secret", "secret": JWT_SECRET},
        ],
        "api_keys": [
            {"name": "service_role", "api_key": "must-be-ignored"},
            {"type": "anon", "api_key": PUBLISHABLE},
        ],
    }
    credentials = RUNNER.extract_branch_credentials(value, CHILD_REF)
    assert credentials.password == DB_SECRET
    assert credentials.publishable_key == PUBLISHABLE
    assert credentials.jwt_secret == JWT_SECRET


def test_branch_credentials_parse_structured_cli_shape() -> None:
    value = {
        "database": {
            "hostname": f"db.{CHILD_REF}.supabase.co",
            "port": "5432",
            "username": "postgres",
            "db_password": DB_SECRET,
        },
        "credentials": [
            {"name": "Legacy JWT Secret", "value": JWT_SECRET},
            {"label": "Publishable Key", "value": PUBLISHABLE},
        ],
    }
    credentials = RUNNER.extract_branch_credentials(value, CHILD_REF)
    assert credentials.host == f"db.{CHILD_REF}.supabase.co"
    assert credentials.password == DB_SECRET
    assert credentials.project_url == f"https://{CHILD_REF}.supabase.co"
    assert credentials.publishable_key == PUBLISHABLE


class StubPopen:
    def __init__(
        self,
        command: list[str],
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        timeout_once: bool = False,
    ) -> None:
        self.command = command
        self.returncode = returncode
        self.stdout_payload = stdout
        self.stderr_payload = stderr
        self.timeout_once = timeout_once
        self.communicate_calls = 0
        self.pid = os.getpid()

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        self.communicate_calls += 1
        if self.timeout_once and self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(
                self.command,
                1,
                output=self.stdout_payload,
                stderr=self.stderr_payload,
            )
        return self.stdout_payload, self.stderr_payload

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    (
        (b"11 424242 Z\n12 424242 Z+\n13 7 S\n", "DEAD_ONLY"),
        (b"11 424242 Z\n12 424242 S\n", "LIVE"),
        (b"malformed\n", "UNKNOWN"),
    ),
)
def test_process_group_state_snapshot_is_strict_and_zombie_aware(
    snapshot: bytes,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "killpg", lambda _pgid, _signum: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=snapshot),
    )

    assert RUNNER.ProcessRunner._process_group_state(424242) == expected


def test_process_group_state_ps_timeout_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "killpg", lambda _pgid, _signum: None)

    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(["ps"], 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert (
        RUNNER.ProcessRunner._process_group_state(424242)
        == RUNNER.PROCESS_GROUP_UNKNOWN
    )


@pytest.mark.parametrize(
    ("state", "expected_code"),
    (
        ("ABSENT", None),
        ("DEAD_ONLY", None),
        ("LIVE", "synthetic_process_group_still_present"),
        ("UNKNOWN", "synthetic_process_group_unconfirmed"),
    ),
)
def test_external_group_confirmation_uses_same_quiescent_states_without_signals(
    state: str,
    expected_code: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "_process_group_state",
        staticmethod(lambda _pgid: state),
    )
    monkeypatch.setattr(RUNNER, "PROCESS_GROUP_KILL_WAIT_SECONDS", 0.0)
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, signum: kill_calls.append((pgid, signum)),
    )

    if expected_code is None:
        RUNNER.ProcessRunner.confirm_external_process_group_quiescent(
            424242,
            code="synthetic",
        )
    else:
        with pytest.raises(RUNNER.ProofError) as caught:
            RUNNER.ProcessRunner.confirm_external_process_group_quiescent(
                424242,
                code="synthetic",
            )
        assert caught.value.code == expected_code

    assert kill_calls == []


@pytest.mark.parametrize(
    ("state", "succeeds"),
    (
        ("DEAD_ONLY", True),
        ("LIVE", False),
        ("UNKNOWN", False),
    ),
)
def test_process_group_fence_signals_term_and_kill_at_most_once(
    state: str,
    succeeds: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = StubPopen(["synthetic-child"])
    process.pid = 424242
    signals: list[signal.Signals] = []
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, signum: (
            signals.append(signal.Signals(signum))
            if pgid == process.pid
            else None
        ),
    )
    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "_process_group_state",
        staticmethod(lambda _pgid: state),
    )
    monkeypatch.setattr(RUNNER, "PROCESS_GROUP_KILL_WAIT_SECONDS", 0.0)

    if succeeds:
        RUNNER.ProcessRunner().terminate_process_group(process, code="synthetic")
    else:
        with pytest.raises(RUNNER.ProofError) as caught:
            RUNNER.ProcessRunner().terminate_process_group(
                process,
                code="synthetic",
            )
        assert caught.value.code == "synthetic_process_group_unconfirmed"

    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_command_failure_never_interpolates_captured_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda command, **_kwargs: StubPopen(
        command,
        returncode=1,
        stdout=b'{"jwt_secret":"' + JWT_SECRET.encode() + b'"}',
        stderr=DB_SECRET.encode(),
    ))
    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "terminate_process_group",
        lambda _self, _process, *, code: None,
    )
    with pytest.raises(RUNNER.CommandError) as caught:
        RUNNER.ProcessRunner().run_json(
            ["supabase", "branches", "get"],
            code="supabase_branch_get",
        )
    assert str(caught.value) == "supabase_branch_get_failed"
    assert JWT_SECRET not in str(caught.value)
    assert DB_SECRET not in str(caught.value)


def test_exact_sql_is_piped_to_psql_without_a_mutable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingPopen(StubPopen):
        def communicate(
            self,
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            captured["input"] = input
            captured["timeout"] = timeout
            return super().communicate(input=input, timeout=timeout)

    def complete(command: list[str], **kwargs: object) -> CapturingPopen:
        captured["command"] = command
        captured.update(kwargs)
        return CapturingPopen(command)

    monkeypatch.setattr(subprocess, "Popen", complete)
    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "terminate_process_group",
        lambda _self, _process, *, code: None,
    )
    RUNNER.ProcessRunner().run_quiet(
        ["psql", "-f", "-"],
        input_bytes=SQL_PAYLOAD,
        timeout=3,
        code="preview_migration_apply",
    )
    assert captured["input"] == SQL_PAYLOAD
    assert captured["stdin"] == subprocess.PIPE
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.PIPE
    assert captured["start_new_session"] is True
    assert captured["close_fds"] is True


@pytest.mark.parametrize(
    "code",
    (
        "supabase_branch_list",
        "supabase_branch_get",
        "supabase_branch_create",
        "supabase_branch_delete",
    ),
)
def test_management_cli_timeout_is_typed_and_drops_captured_secrets(
    code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = StubPopen(
        ["supabase"],
        stdout=MANAGEMENT_TOKEN.encode(),
        stderr=DB_SECRET.encode(),
        timeout_once=True,
    )
    group_reaped: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "terminate_process_group",
        lambda _self, _process, *, code: group_reaped.append(code),
    )
    with pytest.raises(RUNNER.CommandError) as caught:
        RUNNER.ProcessRunner().run_bytes(
            ["supabase", "branches", "list"], timeout=1, code=code
        )
    assert caught.value.code == f"{code}_timeout"
    assert caught.value.ambiguous is True
    assert group_reaped == [code]
    assert MANAGEMENT_TOKEN not in str(caught.value)
    assert DB_SECRET not in str(caught.value)


def test_probe_timeout_reaps_child_and_grandchild_process_group(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "process-group-pids"
    heartbeat_path = tmp_path / "grandchild-heartbeat"
    grandchild_code = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "ab", buffering=0) as heartbeat:
    while True:
        heartbeat.write(b"x")
        time.sleep(0.02)
"""
    parent_code = """
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [sys.executable, "-c", sys.argv[3], sys.argv[2]],
    close_fds=True,
)
Path(sys.argv[1]).write_text(
    f"{os.getpid()} {grandchild.pid}", encoding="ascii"
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
"""
    runner = RUNNER.ProcessRunner()
    with pytest.raises(RUNNER.CommandError) as caught:
        runner.run_bytes(
            [
                sys.executable,
                "-I",
                "-c",
                parent_code,
                str(pid_path),
                str(heartbeat_path),
                grandchild_code,
            ],
            env={"LC_ALL": "C"},
            timeout=0.3,
            code="direct_db_probe",
        )
    assert caught.value.code == "direct_db_probe_timeout"
    assert caught.value.ambiguous is True
    parent_pid, grandchild_pid = (
        int(value) for value in pid_path.read_text(encoding="ascii").split()
    )
    heartbeat_size = heartbeat_path.stat().st_size
    time.sleep(0.15)
    assert heartbeat_path.stat().st_size == heartbeat_size

    assert grandchild_pid > 1
    _assert_no_live_group_members(parent_pid)


def test_signal_mask_block_failure_still_reaps_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_path = tmp_path / "mask-failure-process-group-pids"
    heartbeat_path = tmp_path / "mask-failure-grandchild-heartbeat"
    grandchild_code = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "ab", buffering=0) as heartbeat:
    while True:
        heartbeat.write(b"x")
        time.sleep(0.02)
"""
    parent_code = """
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [sys.executable, "-c", sys.argv[3], sys.argv[2]],
    close_fds=True,
)
Path(sys.argv[1]).write_text(
    f"{os.getpid()} {grandchild.pid}", encoding="ascii"
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            parent_code,
            str(pid_path),
            str(heartbeat_path),
            grandchild_code,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    pids: tuple[int, int] | None = None
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if pid_path.exists() and heartbeat_path.exists():
                if heartbeat_path.stat().st_size > 0:
                    pids = tuple(
                        int(value)
                        for value in pid_path.read_text(encoding="ascii").split()
                    )
                    break
            time.sleep(0.05)
        assert pids is not None

        def fail_mask(*, code: str) -> set[signal.Signals]:
            raise RUNNER.ProofError(f"{code}_signal_mask_failed")

        monkeypatch.setattr(
            RUNNER.ProcessRunner,
            "_block_interrupt_signals",
            staticmethod(fail_mask),
        )
        with pytest.raises(RUNNER.ProofError) as caught:
            RUNNER.ProcessRunner().terminate_process_group(
                process,
                code="direct_database_probe",
            )
        assert caught.value.code == "direct_database_probe_signal_mask_failed"
        heartbeat_size = heartbeat_path.stat().st_size
        time.sleep(0.15)
        assert heartbeat_path.stat().st_size == heartbeat_size

        _assert_no_live_group_members(pids[0])
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def test_process_group_failure_outranks_signal_mask_restore_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = StubPopen(["synthetic-child"])
    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "_block_interrupt_signals",
        staticmethod(lambda *, code: set()),
    )

    def fail_restore(
        _previous: set[signal.Signals],
        *,
        code: str,
    ) -> None:
        raise RUNNER.ProofError(f"{code}_signal_mask_restore_failed")

    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "_restore_signal_mask",
        staticmethod(fail_restore),
    )
    monkeypatch.setattr(
        os,
        "killpg",
        lambda _pid, _signum: (_ for _ in ()).throw(OSError("synthetic")),
    )
    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "_process_group_state",
        staticmethod(lambda _pgid: RUNNER.PROCESS_GROUP_UNKNOWN),
    )
    with pytest.raises(RUNNER.ProofError) as caught:
        RUNNER.ProcessRunner().terminate_process_group(
            process,
            code="signed_postgrest_probe",
        )
    assert caught.value.code == "signed_postgrest_probe_process_group_unconfirmed"


@pytest.mark.parametrize(("returncode", "fails"), ((0, False), (9, True)))
def test_success_and_nonzero_exit_reap_background_process_group(
    returncode: int,
    fails: bool,
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / f"exit-{returncode}-process-group-pids"
    heartbeat_path = tmp_path / f"exit-{returncode}-heartbeat"
    grandchild_code = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "ab", buffering=0) as heartbeat:
    while True:
        heartbeat.write(b"x")
        time.sleep(0.02)
"""
    child_code = """
import os
from pathlib import Path
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [sys.executable, "-c", sys.argv[4], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
deadline = time.monotonic() + 5
while not Path(sys.argv[2]).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
Path(sys.argv[1]).write_text(
    f"{os.getpid()} {grandchild.pid}", encoding="ascii"
)
raise SystemExit(int(sys.argv[3]))
"""
    command = [
        sys.executable,
        "-I",
        "-c",
        child_code,
        str(pid_path),
        str(heartbeat_path),
        str(returncode),
        grandchild_code,
    ]
    runner = RUNNER.ProcessRunner()
    if fails:
        with pytest.raises(RUNNER.CommandError) as caught:
            runner.run_bytes(
                command,
                env={"LC_ALL": "C"},
                timeout=10,
                code="direct_database_probe",
            )
        assert caught.value.code == "direct_database_probe_failed"
    else:
        result = runner.run_bytes(
            command,
            env={"LC_ALL": "C"},
            timeout=10,
            code="direct_database_probe",
        )
        assert result == b""

    pids = tuple(
        int(value) for value in pid_path.read_text(encoding="ascii").split()
    )
    assert len(pids) == 2
    heartbeat_size = heartbeat_path.stat().st_size
    time.sleep(0.15)
    assert heartbeat_path.stat().st_size == heartbeat_size
    _assert_no_live_group_members(pids[0])


def test_sigterm_to_runner_reaps_active_child_and_grandchild_group(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "signal-process-group-pids"
    heartbeat_path = tmp_path / "signal-grandchild-heartbeat"
    result_path = tmp_path / "signal-result"
    grandchild_code = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "ab", buffering=0) as heartbeat:
    while True:
        heartbeat.write(b"x")
        time.sleep(0.02)
"""
    child_code = """
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [sys.executable, "-c", sys.argv[3], sys.argv[2]],
    close_fds=True,
)
Path(sys.argv[1]).write_text(
    f"{os.getpid()} {grandchild.pid}", encoding="ascii"
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
"""
    outer_code = f"""
import importlib.util
from pathlib import Path
import signal
import sys

spec = importlib.util.spec_from_file_location("signal_harmony_runner", {str(SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

def interrupt(_signum, _frame):
    raise module.ProofError("signal_interrupt")

signal.signal(signal.SIGTERM, interrupt)
try:
    module.ProcessRunner().run_bytes(
        [
            sys.executable,
            "-I",
            "-c",
            {child_code!r},
            sys.argv[1],
            sys.argv[2],
            {grandchild_code!r},
        ],
        env={{"LC_ALL": "C"}},
        timeout=60,
        code="signed_postgrest_probe",
    )
except module.ProofError as exc:
    Path(sys.argv[3]).write_text(exc.code, encoding="ascii")
    raise SystemExit(3)
raise SystemExit(4)
"""
    outer = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            outer_code,
            str(pid_path),
            str(heartbeat_path),
            str(result_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    inner_pids: tuple[int, int] | None = None
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if pid_path.exists() and heartbeat_path.exists():
                if heartbeat_path.stat().st_size > 0:
                    inner_pids = tuple(
                        int(value)
                        for value in pid_path.read_text(encoding="ascii").split()
                    )
                    break
            time.sleep(0.05)
        assert inner_pids is not None

        os.kill(outer.pid, signal.SIGTERM)
        _stdout, stderr = outer.communicate(timeout=12)
        assert outer.returncode == 3, stderr.decode("utf-8", "replace")
        assert result_path.read_text(encoding="ascii") == "signal_interrupt"
        heartbeat_size = heartbeat_path.stat().st_size
        time.sleep(0.15)
        assert heartbeat_path.stat().st_size == heartbeat_size

        _assert_no_live_group_members(inner_pids[0])
    finally:
        if outer.poll() is None:
            outer.kill()
            outer.wait(timeout=5)


def test_sigterm_at_post_communicate_fence_edge_reaps_grandchild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_path = tmp_path / "post-communicate-process-group-pids"
    heartbeat_path = tmp_path / "post-communicate-grandchild-heartbeat"
    grandchild_code = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "ab", buffering=0) as heartbeat:
    while True:
        heartbeat.write(b"x")
        time.sleep(0.02)
"""
    child_code = """
import os
from pathlib import Path
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [sys.executable, "-c", sys.argv[3], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
deadline = time.monotonic() + 5
while not Path(sys.argv[2]).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
Path(sys.argv[1]).write_text(
    f"{os.getpid()} {grandchild.pid}", encoding="ascii"
)
"""
    original_block = RUNNER.ProcessRunner._block_interrupt_signals
    block_calls = 0

    def interrupt_at_fence(*, code: str) -> set[signal.Signals]:
        nonlocal block_calls
        block_calls += 1
        if block_calls == 2:
            os.kill(os.getpid(), signal.SIGTERM)
        return original_block(code=code)

    def interrupt(_signum: int, _frame: object) -> None:
        raise RUNNER.ProofError("signal_interrupt")

    monkeypatch.setattr(
        RUNNER.ProcessRunner,
        "_block_interrupt_signals",
        staticmethod(interrupt_at_fence),
    )

    previous_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, interrupt)
    pids: tuple[int, int] | None = None
    try:
        with pytest.raises(RUNNER.ProofError) as caught:
            RUNNER.ProcessRunner().run_bytes(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    child_code,
                    str(pid_path),
                    str(heartbeat_path),
                    grandchild_code,
                ],
                env={"LC_ALL": "C"},
                timeout=10,
                code="signed_postgrest_probe",
            )
        assert caught.value.code == "signal_interrupt"
        pids = tuple(
            int(value)
            for value in pid_path.read_text(encoding="ascii").split()
        )
        assert len(pids) == 2
        assert block_calls >= 3
        heartbeat_size = heartbeat_path.stat().st_size
        time.sleep(0.15)
        assert heartbeat_path.stat().st_size == heartbeat_size

        _assert_no_live_group_members(pids[0])
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


@pytest.mark.parametrize("mode", ("timeout", "nonzero", "success"))
def test_retained_watchdog_reaps_credential_cli_descendants(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / f"watchdog-{mode}-deleted"
    pid_path = tmp_path / f"watchdog-{mode}-pids"
    heartbeat_path = tmp_path / f"watchdog-{mode}-heartbeat"
    fake_cli = tmp_path / f"fake-supabase-{mode}"
    branch_name = f"hc-proof-{'a' * 12}-20260828000000-{'b' * 12}"
    grandchild_code = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "ab", buffering=0) as heartbeat:
    while True:
        heartbeat.write(b"x")
        time.sleep(0.02)
"""
    fake_cli.write_text(
        f'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

state = Path(os.environ["HARMONY_WATCHDOG_TEST_STATE"])
pid_path = Path(os.environ["HARMONY_WATCHDOG_TEST_PIDS"])
heartbeat = Path(os.environ["HARMONY_WATCHDOG_TEST_HEARTBEAT"])
mode = os.environ["HARMONY_WATCHDOG_TEST_MODE"]
if "list" in sys.argv:
    rows = [
        {{"id": "parent", "project_ref": {PARENT_REF!r}, "is_default": True}}
    ]
    if not state.exists():
        rows.append(
            {{
                "id": "branch-id-1",
                "name": {branch_name!r},
                "project_ref": {CHILD_REF!r},
                "is_default": False,
            }}
        )
    print(json.dumps(rows))
    raise SystemExit(0)
if "delete" in sys.argv:
    state.write_text("deleted", encoding="ascii")
    grandchild = subprocess.Popen(
        [sys.executable, "-c", {grandchild_code!r}, str(heartbeat)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.monotonic() + 5
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    pid_path.write_text(
        f"{{os.getpid()}} {{grandchild.pid}}", encoding="ascii"
    )
    if mode == "nonzero":
        raise SystemExit(9)
    if mode == "success":
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)
raise SystemExit(2)
''',
        encoding="utf-8",
    )
    fake_cli.chmod(0o700)

    monkeypatch.setattr(RUNNER, "WATCHDOG_SECONDS", 0)
    monkeypatch.setenv("HARMONY_WATCHDOG_TEST_STATE", str(state_path))
    monkeypatch.setenv("HARMONY_WATCHDOG_TEST_PIDS", str(pid_path))
    monkeypatch.setenv(
        "HARMONY_WATCHDOG_TEST_HEARTBEAT", str(heartbeat_path)
    )
    monkeypatch.setenv("HARMONY_WATCHDOG_TEST_MODE", mode)
    args = _args(tmp_path)
    args.supabase = str(fake_cli)
    args.supabase_read_timeout_seconds = 1
    args.supabase_mutation_timeout_seconds = 0.2
    proof = RUNNER.HarmonyPreviewProof(args, runner=RUNNER.ProcessRunner())
    proof.management_token = MANAGEMENT_TOKEN
    proof.management_home = tempfile.mkdtemp(prefix="harmony-supabase-home-")
    proof.management_home_cleanup_confirmed = False
    proof._arm_watchdog(branch_name)
    watchdog_root = proof.watchdog_control_dir
    proof._detach_watchdog()
    proof._clear_management_home()
    assert proof.management_home_cleanup_confirmed is True
    watchdog = proof.watchdog
    assert isinstance(watchdog, subprocess.Popen)
    pids: tuple[int, int] | None = None
    try:
        watchdog.wait(timeout=20)
        assert watchdog.returncode == 0
        assert not os.path.lexists(watchdog_root)
        pids = tuple(
            int(value)
            for value in pid_path.read_text(encoding="ascii").split()
        )
        assert len(pids) == 2
        heartbeat_size = heartbeat_path.stat().st_size
        time.sleep(0.15)
        assert heartbeat_path.stat().st_size == heartbeat_size

        _assert_no_live_group_members(pids[0])
    finally:
        if watchdog.poll() is None:
            proof.runner.terminate_process_group(
                watchdog,
                code="cleanup_watchdog_test",
            )
        proof.watchdog = None
        proof.management_token = ""
        proof._close_watchdog_control_socket()


def test_watchdog_cancel_waits_for_active_cli_group_and_home_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_path = tmp_path / "watchdog-cancel-pids"
    heartbeat_path = tmp_path / "watchdog-cancel-heartbeat"
    fake_cli = tmp_path / "fake-supabase-cancel"
    branch_name = f"hc-proof-{'a' * 12}-20260828000000-{'c' * 12}"
    grandchild_code = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "ab", buffering=0) as heartbeat:
    while True:
        heartbeat.write(b"x")
        time.sleep(0.02)
"""
    fake_cli.write_text(
        f'''#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

pid_path = Path(os.environ["HARMONY_WATCHDOG_CANCEL_PIDS"])
heartbeat = Path(os.environ["HARMONY_WATCHDOG_CANCEL_HEARTBEAT"])
grandchild = subprocess.Popen(
    [sys.executable, "-c", {grandchild_code!r}, str(heartbeat)],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
deadline = time.monotonic() + 5
while not heartbeat.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
pid_path.write_text(
    f"{{os.getpid()}} {{grandchild.pid}}", encoding="ascii"
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
''',
        encoding="utf-8",
    )
    fake_cli.chmod(0o700)

    monkeypatch.setattr(RUNNER, "WATCHDOG_SECONDS", 0)
    monkeypatch.setenv("HARMONY_WATCHDOG_CANCEL_PIDS", str(pid_path))
    monkeypatch.setenv(
        "HARMONY_WATCHDOG_CANCEL_HEARTBEAT", str(heartbeat_path)
    )
    args = _args(tmp_path)
    args.supabase = str(fake_cli)
    args.supabase_read_timeout_seconds = 60
    proof = RUNNER.HarmonyPreviewProof(args, runner=RUNNER.ProcessRunner())
    proof.management_token = MANAGEMENT_TOKEN
    proof.management_home = tempfile.mkdtemp(prefix="harmony-supabase-home-")
    proof.management_home_cleanup_confirmed = False
    proof._arm_watchdog(branch_name)
    watchdog_root = proof.watchdog_control_dir
    watchdog = proof.watchdog
    assert isinstance(watchdog, subprocess.Popen)
    pids: tuple[int, int] | None = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pid_path.exists() and heartbeat_path.exists():
                if heartbeat_path.stat().st_size > 0:
                    pids = tuple(
                        int(value)
                        for value in pid_path.read_text(encoding="ascii").split()
                    )
                    break
            time.sleep(0.05)
        assert pids is not None

        proof._cancel_watchdog()
        assert proof.cleanup_receipt["watchdog_cancelled"] is True
        assert watchdog.poll() is not None
        assert proof.watchdog is None
        assert not os.path.lexists(watchdog_root)
        heartbeat_size = heartbeat_path.stat().st_size
        time.sleep(0.15)
        assert heartbeat_path.stat().st_size == heartbeat_size

        _assert_no_live_group_members(pids[0])
    finally:
        if watchdog.poll() is None:
            proof.runner.terminate_process_group(
                watchdog,
                code="cleanup_watchdog_test",
                term_grace_seconds=RUNNER.WATCHDOG_CANCEL_GRACE_SECONDS,
            )
        proof.watchdog = None
        proof.management_token = ""
        proof._clear_management_home()


@pytest.mark.parametrize(
    ("code", "expected_failure"),
    (
        ("supabase_branch_list", "supabase_branch_list_timeout"),
        ("supabase_branch_get", "supabase_branch_get_timeout"),
    ),
)
def test_read_timeout_fails_closed_with_typed_receipt(
    code: str,
    expected_failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = FakeRunner(timeout_code=code)
    receipt, exit_code = RUNNER.HarmonyPreviewProof(
        _args(tmp_path),
        runner=fake,
        opener=lambda *_args, **_kwargs: OpenApiResponse(),
        sleeper=lambda _seconds: None,
        clock=_clock(),
    ).run()
    assert exit_code == 1
    assert receipt["failure_code"] == expected_failure
    if code == "supabase_branch_get":
        assert "branch_delete" in fake.events
        assert receipt["cleanup"]["absence_confirmations"] == 3


class FakeGitRunner:
    def __init__(self, *, dirty: bool = False) -> None:
        self.dirty = dirty

    def run_bytes(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
        code: str,
    ) -> bytes:
        if "rev-parse" in command:
            return (RELEASE_SHA + "\n").encode()
        if "status" in command:
            return b" M unsafe.sql\n" if self.dirty else b""
        if "ls-files" in command:
            return (command[-1] + "\n").encode()
        raise AssertionError(command)


def _write_manifest_fixture(root: Path) -> None:
    (root / "supabase/migrations").mkdir(parents=True)
    for filename in RUNNER.MIGRATIONS:
        (root / "supabase/migrations" / filename).write_text(
            f"-- {filename}\n", encoding="utf-8"
        )
    for relative in RUNNER.SUPPORT_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_support_payload(relative))


def test_exact_checkout_requires_clean_head_and_emits_nine_hashes(
    tmp_path: Path,
) -> None:
    _write_manifest_fixture(tmp_path)
    manifest, support_manifest = RUNNER.verify_exact_checkout(
        FakeGitRunner(), tmp_path, RELEASE_SHA
    )
    assert tuple(manifest) == RUNNER.MIGRATIONS
    assert len(manifest) == 9
    assert all(RUNNER.SHA256_PATTERN.fullmatch(value) for value in manifest.values())
    assert tuple(support_manifest) == tuple(map(str, RUNNER.SUPPORT_PATHS))
    assert support_manifest == _support_manifest()

    with pytest.raises(RUNNER.ProofError, match="exact_head_worktree_not_clean"):
        RUNNER.verify_exact_checkout(FakeGitRunner(dirty=True), tmp_path, RELEASE_SHA)
    with pytest.raises(RUNNER.ProofError, match="release_sha_not_current_head"):
        RUNNER.verify_exact_checkout(FakeGitRunner(), tmp_path, "d" * 40)


def test_management_temp_cleanup_and_in_memory_probe_scrub_are_separate() -> None:
    proof = RUNNER.HarmonyPreviewProof(_args(Path.cwd()), runner=FakeRunner())
    with tempfile.TemporaryDirectory(prefix="harmony-supabase-home-") as home:
        home_path = Path(home)
        (home_path / "config.json").write_text("secret", encoding="utf-8")
        proof.management_home = home
        proof._clear_management_home()
        assert proof.management_home == ""
        assert not home_path.exists()

    proof.proof_snapshot_payloads[str(RUNNER.PROBE_PATHS[0])] = PROBE_PAYLOAD
    proof._clear_proof_snapshot_payloads()
    assert proof.proof_snapshot_payloads == {}


def test_source_contract_has_no_secret_cli_arguments_and_one_postgrest_call() -> None:
    source = SCRIPT.read_text()
    for forbidden in (
        "--db-password",
        "--jwt-secret",
        "--publishable-key",
        "--service-role-key",
        "--persistent\",",
        "--with-data\",",
    ):
        assert forbidden not in source
    assert "WATCHDOG_SECONDS = 110 * 60" in source
    assert "same_child_repair_attempts\": 0" in source
    assert "replacement_branch_attempts\": 0" in source
    assert source.count("code=\"signed_postgrest_probe\"") == 1
    assert 'sys.executable,\n                    "-I",\n                    "-",' in source
    assert "harmony-proof-snapshot-" not in source
    assert len(RUNNER.MIGRATIONS) == 9
    assert MANAGEMENT_TOKEN not in source
    assert "--profile" not in source
