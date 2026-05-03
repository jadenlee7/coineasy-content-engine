import re
from datetime import datetime, timedelta, timezone


def test_start_time_format_matches_x_api_spec():
    """X API v2 requires start_time as 'YYYY-MM-DDTHH:mm:ssZ' (RFC 3339, whole seconds, Z suffix)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", since), f"start_time has wrong format: {since}"
    assert "." not in since, "must not contain microseconds"
    assert "+" not in since, "must not contain offset"
