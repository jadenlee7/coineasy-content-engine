#!/usr/bin/env python3
"""Run one disposable, exact-SHA Harmony Preview proof.

The runner deliberately owns the full Preview lifecycle so a credential-bearing
Supabase CLI response can never be printed by an outer shell or agent tool.  It
creates exactly one non-persistent Small branch without Production data, applies
only the nine allow-listed migrations, runs the direct-DB proof before the
signed PostgREST proof, and deletes the child in ``finally``.

There is no provider, Grok, Buzz, approval, message, Recap delivery, feature
flag, publication, or Production database path here.  A failed or ambiguous
operation is never repaired or retried on the same child.  Read-only readiness
and deletion-observation polling are the only repeated network operations.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import gc
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable, Mapping, MutableMapping, Sequence
from urllib import error, parse, request
import uuid


SCHEMA_VERSION = "harmony-preview-one-shot-proof@2"
PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")
SHA40_PATTERN = re.compile(r"^[a-f0-9]{40}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
READY_STATUS = "ACTIVE_HEALTHY"
FAILED_PROJECT_STATUSES = {
    "ACTIVE_UNHEALTHY",
    "INIT_FAILED",
    "PAUSE_FAILED",
    "REMOVED",
    "RESTORE_FAILED",
}
PENDING_LIFECYCLE_STATUSES = {"CREATING_PROJECT", "RUNNING_MIGRATIONS"}
SUCCESS_LIFECYCLE_STATUSES = {"", "MIGRATIONS_PASSED", "FUNCTIONS_DEPLOYED"}
FAILED_LIFECYCLE_STATUSES = {"MIGRATIONS_FAILED", "FUNCTIONS_FAILED"}
WATCHDOG_SECONDS = 110 * 60
PROCESS_GROUP_TERM_GRACE_SECONDS = 1.0
PROCESS_GROUP_KILL_WAIT_SECONDS = 5.0
PROCESS_GROUP_STATE_TIMEOUT_SECONDS = 2.0
PROCESS_GROUP_STATE_MAX_BYTES = 2_097_152
PROCESS_GROUP_ABSENT = "ABSENT"
PROCESS_GROUP_DEAD_ONLY = "DEAD_ONLY"
PROCESS_GROUP_LIVE = "LIVE"
PROCESS_GROUP_UNKNOWN = "UNKNOWN"
PROCESS_STATE_PS = next(
    (
        candidate
        for candidate in ("/bin/ps", "/usr/bin/ps")
        if Path(candidate).is_file()
    ),
    "/bin/ps",
)
WATCHDOG_CANCEL_GRACE_SECONDS = 15.0
WATCHDOG_ACTIVE_PGID_FILENAME = "active-pgid"
WATCHDOG_PROTOCOL_SCHEMA = 1
WATCHDOG_MESSAGE_MAX_BYTES = 1024
MAX_OPENAPI_BYTES = 2_097_152
MAX_MANAGEMENT_API_BYTES = 2_097_152
MANAGEMENT_API_BASE_URL = "https://api.supabase.com/v1"
POSTGREST_RPC_PATH = "/rpc/submit_preview_harmony_signal"

MIGRATIONS = (
    "20260825130000_agent_work_order_ledger.sql",
    "20260825131000_agent_work_order_roles.sql",
    "20260825132000_harmony_preview_collaboration.sql",
    "20260825133000_harmony_preview_vertical_slice.sql",
    "20260825134000_harmony_preview_stage_chain.sql",
    "20260825135000_harmony_preview_dashboard_roles.sql",
    "20260825140000_harmony_preview_fixed_specialist_chain.sql",
    "20260826210000_harmony_preview_trust_hardening.sql",
    "20260827220000_harmony_preview_codex_gate_durable.sql",
)

SECURITY_SUITES = (
    "harmony_preview_collaboration_security.sql",
    "harmony_preview_trust_hardening_security.sql",
    "harmony_preview_codex_gate_security.sql",
)

CONFIG_PATH = Path("examples/harmony-preview-squid-config.json")
PROBE_PATHS = (
    Path("scripts/probe_harmony_preview_concurrency.py"),
    Path("scripts/probe_harmony_preview_postgrest.py"),
)
SUPPORT_PATHS = (
    CONFIG_PATH,
    *PROBE_PATHS,
    *(Path("supabase/tests") / filename for filename in SECURITY_SUITES),
)
MANAGEMENT_TOKEN_SOURCE_ENV = "HARMONY_SUPABASE_MANAGEMENT_TOKEN"
BRANCH_SECRET_ENV_NAMES = {
    "DATABASE_URL",
    MANAGEMENT_TOKEN_SOURCE_ENV,
    "HARMONY_PREVIEW_SUPABASE_PUBLISHABLE_KEY",
    "HARMONY_PREVIEW_SUPABASE_LEGACY_JWT_SECRET",
    "PGDATABASE",
    "PGHOST",
    "PGOPTIONS",
    "PGPASSFILE",
    "PGPORT",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGPASSWORD",
    "PGUSER",
    "POSTGRES_URL",
    "POSTGRES_URL_NON_POOLING",
    "SUPABASE_ANON_KEY",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_DB_PASSWORD",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ACCESS_TOKEN",
}
SUBPROCESS_BLOCKED_ENV_NAMES = BRANCH_SECRET_ENV_NAMES | {
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "SSLKEYLOGFILE",
    "SUPABASE_CONFIG_DIR",
    "SUPABASE_CONFIG_PATH",
    "SUPABASE_PROFILE",
}


class ProofError(RuntimeError):
    """A typed, non-secret failure suitable for a redacted receipt."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", code):
            code = "unclassified_preview_proof_failure"
        super().__init__(code)
        self.code = code


class CommandError(ProofError):
    def __init__(self, code: str, *, ambiguous: bool = False) -> None:
        super().__init__(code)
        self.ambiguous = ambiguous


