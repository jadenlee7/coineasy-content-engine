"""Durable local request barriers; no provider calls or production state."""
import multiprocessing
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from core.automation.news_generation_journal import (
    NewsGenerationJournal, NewsGenerationJournalError, canonical_request_id, request_digest,
)

REQUEST_ID = "12345678-1234-4234-8234-123456789abc"
PAYLOAD = {"source_content": "private source fixture", "options": {"mode": "reference"}}


@pytest.fixture
def paths(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    output = tmp_path / "output"
    NewsGenerationJournal(private.resolve(), output_root=output.resolve(), initialize=True)
    return private.resolve(), output.resolve()


def reserve(private, output, payload=PAYLOAD, client="yellow"):
    return NewsGenerationJournal(private, output_root=output).reserve(REQUEST_ID, client, payload)


def response_for(reservation):
    output = reservation.output_dir
    output.mkdir(parents=True)
    (output / "card.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    (output / "manifest.json").write_text("{}")
    return {"client_id": reservation.client_id, "content_type": "news_card",
        "spec": {"title": "검토용"}, "png_path": str(output / "card.png"),
        "manifest_path": str(output / "manifest.json"), "template_style": "classic",
        "requested_template_style": "classic", "source_image_used": False,
        "source_image_url": "", "source_image_sha256": "", "source_visual_path": None,
        "figma_template": None, "duration_ms": 1}


def _process_reserve(args):
    try:
        reservation = reserve(*args)
        return "started" if reservation.owner_token else "replayed"
    except NewsGenerationJournalError as exc:
        return str(exc)


def _process_pin(args):
    private, output, mode = args
    try:
        NewsGenerationJournal(private, output_root=output).pin_mode(REQUEST_ID, "yellow", PAYLOAD, mode)
        return mode
    except NewsGenerationJournalError as exc:
        return str(exc)


def test_concurrent_opposite_modes_have_exactly_one_cross_process_winner(paths):
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        outcomes = list(pool.map(_process_pin, [(*paths, "legacy"), (*paths, "quality")]))
    assert outcomes.count("news_generation_mode_conflict") == 1
    assert len(set(outcomes) & {"legacy", "quality"}) == 1
    winner = next(mode for mode in outcomes if mode in {"legacy", "quality"})
    assert _process_pin((*paths, winner)) == winner
    assert _process_pin((*paths, "quality" if winner == "legacy" else "legacy")) == "news_generation_mode_conflict"


def test_quality_pin_is_restart_safe_without_generic_terminal_reservation(paths):
    private, output = paths
    for _ in range(2):
        NewsGenerationJournal(private, output_root=output).pin_mode(REQUEST_ID, "yellow", PAYLOAD, "quality")
    with sqlite3.connect(private / "news-card-requests.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM news_generation_requests").fetchone()[0] == 0
        assert db.execute("SELECT mode FROM news_generation_modes").fetchall() == [("quality",)]
    with pytest.raises(NewsGenerationJournalError, match="mode_conflict"):
        reserve(private, output)
    assert PAYLOAD["source_content"].encode() not in (private / "news-card-requests.sqlite3").read_bytes()


@pytest.mark.parametrize("mutation", ["payload", "client", "output", "mode"])
def test_mode_pin_binds_client_full_payload_and_output_root(paths, mutation):
    private, output = paths
    NewsGenerationJournal(private, output_root=output).pin_mode(REQUEST_ID, "yellow", PAYLOAD, "quality")
    with pytest.raises(NewsGenerationJournalError, match="mode_conflict" if mutation == "mode" else "request_conflict"):
        NewsGenerationJournal(private, output_root=output / "changed" if mutation == "output" else output).pin_mode(
            REQUEST_ID, "babylon" if mutation == "client" else "yellow",
            {**PAYLOAD, "source_content": "mutated"} if mutation == "payload" else PAYLOAD,
            "legacy" if mutation == "mode" else "quality")


def test_existing_legacy_reservation_is_never_treated_as_unpinned_quality(paths):
    private, output = paths
    reserve(private, output)
    # Models an externally reconciled legacy reservation without a modes row.
    with sqlite3.connect(private / "news-card-requests.sqlite3") as db:
        db.execute("DELETE FROM news_generation_modes")
    with pytest.raises(NewsGenerationJournalError, match="mode_conflict"):
        NewsGenerationJournal(private, output_root=output).pin_mode(REQUEST_ID, "yellow", PAYLOAD, "quality")
    with pytest.raises(NewsGenerationJournalError, match="outcome_unknown"):
        reserve(private, output)


@pytest.mark.parametrize("initialize", [False, True])
def test_old_schema_is_never_silently_upgraded_or_trusted(paths, initialize):
    private, output = paths
    reserve(private, output)
    path = private / "news-card-requests.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE news_generation_modes")
        db.execute("DROP TABLE news_generation_schema")
        db.execute("CREATE TABLE news_generation_schema (version INTEGER PRIMARY KEY CHECK(version=1))")
        db.execute("INSERT INTO news_generation_schema VALUES (1)")
    original = path.read_bytes()
    with pytest.raises(NewsGenerationJournalError, match="journal_unavailable"):
        NewsGenerationJournal(private, output_root=output, initialize=initialize)
    assert path.read_bytes() == original
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM news_generation_schema").fetchall() == [(1,)]
        assert db.execute("SELECT count(*) FROM news_generation_requests").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='news_generation_modes'").fetchall() == []


def test_exactly_one_cross_process_winner_and_restart_stays_held(paths):
    private, output = paths
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        outcomes = list(pool.map(_process_reserve, [paths] * 8))
    assert outcomes.count("started") == 1
    assert outcomes.count("news_generation_outcome_unknown") == 7
    with pytest.raises(NewsGenerationJournalError, match="outcome_unknown"):
        reserve(private, output)
    assert (private / "news-card-requests.sqlite3").stat().st_mode & 0o777 == 0o600
    assert PAYLOAD["source_content"].encode() not in (private / "news-card-requests.sqlite3").read_bytes()


def test_success_replays_immutable_response_after_restart(paths):
    private, output = paths
    journal = NewsGenerationJournal(private, output_root=output)
    first = reserve(private, output)
    response = response_for(first)
    journal.record_success(first, response)
    replay = reserve(private, output)
    assert replay.owner_token is None and replay.response == response
    assert replay.output_dir.name == "news_request_" + REQUEST_ID


@pytest.mark.parametrize("change", ["source", "client", "output"])
def test_request_identity_and_full_payload_are_bound(paths, change):
    private, output = paths
    reserve(private, output)
    payload = {**PAYLOAD, "source_content": "changed"} if change == "source" else PAYLOAD
    with pytest.raises(NewsGenerationJournalError, match="request_conflict"):
        reserve(private, output / "changed" if change == "output" else output,
                payload, "babylon" if change == "client" else "yellow")
    assert request_digest("yellow", {"a": 1, "b": 2}) == request_digest("yellow", {"b": 2, "a": 1})
    assert request_digest("yellow", PAYLOAD) != request_digest("yellow", {**PAYLOAD, "mock_mode": True})


@pytest.mark.parametrize("asset", ["png_path", "manifest_path"])
@pytest.mark.parametrize("mutation", ["missing", "changed", "symlink"])
def test_cached_assets_cannot_disappear_drift_or_escape(paths, asset, mutation):
    private, output = paths
    first = reserve(private, output)
    response = response_for(first)
    NewsGenerationJournal(private, output_root=output).record_success(first, response)
    path = Path(response[asset])
    if mutation == "changed":
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        path.unlink()
        if mutation == "symlink":
            target = private / "foreign"; target.write_bytes(b"foreign")
            path.symlink_to(target)
    with pytest.raises(NewsGenerationJournalError, match="cached_assets_unavailable"):
        reserve(private, output)


def test_unknown_and_stable_success_are_outside_legacy_retention(paths):
    from api.output_retention import cleanup_generated_runs, RetentionPolicy
    private, output = paths
    first = reserve(private, output)
    response = response_for(first)
    old = 1_000_000
    os.utime(first.output_dir, (old, old))
    policy = RetentionPolicy(1, 1, 1, 1)
    result = cleanup_generated_runs(output, policy=policy, now=old + 100_000)
    assert result.deleted_runs == 0 and Path(response["png_path"]).is_file()
    NewsGenerationJournal(private, output_root=output).record_success(first, response)
    assert cleanup_generated_runs(output, policy=policy, now=old + 200_000).deleted_runs == 0
    assert reserve(private, output).response == response


def test_private_root_symlink_and_public_path_refused(paths):
    private, output = paths
    with pytest.raises(NewsGenerationJournalError, match="output_root_invalid"):
        reserve(private, private)
    link = private.parent / "link"; link.symlink_to(private, target_is_directory=True)
    with pytest.raises(NewsGenerationJournalError, match="private_volume_required"):
        NewsGenerationJournal(link, output_root=output)
    private.chmod(0o755)
    with pytest.raises(NewsGenerationJournalError, match="private_volume_required"):
        NewsGenerationJournal(private, output_root=output)


@pytest.mark.parametrize("loss", ["file", "sentinel", "requests", "modes"])
def test_missing_volume_or_schema_never_reinitializes(paths, loss):
    private, output = paths
    reserve(private, output)
    path = private / "news-card-requests.sqlite3"
    if loss == "file":
        path.unlink()
    else:
        table = {"sentinel": "news_generation_schema", "requests": "news_generation_requests",
                 "modes": "news_generation_modes"}[loss]
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE " + table)
    with pytest.raises(NewsGenerationJournalError):
        NewsGenerationJournal(private, output_root=output)
    if loss == "file":
        assert not path.exists()
    else:
        with sqlite3.connect(path) as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert table not in names


def test_owner_proof_and_response_size_are_bounded(paths):
    private, output = paths
    first = reserve(private, output)
    response = response_for(first)
    journal = NewsGenerationJournal(private, output_root=output)
    with pytest.raises(NewsGenerationJournalError, match="request_conflict"):
        journal.record_success(replace(first, owner_token="a" * 64), response)
    with pytest.raises(NewsGenerationJournalError, match="payload_invalid"):
        journal.record_success(first, {**response, "spec": {"body": "x" * 1_000_001}})
    with pytest.raises(NewsGenerationJournalError, match="outcome_unknown"):
        reserve(private, output)


@pytest.mark.parametrize("value", [None, "", REQUEST_ID.upper(), " " + REQUEST_ID,
    "00000000-0000-0000-0000-000000000000", "not-a-uuid"])
def test_canonical_uuid_required(value):
    with pytest.raises(NewsGenerationJournalError, match="request_id_invalid"):
        canonical_request_id(value)
