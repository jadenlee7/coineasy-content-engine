from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.run_official_x_same_day_refresh import _parser, build_runner, run


NOW = datetime(2026, 10, 5, 3, tzinfo=timezone.utc)
SHA = "a" * 40
WORKSPACE = "11111111-1111-4111-8111-111111111111"
REFRESH = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"
SOURCE = "44444444-4444-4444-8444-444444444444"
JOB = "55555555-5555-4555-8555-555555555555"
VERSION = "66666666-6666-4666-8666-666666666666"


def argv(command="generate-once"):
    common = [command, "--workspace-id", WORKSPACE, "--refresh-id", REFRESH, "--release-sha", SHA]
    if command == "inspect":
        return common + ["--content-version-id", VERSION]
    common += ["--client-id", "yellow", "--kst-date", "2026-10-05", "--request-id", REQUEST,
        "--source-item-id", SOURCE, "--expires-at", "2026-10-05T04:00:00Z"]
    if command == "generate-once":
        return common + ["--job-id", JOB]
    return common + ["--predecessor-job-id", "77777777-7777-4777-8777-777777777777",
        "--predecessor-content-item-id", "88888888-8888-4888-8888-888888888888",
        "--predecessor-content-version-id", "99999999-9999-4999-8999-999999999999"]


class GuardedEnv(dict):
    def __init__(self, **updates):
        super().__init__(OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA=SHA, RAILWAY_GIT_COMMIT_SHA=SHA,
                         CONTENT_STUDIO_WORKSPACE_ID=WORKSPACE)
        self.update(updates)
        self.reads = []

    def get(self, key, default=None):
        self.reads.append(key)
        if key in {"SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "STUDIO_BASE_URL", "STUDIO_AUTOMATION_TOKEN"}:
            raise AssertionError("OFF/invalid/validate-only must not read credentials")
        return super().get(key, default)


def forbidden_factory(*args, **kwargs):
    raise AssertionError("must not create any DB/HTTP/provider clients")


def execute(args, env=None, factory=forbidden_factory):
    return run(_parser().parse_args(args), environ=env if env is not None else GuardedEnv(),
               now_factory=lambda: NOW, runner_factory=factory, stamp_reader=lambda: SHA)


def test_default_off_no_manifest_no_credentials_no_clients():
    env = GuardedEnv()
    receipt = execute([], env)
    assert receipt["ok"] and receipt["enabled"] is False
    assert receipt["network_calls"] is receipt["database_calls"] is receipt["provider_calls"] is False
    assert env.reads == ["OFFICIAL_X_SAME_DAY_REFRESH_ENABLED"]


@pytest.mark.parametrize("command", ["queue-once", "generate-once", "inspect"])
def test_off_exact_operation_remains_zero_io(command):
    receipt = execute(argv(command))
    assert receipt["ok"] and receipt["enabled"] is False
    assert receipt["private_send_attempted"] is receipt["public_send_attempted"] is False


@pytest.mark.parametrize("position", ["before", "after"])
@pytest.mark.parametrize("enabled", ["true", "false"])
def test_validate_only_even_enabled_reads_no_secrets_and_creates_no_clients(position, enabled):
    args = (["--validate-only"] + argv()) if position == "before" else (argv() + ["--validate-only"])
    receipt = execute(args, GuardedEnv(OFFICIAL_X_SAME_DAY_REFRESH_ENABLED=enabled))
    assert receipt["ok"] and receipt["mode"] == "validate_only"
    assert receipt["network_calls"] is receipt["database_calls"] is receipt["provider_calls"] is False
    assert receipt["image_attestation"] is False and receipt["native_runtime_sha_matches"] is True


@pytest.mark.parametrize("key,value", [
    ("RAILWAY_GIT_COMMIT_SHA", "b" * 40), ("RAILWAY_GIT_COMMIT_SHA", ""),
    ("OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA", "b" * 40),
    ("OFFICIAL_X_SAME_DAY_REFRESH_ENABLED", "yes"),
])
def test_bad_native_release_or_flag_blocks_before_credentials(key, value):
    env = GuardedEnv(OFFICIAL_X_SAME_DAY_REFRESH_ENABLED="true")
    env[key] = value
    receipt = execute(argv(), env)
    assert not receipt["ok"] and receipt["error"] == "same_day_refresh_failed"


@pytest.mark.parametrize("configured", ["", "77777777-7777-4777-8777-777777777777", "bad"])
def test_wrong_or_unset_configured_workspace_blocks_before_clients_and_secrets(configured):
    receipt = execute(argv(), GuardedEnv(OFFICIAL_X_SAME_DAY_REFRESH_ENABLED="true",
        CONTENT_STUDIO_WORKSPACE_ID=configured))
    assert not receipt["ok"] and receipt["error"] == "same_day_refresh_failed"


@pytest.mark.parametrize("flag,value", [
    ("--request-id", "not-uuid"), ("--source-item-id", "not-uuid"),
    ("--kst-date", "2026-10-04"), ("--expires-at", "2026-10-05T04:00:00"),
    ("--expires-at", "2026-10-05T03:00:00Z"), ("--expires-at", "2026-10-05T06:00:00Z"),
    ("--release-sha", "short"), ("--client-id", "unlisted"),
])
def test_invalid_manifest_blocks_before_secret_read_or_client_creation(flag, value):
    args = argv()
    args[args.index(flag) + 1] = value
    receipt = execute(args, GuardedEnv(OFFICIAL_X_SAME_DAY_REFRESH_ENABLED="true"))
    assert not receipt["ok"]


class RunnerFake:
    def __init__(self):
        self.calls = []

    async def queue_once(self, manifest):
        self.calls.append(("queue", manifest))
        return {"ok": True, "status": "queued", "job_id": JOB,
                "private_send_attempted": False, "public_send_attempted": False}

    async def generate_once(self, manifest):
        self.calls.append(("generate", manifest))
        return {"ok": True, "status": "needs_review", "content_version_id": VERSION,
                "private_send_attempted": False, "public_send_attempted": False}

    async def inspect(self, **kwargs):
        self.calls.append(("inspect", kwargs))
        return {"status": "refresh_ready", "release_sha": SHA,
                "execution_authorized": False, "delivery_authorized": False}


@pytest.mark.parametrize("command,expected", [
    ("queue-once", "queue"), ("generate-once", "generate"), ("inspect", "inspect")])
def test_enabled_valid_manifest_dispatches_only_selected_exact_operation(command, expected):
    runner = RunnerFake()
    calls = []

    def factory(env, *, workspace_id, operation):
        calls.append(workspace_id)
        assert operation == command
        return runner

    receipt = execute(argv(command), GuardedEnv(OFFICIAL_X_SAME_DAY_REFRESH_ENABLED="true"), factory)
    assert receipt["ok"] and calls == [WORKSPACE]
    assert len(runner.calls) == 1 and runner.calls[0][0] == expected


@pytest.mark.parametrize("command", ["queue-once", "generate-once", "inspect"])
def test_enabled_operation_requires_native_image_stamp_before_runner(command):
    created = []

    def factory(*args, **kwargs):
        created.append(True)
        return forbidden_factory()

    receipt = run(_parser().parse_args(argv(command)),
        environ=GuardedEnv(OFFICIAL_X_SAME_DAY_REFRESH_ENABLED="true"),
        now_factory=lambda: NOW, runner_factory=factory, stamp_reader=lambda: "b" * 40)
    assert not receipt["ok"] and receipt["error"] == "same_day_refresh_failed"
    assert created == []


def test_no_fifo_batch_poll_send_publish_or_all_cli_options():
    for option in ("--all", "--poll", "--batch", "--send", "--publish", "--activate"):
        with pytest.raises(SystemExit):
            _parser().parse_args(argv() + [option])


@pytest.mark.parametrize("operation", ["queue-once", "inspect"])
def test_queue_and_inspection_builder_need_no_studio_credentials_or_client(monkeypatch, operation):
    import scripts.run_official_x_same_day_refresh as module
    import core.automation.generation_client as generation
    created = []

    class DatabaseFake:
        def __init__(self, **kwargs):
            created.append(kwargs)

    class OnlyDatabaseEnv(dict):
        def get(self, key, default=None):
            assert not key.startswith("STUDIO_")
            return super().get(key, default)

    monkeypatch.setattr(module, "SupabaseSameDayRefreshRepository", DatabaseFake)
    monkeypatch.setattr(generation, "StudioGenerationClient", forbidden_factory)
    runner = build_runner(OnlyDatabaseEnv(SUPABASE_URL="https://synthetic.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="x" * 64), workspace_id=WORKSPACE, operation=operation)
    assert runner.generation is None and len(created) == 1
