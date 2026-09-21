"""Offline tests for the explicit, default-OFF banner worker boundary."""
import asyncio

import httpx
import pytest

from core.content_ops.banner_regeneration import BannerError, BannerJournal
from core.content_ops.banner_worker import (
    BannerWorker, compose_banner_worker, run_banner_worker_once,
)
from test_content_ops_banner_regeneration import LOGO, Owner, Provider, TIME, request


class Unreadable:
    def __getattribute__(self, _name):  # pragma: no cover - assertion helper
        raise AssertionError("disabled path inspected a dependency")


def test_default_off_does_not_construct_or_inspect_runtime_dependencies():
    dependency = Unreadable()
    assert compose_banner_worker(enabled=False, journal=dependency,
                                 owner=dependency, provider=dependency) is None
    assert asyncio.run(run_banner_worker_once(enabled=False, journal=dependency,
                                              owner=dependency,
                                              provider=dependency)) == {
        "status": "disabled", "provider_called": False,
        "public_send_attempted": False,
    }


def test_enabled_requires_all_explicit_dependencies_before_claiming():
    with pytest.raises(BannerError, match="banner_worker_dependency_invalid"):
        compose_banner_worker(enabled=True, journal=None, owner=Owner(),
                              provider=Provider())


def test_enabled_composition_is_one_shot_and_restart_safe(tmp_path):
    journal = BannerJournal(tmp_path / "jobs.sqlite3")
    owner, provider, req = Owner(), Provider(), request()
    journal.enqueue(req, LOGO, now=TIME)
    worker = compose_banner_worker(enabled=True, journal=journal, owner=owner,
                                   provider=provider)
    assert isinstance(worker, BannerWorker)
    assert asyncio.run(worker.run_once(now=TIME))["status"] == "result_ready"
    restarted = compose_banner_worker(enabled=True,
                                      journal=BannerJournal(journal.path),
                                      owner=owner, provider=provider)
    assert asyncio.run(restarted.run_once(now=TIME))["status"] == "revision_saved"
    assert asyncio.run(restarted.run_once(now=TIME))["status"] == "idle"
    assert provider.calls == owner.saves == 1


def test_provider_unknown_is_terminal_and_never_retried(tmp_path):
    journal = BannerJournal(tmp_path / "jobs.sqlite3")
    provider = Provider(httpx.ReadTimeout("private provider detail"))
    journal.enqueue(request(), LOGO, now=TIME)
    result = asyncio.run(run_banner_worker_once(enabled=True, journal=journal,
        owner=Owner(), provider=provider, now=TIME))
    assert result == {"status": "provider_unknown", "provider_called": True,
                      "public_send_attempted": False}
    second = asyncio.run(run_banner_worker_once(enabled=True,
        journal=BannerJournal(journal.path), owner=Owner(), provider=provider,
        now=TIME))
    assert second["status"] == "idle"
    assert provider.calls == 1
