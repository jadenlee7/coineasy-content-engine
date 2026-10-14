"""Credential-free one-shot identity and canonical readiness-anchored bounds."""
from datetime import datetime, timedelta, timezone

TRIAL_ID = "1a2bef47-4827-4bc3-9b4d-5baf2714015d"
ELIGIBLE_CLIENTS = ("babylon", "origintrail")
WINDOW_SECONDS = 7200
KST = timezone(timedelta(hours=9))


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
