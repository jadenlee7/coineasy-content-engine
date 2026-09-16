from __future__ import annotations

import copy
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.content_ops.coverage import CLIENT_ROSTER, build_daily_coverage


NOW = datetime(2026, 9, 6, 1, 0, tzinfo=timezone.utc)


def _id(client: str, position: int) -> str:
    index = [item[0] for item in CLIENT_ROSTER].index(client) + 1
    return f"00000000-0000-4000-8000-{index * 10 + position:012d}"


def _ready(client: str = "yellow", **changes) -> dict:
    value = {
        "client_id": client,
        "observed_at": NOW.isoformat(),
        "kst_date": "2026-09-06",
        "feed_active": True,
        "feed_last_polled_at": (NOW - timedelta(minutes=1)).isoformat(),
        "source_count": 1,
        "source_scan_complete": True,
        "source_is_latest": True,
        "source_published_at": (NOW - timedelta(hours=1)).isoformat(),
        "source_item_id": _id(client, 1),
        "generate_job_id": _id(client, 2),
        "content_item_id": _id(client, 3),
        "content_version_id": _id(client, 4),
        "draft_reserved": True,
        "generation_status": "succeeded",
        "content_kind": "daily_news",
        "content_status": "needs_review",
        "current_version": True,
        "mock_mode": False,
        "png_count": 1,
        "banner_sha256": "a" * 64,
        "approval_count": 0,
        "publication_count": 0,
        "internal_review_status": "none",
    }
    value.update(changes)
    return value


def _no_source(**changes) -> dict:
    value = {
        "client_id": "yellow", "observed_at": NOW, "kst_date": "2026-09-06",
        "generation_status": "none", "draft_reserved": False,
        "source_count": 0, "source_scan_complete": True,
        "feed_active": True, "feed_last_polled_at": NOW,
        "internal_review_status": "none",
    }
    value.update(changes)
    return value


def _row(value: dict, *, now=NOW) -> dict:
    return next(
        row for row in build_daily_coverage([value], now=now)["clients"]
        if row["client_id"] == value["client_id"]
    )