class ManagementApiError(ProofError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.retryable = retryable


class RejectRedirectHandler(request.HTTPRedirectHandler):
    """Reject every redirect so a scoped PAT never crosses origins."""

    def redirect_request(
        self,
        req: request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


@dataclass
class BranchIdentity:
    branch_id: str
    ref: str
    name: str
    status: str
    migration_status: str = ""
    is_default: bool = False
    persistent: bool | None = None
    with_data: bool | None = None


@dataclass
class BranchCredentials:
    host: str
    port: int
    user: str
    database: str
    password: str
    project_url: str
    publishable_key: str
    jwt_secret: str

    def scrub(self) -> None:
        self.password = ""
        self.publishable_key = ""
        self.jwt_secret = ""


class ProcessRunner:
    """Subprocess adapter that never exposes captured output in exceptions."""

    @staticmethod
    def _block_interrupt_signals(*, code: str) -> set[signal.Signals]:
        """Defer SIGINT/SIGTERM across spawn assignment and group fencing."""

        try:
            return signal.pthread_sigmask(
                signal.SIG_BLOCK,
                {signal.SIGINT, signal.SIGTERM},
            )
        except (AttributeError, OSError, ValueError):
            raise ProofError(f"{code}_signal_mask_failed") from None

    @staticmethod
    def _restore_signal_mask(
        previous: set[signal.Signals],
        *,
        code: str,
    ) -> None:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        except (AttributeError, OSError, ValueError):
            raise ProofError(f"{code}_signal_mask_restore_failed") from None

    @staticmethod
    def _process_group_state(pgid: int) -> str:
        """Return a bounded, read-only view of a process group's liveness.

        ``killpg(..., 0)`` alone cannot distinguish a runnable process from an
        orphan zombie that launchd/init has not reaped yet.  Zombies have no
        address space, file descriptors, or credential-bearing environment, so
        a zombie-only group is cleanup-safe even while its numeric PGID exists.
        Any inspection failure remains UNKNOWN and therefore fails closed.
        """

        if not isinstance(pgid, int) or pgid <= 1 or pgid == os.getpgrp():
            return PROCESS_GROUP_UNKNOWN
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return PROCESS_GROUP_ABSENT
        except OSError:
            return PROCESS_GROUP_UNKNOWN

        try:
            completed = subprocess.run(
                [PROCESS_STATE_PS, "-axo", "pid=,pgid=,state="],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                close_fds=True,
                start_new_session=True,
                timeout=PROCESS_GROUP_STATE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return PROCESS_GROUP_UNKNOWN
        output = completed.stdout or b""
        if completed.returncode != 0 or len(output) > PROCESS_GROUP_STATE_MAX_BYTES:
            output = b""
            return PROCESS_GROUP_UNKNOWN
        try:
            text = output.decode("ascii", "strict")
        except UnicodeDecodeError:
            output = b""
            return PROCESS_GROUP_UNKNOWN
        finally:
            output = b""

        matched = False
        dead_only = True
        for raw_line in text.splitlines():
            if not raw_line.strip():
                continue
            fields = raw_line.split()
            if len(fields) != 3:
                return PROCESS_GROUP_UNKNOWN
            try:
                row_pid = int(fields[0])
                row_pgid = int(fields[1])
            except ValueError:
                return PROCESS_GROUP_UNKNOWN
            state = fields[2]
            if row_pid <= 0 or row_pgid <= 0 or not state:
                return PROCESS_GROUP_UNKNOWN
            if row_pgid != pgid:
                continue
            matched = True
            if not state.startswith("Z"):
                dead_only = False
        text = ""
        if matched:
            return PROCESS_GROUP_DEAD_ONLY if dead_only else PROCESS_GROUP_LIVE

        # The group may have disappeared between kill(0) and the ps snapshot.
        # Recheck without ever signalling a possibly reused PGID.
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return PROCESS_GROUP_ABSENT
        except OSError:
            return PROCESS_GROUP_UNKNOWN
        return PROCESS_GROUP_UNKNOWN

    def terminate_process_group(
        self,
        process: subprocess.Popen[bytes],
        *,
        code: str,
        term_grace_seconds: float = PROCESS_GROUP_TERM_GRACE_SECONDS,
    ) -> None:
        """Stop and reap the complete isolated subprocess group.

        Proof probes can own many concurrent ``psql`` descendants, and the
        cleanup watchdog can be inside a Supabase CLI call.  Killing only the
        immediate Python process would leave those descendants able to keep a
        Preview credential or write after branch cleanup starts.
        """

        previous_mask: set[signal.Signals] | None = None
        mask_failure: BaseException | None = None
        group_failure: BaseException | None = None
        restore_failure: BaseException | None = None
        try:
            previous_mask = self._block_interrupt_signals(code=code)
        except BaseException as exc:
            # Signal masking is a race hardening layer, not a reason to skip
            # the only operation that can revoke a live child's credentials.
            mask_failure = exc
        try:
            pid = getattr(process, "pid", None)
            if not isinstance(pid, int) or pid <= 0:
                raise ProofError(f"{code}_process_group_invalid")
            group_absent = False
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                try:
                    process.wait(timeout=0)
                except subprocess.TimeoutExpired:
                    raise ProofError(
                        f"{code}_process_group_unconfirmed"
                    ) from None
                group_absent = True
            except OSError:
                # TERM is only the cooperative first pass.  Even if the OS
                # reports a transient/ambiguous TERM error, continue to the
                # mandatory SIGKILL fence before deciding group ownership.
                pass

            if not group_absent:
                try:
                    process.wait(timeout=term_grace_seconds)
                except subprocess.TimeoutExpired:
                    pass

                # Always fence the group with SIGKILL after the grace period.
                # The direct child may already have exited while a grandchild
                # still owns inherited pipes or credentials.
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    # A just-reaped group can report a transient kill error on
                    # macOS.  The PGID absence loop below is authoritative.
                    pass
                try:
                    process.wait(timeout=PROCESS_GROUP_KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    raise ProofError(
                        f"{code}_process_group_unconfirmed"
                    ) from None
                confirm_deadline = time.monotonic() + PROCESS_GROUP_KILL_WAIT_SECONDS
                while True:
                    state = self._process_group_state(pid)
                    if state in {PROCESS_GROUP_ABSENT, PROCESS_GROUP_DEAD_ONLY}:
                        break
                    if time.monotonic() >= confirm_deadline:
                        raise ProofError(
                            f"{code}_process_group_unconfirmed"
                        )
                    # A process can disappear between kill(0) and the ps
                    # snapshot.  UNKNOWN is never accepted as clean, but a
                    # bounded read-only retry lets that benign race converge
                    # without sending another signal to a stale PGID.
                    time.sleep(0.05)
        except BaseException as exc:
            group_failure = exc
        finally:
            if previous_mask is not None:
                try:
                    self._restore_signal_mask(previous_mask, code=code)
                except BaseException as exc:
                    restore_failure = exc
        # Never let a weaker mask/restore error hide an unconfirmed group.
        if group_failure is not None:
            raise group_failure
        if restore_failure is not None:
            raise restore_failure
        if mask_failure is not None:
            raise mask_failure

    def run_bytes(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
        cwd: str | os.PathLike[str] | None = None,
        timeout: float = 120,
        code: str,
        before_spawn: Callable[[], None] | None = None,
    ) -> bytes:
        process: subprocess.Popen[bytes] | None = None
        previous_handlers: dict[signal.Signals, object] = {}
        installed_handlers: set[signal.Signals] = set()
        pending_interrupt: tuple[int, object | None] | None = None
        interrupt_phase = "before_guard"
        interrupt_unwind_started = False
        cleanup_context = "exit"
        communicate_completed = False
        process_group_fence_attempted = False
        stdout = b""
        try:
            previous_mask = self._block_interrupt_signals(code=code)
            try:
                for interrupt_signal in (signal.SIGINT, signal.SIGTERM):
                    previous_handlers[interrupt_signal] = signal.getsignal(
                        interrupt_signal
                    )

                def defer_handoff_interrupt(
                    signum: int,
                    frame: object,
                ) -> None:
                    nonlocal cleanup_context
                    nonlocal interrupt_unwind_started
                    nonlocal pending_interrupt
                    # pthread_sigmask is thread-local.  On Linux a
                    # process-directed signal can be accepted by another
                    # unblocked thread and schedule Python's main-thread
                    # handler while Popen is still returning.  Coalesce one
                    # interrupt without retaining the signal frame (which can
                    # reference secret-bearing locals).  Only the OWNED phase
                    # may unwind; FENCING/RESTORING must finish atomically.
                    if pending_interrupt is None:
                        pending_interrupt = (signum, None)
                    if (
                        interrupt_phase == "owned"
                        and not interrupt_unwind_started
                    ):
                        interrupt_unwind_started = True
                        cleanup_context = "interrupted"
                        raise ProofError(f"{code}_interrupted")

                try:
                    for interrupt_signal in (signal.SIGINT, signal.SIGTERM):
                        signal.signal(
                            interrupt_signal,
                            defer_handoff_interrupt,
                        )
                        installed_handlers.add(interrupt_signal)
                except (OSError, RuntimeError, ValueError):
                    raise CommandError(
                        f"{code}_signal_handoff_guard_failed",
                        ambiguous=False,
                    ) from None

                try:
                    interrupt_phase = "handoff"
                    # Publish a mutation's sticky commit-state fence inside
                    # the same ownership handoff as Popen.  The process-wide
                    # Python handler above complements the thread-local mask,
                    # so no interrupt can unwind this callback-to-assignment
                    # interval before the child group is owned.
                    if before_spawn is not None:
                        before_spawn()
                    process = subprocess.Popen(
                        list(command),
                        stdin=(
                            subprocess.DEVNULL
                            if input_bytes is None
                            else subprocess.PIPE
                        ),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=None if env is None else dict(env),
                        cwd=None if cwd is None else os.fspath(cwd),
                        close_fds=True,
                        start_new_session=True,
                    )
                except OSError:
                    raise CommandError(f"{code}_spawn_failed") from None
                interrupt_phase = "owned"
                # A signal accepted by another thread while Popen returned
                # may already have run the coalescing handler.  Unwind only
                # now, after process assignment; outer finally owns fencing.
                if pending_interrupt is not None:
                    interrupt_unwind_started = True
                    cleanup_context = "interrupted"
                    raise ProofError(f"{code}_interrupted")
            finally:
                # The phase-aware handler is installed before mutation and
                # remains active through final group fencing, so restoring the
                # caller thread's mask cannot expose an unowned child.
                self._restore_signal_mask(previous_mask, code=code)

            try:
                stdout, stderr = process.communicate(
                    input=input_bytes,
                    timeout=timeout,
                )
                communicate_completed = True
            except subprocess.TimeoutExpired as exc:
                # TimeoutExpired may retain captured bytes.  Clear those
                # references before raising the typed, non-secret failure.
                exc.output = b""
                exc.stderr = b""
                cleanup_context = "timeout"
                raise CommandError(f"{code}_timeout", ambiguous=True) from None
            except BaseException:
                cleanup_context = "interrupted"
                raise

            stdout = stdout or b""
            stderr = b""
            if process.returncode != 0:
                # stdout/stderr may contain a password-bearing connection
                # string or branch JSON.  Never interpolate either.
                stdout = b""
                raise CommandError(f"{code}_failed", ambiguous=True)
            return stdout
        finally:
            try:
                # A signal can be scheduled at the first line event of this
                # finally while the prior phase is still OWNED.  If that first
                # assignment raises, the unconditional inner finally still
                # enters FENCING and owns the entire cleanup sequence.
                interrupt_phase = "fencing"
            finally:
                interrupt_phase = "fencing"
                cleanup_failure: BaseException | None = None
                if process is not None:
                    try:
                        # The finally spans Popen assignment, communicate, result
                        # handling, and the return edge.  Together with the signal
                        # mask inside terminate_process_group this closes both the
                        # pre-communicate and post-communicate SIGTERM races.
                        if not process_group_fence_attempted:
                            process_group_fence_attempted = True
                            self.terminate_process_group(process, code=code)
                        if not communicate_completed:
                            drained_stdout, drained_stderr = process.communicate(
                                timeout=PROCESS_GROUP_KILL_WAIT_SECONDS
                            )
                            drained_stdout = b""
                            drained_stderr = b""
                    except ProofError as exc:
                        if not exc.code.startswith(f"{code}_process_group_"):
                            # A deferred caller signal is delivered only after the
                            # group has been fenced; retain that original signal.
                            cleanup_failure = exc
                        else:
                            stdout = b""
                            suffix = {
                                "timeout": "timeout_process_group_unconfirmed",
                                "interrupted": (
                                    "interrupted_process_group_unconfirmed"
                                ),
                                "exit": "exit_process_group_unconfirmed",
                            }[cleanup_context]
                            cleanup_failure = CommandError(
                                f"{code}_{suffix}", ambiguous=True
                            )
                    except (OSError, subprocess.TimeoutExpired, ValueError):
                        stdout = b""
                        suffix = {
                            "timeout": "timeout_process_group_unconfirmed",
                            "interrupted": (
                                "interrupted_process_group_unconfirmed"
                            ),
                            "exit": "exit_process_group_unconfirmed",
                        }[cleanup_context]
                        cleanup_failure = CommandError(
                            f"{code}_{suffix}", ambiguous=True
                        )

                interrupt_phase = "restoring"
                handler_restore_failure: BaseException | None = None
                restore_guard_mask: set[signal.Signals] | None = None
                guard_mask_failure: BaseException | None = None
                guard_unmask_failure: BaseException | None = None
                if installed_handlers:
                    try:
                        restore_guard_mask = self._block_interrupt_signals(
                            code=code
                        )
                    except BaseException as exc:
                        guard_mask_failure = exc
                    if restore_guard_mask is not None:
                        try:
                            # Unmask while the coalescing handler still owns both
                            # signals.  Any pending caller interrupt is therefore
                            # recorded, not delivered into a half-restored pair.
                            self._restore_signal_mask(
                                restore_guard_mask,
                                code=code,
                            )
                        except BaseException as exc:
                            guard_unmask_failure = exc

                    handler_items = [
                        (interrupt_signal, previous_handlers[interrupt_signal])
                        for interrupt_signal in (signal.SIGINT, signal.SIGTERM)
                        if interrupt_signal in installed_handlers
                    ]

                    def restore_remaining_handlers(index: int) -> None:
                        nonlocal handler_restore_failure
                        if index >= len(handler_items):
                            return
                        interrupt_signal, previous = handler_items[index]
                        try:
                            try:
                                signal.signal(interrupt_signal, previous)
                            except BaseException as exc:
                                if handler_restore_failure is None:
                                    handler_restore_failure = exc
                        finally:
                            # A signal restored earlier can run its caller handler
                            # between bytecodes.  Nested finally guarantees every
                            # later handler still receives a restoration attempt.
                            restore_remaining_handlers(index + 1)

                    try:
                        restore_remaining_handlers(0)
                    except BaseException as exc:
                        if handler_restore_failure is None:
                            handler_restore_failure = exc

                # Preserve the strongest ownership failure.  Handler/mask
                # restoration is best-effort even when its own mask step fails.
                # Only after both succeed may a coalesced caller interrupt replay.
                if cleanup_failure is not None:
                    raise cleanup_failure
                if handler_restore_failure is not None:
                    raise ProofError(f"{code}_signal_guard_restore_failed")
                if guard_unmask_failure is not None:
                    raise guard_unmask_failure
                if guard_mask_failure is not None:
                    raise guard_mask_failure
                if pending_interrupt is not None:
                    signum, frame = pending_interrupt
                    pending_interrupt = None
                    previous = previous_handlers.get(signal.Signals(signum))
                    if callable(previous):
                        previous(signum, frame)
                    raise ProofError(f"{code}_interrupted")

    @staticmethod
    def confirm_external_process_group_absent(
        pgid: int,
        *,
        code: str,
    ) -> None:
        """Confirm a reported PGID is absent without signalling or killing it."""

        if not isinstance(pgid, int) or pgid <= 1 or pgid == os.getpgrp():
            raise ProofError(f"{code}_process_group_invalid")
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except OSError:
            raise ProofError(f"{code}_process_group_unconfirmed") from None
        # The PGID may already have been reused.  Never signal a group after
        # the live watchdog has declared its own child absent; fail closed.
        raise ProofError(f"{code}_process_group_still_present")

    @classmethod
    def confirm_external_process_group_quiescent(
        cls,
        pgid: int,
        *,
        code: str,
    ) -> None:
        """Confirm there are no live members without signalling a stale PGID."""

        deadline = time.monotonic() + PROCESS_GROUP_KILL_WAIT_SECONDS
        state = PROCESS_GROUP_UNKNOWN
        while True:
            state = cls._process_group_state(pgid)
            if state in {PROCESS_GROUP_ABSENT, PROCESS_GROUP_DEAD_ONLY}:
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        if state == PROCESS_GROUP_LIVE:
            raise ProofError(f"{code}_process_group_still_present")
        raise ProofError(f"{code}_process_group_unconfirmed")

    def run_json(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
        cwd: str | os.PathLike[str] | None = None,
        timeout: float = 120,
        code: str,
        before_spawn: Callable[[], None] | None = None,
    ) -> object:
        raw = self.run_bytes(
            command,
            env=env,
            input_bytes=input_bytes,
            cwd=cwd,
            timeout=timeout,
            code=code,
            before_spawn=before_spawn,
        )
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CommandError(f"{code}_invalid_json") from None
        finally:
            raw = b""
        return decoded

    def run_quiet(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
        cwd: str | os.PathLike[str] | None = None,
        timeout: float = 120,
        code: str,
    ) -> None:
        raw = self.run_bytes(
            command,
            env=env,
            input_bytes=input_bytes,
            cwd=cwd,
            timeout=timeout,
            code=code,
        )
        raw = b""

    def popen(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        pass_fds: Sequence[int] = (),
    ) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=dict(env),
                close_fds=True,
                pass_fds=tuple(pass_fds),
                start_new_session=True,
            )
        except OSError:
            raise ProofError("cleanup_watchdog_spawn_failed") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _mint_readiness_jwt(secret: str, branch_ref: str) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": "supabase",
        "aud": "authenticated",
        "role": "coineasy_harmony_connector",
        "ref": branch_ref,
        "iat": now,
        "exp": now + 120,
        "automatic_publication": False,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
    }
    signing_input = (
        _b64url(_compact(header).encode())
        + "."
        + _b64url(_compact(claims).encode())
    )
    signature = hmac.new(
        secret.encode(), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return signing_input + "." + _b64url(signature)


def _safe_process_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in SUBPROCESS_BLOCKED_ENV_NAMES:
        environment.pop(name, None)
    return environment


def _scrub_parent_secret_environment() -> None:
    for name in BRANCH_SECRET_ENV_NAMES:
        os.environ.pop(name, None)


def _git_text(
    runner: ProcessRunner,
    repo_root: Path,
    args: Sequence[str],
    code: str,
) -> str:
    raw = runner.run_bytes(
        ["git", "-C", str(repo_root), *args],
        env=_safe_process_environment(),
        timeout=30,
        code=code,
    )
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ProofError(f"{code}_invalid_utf8") from None
    finally:
        raw = b""


def verify_exact_checkout(
    runner: ProcessRunner,
    repo_root: Path,
    release_sha: str,
) -> tuple[dict[str, str], dict[str, str]]:
    if not SHA40_PATTERN.fullmatch(release_sha):
        raise ProofError("release_sha_invalid")
    head = _git_text(runner, repo_root, ["rev-parse", "HEAD"], "git_head")
    if head != release_sha:
        raise ProofError("release_sha_not_current_head")
    status = _git_text(
        runner,
        repo_root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        "git_status",
    )
    if status:
        raise ProofError("exact_head_worktree_not_clean")

    manifest: dict[str, str] = {}
    for filename in MIGRATIONS:
        relative = Path("supabase/migrations") / filename
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise ProofError("migration_manifest_file_missing")
        _git_text(
            runner,
            repo_root,
            ["ls-files", "--error-unmatch", str(relative)],
            "migration_manifest_tracking",
        )
        manifest[filename] = _sha256(path)
    if len(manifest) != 9 or not all(
        SHA256_PATTERN.fullmatch(value) for value in manifest.values()
    ):
        raise ProofError("migration_manifest_invalid")

    support_manifest: dict[str, str] = {}
    for relative in SUPPORT_PATHS:
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise ProofError("proof_support_file_missing")
        _git_text(
            runner,
            repo_root,
            ["ls-files", "--error-unmatch", str(relative)],
            "proof_support_tracking",
        )
        support_manifest[str(relative)] = _sha256(path)
    if len(support_manifest) != len(SUPPORT_PATHS) or not all(
        SHA256_PATTERN.fullmatch(value) for value in support_manifest.values()
    ):
        raise ProofError("proof_support_manifest_invalid")
    return manifest, support_manifest


def snapshot_exact_artifacts(
    runner: ProcessRunner,
    repo_root: Path,
    release_sha: str,
    manifest: Mapping[str, str],
    support_manifest: Mapping[str, str],
) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, str]]:
    """Load every executable proof artifact from the immutable commit once."""

    migration_payloads: dict[str, bytes] = {}
    support_payloads: dict[str, bytes] = {}
    artifact_sha256: dict[str, str] = {}
    for filename in MIGRATIONS:
        relative = Path("supabase/migrations") / filename
        payload = runner.run_bytes(
            ["git", "-C", str(repo_root), "show", f"{release_sha}:{relative}"],
            env=_safe_process_environment(),
            timeout=30,
            code="migration_snapshot",
        )
        if hashlib.sha256(payload).hexdigest() != manifest.get(filename):
            payload = b""
            raise ProofError("migration_snapshot_digest_mismatch")
        migration_payloads[filename] = payload
    for relative in SUPPORT_PATHS:
        payload = runner.run_bytes(
            ["git", "-C", str(repo_root), "show", f"{release_sha}:{relative}"],
            env=_safe_process_environment(),
            timeout=30,
            code="proof_support_snapshot",
        )
        digest = hashlib.sha256(payload).hexdigest()
        if not payload or digest != support_manifest.get(str(relative)):
            payload = b""
            raise ProofError("proof_support_snapshot_digest_mismatch")
        support_payloads[str(relative)] = payload
        artifact_sha256[str(relative)] = digest
    return migration_payloads, support_payloads, artifact_sha256


def build_postgrest_probe_bundle(
    concurrency_payload: bytes,
    postgrest_payload: bytes,
) -> bytes:
    """Bind the PostgREST probe and its BASE dependency to exact commit bytes."""

    if not concurrency_payload or not postgrest_payload:
        raise ProofError("postgrest_probe_bundle_input_missing")
    module_name = "harmony_preview_concurrency_probe_for_postgrest"
    base_encoded = base64.b64encode(concurrency_payload).decode("ascii")
    postgrest_encoded = base64.b64encode(postgrest_payload).decode("ascii")
    wrapper = f'''import base64
import sys
import types

module_name = {module_name!r}
base_source = base64.b64decode({base_encoded!r}, validate=True)
base_module = types.ModuleType(module_name)
base_module.__file__ = "<exact-sha-concurrency-probe>"
sys.modules[module_name] = base_module
exec(compile(base_source, base_module.__file__, "exec"), base_module.__dict__)
base_source = b""
postgrest_source = base64.b64decode({postgrest_encoded!r}, validate=True)
postgrest_globals = {{
    "__name__": "__main__",
    "__file__": "<exact-sha-postgrest-probe>",
    "__package__": None,
}}
exec(
    compile(postgrest_source, postgrest_globals["__file__"], "exec"),
    postgrest_globals,
)
'''
    return wrapper.encode("utf-8")


def _walk_dicts(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _normalize_label(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _first_string(value: object, names: set[str]) -> str | None:
    normalized = {_normalize_label(name) for name in names}
    for mapping in _walk_dicts(value):
        for key, item in mapping.items():
            if _normalize_label(key) in normalized and isinstance(item, str) and item:
                return item
    return None


def _first_labeled_string(value: object, names: set[str]) -> str | None:
    """Read either env-style objects or named API-key objects safely."""

    normalized = {_normalize_label(name) for name in names}
    label_fields = {"name", "key", "label", "type", "variable"}
    value_fields = {"value", "api_key", "secret", "token"}
    for mapping in _walk_dicts(value):
        labels = {
            _normalize_label(item)
            for key, item in mapping.items()
            if _normalize_label(key) in label_fields and isinstance(item, str)
        }
        if not labels.intersection(normalized):
            continue
        for key, item in mapping.items():
            if (
                _normalize_label(key) in value_fields
                and isinstance(item, str)
                and item
            ):
                return item
    return None


def _first_int(value: object, names: set[str]) -> int | None:
    normalized = {_normalize_label(name) for name in names}
    for mapping in _walk_dicts(value):
        for key, item in mapping.items():
            if _normalize_label(key) not in normalized:
                continue
            if isinstance(item, int):
                return item
            if isinstance(item, str) and item.isdigit():
                return int(item)
    return None


def extract_branches(value: object) -> list[BranchIdentity]:
    branches: list[BranchIdentity] = []
    seen: set[tuple[str, str]] = set()
    for mapping in _walk_dicts(value):
        branch_id = mapping.get("id")
        name = mapping.get("name")
        ref = mapping.get("project_ref", mapping.get("ref"))
        if not all(isinstance(item, str) and item for item in (branch_id, name, ref)):
            continue
        if not PROJECT_REF_PATTERN.fullmatch(str(ref)):
            continue
        key = (str(branch_id), str(ref))
        if key in seen:
            continue
        seen.add(key)
        preview_project_status = mapping.get("preview_project_status")
        legacy_status = mapping.get("status")
        if not isinstance(preview_project_status, str) or not preview_project_status:
            preview_project_status = (
                legacy_status if isinstance(legacy_status, str) else ""
            )
        branches.append(
            BranchIdentity(
                branch_id=str(branch_id),
                ref=str(ref),
                name=str(name),
                status=preview_project_status,
                migration_status=(
                    legacy_status
                    if isinstance(legacy_status, str)
                    and mapping.get("preview_project_status") is not None
                    else ""
                ),
                is_default=(
                    mapping.get("is_default") is True
                    or str(mapping.get("is_default", "")).lower() == "true"
                ),
                persistent=(
                    mapping.get("persistent")
                    if type(mapping.get("persistent")) is bool
                    else None
                ),
                with_data=(
                    mapping.get("with_data")
                    if type(mapping.get("with_data")) is bool
                    else None
                ),
            )
        )
    return branches


def preview_branch_readiness(branch: BranchIdentity) -> str:
    """Classify both project health and the deprecated workflow lifecycle."""

    if (
        branch.status in FAILED_PROJECT_STATUSES
        or branch.migration_status in FAILED_LIFECYCLE_STATUSES
    ):
        return "failed"
    if branch.status != READY_STATUS:
        return "waiting"
    if branch.migration_status in PENDING_LIFECYCLE_STATUSES:
        return "waiting"
    if branch.migration_status in SUCCESS_LIFECYCLE_STATUSES:
        return "ready"
    return "invalid"


def extract_compute_addon_size(value: object) -> str:
    """Fail closed unless one selected compute add-on reports one size.

    ``supabase projects list`` does not include Preview branches and its
    response shape does not expose ``databases[].infra_compute_size``.  The
    exact-child billing add-ons endpoint is the narrow authoritative readback
    for the active compute selection.
    """

    if not isinstance(value, dict):
        raise ProofError("preview_child_compute_size_readback_invalid")
    selected = value.get("selected_addons")
    if not isinstance(selected, list):
        raise ProofError("preview_child_compute_size_readback_invalid")
    matches = [
        addon
        for addon in selected
        if isinstance(addon, dict) and addon.get("type") == "compute_instance"
    ]
    if not matches:
        raise ProofError("preview_child_compute_size_unavailable")
    if len(matches) != 1:
        raise ProofError("preview_child_compute_size_readback_invalid")
    variant = matches[0].get("variant")
    if not isinstance(variant, dict):
        raise ProofError("preview_child_compute_size_readback_invalid")
    variant_id = variant.get("id")
    if not isinstance(variant_id, str):
        raise ProofError("preview_child_compute_size_readback_invalid")
    if re.fullmatch(r"ci_[a-z0-9_]+", variant_id) is None:
        raise ProofError("preview_child_compute_size_readback_invalid")
    if variant_id != "ci_small":
        raise ProofError("preview_child_compute_size_not_small")
    return "small"


def _parse_postgres_url(value: str) -> dict[str, object]:
    try:
        parsed = parse.urlsplit(value)
        port = parsed.port or 5432
    except ValueError:
        raise ProofError("branch_database_url_invalid") from None
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ProofError("branch_database_url_invalid")
    if not parsed.hostname or parsed.password is None or parsed.username is None:
        raise ProofError("branch_database_url_incomplete")
    return {
        "host": parsed.hostname,
        "port": port,
        "user": parse.unquote(parsed.username),
        "password": parse.unquote(parsed.password),
        "database": parsed.path.removeprefix("/") or "postgres",
    }


def extract_branch_credentials(value: object, branch_ref: str) -> BranchCredentials:
    non_pooling = _first_string(
        value, {"POSTGRES_URL_NON_POOLING", "postgres_url_non_pooling"}
    ) or _first_labeled_string(
        value, {"POSTGRES_URL_NON_POOLING", "postgres_url_non_pooling"}
    ) or _first_string(
        value, {"database_url", "db_url", "postgres_url", "connection_string"}
    ) or _first_labeled_string(
        value, {"database_url", "db_url", "postgres_url", "connection_string"}
    )
    parsed_url: dict[str, object] = {}
    if non_pooling:
        parsed_url = _parse_postgres_url(non_pooling)

    host = _first_string(value, {"db_host", "host", "hostname"}) or str(
        parsed_url.get("host", "")
    )
    port = _first_int(value, {"db_port", "port"}) or int(
        parsed_url.get("port", 5432)
    )
    user = _first_string(value, {"db_user", "user", "username"}) or str(
        parsed_url.get("user", "")
    )
    password = _first_string(
        value, {"db_pass", "db_password", "password"}
    ) or str(
        parsed_url.get("password", "")
    )
    database = str(parsed_url.get("database", "postgres"))
    jwt_secret = _first_string(
        value, {"jwt_secret", "SUPABASE_JWT_SECRET"}
    ) or _first_labeled_string(
        value,
        {
            "jwt_secret",
            "SUPABASE_JWT_SECRET",
            "JWT Secret",
            "legacy_jwt_secret",
            "Legacy JWT Secret",
        },
    ) or ""
    publishable_key = _first_string(
        value,
        {
            "SUPABASE_PUBLISHABLE_KEY",
            "publishable_key",
            "SUPABASE_ANON_KEY",
            "anon_key",
        },
    ) or _first_labeled_string(
        value,
        {
            "SUPABASE_PUBLISHABLE_KEY",
            "publishable_key",
            "publishable",
            "Publishable Key",
            "SUPABASE_ANON_KEY",
            "anon_key",
            "anon",
            "Anon Key",
            "legacy_anon",
        },
    ) or ""
    project_url = (
        _first_string(value, {"SUPABASE_URL", "project_url"})
        or _first_labeled_string(value, {"SUPABASE_URL", "project_url"})
        or f"https://{branch_ref}.supabase.co"
    )

    expected_host = f"db.{branch_ref}.supabase.co"
    expected_url = f"https://{branch_ref}.supabase.co"
    if host != expected_host or port != 5432:
        raise ProofError("branch_direct_database_fence_mismatch")
    if user != "postgres" or database != "postgres":
        raise ProofError("branch_database_principal_invalid")
    if project_url.rstrip("/") != expected_url:
        raise ProofError("branch_project_url_fence_mismatch")
    if not password or len(jwt_secret.encode()) < 32:
        raise ProofError("branch_credentials_incomplete")
    if not publishable_key or publishable_key.startswith("sb_secret_"):
        raise ProofError("branch_publishable_key_invalid")
    return BranchCredentials(
        host=host,
        port=port,
        user=user,
        database=database,
        password=password,
        project_url=expected_url,
        publishable_key=publishable_key,
        jwt_secret=jwt_secret,
    )


class HarmonyPreviewProof:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        runner: ProcessRunner | None = None,
        opener: Callable[..., object] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.args = args
        self.runner = runner or ProcessRunner()
        self._http_opener = (
            request.build_opener(RejectRedirectHandler())
            if opener is None
            else None
        )
        self.opener = self._http_opener.open if self._http_opener else opener
        self.sleeper = sleeper
        self.clock = clock
        self.repo_root = Path(args.repo_root).resolve()
        self.management_token = ""
        self.management_home = ""
        self.management_home_cleanup_confirmed = True
        self.branch: BranchIdentity | None = None
        self.branch_name = ""
        # Sticky commit-ambiguity fence: this flips immediately before the
        # first (and only) create mutation is handed to the CLI.  Cleanup may
        # safely cancel the token-bearing watchdog when an interrupt occurs
        # before that point; once invoked, only the exact-name reconciler may
        # resolve a late-visible child.
        self.branch_create_mutation_invoked = False
        self.branch_shape: dict[str, object] | None = None
        self.watchdog: object | None = None
        # ``spawning`` is intentionally sticky when ownership cannot be
        # proven.  A receipt may only claim watchdog secret release from the
        # unambiguous ``never_started`` or handshake-complete ``released``
        # states.
        self.watchdog_spawn_state = "never_started"
        self.watchdog_control_dir = ""
        self.watchdog_control_socket: socket.socket | None = None
        self.watchdog_nonce = ""
        self.watchdog_armed_at: str | None = None
        self.watchdog_deadline: str | None = None
        self.credentials: BranchCredentials | None = None
        self.proof_snapshot_payloads: dict[str, bytes] = {}
        self.completed_steps: list[str] = []
        self.cleanup_receipt: dict[str, object] = {
            "delete_requested": False,
            "absence_confirmations": 0,
            "watchdog_armed": False,
            "watchdog_cancelled": False,
            "branch_create_mutation_invoked": False,
        }

    def _complete_step(self, step: str) -> None:
        if step in self.completed_steps:
            raise ProofError("preview_proof_step_completed_twice")
        self.completed_steps.append(step)

    def _supabase_json(
        self,
        args: Sequence[str],
        *,
        code: str,
        timeout: float = 120,
        before_spawn: Callable[[], None] | None = None,
    ) -> object:
        if not self.management_token or not self.management_home:
            raise ProofError("supabase_management_token_unavailable")
        environment = _safe_process_environment()
        environment["SUPABASE_ACCESS_TOKEN"] = self.management_token
        environment["HOME"] = self.management_home
        environment["XDG_CONFIG_HOME"] = str(
            Path(self.management_home) / ".config"
        )
        try:
            return self.runner.run_json(
                [self.args.supabase, *args],
                env=environment,
                cwd=self.management_home,
                timeout=timeout,
                code=code,
                before_spawn=before_spawn,
            )
        finally:
            environment.pop("SUPABASE_ACCESS_TOKEN", None)
            environment.clear()

    def _take_management_token(self) -> None:
        token = os.environ.pop(MANAGEMENT_TOKEN_SOURCE_ENV, "")
        # Never allow an ambient default token or stale child credentials to
        # influence any subprocess, including the initial git verification.
        _scrub_parent_secret_environment()
        if (
            not 16 <= len(token) <= 4096
            or token != token.strip()
            or any(character.isspace() for character in token)
        ):
            token = ""
            raise ProofError("supabase_management_token_missing")
        self.management_token = token
        try:
            self.management_home = tempfile.mkdtemp(
                prefix="harmony-supabase-home-"
            )
            self.management_home_cleanup_confirmed = False
        except OSError:
            self.management_token = ""
            raise ProofError("supabase_management_home_create_failed") from None

    def _clear_management_home(self) -> None:
        home = self.management_home
        if not home:
            self.management_home_cleanup_confirmed = True
            return
        path = Path(home)
        expected_parent = Path(tempfile.gettempdir()).resolve()
        try:
            resolved_parent = path.resolve().parent
        except OSError:
            raise ProofError("supabase_management_home_cleanup_failed") from None
        if (
            resolved_parent != expected_parent
            or not path.name.startswith("harmony-supabase-home-")
        ):
            raise ProofError("supabase_management_home_fence_invalid")
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except OSError:
            raise ProofError("supabase_management_home_cleanup_failed") from None
        self.management_home = ""
        self.management_home_cleanup_confirmed = True

    def _clear_watchdog_control_dir(self) -> None:
        control_dir = self.watchdog_control_dir
        if not control_dir:
            return
        path = Path(control_dir)
        expected_parent = Path(tempfile.gettempdir()).resolve()
        try:
            resolved_parent = path.resolve().parent
        except OSError:
            raise ProofError("cleanup_watchdog_control_cleanup_failed") from None
        if (
            resolved_parent != expected_parent
            or not path.name.startswith("harmony-watchdog-control-")
        ):
            raise ProofError("cleanup_watchdog_control_fence_invalid")
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except OSError:
            raise ProofError("cleanup_watchdog_control_cleanup_failed") from None
        self.watchdog_control_dir = ""

    def _close_watchdog_control_socket(self) -> None:
        control = self.watchdog_control_socket
        self.watchdog_control_socket = None
        if control is None:
            return
        try:
            control.close()
        except OSError:
            pass

    def _bind_proof_snapshot_payloads(
        self,
        support_payloads: Mapping[str, bytes],
    ) -> None:
        bound: dict[str, bytes] = {}
        for relative in PROBE_PATHS:
            payload = support_payloads.get(str(relative))
            if not payload:
                bound.clear()
                raise ProofError("proof_executable_snapshot_missing")
            bound[str(relative)] = bytes(payload)
        self.proof_snapshot_payloads = bound

    def _clear_proof_snapshot_payloads(self) -> None:
        self.proof_snapshot_payloads.clear()

    def _list_branches(self) -> list[BranchIdentity]:
        value = self._supabase_json(
            [
                "branches",
                "list",
                "--project-ref",
                self.args.parent_project_ref,
                "--output-format",
                "json",
            ],
            code="supabase_branch_list",
            timeout=self.args.supabase_read_timeout_seconds,
        )
        branches = extract_branches(value)
        parents = [
            branch
            for branch in branches
            if branch.ref == self.args.parent_project_ref
        ]
        if len(parents) != 1:
            # Prevent an empty, unauthorized, or shape-drifted list response
            # from being misread as proof that the disposable child is absent.
            raise ProofError("supabase_branch_list_parent_fence_missing")
        return branches

    def _management_get_json(
        self,
        path: str,
        *,
        code: str,
        timeout: float,
    ) -> object:
        if not self.management_token:
            raise ProofError("supabase_management_token_unavailable")
        if not re.fullmatch(r"/projects/[a-z0-9]{20}/billing/addons", path):
            raise ProofError("supabase_management_path_invalid")
        url = MANAGEMENT_API_BASE_URL + path
        parsed_url = parse.urlsplit(url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != "api.supabase.com"
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.port is not None
            or parsed_url.query
            or parsed_url.fragment
            or not parsed_url.path.startswith("/v1/")
        ):
            raise ProofError("supabase_management_url_fence_invalid")
        authorization = f"Bearer {self.management_token}"
        req = request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": authorization,
                "User-Agent": "coineasy-harmony-preview-proof/1",
            },
        )
        authorization = ""
        raw = b""
        try:
            response = self.opener(req, timeout=timeout)
            with response:  # type: ignore[attr-defined]
                status = getattr(response, "status", None)
                raw = response.read(  # type: ignore[attr-defined]
                    MAX_MANAGEMENT_API_BYTES + 1
                )
            if status != 200:
                raise self._management_status_error(code, status)
            if len(raw) > MAX_MANAGEMENT_API_BYTES:
                raise ProofError(f"{code}_response_too_large")
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ProofError(f"{code}_invalid_json") from None
        except ProofError:
            raise
        except error.HTTPError as exc:
            try:
                raw = exc.read(MAX_MANAGEMENT_API_BYTES + 1)
            except (OSError, ValueError):
                pass
            finally:
                exc.close()
            raise self._management_status_error(code, exc.code) from None
        except (error.URLError, TimeoutError, OSError, ValueError):
            raise ManagementApiError(
                f"{code}_transport_failed",
                retryable=True,
            ) from None
        finally:
            raw = b""
            req.remove_header("Authorization")

    @staticmethod
    def _management_status_error(code: str, status: object) -> ManagementApiError:
        if status in {401, 403}:
            suffix = "authorization_failed"
            retryable = False
        elif status == 404:
            suffix = "not_found"
            retryable = True
        elif status == 429:
            suffix = "rate_limited"
            retryable = True
        elif isinstance(status, int) and 500 <= status <= 599:
            suffix = "server_error"
            retryable = True
        elif isinstance(status, int) and 300 <= status <= 399:
            suffix = "redirect_rejected"
            retryable = False
        else:
            suffix = "failed"
            retryable = False
        return ManagementApiError(f"{code}_{suffix}", retryable=retryable)

    def _preflight_management_permissions(self) -> None:
        value = self._management_get_json(
            f"/projects/{self.args.parent_project_ref}/billing/addons",
            code="supabase_billing_addons_preflight",
            timeout=self.args.supabase_read_timeout_seconds,
        )
        try:
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("selected_addons"), list)
            ):
                raise ProofError("supabase_billing_addons_preflight_invalid")
        finally:
            if isinstance(value, MutableMapping):
                value.clear()
            del value

    def _read_child_compute_size(
        self,
        branch_ref: str,
        *,
        deadline: float,
    ) -> str:
        if not PROJECT_REF_PATTERN.fullmatch(branch_ref):
            raise ProofError("preview_child_ref_invalid")
        last_retryable: ProofError | None = None
        while self.clock() < deadline:
            value: object | None = None
            try:
                value = self._management_get_json(
                    f"/projects/{branch_ref}/billing/addons",
                    code="supabase_billing_addons_get",
                    timeout=self.args.supabase_read_timeout_seconds,
                )
                return extract_compute_addon_size(value)
            except ManagementApiError as exc:
                if not exc.retryable:
                    raise
                last_retryable = exc
            except ProofError as exc:
                if exc.code != "preview_child_compute_size_unavailable":
                    raise
                last_retryable = exc
            finally:
                if isinstance(value, MutableMapping):
                    value.clear()
                value = None
            self.sleeper(self.args.poll_interval_seconds)
        if last_retryable is not None:
            raise last_retryable
        raise ProofError("preview_child_compute_size_unavailable")

    def _validate_branch_shape(
        self,
        branch: BranchIdentity,
        *,
        deadline: float,
    ) -> None:
        if branch.persistent is not False:
            raise ProofError("preview_child_persistent_readback_invalid")
        if branch.with_data is not False:
            raise ProofError("preview_child_with_data_readback_invalid")
        compute_size = self._read_child_compute_size(
            branch.ref,
            deadline=deadline,
        )
        if compute_size != "small":
            raise ProofError("preview_child_compute_size_not_small")
        self.branch_shape = {
            "size": compute_size,
            "persistent": branch.persistent,
            "with_data": branch.with_data,
        }

    def _branch_name(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"hc-proof-{self.args.release_sha[:12]}-{stamp}-{uuid.uuid4().hex[:12]}"

    def _find_created_branch(
        self,
        name: str,
        baseline: set[tuple[str, str]],
        *,
        deadline: float,
    ) -> BranchIdentity | None:
        while self.clock() < deadline:
            matches = [
                branch
                for branch in self._list_branches()
                if branch.name == name
                and (branch.branch_id, branch.ref) not in baseline
                and not branch.is_default
            ]
            if len(matches) > 1:
                raise ProofError("multiple_preview_children_created")
            if len(matches) == 1:
                return matches[0]
            self.sleeper(self.args.poll_interval_seconds)
        return None

    def _arm_watchdog(self, branch_name: str) -> None:
        if self.watchdog is not None:
            raise ProofError("cleanup_watchdog_already_armed")
        if not self.management_token or not self.management_home:
            raise ProofError("supabase_management_token_unavailable")
        if not re.fullmatch(r"hc-proof-[a-f0-9]{12}-[0-9]{14}-[a-f0-9]{12}", branch_name):
            raise ProofError("preview_branch_name_invalid")
        try:
            self.watchdog_control_dir = tempfile.mkdtemp(
                prefix="harmony-watchdog-control-"
            )
            os.chmod(self.watchdog_control_dir, 0o700)
            watchdog_home = Path(self.watchdog_control_dir) / "home"
            watchdog_home.mkdir(mode=0o700)
        except OSError:
            self.watchdog_control_dir = ""
            raise ProofError("cleanup_watchdog_control_create_failed") from None
        parent_control: socket.socket | None = None
        child_control: socket.socket | None = None
        try:
            parent_control, child_control = socket.socketpair()
            self.watchdog_nonce = uuid.uuid4().hex
        except OSError:
            if parent_control is not None:
                parent_control.close()
            if child_control is not None:
                child_control.close()
            self.watchdog_nonce = ""
            self._clear_watchdog_control_dir()
            raise ProofError("cleanup_watchdog_control_create_failed") from None
        deadline_epoch = time.time() + WATCHDOG_SECONDS
        code = f"""import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

deadline = {deadline_epoch!r}
control_dir = os.environ.pop("HARMONY_WATCHDOG_CONTROL_DIR", "")
control_fd = int(os.environ.pop("HARMONY_WATCHDOG_CONTROL_FD", "-1"))
control_nonce = os.environ.pop("HARMONY_WATCHDOG_NONCE", "")
control_socket = socket.socket(fileno=control_fd)
active_path = os.path.join(control_dir, {WATCHDOG_ACTIVE_PGID_FILENAME!r})
home = os.path.join(control_dir, "home")
cancel_requested = False
control_detached = False
control_protocol_invalid = False
watchdog_safe = True
active_cli_pgid = None
last_cli_pgid = None

class WatchdogFenceError(RuntimeError):
    pass

def atomic_write(path, value):
    temporary = path + ".tmp-" + str(os.getpid())
    try:
        with open(temporary, "w", encoding="ascii") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

def clear_active():
    try:
        os.unlink(active_path)
    except FileNotFoundError:
        pass

def top_level_interrupt(signum, _frame):
    raise SystemExit(128 + signum)

for interrupt_signal in (signal.SIGINT, signal.SIGTERM):
    signal.signal(interrupt_signal, top_level_interrupt)

def control_worker():
    global cancel_requested
    global control_detached
    global control_protocol_invalid
    payload = b""
    try:
        while b"\\n" not in payload:
            chunk = control_socket.recv({WATCHDOG_MESSAGE_MAX_BYTES!r})
            if not chunk:
                control_detached = True
                return
            payload += chunk
            if len(payload) > {WATCHDOG_MESSAGE_MAX_BYTES!r}:
                raise ValueError("control_message_too_large")
        line, remainder = payload.split(b"\\n", 1)
        if remainder:
            raise ValueError("control_message_trailing_bytes")
        message = json.loads(line)
        if (
            not isinstance(message, dict)
            or set(message) != {{"schema", "type", "nonce"}}
            or message.get("schema") != {WATCHDOG_PROTOCOL_SCHEMA!r}
            or message.get("type") != "cancel"
            or message.get("nonce") != control_nonce
        ):
            raise ValueError("control_message_invalid")
        cancel_requested = True
    except BaseException:
        control_protocol_invalid = True
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        control_protocol_invalid = True

threading.Thread(target=control_worker, daemon=True).start()

def block_interrupts():
    return signal.pthread_sigmask(
        signal.SIG_BLOCK,
        (signal.SIGINT, signal.SIGTERM),
    )

def restore_mask(previous):
    signal.pthread_sigmask(signal.SIG_SETMASK, previous)

def process_group_state(pgid):
    if not isinstance(pgid, int) or pgid <= 1 or pgid == os.getpgrp():
        return {PROCESS_GROUP_UNKNOWN!r}
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return {PROCESS_GROUP_ABSENT!r}
    except OSError:
        return {PROCESS_GROUP_UNKNOWN!r}
    try:
        completed = subprocess.run(
            [{PROCESS_STATE_PS!r}, "-axo", "pid=,pgid=,state="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={{"LC_ALL": "C", "PATH": "/usr/bin:/bin"}},
            close_fds=True,
            start_new_session=True,
            timeout={PROCESS_GROUP_STATE_TIMEOUT_SECONDS!r},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {PROCESS_GROUP_UNKNOWN!r}
    output = completed.stdout or b""
    if (
        completed.returncode != 0
        or len(output) > {PROCESS_GROUP_STATE_MAX_BYTES!r}
    ):
        output = b""
        return {PROCESS_GROUP_UNKNOWN!r}
    try:
        text = output.decode("ascii", "strict")
    except UnicodeDecodeError:
        output = b""
        return {PROCESS_GROUP_UNKNOWN!r}
    finally:
        output = b""
    matched = False
    dead_only = True
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        fields = raw_line.split()
        if len(fields) != 3:
            return {PROCESS_GROUP_UNKNOWN!r}
        try:
            row_pid = int(fields[0])
            row_pgid = int(fields[1])
        except ValueError:
            return {PROCESS_GROUP_UNKNOWN!r}
        state = fields[2]
        if row_pid <= 0 or row_pgid <= 0 or not state:
            return {PROCESS_GROUP_UNKNOWN!r}
        if row_pgid != pgid:
            continue
        matched = True
        if not state.startswith("Z"):
            dead_only = False
    text = ""
    if matched:
        return (
            {PROCESS_GROUP_DEAD_ONLY!r}
            if dead_only
            else {PROCESS_GROUP_LIVE!r}
        )
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return {PROCESS_GROUP_ABSENT!r}
    except OSError:
        return {PROCESS_GROUP_UNKNOWN!r}
    return {PROCESS_GROUP_UNKNOWN!r}

def fence_group(process):
    previous = None
    mask_failure = None
    group_failure = None
    restore_failure = None
    try:
        previous = block_interrupts()
    except Exception as exc:
        mask_failure = WatchdogFenceError("signal_mask_failed")
        mask_failure.__cause__ = exc
    try:
        pid = process.pid
        group_absent = False
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired as exc:
                raise WatchdogFenceError("direct_child_unconfirmed") from exc
            group_absent = True
        except OSError:
            # Continue to the mandatory KILL pass on ambiguous TERM errors.
            pass

        if not group_absent:
            try:
                process.wait(timeout={PROCESS_GROUP_TERM_GRACE_SECONDS!r})
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                pass
            process.wait(timeout={PROCESS_GROUP_KILL_WAIT_SECONDS!r})

            confirm_deadline = time.monotonic() + {PROCESS_GROUP_KILL_WAIT_SECONDS!r}
            while True:
                state = process_group_state(pid)
                if state in ({PROCESS_GROUP_ABSENT!r}, {PROCESS_GROUP_DEAD_ONLY!r}):
                    break
                if time.monotonic() >= confirm_deadline:
                    raise WatchdogFenceError("process_group_unconfirmed")
                time.sleep(0.05)
    except BaseException as exc:
        group_failure = exc
    finally:
        if previous is not None:
            try:
                restore_mask(previous)
            except BaseException as exc:
                restore_failure = exc
    if group_failure is not None:
        if isinstance(group_failure, (SystemExit, KeyboardInterrupt)):
            raise group_failure
        raise WatchdogFenceError("process_group_fence_failed") from group_failure
    if restore_failure is not None:
        if isinstance(restore_failure, (SystemExit, KeyboardInterrupt)):
            raise restore_failure
        raise WatchdogFenceError("signal_mask_restore_failed") from restore_failure
    if mask_failure is not None:
        raise mask_failure

def run_cli(command, *, timeout, capture):
    global active_cli_pgid
    global last_cli_pgid
    global watchdog_safe
    process = None
    active_registered = False
    group_fence_attempted = False
    previous_handlers = {{}}
    output = b""
    try:
        previous = None
        try:
            try:
                previous = block_interrupts()
            except Exception as exc:
                raise WatchdogFenceError("signal_mask_failed") from exc
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=(subprocess.PIPE if capture else subprocess.DEVNULL),
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
                cwd=home,
            )
            atomic_write(active_path, str(process.pid))
            active_registered = True
            active_cli_pgid = process.pid
            last_cli_pgid = process.pid
            for interrupt_signal in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[interrupt_signal] = signal.getsignal(
                    interrupt_signal
                )

            def guarded_interrupt(signum, frame):
                global watchdog_safe
                nonlocal group_fence_attempted
                try:
                    if not group_fence_attempted:
                        group_fence_attempted = True
                        fence_group(process)
                except BaseException:
                    watchdog_safe = False
                    raise
                prior = previous_handlers.get(signal.Signals(signum))
                if callable(prior):
                    prior(signum, frame)
                raise SystemExit(128 + signum)

            for interrupt_signal in (signal.SIGINT, signal.SIGTERM):
                signal.signal(interrupt_signal, guarded_interrupt)
        finally:
            # Assignment, parent-visible PGID publication, and signal guard
            # all complete before a deferred TERM can be delivered.
            if previous is not None:
                try:
                    restore_mask(previous)
                except Exception as exc:
                    raise WatchdogFenceError(
                        "signal_mask_restore_failed"
                    ) from exc
        try:
            output, _stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            exc.output = b""
            exc.stderr = b""
            return None
        return process.returncode, output or b""
    finally:
        if process is not None:
            pending_failure = sys.exc_info()[1]
            fence_failure = None
            try:
                # Fence every CLI session on timeout, nonzero, interruption,
                # and success before withdrawing its parent-visible PGID.
                if not group_fence_attempted:
                    group_fence_attempted = True
                    fence_group(process)
                process.communicate(
                    timeout={PROCESS_GROUP_KILL_WAIT_SECONDS!r}
                )
                if active_registered:
                    clear_active()
                    active_cli_pgid = None
            except BaseException as exc:
                watchdog_safe = False
                fence_failure = exc
            restore_failure = None
            if previous_handlers:
                restore_previous = None
                restore_mask_failure = None
                handler_restore_failure = None
                unmask_failure = None
                try:
                    try:
                        restore_previous = block_interrupts()
                    except Exception as exc:
                        restore_mask_failure = exc
                    for interrupt_signal, prior in previous_handlers.items():
                        signal.signal(interrupt_signal, prior)
                except Exception as exc:
                    handler_restore_failure = exc
                finally:
                    if restore_previous is not None:
                        try:
                            restore_mask(restore_previous)
                        except Exception as exc:
                            unmask_failure = exc
                if handler_restore_failure is not None:
                    restore_failure = WatchdogFenceError(
                        "signal_handler_restore_failed"
                    )
                elif unmask_failure is not None:
                    restore_failure = WatchdogFenceError(
                        "signal_mask_restore_failed"
                    )
                elif restore_mask_failure is not None:
                    restore_failure = WatchdogFenceError(
                        "signal_mask_failed"
                    )
            output = b""
            if isinstance(pending_failure, WatchdogFenceError):
                watchdog_safe = False
                raise pending_failure
            if fence_failure is not None:
                if isinstance(fence_failure, (SystemExit, KeyboardInterrupt)):
                    raise fence_failure
                raise WatchdogFenceError(
                    "process_group_fence_failed"
                ) from fence_failure
            if restore_failure is not None:
                watchdog_safe = False
                raise restore_failure

def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

try:
    # The parent deliberately blocks SIGINT/SIGTERM across Popen ownership,
    # so the child inherits that mask.  Install handlers and start the
    # control thread first, then unblock in the watchdog main thread before
    # any deadline sleep or credential-bearing CLI.  A queued cancel TERM is
    # thereby delivered through top_level_interrupt and reaches the cleanup
    # handshake immediately.
    try:
        signal.pthread_sigmask(
            signal.SIG_UNBLOCK,
            (signal.SIGINT, signal.SIGTERM),
        )
    except Exception as exc:
        raise WatchdogFenceError("initial_signal_unmask_failed") from exc
    time.sleep(max(0.0, deadline - time.time()))
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")

    attempted_ids = set()
    target_observed = False
    absence_confirmations = 0
    hard_stop = deadline + 9 * 60
    while time.time() < hard_stop:
        try:
            listed = run_cli(
                [
                    {self.args.supabase!r},
                    "branches",
                    "list",
                    "--project-ref",
                    {self.args.parent_project_ref!r},
                    "--output-format",
                    "json",
                ],
                timeout={self.args.supabase_read_timeout_seconds!r},
                capture=True,
            )
            value = (
                json.loads(listed[1])
                if listed is not None and listed[0] == 0
                else None
            )
            rows = list(walk(value)) if value is not None else []
            parent_seen = any(
                row.get("project_ref", row.get("ref"))
                    == {self.args.parent_project_ref!r}
                for row in rows
            )
            if parent_seen:
                matches = [
                    str(row["id"])
                    for row in rows
                    if row.get("name") == {branch_name!r}
                    and row.get("project_ref", row.get("ref"))
                        != {self.args.parent_project_ref!r}
                    and row.get("is_default") is not True
                    and isinstance(row.get("id"), str)
                    and row.get("id")
                ]
                if matches:
                    target_observed = True
                    absence_confirmations = 0
                    for branch_id in dict.fromkeys(matches):
                        if branch_id in attempted_ids:
                            continue
                        attempted_ids.add(branch_id)
                        run_cli(
                            [
                                {self.args.supabase!r},
                                "branches",
                                "delete",
                                branch_id,
                                "--project-ref",
                                {self.args.parent_project_ref!r},
                                "--yes",
                                "--output-format",
                                "json",
                            ],
                            timeout={self.args.supabase_mutation_timeout_seconds!r},
                            capture=False,
                        )
                elif target_observed:
                    absence_confirmations += 1
                    if absence_confirmations >= 3:
                        break
            listed = None
        except WatchdogFenceError:
            # Never launch another credential-bearing CLI after an unconfirmed
            # group fence.  The retained watchdog fails closed instead.
            watchdog_safe = False
            break
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception:
            if cancel_requested or not watchdog_safe:
                break
        time.sleep({self.args.poll_interval_seconds!r})
except SystemExit:
    if not cancel_requested or control_protocol_invalid:
        watchdog_safe = False
except KeyboardInterrupt:
    watchdog_safe = False
except BaseException:
    watchdog_safe = False
finally:
    # No later interrupt may preempt token release, descendant ownership
    # acknowledgement, or removal of the watchdog's independent HOME.
    try:
        signal.pthread_sigmask(
            signal.SIG_BLOCK,
            (signal.SIGINT, signal.SIGTERM),
        )
    except Exception:
        watchdog_safe = False
    for interrupt_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(interrupt_signal, signal.SIG_IGN)
        except Exception:
            watchdog_safe = False
    os.environ.pop("SUPABASE_ACCESS_TOKEN", None)
    os.environ.pop("HOME", None)
    os.environ.pop("XDG_CONFIG_HOME", None)
    active_absent = not os.path.exists(active_path)
    if watchdog_safe and active_absent and not control_protocol_invalid:
        try:
            shutil.rmtree(control_dir)
        except OSError:
            watchdog_safe = False
    root_absent = not os.path.lexists(control_dir)

handshake_ok = False
clean = (
    watchdog_safe
    and active_cli_pgid is None
    and root_absent
    and not control_protocol_invalid
)
if cancel_requested and not control_detached:
    ready = {{
        "schema": {WATCHDOG_PROTOCOL_SCHEMA!r},
        "type": "clean_ready",
        "nonce": control_nonce,
        "status": "cancel_clean" if clean else "cancel_unsafe",
        "active_cli_pgid": active_cli_pgid,
        "last_cli_pgid": last_cli_pgid,
        "root_absent": root_absent,
    }}
    try:
        encoded = json.dumps(
            ready, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\\n"
        control_socket.sendall(encoded)
        if clean:
            control_socket.settimeout(5.0)
            payload = b""
            while b"\\n" not in payload:
                chunk = control_socket.recv({WATCHDOG_MESSAGE_MAX_BYTES!r})
                if not chunk:
                    raise ValueError("ack_eof")
                payload += chunk
                if len(payload) > {WATCHDOG_MESSAGE_MAX_BYTES!r}:
                    raise ValueError("ack_too_large")
            line, remainder = payload.split(b"\\n", 1)
            message = json.loads(line)
            handshake_ok = (
                not remainder
                and isinstance(message, dict)
                and set(message) == {{"schema", "type", "nonce"}}
                and message.get("schema") == {WATCHDOG_PROTOCOL_SCHEMA!r}
                and message.get("type") == "ack_accepted"
                and message.get("nonce") == control_nonce
            )
    except BaseException:
        handshake_ok = False
try:
    control_socket.close()
except OSError:
    pass
raise SystemExit(
    0 if clean and (not cancel_requested or handshake_ok) else 3
)
"""
        try:
            compile(code, "<harmony-preview-cleanup-watchdog>", "exec")
        except SyntaxError:
            parent_control.close()
            child_control.close()
            self.watchdog_nonce = ""
            self._clear_watchdog_control_dir()
            raise ProofError("cleanup_watchdog_program_invalid") from None
        environment = _safe_process_environment()
        # The scoped token exists only in management subprocess environments;
        # it is never inherited by git, psql, or either proof subprocess.
        environment["SUPABASE_ACCESS_TOKEN"] = self.management_token
        environment["HOME"] = str(watchdog_home)
        environment["XDG_CONFIG_HOME"] = str(watchdog_home / ".config")
        environment["HARMONY_WATCHDOG_CONTROL_DIR"] = (
            self.watchdog_control_dir
        )
        environment["HARMONY_WATCHDOG_CONTROL_FD"] = str(
            child_control.fileno()
        )
        environment["HARMONY_WATCHDOG_NONCE"] = self.watchdog_nonce
        previous_mask: set[signal.Signals] | None = None
        spawn_failure: BaseException | None = None
        restore_failure: BaseException | None = None
        popen_invoked = False
        self.watchdog_spawn_state = "spawning"
        try:
            # Defer SIGINT/SIGTERM from immediately before Popen until both
            # the child object and its control socket are parent-owned.  This
            # closes the post-spawn/pre-assignment orphan window for the
            # token-bearing watchdog.
            previous_mask = ProcessRunner._block_interrupt_signals(
                code="cleanup_watchdog_spawn"
            )
            popen_invoked = True
            self.watchdog = self.runner.popen(
                [sys.executable, "-I", "-c", code],
                env=environment,
                pass_fds=(child_control.fileno(),),
            )
            self.watchdog_control_socket = parent_control
            parent_control = None
            self.watchdog_spawn_state = "tracked"
        except BaseException as exc:
            spawn_failure = exc
        finally:
            if previous_mask is not None:
                # A pending interrupt is delivered only after the watchdog is
                # tracked.  The outer proof cleanup can therefore cancel and
                # reap it before making any secret-clean assertion.
                try:
                    ProcessRunner._restore_signal_mask(
                        previous_mask,
                        code="cleanup_watchdog_spawn",
                    )
                except BaseException as exc:
                    restore_failure = exc
            if parent_control is not None:
                parent_control.close()
            child_control.close()
            environment.pop("SUPABASE_ACCESS_TOKEN", None)
            environment.pop("HARMONY_WATCHDOG_CONTROL_DIR", None)
            environment.pop("HARMONY_WATCHDOG_CONTROL_FD", None)
            environment.pop("HARMONY_WATCHDOG_NONCE", None)
            environment.clear()
            if not popen_invoked:
                self.watchdog_nonce = ""
                try:
                    self._clear_watchdog_control_dir()
                except BaseException as exc:
                    if spawn_failure is None and restore_failure is None:
                        restore_failure = exc
        if spawn_failure is not None:
            raise spawn_failure
        if restore_failure is not None:
            raise restore_failure
        armed = datetime.now(UTC).replace(microsecond=0)
        self.watchdog_armed_at = armed.isoformat().replace("+00:00", "Z")
        self.watchdog_deadline = datetime.fromtimestamp(
            deadline_epoch, UTC
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.cleanup_receipt.update(
            {
                "watchdog_armed": True,
                "watchdog_absolute_deadline": True,
                "watchdog_armed_at": self.watchdog_armed_at,
                "watchdog_deadline": self.watchdog_deadline,
            }
        )

    def _mark_branch_create_mutation_invoked(self) -> None:
        self.branch_create_mutation_invoked = True
        self.cleanup_receipt["branch_create_mutation_invoked"] = True

    def _create_branch(self) -> None:
        baseline_rows = self._list_branches()
        baseline = {(branch.branch_id, branch.ref) for branch in baseline_rows}
        name = self._branch_name()
        self.branch_name = name
        self.cleanup_receipt["branch_name"] = name
        # Arm the absolute-deadline, exact-name reconciler before the mutation.
        # It can find and delete the child even if the create response is lost
        # before a branch id/ref reaches this process.
        self._arm_watchdog(name)
        create_error: CommandError | None = None
        branch: BranchIdentity | None = None
        create_value: object | None = None
        try:
            try:
                # Deliberate omissions: --persistent and --with-data.
                create_value = self._supabase_json(
                    [
                        "branches",
                        "create",
                        name,
                        "--project-ref",
                        self.args.parent_project_ref,
                        "--size",
                        "small",
                        "--yes",
                        "--output-format",
                        "json",
                    ],
                    code="supabase_branch_create",
                    timeout=self.args.supabase_mutation_timeout_seconds,
                    before_spawn=self._mark_branch_create_mutation_invoked,
                )
                create_candidates = [
                    item
                    for item in extract_branches(create_value)
                    if item.name == name
                    and (item.branch_id, item.ref) not in baseline
                    and not item.is_default
                ]
                if len(create_candidates) > 1:
                    raise ProofError("multiple_preview_children_created")
                if len(create_candidates) == 1:
                    branch = create_candidates[0]
                    if branch.ref == self.args.parent_project_ref:
                        raise ProofError("preview_child_equals_production")
                    self.branch = branch
            finally:
                if isinstance(create_value, MutableMapping):
                    create_value.clear()
                create_value = None
                gc.collect()
        except CommandError as exc:
            create_error = exc
            self.cleanup_receipt["create_response_ambiguous"] = True
            self.cleanup_receipt["create_failure_code"] = exc.code

        if branch is None:
            branch = self._find_created_branch(
                name,
                baseline,
                deadline=self.clock() + self.args.branch_ready_timeout_seconds,
            )
        if branch is None:
            if create_error is not None:
                raise ProofError(create_error.code)
            raise ProofError("branch_create_succeeded_child_not_observed")
        if branch.ref == self.args.parent_project_ref:
            raise ProofError("preview_child_equals_production")
        if self.branch is None:
            self.branch = branch
        if create_error is not None:
            # The child is cleanup-only: never continue after ambiguous create.
            raise ProofError("branch_create_commit_state_unknown")

        deadline = self.clock() + self.args.branch_ready_timeout_seconds
        while self.clock() < deadline:
            rows = [
                item
                for item in self._list_branches()
                if item.name == name
                and (item.branch_id, item.ref) not in baseline
                and not item.is_default
            ]
            if len(rows) > 1:
                raise ProofError("multiple_preview_children_created")
            if len(rows) != 1:
                raise ProofError("preview_child_identity_changed")
            if (
                rows[0].branch_id != branch.branch_id
                or rows[0].ref != branch.ref
            ):
                raise ProofError("preview_child_identity_changed")
            self.branch = rows[0]
            readiness = preview_branch_readiness(rows[0])
            if readiness == "failed":
                raise ProofError("preview_child_failed_readiness")
            if readiness == "invalid":
                raise ProofError("preview_child_lifecycle_readback_invalid")
            if readiness == "ready":
                self._validate_branch_shape(rows[0], deadline=deadline)
                return
            self.sleeper(self.args.poll_interval_seconds)
        raise ProofError("preview_child_readiness_timeout")

    def _load_credentials(self) -> None:
        assert self.branch is not None
        value = self._supabase_json(
            [
                "branches",
                "get",
                self.branch.branch_id,
                "--project-ref",
                self.args.parent_project_ref,
                "--output-format",
                "json",
            ],
            code="supabase_branch_get",
            timeout=self.args.supabase_read_timeout_seconds,
        )
        try:
            self.credentials = extract_branch_credentials(value, self.branch.ref)
        finally:
            if isinstance(value, MutableMapping):
                value.clear()
            del value
            gc.collect()
        _scrub_parent_secret_environment()

    def _db_environment(self) -> dict[str, str]:
        assert self.credentials is not None
        environment = _safe_process_environment()
        environment.update(
            {
                "PGPASSWORD": self.credentials.password,
                "PGSSLMODE": "verify-full",
                "PGSSLROOTCERT": "system",
                "PGAPPNAME": "coineasy-harmony-preview-proof",
            }
        )
        return environment

    def _psql_base(self) -> list[str]:
        assert self.credentials is not None
        return [
            self.args.psql,
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            self.credentials.host,
            "-p",
            str(self.credentials.port),
            "-U",
            self.credentials.user,
            "-d",
            self.credentials.database,
        ]

    def _apply_migrations_and_security(
        self,
        migration_payloads: Mapping[str, bytes],
        security_payloads: Mapping[str, bytes],
    ) -> None:
        environment = self._db_environment()
        try:
            for filename in MIGRATIONS:
                self.runner.run_quiet(
                    [
                        *self._psql_base(),
                        "-f",
                        "-",
                    ],
                    env=environment,
                    input_bytes=migration_payloads[filename],
                    timeout=self.args.migration_timeout_seconds,
                    code="preview_migration_apply",
                )
            for filename in SECURITY_SUITES:
                self.runner.run_quiet(
                    [
                        *self._psql_base(),
                        "-f",
                        "-",
                    ],
                    env=environment,
                    input_bytes=security_payloads[filename],
                    timeout=self.args.migration_timeout_seconds,
                    code="preview_security_suite",
                )
        finally:
            environment.clear()

    def _assert_exact_checkout_unchanged(
        self,
        expected_manifest: Mapping[str, str],
        expected_support_manifest: Mapping[str, str],
    ) -> None:
        manifest, support_manifest = verify_exact_checkout(
            self.runner, self.repo_root, self.args.release_sha
        )
        if (
            manifest != dict(expected_manifest)
            or support_manifest != dict(expected_support_manifest)
        ):
            raise ProofError("exact_checkout_changed_during_preview")

    def _run_direct_probe(self, config_sha256: str) -> object:
        assert self.branch is not None and self.credentials is not None
        probe_payload = self.proof_snapshot_payloads.get(str(PROBE_PATHS[0]))
        if not probe_payload:
            raise ProofError("direct_probe_snapshot_unavailable")
        environment = self._db_environment()
        try:
            return self.runner.run_json(
                [
                    sys.executable,
                    "-I",
                    "-",
                    "--host",
                    self.credentials.host,
                    "--port",
                    str(self.credentials.port),
                    "--user",
                    self.credentials.user,
                    "--database",
                    self.credentials.database,
                    "--psql",
                    self.args.psql,
                    "--confirm-disposable-preview",
                    "--expected-branch-ref",
                    self.branch.ref,
                    "--parent-project-ref",
                    self.args.parent_project_ref,
                    "--release-sha",
                    self.args.release_sha,
                    "--config-sha256",
                    config_sha256,
                    "--fence-ttl-minutes",
                    str(self.args.fence_ttl_minutes),
                ],
                env=environment,
                input_bytes=probe_payload,
                timeout=self.args.probe_timeout_seconds,
                code="direct_database_probe",
            )
        finally:
            environment.clear()

    def _schema_cache_ready(self) -> None:
        assert self.branch is not None and self.credentials is not None
        deadline = self.clock() + self.args.schema_ready_timeout_seconds
        while self.clock() < deadline:
            token = _mint_readiness_jwt(self.credentials.jwt_secret, self.branch.ref)
            req = request.Request(
                self.credentials.project_url + "/rest/v1/",
                method="GET",
                headers={
                    "Accept": "application/openapi+json",
                    "Authorization": f"Bearer {token}",
                    "apikey": self.credentials.publishable_key,
                    "User-Agent": "coineasy-harmony-preview-readiness/1",
                },
            )
            token = ""
            try:
                response = self.opener(req, timeout=10)
                with response:  # type: ignore[attr-defined]
                    raw = response.read(MAX_OPENAPI_BYTES + 1)  # type: ignore[attr-defined]
                if len(raw) > MAX_OPENAPI_BYTES:
                    raise ProofError("postgrest_openapi_response_too_large")
                value = json.loads(raw)
                paths = value.get("paths", {}) if isinstance(value, dict) else {}
                if isinstance(paths, dict) and POSTGREST_RPC_PATH in paths:
                    raw = b""
                    return
                raw = b""
            except ProofError:
                raise
            except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                pass
            self.sleeper(self.args.poll_interval_seconds)
        raise ProofError("postgrest_schema_cache_not_ready")

    def _run_postgrest_probe(self, config_sha256: str) -> object:
        assert self.branch is not None and self.credentials is not None
        probe_payload = self.proof_snapshot_payloads.get(str(PROBE_PATHS[1]))
        concurrency_payload = self.proof_snapshot_payloads.get(str(PROBE_PATHS[0]))
        if not probe_payload or not concurrency_payload:
            raise ProofError("postgrest_probe_snapshot_unavailable")
        bundled_payload = build_postgrest_probe_bundle(
            concurrency_payload, probe_payload
        )
        environment = self._db_environment()
        environment.update(
            {
                "HARMONY_PREVIEW_SUPABASE_PUBLISHABLE_KEY": (
                    self.credentials.publishable_key
                ),
                "HARMONY_PREVIEW_SUPABASE_LEGACY_JWT_SECRET": (
                    self.credentials.jwt_secret
                ),
            }
        )
        try:
            # Exactly one invocation.  The child probe itself treats transport
            # ambiguity as terminal and never retries a PostgREST write.
            return self.runner.run_json(
                [
                    sys.executable,
                    "-I",
                    "-",
                    "--project-url",
                    self.credentials.project_url,
                    "--host",
                    self.credentials.host,
                    "--port",
                    str(self.credentials.port),
                    "--user",
                    self.credentials.user,
                    "--database",
                    self.credentials.database,
                    "--psql",
                    self.args.psql,
                    "--confirm-disposable-preview",
                    "--expected-branch-ref",
                    self.branch.ref,
                    "--parent-project-ref",
                    self.args.parent_project_ref,
                    "--release-sha",
                    self.args.release_sha,
                    "--config-sha256",
                    config_sha256,
                    "--fence-ttl-minutes",
                    str(self.args.fence_ttl_minutes),
                ],
                env=environment,
                input_bytes=bundled_payload,
                timeout=self.args.probe_timeout_seconds,
                code="signed_postgrest_probe",
            )
        finally:
            bundled_payload = b""
            environment.clear()

    def _cancel_watchdog(self) -> None:
        if self.watchdog is None:
            return
        control = self.watchdog_control_socket
        root = self.watchdog_control_dir
        nonce = self.watchdog_nonce
        if control is None or not root or not nonce:
            self.cleanup_receipt["watchdog_cancelled"] = False
            raise ProofError("cleanup_watchdog_cancel_failed")
        watchdog = self.watchdog
        protocol_complete = False
        try:
            control.settimeout(WATCHDOG_CANCEL_GRACE_SECONDS)
            cancel = json.dumps(
                {
                    "schema": WATCHDOG_PROTOCOL_SCHEMA,
                    "type": "cancel",
                    "nonce": nonce,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii") + b"\n"
            control.sendall(cancel)
            cancel = b""

            payload = b""
            while b"\n" not in payload:
                chunk = control.recv(WATCHDOG_MESSAGE_MAX_BYTES)
                if not chunk:
                    raise ProofError("cleanup_watchdog_ack_eof")
                payload += chunk
                if len(payload) > WATCHDOG_MESSAGE_MAX_BYTES:
                    raise ProofError("cleanup_watchdog_ack_too_large")
            line, remainder = payload.split(b"\n", 1)
            payload = b""
            if remainder:
                raise ProofError("cleanup_watchdog_ack_invalid")
            try:
                ready = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ProofError("cleanup_watchdog_ack_invalid") from None
            finally:
                line = b""
            expected_keys = {
                "schema",
                "type",
                "nonce",
                "status",
                "active_cli_pgid",
                "last_cli_pgid",
                "root_absent",
            }
            if (
                not isinstance(ready, dict)
                or set(ready) != expected_keys
                or ready.get("schema") != WATCHDOG_PROTOCOL_SCHEMA
                or ready.get("type") != "clean_ready"
                or ready.get("nonce") != nonce
                or ready.get("status") != "cancel_clean"
                or ready.get("active_cli_pgid") is not None
                or ready.get("root_absent") is not True
            ):
                raise ProofError("cleanup_watchdog_ack_invalid")
            last_cli_pgid = ready.get("last_cli_pgid")
            if last_cli_pgid is not None:
                if type(last_cli_pgid) is not int:
                    raise ProofError("cleanup_watchdog_ack_invalid")
                self.runner.confirm_external_process_group_quiescent(
                    last_cli_pgid,
                    code="cleanup_watchdog_child_fence",
                )
            ready.clear()
            if os.path.lexists(root):
                raise ProofError("cleanup_watchdog_root_still_present")

            accepted = json.dumps(
                {
                    "schema": WATCHDOG_PROTOCOL_SCHEMA,
                    "type": "ack_accepted",
                    "nonce": nonce,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii") + b"\n"
            control.sendall(accepted)
            accepted = b""
            try:
                watchdog.wait(timeout=PROCESS_GROUP_KILL_WAIT_SECONDS)  # type: ignore[attr-defined]
            except subprocess.TimeoutExpired:
                raise ProofError("cleanup_watchdog_exit_timeout") from None
            if getattr(watchdog, "returncode", None) != 0:
                raise ProofError("cleanup_watchdog_exit_failed")
            watchdog_pid = getattr(watchdog, "pid", None)
            if type(watchdog_pid) is not int:
                raise ProofError("cleanup_watchdog_process_invalid")
            self.runner.confirm_external_process_group_absent(
                watchdog_pid,
                code="cleanup_watchdog_parent_fence",
            )
            protocol_complete = True
            self.cleanup_receipt["watchdog_cancelled"] = True
            self.watchdog_spawn_state = "released"
        except Exception:
            self.cleanup_receipt["watchdog_cancelled"] = False
            try:
                self.runner.terminate_process_group(
                    watchdog,  # type: ignore[arg-type]
                    code="cleanup_watchdog_cancel",
                    term_grace_seconds=WATCHDOG_CANCEL_GRACE_SECONDS,
                )
            except Exception:
                pass
            raise ProofError("cleanup_watchdog_cancel_failed") from None
        finally:
            self._close_watchdog_control_socket()
            if protocol_complete:
                self.watchdog = None
                self.watchdog_control_dir = ""
                self.watchdog_nonce = ""

    def _detach_watchdog(self) -> None:
        """Hand the late-visibility reconciler fully to its child process."""

        self._close_watchdog_control_socket()
        self.watchdog_nonce = ""
        self.watchdog_spawn_state = "detached"
        self.cleanup_receipt["watchdog_detached"] = True

    def _confirm_absent(self) -> int:
        if not self.branch_name and self.branch is None:
            return 0
        confirmations = 0
        deadline = self.clock() + self.args.cleanup_timeout_seconds
        while self.clock() < deadline:
            present = any(
                not row.is_default
                and row.ref != self.args.parent_project_ref
                and (
                    (self.branch is not None and (
                        row.branch_id == self.branch.branch_id
                        or row.ref == self.branch.ref
                    ))
                    or (self.branch_name and row.name == self.branch_name)
                )
                for row in self._list_branches()
            )
            if present:
                confirmations = 0
            else:
                confirmations += 1
                if confirmations == 3:
                    return confirmations
            self.sleeper(self.args.poll_interval_seconds)
        raise ProofError("preview_branch_absence_not_confirmed")

    def _cleanup(self) -> None:
        if self.credentials is not None:
            self.credentials.scrub()
            self.credentials = None
            gc.collect()
        _scrub_parent_secret_environment()
        if self.branch is None and not self.branch_name:
            return

        targets: list[BranchIdentity]
        if self.branch is not None:
            targets = [self.branch]
        else:
            targets = [
                row
                for row in self._list_branches()
                if row.name == self.branch_name
                and not row.is_default
                and row.ref != self.args.parent_project_ref
            ]
            if len(targets) > 1:
                raise ProofError("multiple_preview_children_created")
            if len(targets) == 1:
                self.branch = targets[0]
            else:
                if not self.branch_create_mutation_invoked:
                    # The exact-name mutation was provably never called.  The
                    # baseline was read before watchdog arming, and three
                    # current exact-name absences are therefore sufficient to
                    # cancel/reap the watchdog immediately instead of keeping
                    # its scoped token alive until the absolute deadline.
                    confirmations = self._confirm_absent()
                    self.cleanup_receipt.update(
                        {
                            "absence_confirmations": confirmations,
                            "delete_requested": False,
                            "delete_target_count": 0,
                            "create_not_invoked_absence_confirmed": True,
                        }
                    )
                    self._cancel_watchdog()
                    return
                # An accepted or ambiguous create can become visible after the
                # foreground read window.  Three early empty lists are not
                # deletion proof when no immutable child identity was ever
                # observed, so retain the exact-name absolute watchdog.
                self.cleanup_receipt.update(
                    {
                        "absence_confirmations": 0,
                        "delete_requested": False,
                        "delete_target_count": 0,
                        "late_visibility_watchdog_retained": True,
                    }
                )
                self._detach_watchdog()
                return

        unique_targets = {
            (target.branch_id, target.ref): target for target in targets
        }
        self.cleanup_receipt["delete_target_count"] = len(unique_targets)
        self.cleanup_receipt["delete_requested"] = bool(unique_targets)
        delete_failures: list[str] = []
        for target in unique_targets.values():
            try:
                value = self._supabase_json(
                    [
                        "branches",
                        "delete",
                        target.branch_id,
                        "--project-ref",
                        self.args.parent_project_ref,
                        "--yes",
                        "--output-format",
                        "json",
                    ],
                    code="supabase_branch_delete",
                    timeout=self.args.supabase_mutation_timeout_seconds,
                )
                if isinstance(value, MutableMapping):
                    value.clear()
                del value
            except CommandError as exc:
                # Delete may have committed even when its response was lost.
                # Never retry it; determine state through read-only lists.
                delete_failures.append(exc.code)
        if delete_failures:
            self.cleanup_receipt["delete_response_ambiguous"] = True
            self.cleanup_receipt["delete_failure_code"] = delete_failures[0]

        confirmations = self._confirm_absent()
        self.cleanup_receipt["absence_confirmations"] = confirmations
        self.cleanup_receipt["deleted_at"] = (
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        self._complete_step("branch_delete_absence_confirmed")
        self._cancel_watchdog()

    def _validate_probe_receipt(
        self,
        value: object,
        schema_version: str,
        config_sha256: str,
        *,
        require_branch_ref: bool,
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ProofError("probe_receipt_not_object")
        if value.get("ok") is not True or value.get("schema_version") != schema_version:
            raise ProofError("probe_receipt_contract_invalid")
        if (
            value.get("release_sha") != self.args.release_sha
            or value.get("config_sha256") != config_sha256
            or type(value.get("connections")) is not int
            or value.get("connections") != 64
            or type(value.get("new")) is not int
            or value.get("new") != 1
            or type(value.get("reused")) is not int
            or value.get("reused") != 63
            or value.get("side_effect_baseline_unchanged") is not True
        ):
            raise ProofError("probe_receipt_exact_fence_invalid")
        if require_branch_ref:
            if self.branch is None or value.get("branch_ref") != self.branch.ref:
                raise ProofError("probe_receipt_branch_fence_invalid")
        for field in (
            "automatic_publication",
            "external_calls",
            "provider_calls",
            "publication_calls",
        ):
            if value.get(field) is not False:
                raise ProofError("probe_forbidden_side_effect_flag")
        if schema_version == "harmony-preview-postgrest-proof@2":
            for field in ("buzz_calls", "approval_decisions"):
                if value.get(field) is not False:
                    raise ProofError("probe_forbidden_side_effect_flag")

        # Never forward arbitrary probe JSON to stdout.  Only an allow-listed,
        # non-secret proof summary is retained in the final receipt.
        safe_fields = (
            "ok",
            "schema_version",
            "branch_ref",
            "release_sha",
            "config_sha256",
            "connections",
            "new",
            "reused",
            "side_effect_baseline_unchanged",
            "automatic_publication",
            "external_calls",
            "provider_calls",
            "buzz_calls",
            "approval_decisions",
            "publication_calls",
        )
        return {field: value[field] for field in safe_fields if field in value}

    def run(self) -> tuple[dict[str, object], int]:
        manifest: dict[str, str] = {}
        support_manifest: dict[str, str] = {}
        migration_payloads: dict[str, bytes] = {}
        support_payloads: dict[str, bytes] = {}
        security_payloads: dict[str, bytes] = {}
        artifact_sha256: dict[str, str] = {}
        config_sha256 = ""
        direct_receipt: dict[str, object] | None = None
        postgrest_receipt: dict[str, object] | None = None
        failure_code: str | None = None
        cleanup_failure: str | None = None
        started = datetime.now(UTC).replace(microsecond=0)

        try:
            self._take_management_token()
            manifest, support_manifest = verify_exact_checkout(
                self.runner, self.repo_root, self.args.release_sha
            )
            (
                migration_payloads,
                support_payloads,
                artifact_sha256,
            ) = snapshot_exact_artifacts(
                self.runner,
                self.repo_root,
                self.args.release_sha,
                manifest,
                support_manifest,
            )
            config_sha256 = artifact_sha256[str(CONFIG_PATH)]
            security_payloads = {
                filename: support_payloads[
                    str(Path("supabase/tests") / filename)
                ]
                for filename in SECURITY_SUITES
            }
            self._bind_proof_snapshot_payloads(support_payloads)
            self._complete_step("exact_sha_snapshot_bound")
            self._preflight_management_permissions()
            self._complete_step("management_permission_preflight")
            self._create_branch()
            self._complete_step("branch_ready_and_shape_verified")
            self._load_credentials()
            self._assert_exact_checkout_unchanged(manifest, support_manifest)
            self._apply_migrations_and_security(
                migration_payloads, security_payloads
            )
            self._complete_step("migration_and_rls_security")
            self._assert_exact_checkout_unchanged(manifest, support_manifest)
            direct_receipt = self._validate_probe_receipt(
                self._run_direct_probe(config_sha256),
                "harmony-preview-concurrency-proof@3",
                config_sha256,
                require_branch_ref=False,
            )
            self._complete_step("direct_database_64_way")
            self._schema_cache_ready()
            self._complete_step("postgrest_schema_readiness_get")
            self._assert_exact_checkout_unchanged(manifest, support_manifest)
            postgrest_receipt = self._validate_probe_receipt(
                self._run_postgrest_probe(config_sha256),
                "harmony-preview-postgrest-proof@2",
                config_sha256,
                require_branch_ref=True,
            )
            self._complete_step("signed_postgrest_once")
        except ProofError as exc:
            failure_code = exc.code
        except Exception:
            failure_code = "unclassified_preview_proof_failure"
        finally:
            try:
                self._cleanup()
            except ProofError as exc:
                cleanup_failure = exc.code
            except Exception:
                cleanup_failure = "unclassified_preview_cleanup_failure"
            finally:
                migration_payloads.clear()
                support_payloads.clear()
                security_payloads.clear()
                self.management_token = ""
                _scrub_parent_secret_environment()
                try:
                    self._clear_management_home()
                except ProofError as exc:
                    if cleanup_failure is None:
                        cleanup_failure = exc.code
                self._clear_proof_snapshot_payloads()

        ok = (
            failure_code is None
            and cleanup_failure is None
            and direct_receipt is not None
            and postgrest_receipt is not None
            and self.branch_shape is not None
            and self.cleanup_receipt.get("absence_confirmations") == 3
            and self.cleanup_receipt.get("watchdog_cancelled") is True
        )
        finished = datetime.now(UTC).replace(microsecond=0)
        watchdog_secret_released = self.watchdog_spawn_state in {
            "never_started",
            "released",
        }
        secret_cleanup_confirmed = (
            self.management_home_cleanup_confirmed
            and watchdog_secret_released
        )
        self.cleanup_receipt.update(
            {
                "management_home_removed": self.management_home_cleanup_confirmed,
                "watchdog_secret_released": watchdog_secret_released,
            }
        )
        receipt: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "ok": ok,
            "release_sha": self.args.release_sha,
            "parent_project_ref": self.args.parent_project_ref,
            "config_sha256": config_sha256 or None,
            "migration_sha256": manifest,
            "migration_count": len(manifest),
            "proof_artifact_sha256": artifact_sha256,
            "security_suites": list(SECURITY_SUITES),
            "branch": None
            if self.branch is None
            else {
                "ref": self.branch.ref,
                "name": self.branch.name,
                "size": (
                    None if self.branch_shape is None
                    else self.branch_shape.get("size")
                ),
                "persistent": (
                    None if self.branch_shape is None
                    else self.branch_shape.get("persistent")
                ),
                "with_data": (
                    None if self.branch_shape is None
                    else self.branch_shape.get("with_data")
                ),
            },
            "parent_child_fence": (
                None
                if self.branch is None
                else self.branch.ref != self.args.parent_project_ref
            ),
            "planned_execution_order": [
                "exact_sha_snapshot_bound",
                "management_permission_preflight",
                "branch_ready_and_shape_verified",
                "migration_and_rls_security",
                "direct_database_64_way",
                "postgrest_schema_readiness_get",
                "signed_postgrest_once",
                "branch_delete_absence_confirmed",
            ],
            "completed_steps": list(self.completed_steps),
            "direct_database": direct_receipt,
            "signed_postgrest": postgrest_receipt,
            "cleanup": self.cleanup_receipt,
            "secrets_printed": False,
            "secret_cleanup_confirmed": secret_cleanup_confirmed,
            "secrets_persisted": (
                False if secret_cleanup_confirmed else None
            ),
            "same_child_repair_attempts": 0,
            "replacement_branch_attempts": 0,
            "feature_flags_enabled": False,
            "production_writes": False,
            "provider_calls": False,
            "grok_calls": False,
            "buzz_calls": False,
            "approval_decisions": False,
            "publication_calls": False,
            "automatic_publication": False,
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "finished_at": finished.isoformat().replace("+00:00", "Z"),
        }
        if failure_code is not None:
            receipt["failure_code"] = failure_code
        if cleanup_failure is not None:
            receipt["cleanup_failure_code"] = cleanup_failure
        return receipt, 0 if ok else 1


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create one Small no-data Supabase Preview, run the exact-SHA "
            "Harmony proof once, and delete it with three absence checks."
        ),
        epilog=(
            f"Requires a scoped token in {MANAGEMENT_TOKEN_SOURCE_ENV}; the "
            "token must grant branch lifecycle permissions plus "
            "infra_add_ons_read. The runner removes it from the parent "
            "environment and never prints it."
        ),
    )
    parser.add_argument("--repo-root", default=str(Path(__file__).parents[1]))
    parser.add_argument("--parent-project-ref", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--supabase", default=shutil.which("supabase") or "supabase")
    parser.add_argument(
        "--psql",
        default=(
            shutil.which("psql")
            or "/opt/homebrew/opt/postgresql@16/bin/psql"
        ),
    )
    parser.add_argument("--branch-ready-timeout-seconds", type=float, default=900)
    parser.add_argument("--supabase-read-timeout-seconds", type=float, default=30)
    parser.add_argument("--supabase-mutation-timeout-seconds", type=float, default=60)
    parser.add_argument("--schema-ready-timeout-seconds", type=float, default=180)
    parser.add_argument("--migration-timeout-seconds", type=float, default=180)
    parser.add_argument("--probe-timeout-seconds", type=float, default=900)
    parser.add_argument("--cleanup-timeout-seconds", type=float, default=180)
    parser.add_argument("--poll-interval-seconds", type=float, default=5)
    parser.add_argument("--fence-ttl-minutes", type=int, default=105)
    args = parser.parse_args(list(argv))
    if not PROJECT_REF_PATTERN.fullmatch(args.parent_project_ref):
        parser.error("--parent-project-ref must be an exact 20-character ref")
    if not SHA40_PATTERN.fullmatch(args.release_sha):
        parser.error("--release-sha must be an exact lowercase 40-hex SHA")
    if not 5 <= args.fence_ttl_minutes <= 105:
        parser.error("--fence-ttl-minutes must be between 5 and 105")
    for name in (
        "branch_ready_timeout_seconds",
        "supabase_read_timeout_seconds",
        "supabase_mutation_timeout_seconds",
        "schema_ready_timeout_seconds",
        "migration_timeout_seconds",
        "probe_timeout_seconds",
        "cleanup_timeout_seconds",
    ):
        if not 1 <= getattr(args, name) <= 1800:
            parser.error(f"--{name.replace('_', '-')} must be between 1 and 1800")
    if not 0 < args.poll_interval_seconds <= 30:
        parser.error("--poll-interval-seconds must be between 0 and 30")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    def interrupted(_signum: int, _frame: object) -> None:
        raise ProofError("preview_proof_interrupted")

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    receipt, exit_code = HarmonyPreviewProof(args).run()
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
