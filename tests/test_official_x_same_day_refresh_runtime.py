from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from core.automation import same_day_refresh_runtime as runtime
from scripts.run_official_x_same_day_refresh import _parser, run


SHA = "a" * 40
WORKSPACE = "11111111-1111-4111-8111-111111111111"


def environment(**updates):
    return {
        "OFFICIAL_X_SAME_DAY_REFRESH_ENABLED": "false",
        "OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA": SHA,
        "RAILWAY_GIT_COMMIT_SHA": SHA,
        "CONTENT_STUDIO_WORKSPACE_ID": WORKSPACE,
        **updates,
    }


def forbidden(*args, **kwargs):
    raise AssertionError("external client or manifest must not be constructed")


def execute(argv=None, env=None, stamp=SHA):
    client_calls = []
    clock_calls = []

    def blocked_client(*args, **kwargs):
        client_calls.append(True)
        return forbidden()

    def blocked_clock():
        clock_calls.append(True)
        return forbidden()

    result = run(_parser().parse_args(argv or ["--validate-runtime-only"]),
               environ=environment() if env is None else env,
               runner_factory=blocked_client, stamp_reader=lambda: stamp,
               now_factory=blocked_clock)
    assert client_calls == [] and clock_calls == []
    return result


def assert_zero_io(result):
    for key in ("network_calls", "database_calls", "provider_calls",
                "private_send_attempted", "public_send_attempted"):
        assert result[key] is False


def test_runtime_only_needs_no_candidates_or_clock_and_never_constructs_clients():
    result = execute()
    assert result == {
        "ok": True, "mode": "validate_runtime_only", "enabled": False,
        "network_calls": False, "database_calls": False, "provider_calls": False,
        "private_send_attempted": False, "public_send_attempted": False,
        "release_sha": SHA, "native_runtime_sha_matches": True,
        "image_stamp_matches": True, "workspace_config_valid": True,
        "image_attestation": False, "hosted_provenance_verified": False,
    }


@pytest.mark.parametrize("key,value", [
    ("OFFICIAL_X_SAME_DAY_REFRESH_ENABLED", "true"),
    ("OFFICIAL_X_SAME_DAY_REFRESH_ENABLED", ""),
    ("OFFICIAL_X_SAME_DAY_REFRESH_ENABLED", "False"),
    ("OFFICIAL_X_SAME_DAY_REFRESH_ENABLED", "0"),
    ("OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA", "b" * 40),
    ("OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA", "a" * 39),
    ("OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA", "A" * 40),
    ("RAILWAY_GIT_COMMIT_SHA", "b" * 40),
    ("RAILWAY_GIT_COMMIT_SHA", SHA + "\n"),
    ("RAILWAY_GIT_COMMIT_SHA", None),
    ("CONTENT_STUDIO_WORKSPACE_ID", "00000000-0000-0000-0000-000000000000"),
    ("CONTENT_STUDIO_WORKSPACE_ID", "11111111111141118111111111111111"),
    ("CONTENT_STUDIO_WORKSPACE_ID", "not-uuid"),
    ("CONTENT_STUDIO_WORKSPACE_ID", None),
])
def test_runtime_config_failure_is_bounded_and_zero_io(key, value):
    result = execute(env=environment(**{key: value}))
    assert result["ok"] is False and result["error"] == "same_day_refresh_failed"
    assert_zero_io(result)
    assert "release_sha" not in result


@pytest.mark.parametrize("key", list(environment()))
def test_missing_config_is_not_off_or_runtime_proof(key):
    env = environment()
    del env[key]
    result = execute(env=env)
    assert not result["ok"]
    assert_zero_io(result)


@pytest.mark.parametrize("stamp", ["", "b" * 40, "a" * 39, "A" * 40, SHA + "\n", None, 1])
def test_stale_missing_or_malformed_image_stamp_is_rejected(stamp):
    result = execute(stamp=stamp)
    assert not result["ok"]
    assert_zero_io(result)


@pytest.mark.parametrize("key", [
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TYPEFULLY_API_KEY",
    "GROK_QA_RELAY_TOKEN", "XAI_API_KEY", "X_BEARER_TOKEN", "DATABASE_URL",
    "RAILWAY_API_TOKEN", "UNRELATED_PASSWORD", "OTHER_SECRET", "OTHER_DSN",
    "telegram_bot_token", "GPG_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN", "AZURE_CLIENT_SECRET", "PGPASSWORD", "REDIS_URL", "CLOUDSDK_CONFIG",
    "POSTGRES_URL", "DB_CONNECTION_STRING", "MONGODB_URI", "HTTP_PROXY", "https_proxy", "ALL_PROXY", "NO_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
])
def test_unexpected_credential_names_are_rejected_without_reading_values(key):
    class SecretEnv(dict):
        def __init__(self, values):
            super().__init__(values)
            self.credential_reads = []

        def get(self, name, default=None):
            if name == key:
                self.credential_reads.append(name)
                if key != "GPG_KEY":
                    raise AssertionError("credential value must not be retrieved")
            return super().get(name, default)

    env = SecretEnv(environment(**{key: "synthetic-never-reflected"}))
    result = execute(env=env)
    assert not result["ok"]
    assert "synthetic-never-reflected" not in str(result)
    assert env.credential_reads == (["GPG_KEY"] if key == "GPG_KEY" else [])
    assert_zero_io(result)