class DailyCoverageTests(unittest.TestCase):
    def test_empty_snapshot_always_represents_four_missing_clients(self):
        result = build_daily_coverage([], now=NOW)
        self.assertEqual([row["client_id"] for row in result["clients"]],
                         ["yellow", "origintrail", "squid", "babylon"])
        self.assertEqual(result["client_count"], 4)
        self.assertEqual(result["ready_count"], 0)
        self.assertTrue(all(row["status"] == "evidence_missing" for row in result["clients"]))

    def test_roster_labels_and_activity_match_checked_in_configs(self):
        root = Path(__file__).resolve().parents[1]
        for client, name in CLIENT_ROSTER:
            text = (root / "clients" / client / "config.yaml").read_text()
            self.assertEqual(re.search(r"^name: (.+)$", text, re.MULTILINE).group(1), name)
            self.assertRegex(text, r"(?m)^active: true$")

    def test_all_four_ready_in_canonical_order_without_approval_authority(self):
        result = build_daily_coverage([_ready(client) for client, _ in reversed(CLIENT_ROSTER)], now=NOW)
        self.assertEqual(result["ready_count"], 4)
        self.assertFalse(result["automatic_approval"])
        self.assertFalse(result["automatic_publication"])
        self.assertTrue(all(row["status"] == "needs_review" for row in result["clients"]))
        modes = {row["client_id"]: row["qa_mode"] for row in result["clients"]}
        self.assertEqual(modes.pop("origintrail"), "manual_batch_review")
        self.assertEqual(set(modes.values()), {"codex_advisory"})

    def test_ready_is_pure_deterministic_and_input_immutable(self):
        values = [_ready()]
        before = copy.deepcopy(values)
        with patch("builtins.open", side_effect=AssertionError("unexpected I/O")), \
             patch("pathlib.Path.open", side_effect=AssertionError("unexpected I/O")), \
             patch("socket.socket.connect", side_effect=AssertionError("unexpected network")):
            first = build_daily_coverage(values, now=NOW)
            second = build_daily_coverage(iter(values), now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(values, before)

    def test_malformed_roots_and_unknown_clients_raise_bounded_errors(self):
        for value in (None, "secret", b"secret", {}, [None], [{"client_id": "secret"}], [{"client_id": []}]):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError) as error:
                build_daily_coverage(value, now=NOW)
            self.assertNotIn("secret", str(error.exception))

    def test_input_iterator_is_bounded_to_five_reads(self):
        calls = []

        def unbounded():
            while True:
                calls.append(1)
                yield _ready()

        with self.assertRaisesRegex(ValueError, "limit_exceeded"):
            build_daily_coverage(unbounded(), now=NOW)
        self.assertEqual(len(calls), 5)

    def test_duplicate_client_observations_fail_closed(self):
        result = build_daily_coverage([_ready(), _ready()], now=NOW)
        self.assertEqual(result["clients"][0]["reason_codes"], ["duplicate_client_observations"])
        self.assertEqual(result["ready_count"], 0)

    def test_cross_client_duplicate_candidate_identity_fail_closed(self):
        for key in ("source_item_id", "generate_job_id", "content_item_id", "content_version_id"):
            with self.subTest(key=key):
                first = _ready()
                second = _ready("squid", **{key: first[key]})
                result = build_daily_coverage([first, second], now=NOW)
                for row in result["clients"]:
                    if row["client_id"] in {"yellow", "squid"}:
                        self.assertEqual(row["reason_codes"], ["duplicate_candidate_identity"])
                        self.assertFalse(row["review_ready"])

    def test_no_source_requires_complete_recent_observation_without_job(self):
        self.assertEqual(_row(_no_source())["status"], "no_source")
        for change in ({"source_scan_complete": None}, {"source_scan_complete": False},
                       {"feed_active": False}, {"source_count": None}, {"draft_reserved": None},
                       {"generate_job_id": _id("yellow", 2)}, {"png_count": 1},
                       {"source_published_at": NOW}, {"banner_sha256": "a" * 64},
                       {"publication_count": 1}, {"internal_review_status": None},
                       {"content_status": "approved"}, {"content_kind": "daily_news"},
                       {"current_version": True}):
            with self.subTest(change=change):
                self.assertEqual(_row(_no_source(**change))["status"], "evidence_missing")

    def test_reservation_never_means_complete(self):
        result = _row(_no_source(draft_reserved=True))
        self.assertEqual(result["reason_codes"], ["reservation_without_generation_evidence"])
        self.assertFalse(result["review_ready"])

    def test_generation_states_are_distinct_and_require_job_identity(self):
        for state in ("queued", "running", "retrying", "failed"):
            with self.subTest(state=state):
                value = _no_source(generation_status=state, generate_job_id=_id("yellow", 2))
                row = _row(value)
                self.assertEqual(row["status"], "generation_queued" if state == "retrying" else f"generation_{state}")
                self.assertFalse(row["review_ready"])
                value.pop("generate_job_id")
                self.assertEqual(_row(value)["status"], "evidence_missing")

    def test_internal_receipt_states_never_become_resend_candidates(self):
        for state, expected in {
            "pending": "pending", "claimed": "pending", "sending": "pending",
            "sent": "sent", "rejected": "rejected", "delivery_unknown": "unknown", "obsolete": "obsolete",
        }.items():
            with self.subTest(state=state):
                row = _row(_ready(internal_review_status=state))
                self.assertEqual(row["status"], f"internal_review_{expected}")
                self.assertFalse(row["review_ready"])

    def test_historical_unknown_and_obsolete_receipts_survive_source_age(self):
        for status in ("delivery_unknown", "obsolete", "sent"):
            row = _row(_ready(internal_review_status=status, current_version=False,
                              source_published_at=NOW - timedelta(days=2)))
            self.assertNotEqual(row["status"], "needs_review")
            self.assertNotEqual(row["status"], "evidence_missing")
            self.assertFalse(row["review_ready"])

    def test_receipt_contradicting_generation_or_missing_identity_fails_closed(self):
        for change in ({"generation_status": "failed"}, {"content_version_id": None},
                       {"banner_sha256": None}):
            self.assertEqual(_row(_ready(internal_review_status="sent", **change))["status"], "evidence_missing")

    def test_ready_requires_each_current_version_evidence_field(self):
        for key in ("source_item_id", "source_published_at", "source_is_latest", "source_count",
                    "generate_job_id", "content_item_id", "content_version_id", "content_kind",
                    "content_status", "current_version", "mock_mode", "png_count", "banner_sha256",
                    "approval_count", "publication_count", "internal_review_status", "feed_active",
                    "feed_last_polled_at", "observed_at", "kst_date", "generation_status"):
            with self.subTest(key=key):
                value = _ready()
                value.pop(key)
                row = _row(value)
                self.assertEqual(row["status"], "evidence_missing")
                self.assertFalse(row["review_ready"])

    def test_mock_stale_version_article_and_prior_approval_block_readiness(self):
        for change in ({"mock_mode": True}, {"current_version": False}, {"content_kind": "article"},
                       {"content_kind": "tutorial"}, {"content_status": "approved"},
                       {"source_is_latest": False}, {"approval_count": 1}, {"publication_count": 1},
                       {"png_count": 0}, {"png_count": 2}, {"source_count": 0}):
            with self.subTest(change=change):
                self.assertEqual(_row(_ready(**change))["status"], "evidence_missing")

    def test_source_age_strictly_less_than_24_hours(self):
        self.assertTrue(_row(_ready(source_published_at=NOW - timedelta(hours=24) + timedelta(microseconds=1)))["review_ready"])
        for age in (timedelta(hours=24), timedelta(hours=25)):
            self.assertEqual(_row(_ready(source_published_at=NOW - age))["reason_codes"], ["source_not_fresh"])

    def test_feed_maximum_age_is_exactly_fifteen_minutes(self):
        self.assertTrue(_row(_ready(feed_last_polled_at=NOW - timedelta(minutes=15)))["review_ready"])
        value = _ready(feed_last_polled_at=NOW - timedelta(minutes=15, microseconds=1))
        self.assertEqual(_row(value)["reason_codes"], ["feed_not_fresh"])
        self.assertEqual(_row(_no_source(feed_last_polled_at=NOW - timedelta(minutes=16)))["status"], "evidence_missing")

    def test_future_naive_or_after_snapshot_timestamps_rejected(self):
        for key in ("observed_at", "feed_last_polled_at", "source_published_at"):
            for stamp in (NOW + timedelta(microseconds=1), NOW.replace(tzinfo=None), "2026-09-06T01:00:00"):
                with self.subTest(key=key, stamp=stamp):
                    self.assertEqual(_row(_ready(**{key: stamp}))["status"], "evidence_missing")
        value = _ready(observed_at=NOW - timedelta(minutes=2))
        self.assertEqual(_row(value)["reason_codes"], ["invalid_evidence_timestamp"])

    def test_invalid_now_throws_not_silently_localizes(self):
        for now in (NOW.replace(tzinfo=None), NOW.isoformat(), None):
            with self.assertRaisesRegex(ValueError, "timezone_aware"):
                build_daily_coverage([], now=now)

    def test_stale_observation_is_not_no_source(self):
        value = _no_source(observed_at=NOW - timedelta(minutes=31),
                           feed_last_polled_at=NOW - timedelta(minutes=31))
        self.assertEqual(_row(value)["reason_codes"], ["stale_observation"])

    def test_kst_midnight_does_not_accept_prior_day_snapshot(self):
        now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
        value = _no_source(observed_at=now - timedelta(seconds=1), feed_last_polled_at=now - timedelta(seconds=1))
        self.assertEqual(build_daily_coverage([], now=now)["kst_date"], "2026-09-07")
        self.assertEqual(_row(value, now=now)["reason_codes"], ["kst_day_mismatch"])
        value["kst_date"] = "2026-09-07"
        self.assertEqual(_row(value, now=now)["reason_codes"], ["kst_day_mismatch"])

    def test_non_utc_aware_timestamp_is_normalized(self):
        offset = timezone(timedelta(hours=9))
        value = _ready(observed_at=NOW.astimezone(offset),
                       source_published_at=(NOW - timedelta(hours=1)).astimezone(offset))
        self.assertTrue(_row(value, now=NOW.astimezone(offset))["review_ready"])

    def test_malformed_counts_booleans_hashes_and_identifiers_fail_closed(self):
        for key, bad in (("source_count", True), ("png_count", 1.0), ("approval_count", -1),
                         ("publication_count", 1_000_001), ("mock_mode", 0),
                         ("source_item_id", "private-destination"), ("content_version_id", "0" * 36),
                         ("banner_sha256", "g" * 64), ("internal_review_status", []),
                         ("generation_status", {})):
            with self.subTest(key=key):
                self.assertEqual(_row(_ready(**{key: bad}))["status"], "evidence_missing")

    def test_sensitive_or_unrecognized_fields_never_echo(self):
        for field in ("chat_id", "source_url", "telegram_copy", "token", "title"):
            row = _row(_ready(**{field: "synthetic-private-marker"}))
            self.assertEqual(row["reason_codes"], ["unexpected_observation_fields"])
            self.assertNotIn("synthetic-private-marker", json.dumps(row))
            self.assertNotIn(field, row)


if __name__ == "__main__":
    unittest.main()
