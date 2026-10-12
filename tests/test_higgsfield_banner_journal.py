"""Local-only durable paid-submit guard tests; zero provider/database network."""
import io
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from core.content_ops.higgsfield_banner_journal import (
    HiggsfieldBannerJournal, HiggsfieldJournalError,
)

W = "11111111-1111-4111-8111-111111111111"
S = "22222222-2222-4222-8222-222222222222"
OP = "33333333-3333-4333-8333-333333333333"
REQ = "44444444-4444-4444-8444-444444444444"
HASH = "a" * 64
NOW = 1791795600


@pytest.fixture
def private_path():
    with tempfile.TemporaryDirectory(prefix="coineasy-hf-journal-") as directory:
        yield Path(directory).resolve() / "journal.sqlite3"


def owner(path, *, client="yellow", source=S, limit=3, now=NOW, workspace=W):
    return HiggsfieldBannerJournal(path, workspace_id=workspace, client_id=client,
        source_item_id=source, daily_limit=limit, clock=lambda: now)


def begin(journal, operation_id=OP, digest=HASH, **scope_changes):
    scope = dict(workspace_id=journal.workspace_id, client_id=journal.client_id,
                 source_item_id=journal.source_item_id)
    scope.update(scope_changes)
    return journal.begin_submit(operation_id, digest, **scope)


def png(color="yellow", size=(2880, 2880)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def test_single_winner_across_concurrent_callers_and_restart(private_path):
    journal = owner(private_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: begin(journal), range(8)))
    assert results.count(True) == 1
    restarted = owner(private_path)
    assert begin(restarted) is False
    assert begin(restarted, digest="b" * 64) is False
    with pytest.raises(HiggsfieldJournalError, match="submit_unknown"):
        restarted.read_request_id(OP, HASH)
    assert private_path.stat().st_mode & 0o777 == 0o600


def test_unknown_source_cannot_be_retried_under_new_uuid_or_day(private_path):
    assert begin(owner(private_path))
    assert not begin(owner(private_path), str(uuid4()), "b" * 64)
    assert not begin(owner(private_path, now=NOW + 86400), str(uuid4()), "b" * 64)


def test_workspace_daily_budget_counts_started_and_completed_attempts(private_path):
    assert begin(owner(private_path, client="yellow", limit=2))
    assert begin(owner(private_path, client="babylon", source=str(uuid4()), limit=2), str(uuid4()))
    assert not begin(owner(private_path, client="origintrail", source=str(uuid4()), limit=2), str(uuid4()))
    assert begin(owner(private_path, client="origintrail", source=str(uuid4()), limit=2, now=NOW+86400), str(uuid4()))


def test_one_initial_source_per_client_per_day_even_below_aggregate_limit(private_path):
    assert begin(owner(private_path, limit=4))
    assert not begin(owner(private_path, source=str(uuid4()), limit=4), str(uuid4()))


def test_request_id_persisted_then_collect_can_resume_without_post(private_path):
    journal = owner(private_path)
    assert begin(journal)
    journal.record_submitted(OP, HASH, REQ)
    journal.record_submitted(OP, HASH, REQ)
    restarted = owner(private_path)
    assert restarted.read_request_id(OP, HASH) == REQ
    assert begin(restarted) is False
    assert restarted.read_result(OP, HASH) is None
    image = png()
    restarted.record_result(OP, HASH, REQ, image)
    restarted.record_result(OP, HASH, REQ, image)
    assert owner(private_path).read_result(OP, HASH) == image
    assert restarted.read_request_id(OP, HASH) == REQ
    with pytest.raises(HiggsfieldJournalError, match="result_conflict"):
        restarted.record_result(OP, HASH, REQ, png("red"))


def test_wrong_digest_request_or_scope_cannot_read_or_commit(private_path):
    journal = owner(private_path)
    assert begin(journal)
    journal.record_submitted(OP, HASH, REQ)
    with pytest.raises(HiggsfieldJournalError, match="binding_conflict"):
        journal.record_submitted(OP, HASH, str(uuid4()))
    with pytest.raises(HiggsfieldJournalError, match="binding_conflict"):
        journal.read_request_id(OP, "b" * 64)
    with pytest.raises(HiggsfieldJournalError, match="binding_conflict"):
        owner(private_path, client="babylon").read_request_id(OP, HASH)
    with pytest.raises(HiggsfieldJournalError, match="binding_conflict"):
        owner(private_path, workspace=str(uuid4())).read_request_id(OP, HASH)
    with pytest.raises(HiggsfieldJournalError, match="binding_conflict"):
        journal.record_result(OP, HASH, str(uuid4()), png())


@pytest.mark.parametrize("value", [b"not-image", png(size=(1080,1080))])
def test_low_quality_or_invalid_result_is_not_installed(private_path, value):
    journal = owner(private_path)
    assert begin(journal)
    journal.record_submitted(OP, HASH, REQ)
    with pytest.raises(HiggsfieldJournalError, match="image_invalid"):
        journal.record_result(OP, HASH, REQ, value)
    assert journal.read_result(OP, HASH) is None
    assert begin(journal) is False


def test_private_volume_and_no_symlink_or_world_readable_journal(private_path):
    outside = private_path.parent / "outside.sqlite3"
    outside.touch(mode=0o600)
    private_path.symlink_to(outside)
    with pytest.raises(HiggsfieldJournalError, match="private_volume_required"):
        owner(private_path)
    private_path.unlink()
    private_path.touch(mode=0o644)
    os.chmod(private_path, 0o644)
    with pytest.raises(HiggsfieldJournalError, match="private_volume_required"):
        owner(private_path)


def test_limit_must_be_explicit_positive_request_count(private_path):
    for limit in (0, 5, True, "3"):
        with pytest.raises(HiggsfieldJournalError):
            owner(private_path, limit=limit)
    assert not private_path.exists()


@pytest.mark.parametrize("scope", [
    {"workspace_id": "55555555-5555-4555-8555-555555555555"},
    {"client_id": "babylon"},
    {"source_item_id": "66666666-6666-4666-8666-666666666666"},
])
def test_wrong_submit_scope_consumes_no_reservation_or_quota(private_path, scope):
    journal = owner(private_path, limit=1)
    with pytest.raises(HiggsfieldJournalError, match="binding_conflict"):
        begin(journal, **scope)
    # The exact operation and sole daily quota remain available after rejection.
    assert begin(owner(private_path, limit=1)) is True
    assert begin(owner(private_path, limit=1), operation_id=str(uuid4())) is False


def test_submit_requires_explicit_scope(private_path):
    journal = owner(private_path)
    with pytest.raises(TypeError):
        journal.begin_submit(OP, HASH)
    assert begin(journal) is True
