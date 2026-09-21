"""Explicitly composed, one-shot private banner worker.

This module is intentionally only a runtime boundary around the durable
provider journal in :mod:`banner_regeneration`. It does not discover
credentials, read environment variables, open a database, or construct a
network client. Callers must inject all three dependencies and must opt in
with the literal boolean ``True``. The disabled path returns before even
inspecting those objects.

The worker is one-shot by design: a scheduler may call ``run_once`` again
after a durable ``result_ready`` state, but a provider-started or unknown
state is never reclaimed here. A successful result only installs a new
private version requiring re-review; this wrapper has no publication path.
"""
from __future__ import annotations

from typing import Any

from core.content_ops.banner_regeneration import BannerError, run_banner_once


_DISABLED = {
    "status": "disabled",
    "provider_called": False,
    "public_send_attempted": False,
}


def _require_dependency(value: Any, method: str) -> None:
    if not callable(getattr(value, method, None)):
        raise BannerError("banner_worker_dependency_invalid")


def _validate_dependencies(*, journal: Any, owner: Any, provider: Any) -> None:
    # Validate every method used by run_banner_once before it can reserve a
    # row. This keeps missing wiring from consuming a durable attempt.
    for method in ("take", "change"):
        _require_dependency(journal, method)
    for method in ("is_current_edit", "save_banner_revision"):
        _require_dependency(owner, method)
    _require_dependency(provider, "edit")


class BannerWorker:
    """A dependency-injected, default-unmounted one-shot worker."""

    def __init__(self, *, journal: Any, owner: Any, provider: Any):
        _validate_dependencies(journal=journal, owner=owner, provider=provider)
        self._journal = journal
        self._owner = owner
        self._provider = provider

    async def run_once(self, *, now: int | None = None) -> dict:
        """Advance at most one durable journal transition.

        ``run_banner_once`` owns the state machine and terminal handling. No
        retry loop is added here, so provider/commit ambiguity remains a
        visible hold after a process restart.
        """
        return await run_banner_once(
            enabled=True,
            journal=self._journal,
            owner=self._owner,
            provider=self._provider,
            now=now,
        )


def compose_banner_worker(*, enabled=False, journal=None, owner=None, provider=None):
    """Compose only when explicitly enabled; return ``None`` by default."""
    if enabled is not True:
        return None
    return BannerWorker(journal=journal, owner=owner, provider=provider)


async def run_banner_worker_once(*, enabled=False, journal=None, owner=None,
                                 provider=None, now: int | None = None) -> dict:
    """Default-OFF convenience entry point with no implicit runtime wiring."""
    if enabled is not True:
        return dict(_DISABLED)
    worker = compose_banner_worker(enabled=True, journal=journal, owner=owner,
                                   provider=provider)
    return await worker.run_once(now=now)
