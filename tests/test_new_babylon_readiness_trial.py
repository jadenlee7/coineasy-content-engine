"""The second natural Babylon identity cannot inherit another trial's authority."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest

from core.automation.trial_generation_gate import TrialGenerationDenied, TrialGenerationGate
from core.automation.trial_owner import (LIMIT_MICROS, POLICY_SHA256, TrialError,
    TrialOwner, TrialPlan, canonical_sha256)
from core.automation.trial_producer_adapter import TrialNaturalProducerAdapter, TrialProducerBlocked
from core.content_ops.trial_courier_adapter import TrialCourierError, TrialManifest, canonical_sha256 as courier_sha
from core.content_ops.trial_private_review_runtime import TrialCourierSettings, TrialRuntimeError
from core.content_ops.trial_window import (FRESH_TRIAL_ID, NEW_BABYLON_EARLIEST,
    NEW_BABYLON_TRIAL_ID, TRIAL_ID, trial_anchor_allowed, trial_identity_policy)

START = datetime(2026, 10, 15, 15, 48, tzinfo=timezone.utc)
NOW = START + timedelta(minutes=1)


def plan(**changes):
    payload = {"contract": "ybo-server-trial@1", "trial_id": NEW_BABYLON_TRIAL_ID,
        "not_before": "2026-10-15T15:48:00Z", "deadline": "2026-10-15T17:48:00Z",
        "kst_date": "2026-10-16", "client_ids": ["babylon"], "max_per_client": 1,
        "max_total": 1, "estimated_daily_limit_micros": LIMIT_MICROS,
        "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40, **changes}
    return TrialPlan.from_mapping(payload, canonical_sha256(payload))


def test_policy_is_exact_and_old_fresh_stays_unchanged():
    new = trial_identity_policy(NEW_BABYLON_TRIAL_ID)
    assert new.client_ids == ("babylon",) and new.max_per_client == new.max_total == 1
    assert new.earliest == NEW_BABYLON_EARLIEST and new.natural
    assert trial_identity_policy(FRESH_TRIAL_ID).earliest == datetime(2026, 10, 15, 15, tzinfo=timezone.utc)
    assert trial_identity_policy(TRIAL_ID).client_ids == ("babylon", "origintrail")
    assert not trial_anchor_allowed(NEW_BABYLON_TRIAL_ID, NEW_BABYLON_EARLIEST - timedelta(seconds=1))
    assert trial_anchor_allowed(NEW_BABYLON_TRIAL_ID, NEW_BABYLON_EARLIEST)
    for unknown in (str(uuid4()), None, "", NEW_BABYLON_TRIAL_ID.upper()):
        with pytest.raises(ValueError, match="trial_id_invalid"):
            trial_identity_policy(unknown)


@pytest.mark.parametrize("changes", [
    {"trial_id": str(uuid4())},
    {"client_ids": ["babylon", "origintrail"], "max_total": 2},
    {"max_per_client": 2},
    {"max_total": 2},
    {"not_before": "2026-10-15T15:47:34Z", "deadline": "2026-10-15T17:47:34Z"},
    {"not_before": "2026-10-16T15:48:00Z", "deadline": "2026-10-16T17:48:00Z", "kst_date": "2026-10-17"},
])
def test_plan_rejects_foreign_identity_scope_and_anchor(changes):
    with pytest.raises(TrialError):
        plan(**changes)


def test_new_owner_replay_and_generation_pins(tmp_path):
    candidate = plan()
    owner = TrialOwner(tmp_path / f"{candidate.trial_id}.trial-owner.sqlite3", candidate, clock=lambda: NOW)
    admission = owner.admit(candidate.trial_id, NOW)
    assert admission is not None
    assert owner.admit(candidate.trial_id, NOW) is None
    for client in ("origintrail", "yellow"):
        with pytest.raises(TrialError):
            owner.reserve_phase(admission, f"{client}:intake", client, "b" * 64, NOW)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    env = {"YBO_TRIAL_API_MODE": "true", "YBO_TRIAL_PLAN_JSON": json.dumps(candidate.payload),
        "YBO_TRIAL_PLAN_SHA256": candidate.sha256, "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(private)}
    gate = TrialGenerationGate.from_env(env, clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    assert gate.plan.trial_id == NEW_BABYLON_TRIAL_ID
    with pytest.raises(TrialGenerationDenied):
        gate.require_generation(trial_id=FRESH_TRIAL_ID, client_id="babylon", request_id=str(uuid4()), payload={})
    with pytest.raises(TrialGenerationDenied):
        gate.require_generation(trial_id=NEW_BABYLON_TRIAL_ID, client_id="origintrail", request_id=str(uuid4()), payload={})
    with pytest.raises(TrialGenerationDenied):
        TrialGenerationGate.from_env({**env, "YBO_PRESERVED_BABYLON_RECEIPT_ID": str(uuid4())},
            clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    with pytest.raises(TrialGenerationDenied):
        TrialGenerationGate.from_env({**env, "RAILWAY_GIT_COMMIT_SHA": "b" * 40},
            clock=lambda: NOW, stamp_reader=lambda: "a" * 40)


def test_new_natural_producer_requires_no_recovery_receipt(tmp_path):
    candidate = plan()
    owner = TrialOwner(tmp_path / f"{candidate.trial_id}.trial-owner.sqlite3", candidate)
    args = dict(repository=object(), x_client=object(), studio=object(), owner=owner,
        workspace_id=str(uuid4()), trial_id=NEW_BABYLON_TRIAL_ID, clock=lambda: NOW)
    adapter = TrialNaturalProducerAdapter(**args)
    assert adapter.recovery_receipt_id is None
    with pytest.raises(TrialProducerBlocked):
        TrialNaturalProducerAdapter(**args, recovery_receipt_id=str(uuid4()))


def test_new_courier_manifest_and_runtime_pin_anchor():
    candidate = plan()
    entry = {"client_id": "babylon", **{k: str(uuid4()) for k in
        ("job_id", "request_id", "source_item_id", "content_version_id")},
        "source_sha256": "a" * 64, "banner_sha256": "b" * 64,
        "telegram_copy_sha256": "c" * 64, "x_copy_sha256": "d" * 64}
    entry["content_item_id"] = entry["request_id"]
    payload = {"contract": "ybo-trial-courier-manifest@1", "trial_id": NEW_BABYLON_TRIAL_ID,
        "plan_sha256": candidate.sha256, "release_sha": "a" * 40,
        "not_before": candidate.payload["not_before"], "deadline": candidate.payload["deadline"],
        "destination_policy_sha256": "e" * 64, "entries": [entry]}
    assert TrialManifest.from_mapping(payload, courier_sha(payload)).payload == payload
    for changed in ({"trial_id": str(uuid4())},
                    {"not_before": "2026-10-15T15:47:34Z", "deadline": "2026-10-15T17:47:34Z"},
                    {"entries": [{**entry, "client_id": "origintrail"}]}):
        invalid = {**payload, **changed}
        with pytest.raises(TrialCourierError):
            TrialManifest.from_mapping(invalid, courier_sha(invalid))
    from test_trial_private_review_runtime import env
    from test_content_ops_daily_private_review_runtime import SHA
    configured = env(CONTENT_OPS_TRIAL_ID=NEW_BABYLON_TRIAL_ID,
        CONTENT_OPS_TRIAL_NOT_BEFORE=payload["not_before"], CONTENT_OPS_TRIAL_DEADLINE=payload["deadline"])
    assert TrialCourierSettings.from_env(configured, stamp_reader=lambda: SHA).trial_id == NEW_BABYLON_TRIAL_ID
    with pytest.raises(TrialRuntimeError):
        TrialCourierSettings.from_env({**configured,
            "CONTENT_OPS_TRIAL_NOT_BEFORE": "2026-10-15T15:47:34Z",
            "CONTENT_OPS_TRIAL_DEADLINE": "2026-10-15T17:47:34Z"}, stamp_reader=lambda: SHA)
