"""Credential-free one-shot identity and canonical readiness-anchored bounds."""
from datetime import datetime, timedelta, timezone

TRIAL_ID = "d346ceea-4ea9-479f-a5cf-05097b50ce48"
ELIGIBLE_CLIENTS = ("babylon", "origintrail")
FRESH_TRIAL_ID = "9d7865c1-c13b-4b1b-aba1-83124aa8d2cd"
FRESH_ELIGIBLE_CLIENTS = ("babylon",)
WINDOW_SECONDS = 7200
KST = timezone(timedelta(hours=9))


def trial_clients(trial_id):
    if trial_id == TRIAL_ID:
        return ELIGIBLE_CLIENTS
    if trial_id == FRESH_TRIAL_ID:
        return FRESH_ELIGIBLE_CLIENTS
    raise ValueError("trial_id_invalid")


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
