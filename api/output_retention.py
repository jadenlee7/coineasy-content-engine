"""Bounded, path-safe retention for Railway-local generated assets.

Only directories created by the synchronous generation endpoints are eligible:

    <output root>/<client id>/edu_<time_ns>
    <output root>/<client id>/news_<time_ns>

The hard minimum age gives Netlify enough time to download a completed response.
An active marker protects in-flight work across multiple server workers.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_CLIENT_DIR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Current runs use nanoseconds (19 digits). Older deployments used Unix seconds
# (10 digits), so retain exact numeric matching while covering both generations.
_RUN_DIR_RE = re.compile(r"^(?:edu|news)_\d{10,20}$")
_ACTIVE_MARKER = ".generation-active"
_ACTIVE_RUNS: set[Path] = set()
_ACTIVE_RUNS_LOCK = threading.Lock()

_DEFAULT_TTL_HOURS = 24.0
_DEFAULT_MAX_MIB = 2_048
_DEFAULT_MIN_AGE_MINUTES = 30.0
_DEFAULT_ACTIVE_LEASE_MINUTES = 120.0

# Even a bad environment value must not make a just-returned file disappear.
_HARD_MIN_TTL_SECONDS = 60 * 60
_HARD_MIN_SIZE_BYTES = 64 * 1024 * 1024
_HARD_MIN_AGE_SECONDS = 5 * 60
_HARD_MIN_ACTIVE_LEASE_SECONDS = 30 * 60


@dataclass(frozen=True)
class RetentionPolicy:
    ttl_seconds: float
    max_total_bytes: int
    min_age_seconds: float
    active_lease_seconds: float


@dataclass(frozen=True)
class CleanupResult:
    scanned_runs: int
    deleted_runs: int
    deleted_bytes: int
    remaining_bytes: int


@dataclass(frozen=True)
class _Run:
    path: Path
    modified_at: float
    size_bytes: int


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def load_retention_policy() -> RetentionPolicy:
    """Read conservative retention limits from the process environment."""
    min_age = max(
        _HARD_MIN_AGE_SECONDS,
        _env_float("OUTPUT_RETENTION_MIN_AGE_MINUTES", _DEFAULT_MIN_AGE_MINUTES)
        * 60,
    )
    ttl = max(
        _HARD_MIN_TTL_SECONDS,
        min_age,
        _env_float("OUTPUT_RETENTION_TTL_HOURS", _DEFAULT_TTL_HOURS) * 60 * 60,
    )
    max_bytes = max(
        _HARD_MIN_SIZE_BYTES,
        int(_env_float("OUTPUT_RETENTION_MAX_MIB", _DEFAULT_MAX_MIB) * 1024 * 1024),
    )
    active_lease = max(
        _HARD_MIN_ACTIVE_LEASE_SECONDS,
        _env_float(
            "OUTPUT_RETENTION_ACTIVE_LEASE_MINUTES",
            _DEFAULT_ACTIVE_LEASE_MINUTES,
        )
        * 60,
    )
    return RetentionPolicy(
        ttl_seconds=ttl,
        max_total_bytes=max_bytes,
        min_age_seconds=min_age,
        active_lease_seconds=active_lease,
    )


def _exact_run_path(output_root: Path, requested: Path) -> Path | None:
    """Return a normalized path only when it is an exact generated run path."""
    root = output_root.resolve()
    candidate = requested if requested.is_absolute() else root / requested
    try:
        candidate = candidate.resolve(strict=False)
        relative = candidate.relative_to(root)
    except (OSError, ValueError):
        return None

    if len(relative.parts) != 2:
        return None
    client_name, run_name = relative.parts
    if not _CLIENT_DIR_RE.fullmatch(client_name) or not _RUN_DIR_RE.fullmatch(run_name):
        return None
    if candidate.parent.is_symlink() or candidate.is_symlink():
        return None
    return candidate


def mark_run_active(output_root: Path, run_dir: Path) -> None:
    """Create a cross-worker active marker for one exact run directory."""
    safe_run = _exact_run_path(output_root, run_dir)
    if safe_run is None:
        raise ValueError("refusing to mark a non-generated output directory")
    safe_run.mkdir(parents=True, exist_ok=True)
    marker = safe_run / _ACTIVE_MARKER
    marker.touch(exist_ok=True)
    with _ACTIVE_RUNS_LOCK:
        _ACTIVE_RUNS.add(safe_run)


def clear_run_active(output_root: Path, run_dir: Path) -> None:
    """Remove the active marker without ever unlinking outside an exact run."""
    safe_run = _exact_run_path(output_root, run_dir)
    if safe_run is None:
        return
    try:
        (safe_run / _ACTIVE_MARKER).unlink(missing_ok=True)
    except OSError:
        # A stale marker is lease-bounded and will not retain a crashed run forever.
        pass
    finally:
        with _ACTIVE_RUNS_LOCK:
            _ACTIVE_RUNS.discard(safe_run)


def _directory_size(path: Path) -> int:
    total = 0
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _marker_is_active(run_dir: Path, *, now: float, lease_seconds: float) -> bool:
    with _ACTIVE_RUNS_LOCK:
        if run_dir in _ACTIVE_RUNS:
            return True
    marker = run_dir / _ACTIVE_MARKER
    try:
        if marker.is_symlink() or not marker.is_file():
            return False
        marker_age = now - marker.stat().st_mtime
    except OSError:
        return False
    return marker_age <= lease_seconds


def _discover_runs(output_root: Path) -> list[_Run]:
    root = output_root.resolve()
    if not root.exists() or root.is_symlink() or not root.is_dir():
        return []

    runs: list[_Run] = []
    try:
        client_dirs = list(root.iterdir())
    except OSError:
        return runs
    for client_dir in client_dirs:
        if (
            client_dir.is_symlink()
            or not _CLIENT_DIR_RE.fullmatch(client_dir.name)
            or not client_dir.is_dir()
        ):
            continue
        try:
            children = list(client_dir.iterdir())
        except OSError:
            continue
        for child in children:
            safe_run = _exact_run_path(root, child)
            if safe_run is None or not safe_run.is_dir():
                continue
            try:
                modified_at = safe_run.stat().st_mtime
            except OSError:
                continue
            runs.append(
                _Run(
                    path=safe_run,
                    modified_at=modified_at,
                    size_bytes=_directory_size(safe_run),
                )
            )
    return runs


def _delete_run(
    output_root: Path,
    run: _Run,
    *,
    now: float,
    policy: RetentionPolicy,
) -> bool:
    safe_run = _exact_run_path(output_root, run.path)
    if safe_run is None or not safe_run.exists() or safe_run.is_symlink():
        return False
    if _marker_is_active(
        safe_run,
        now=now,
        lease_seconds=policy.active_lease_seconds,
    ):
        return False
    try:
        shutil.rmtree(safe_run)
    except OSError:
        return False
    return True


def cleanup_generated_runs(
    output_root: Path,
    *,
    policy: RetentionPolicy | None = None,
    preserve: Iterable[Path] = (),
    now: float | None = None,
) -> CleanupResult:
    """Delete expired runs, then oldest safe runs until the byte cap is met.

    Unknown files, client roots, symlinks, active runs, explicitly preserved runs,
    and runs younger than ``min_age_seconds`` are never deleted.
    """
    root = output_root.resolve()
    active_policy = policy or load_retention_policy()
    current_time = time.time() if now is None else now
    preserved = {
        safe
        for path in preserve
        if (safe := _exact_run_path(root, path)) is not None
    }
    runs = sorted(_discover_runs(root), key=lambda run: run.modified_at)
    remaining_bytes = sum(run.size_bytes for run in runs)
    deleted_paths: set[Path] = set()
    deleted_bytes = 0

    def eligible(run: _Run, *, minimum_age: float) -> bool:
        if run.path in preserved or run.path in deleted_paths:
            return False
        if current_time - run.modified_at < minimum_age:
            return False
        return not _marker_is_active(
            run.path,
            now=current_time,
            lease_seconds=active_policy.active_lease_seconds,
        )

    ttl_age = max(active_policy.ttl_seconds, active_policy.min_age_seconds)
    for run in runs:
        if not eligible(run, minimum_age=ttl_age):
            continue
        if _delete_run(root, run, now=current_time, policy=active_policy):
            deleted_paths.add(run.path)
            deleted_bytes += run.size_bytes
            remaining_bytes = max(0, remaining_bytes - run.size_bytes)

    if remaining_bytes > active_policy.max_total_bytes:
        for run in runs:
            if remaining_bytes <= active_policy.max_total_bytes:
                break
            if not eligible(run, minimum_age=active_policy.min_age_seconds):
                continue
            if _delete_run(root, run, now=current_time, policy=active_policy):
                deleted_paths.add(run.path)
                deleted_bytes += run.size_bytes
                remaining_bytes = max(0, remaining_bytes - run.size_bytes)

    return CleanupResult(
        scanned_runs=len(runs),
        deleted_runs=len(deleted_paths),
        deleted_bytes=deleted_bytes,
        remaining_bytes=remaining_bytes,
    )


def cleanup_generated_runs_best_effort(
    output_root: Path,
    *,
    preserve: Iterable[Path] = (),
) -> CleanupResult | None:
    """Run retention without allowing housekeeping to fail generation/startup."""
    try:
        return cleanup_generated_runs(output_root, preserve=preserve)
    except Exception as exc:  # pragma: no cover - defensive operational boundary
        print(f"⚠ Output retention cleanup skipped: {type(exc).__name__}: {exc}")
        return None
