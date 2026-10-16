"""Credential-free one-shot identity and canonical readiness-anchored bounds."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

TRIAL_ID = "d346ceea-4ea9-479f-a5cf-05097b50ce48"
ELIGIBLE_CLIENTS = ("babylon", "origintrail")
FRESH_TRIAL_ID = "9d7865c1-c13b-4b1b-aba1-83124aa8d2cd"
FRESH_ELIGIBLE_CLIENTS = ("babylon",)
NEW_BABYLON_TRIAL_ID = "c34dbb8a-ff7c-484e-8e43-757f692ec743"
NEW_BABYLON_EARLIEST = datetime(2026, 10, 15, 15, 47, 35, tzinfo=timezone.utc)
STANDALONE_BABYLON_TRIAL_ID = "7b27e277-0641-4aef-b739-761f86547287"
STANDALONE_BABYLON_EARLIEST = datetime(2026, 10, 16, 19, 45, 3, tzinfo=timezone.utc)
WINDOW_SECONDS = 7200
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class TrialIdentityPolicy:
    client_ids: tuple[str, ...]
    max_per_client: int
    max_total: int
    earliest: datetime | None = None
    kst_date: date | None = None
    natural: bool = False
    latest_standalone: bool = False


_POLICIES = {
    TRIAL_ID: TrialIdentityPolicy(ELIGIBLE_CLIENTS, 1, 2),
    FRESH_TRIAL_ID: TrialIdentityPolicy(FRESH_ELIGIBLE_CLIENTS, 1, 1,
        datetime(2026, 10, 15, 15, tzinfo=timezone.utc), date(2026, 10, 16), True),
    NEW_BABYLON_TRIAL_ID: TrialIdentityPolicy(FRESH_ELIGIBLE_CLIENTS, 1, 1,
        NEW_BABYLON_EARLIEST, date(2026, 10, 16), True),
    # One new immutable identity; readiness fixes its exact KST day and window.
    # No runtime re-anchoring, recurring identity, or legacy policy widening.
    STANDALONE_BABYLON_TRIAL_ID: TrialIdentityPolicy(FRESH_ELIGIBLE_CLIENTS, 1, 1,
        STANDALONE_BABYLON_EARLIEST, natural=True, latest_standalone=True),
}


def trial_identity_policy(trial_id):
    try:
        return _POLICIES[trial_id]
    except (KeyError, TypeError):
        raise ValueError("trial_id_invalid") from None


def trial_clients(trial_id):
    return trial_identity_policy(trial_id).client_ids


def trial_anchor_allowed(trial_id, start):
    policy = trial_identity_policy(trial_id)
    return ((policy.earliest is None or start >= policy.earliest)
            and (policy.kst_date is None or start.astimezone(KST).date() == policy.kst_date))


def trial_window(not_before, deadline):
    """Validate an immutable operator anchor; never derive one from the clock."""
    def parse(value):
        if type(value) is not str or not value.endswith("Z"):
            raise ValueError("trial_timestamp_invalid")
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        if parsed.isoformat(timespec="seconds").replace("+00:00", "Z") != value:
            raise ValueError("trial_timestamp_invalid")
        return parsed

    start, end = parse(not_before), parse(deadline)
    if ((end - start).total_seconds() != WINDOW_SECONDS
            or start.astimezone(KST).date() != end.astimezone(KST).date()):
        raise ValueError("trial_plan_scope_invalid")
    return start, end