def test_runtime_only_allows_only_named_future_service_credentials_without_reading_them():
    class SecretEnv(dict):
        def get(self, name, default=None):
            if name in {"SUPABASE_SERVICE_ROLE_KEY", "STUDIO_AUTOMATION_TOKEN"}:
                raise AssertionError("credential value must not be retrieved")
            return super().get(name, default)

    env = SecretEnv(environment(SUPABASE_SERVICE_ROLE_KEY=object(),
                               STUDIO_AUTOMATION_TOKEN=object(), GPG_KEY=""))
    assert execute(env=env)["ok"]


@pytest.mark.parametrize("command", ["queue-once", "generate-once", "inspect"])
def test_runtime_only_cannot_select_any_business_operation(command):
    result = execute(["--validate-runtime-only", command])
    assert not result["ok"]
    assert_zero_io(result)
    with pytest.raises(SystemExit):
        _parser().parse_args([command, "--validate-runtime-only"])


def test_validation_modes_are_mutually_exclusive_before_or_after_subcommand():
    with pytest.raises(SystemExit):
        _parser().parse_args(["--validate-runtime-only", "--validate-only"])
    result = execute(["--validate-runtime-only", "inspect", "--validate-only"])
    assert not result["ok"]
    assert_zero_io(result)


def test_stamp_reader_failure_never_becomes_execution_or_a_detailed_error():
    result = run(_parser().parse_args(["--validate-runtime-only"]), environ=environment(),
                 runner_factory=forbidden, now_factory=forbidden,
                 stamp_reader=lambda: (_ for _ in ()).throw(OSError("sensitive-path-error")))
    assert not result["ok"] and "sensitive-path-error" not in str(result)
    assert_zero_io(result)


@pytest.mark.parametrize("raw", [b"", b"a" * 40, b"a" * 39 + b"\n", b"A" * 40 + b"\n",
                                  b"a" * 40 + b"\nextra", b"a" * 40 + b"\r\n"])
def test_fixed_stamp_file_rejects_noncanonical_or_oversized_bytes(tmp_path, monkeypatch, raw):
    path = tmp_path / "stamp"
    path.write_bytes(raw)
    path.chmod(0o444)
    monkeypatch.setattr(runtime, "IMAGE_STAMP_PATH", str(path))
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_mode=0o100444, st_uid=0))
    with pytest.raises(ValueError):
        runtime.read_image_stamp()


def test_fixed_reader_is_bounded_closes_fd_and_rejects_writable_or_user_owned_file(tmp_path, monkeypatch):
    path = tmp_path / "stamp"
    path.write_bytes(SHA.encode() + b"\n")
    path.chmod(0o444)
    monkeypatch.setattr(runtime, "IMAGE_STAMP_PATH", str(path))
    for info in (SimpleNamespace(st_mode=0o100644, st_uid=0),
                 SimpleNamespace(st_mode=0o100444, st_uid=10001)):
        monkeypatch.setattr(runtime.os, "fstat", lambda fd: info)
        with pytest.raises(ValueError):
            runtime.read_image_stamp()
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_mode=0o100444, st_uid=0))
    assert runtime.read_image_stamp() == SHA  # Synthetic metadata, not a hosted image proof.


def test_stamp_symlink_is_not_followed(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_bytes(SHA.encode() + b"\n")
    link = tmp_path / "stamp"
    link.symlink_to(target)
    monkeypatch.setattr(runtime, "IMAGE_STAMP_PATH", str(link))
    with pytest.raises(OSError):
        runtime.read_image_stamp()


def test_environment_cannot_override_image_stamp_path():
    env = environment(OFFICIAL_X_SAME_DAY_REFRESH_IMAGE_STAMP_PATH="/tmp/caller-controlled")
    seen = []
    result = run(_parser().parse_args(["--validate-runtime-only"]), environ=env,
                 runner_factory=forbidden, now_factory=forbidden,
                 stamp_reader=lambda: (seen.append(runtime.IMAGE_STAMP_PATH) or SHA))
    assert result["ok"] and seen == ["/app/same-day-refresh-build-sha"]
