"""Synthetic bounds only; no production start is encoded by these fixtures."""
from datetime import date, datetime, timezone
from core.content_ops.trial_window import TRIAL_ID

NOT_BEFORE = "2026-10-14T15:10:51Z"
LATEST_DEADLINE = "2026-10-14T17:10:51Z"
START = datetime(2026, 10, 14, 15, 10, 51, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 17, 10, 51, tzinfo=timezone.utc)
DAY = date(2026, 10, 15)
WORKER_ID = f"ybo-trial:{TRIAL_ID}"
