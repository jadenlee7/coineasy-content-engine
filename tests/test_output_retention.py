import os
from pathlib import Path

import pytest
from fastapi import BackgroundTasks

from api.output_retention import (
    RetentionPolicy,
    cleanup_generated_runs,
    load_retention_policy,
    mark_run_active,
)


NOW = 2_087_200_000.0


def _policy(*, ttl=100.0, max_bytes=10_000, min_age=10.0, lease=100.0):
    return RetentionPolicy(
        ttl_seconds=ttl,
        max_total_bytes=max_bytes,
        min_age_seconds=min_age,
        active_lease_seconds=lease,
    )


def _run(root: Path, name: str, *, age: float, size: int = 10) -> Path:
    path = root / "squid" / name
    path.mkdir(parents=True)
    (path / "asset.png").write_bytes(b"x" * size)
    os.utime(path, (NOW - age, NOW - age))
    return path


def test_cleanup_deletes_only_stale_runs_and_preserves_fresh_current_and_active(tmp_path):
    stale = _run(tmp_path, "news_2087200000000000001", age=500)
    fresh = _run(tmp_path, "edu_2087200000000000002", age=5)
    current = _run(tmp_path, "news_2087200000000000003", age=500)
    active = _run(tmp_path, "edu_2087200000000000004", age=500)
    marker = active / ".generation-active"
    marker.touch()
    os.utime(marker, (NOW - 1, NOW - 1))

    unrelated = tmp_path / "squid" / "exports"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep")
    os.utime(unrelated, (NOW - 1_000, NOW - 1_000))

    result = cleanup_generated_runs(
        tmp_path,
        policy=_policy(),
        preserve=(current,),
        now=NOW,
    )

    assert result.deleted_runs == 1
    assert not stale.exists()
    assert fresh.exists()
    assert current.exists()
    assert active.exists()
    assert unrelated.exists()


def test_size_cap_removes_oldest_eligible_runs_but_never_current_run(tmp_path):
    current = _run(tmp_path, "news_2087200000000000010", age=300, size=10)
    oldest = _run(tmp_path, "news_2087200000000000011", age=200, size=10)
    newer = _run(tmp_path, "edu_2087200000000000012", age=100, size=10)

    result = cleanup_generated_runs(
        tmp_path,
        policy=_policy(ttl=10_000, max_bytes=15),
        preserve=(current,),
        now=NOW,
    )

    assert current.exists()
    assert not oldest.exists()
    assert not newer.exists()
    assert result.deleted_runs == 2
    assert result.remaining_bytes == 10


def test_cleanup_never_traverses_symlinks_or_deletes_non_run_paths(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    protected = outside / "protected.txt"
    protected.write_text("do not delete")

    client_dir = tmp_path / "squid"
    client_dir.mkdir()
    try:
        (client_dir / "news_2087200000000000020").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    cleanup_generated_runs(tmp_path, policy=_policy(ttl=1), now=NOW)

    assert protected.read_text() == "do not delete"
    with pytest.raises(ValueError, match="non-generated"):
        mark_run_active(tmp_path, tmp_path)
    with pytest.raises(ValueError, match="non-generated"):
        mark_run_active(tmp_path, outside / "news_2087200000000000021")


def test_cleanup_removes_stale_second_resolution_legacy_run_but_keeps_short_name(tmp_path):
    legacy = _run(tmp_path, "edu_2087200000", age=500)
    short_unknown = _run(tmp_path, "news_208720000", age=500)

    result = cleanup_generated_runs(
        tmp_path,
        policy=_policy(),
        now=NOW,
    )

    assert result.deleted_runs == 1
    assert not legacy.exists()
    assert short_unknown.exists()


def test_environment_policy_keeps_hard_download_safety_floors(monkeypatch):
    monkeypatch.setenv("OUTPUT_RETENTION_TTL_HOURS", "0.001")
    monkeypatch.setenv("OUTPUT_RETENTION_MAX_MIB", "1")
    monkeypatch.setenv("OUTPUT_RETENTION_MIN_AGE_MINUTES", "0.001")
    monkeypatch.setenv("OUTPUT_RETENTION_ACTIVE_LEASE_MINUTES", "1")

    policy = load_retention_policy()

    assert policy.ttl_seconds >= 60 * 60
    assert policy.max_total_bytes >= 64 * 1024 * 1024
    assert policy.min_age_seconds >= 5 * 60
    assert policy.active_lease_seconds >= 30 * 60


def test_server_schedules_retention_after_releasing_the_active_run(tmp_path, monkeypatch):
    from api import server

    run = _run(tmp_path, "news_2087200000000000040", age=0)
    mark_run_active(tmp_path, run)
    queued = BackgroundTasks()
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)

    server._finish_output_run(run, queued)

    assert not (run / ".generation-active").exists()
    assert len(queued.tasks) == 1
    assert queued.tasks[0].func is server.cleanup_generated_runs_best_effort
