from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.publications.handoff import (
    CLIENT_TARGETS, PublicationHandoffError, build_publication_handoff,
)


NOW = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)


def _id(number):
    return f"00000000-0000-4000-8000-{number:012d}"


def _snapshot(client="squid"):
    binding = {"workspace_id": _id(1), "content_item_id": _id(2), "content_version_id": _id(3)}
    return {
        **binding, "client_id": client, "client_active": True,
        "current_version_id": _id(3), "content_kind": "daily_news",
        "content_status": "approved", "mock_mode": False,
        "observed_at": NOW.isoformat(), "version_created_at": (NOW - timedelta(hours=1)).isoformat(),
        "primary_asset_id": _id(4), "existing_publication_count": 0,
        "channel_copy": {"telegram": "  승인된 공식 업데이트입니다.\n\n원문과 배너를 확인해 주세요.  ", "x": "검토 완료 업데이트 🚀 #Web3"},
        "latest_approval": {
            **binding, "approval_id": _id(5), "decision": "approved", "reviewer_source": "studio_session",
            "review_sequence": 2, "reviewed_at": (NOW - timedelta(minutes=30)).isoformat(),
            "fact_check_policy_version": "double-fact-check@1",
            "source_facts_verified": True, "output_claims_verified": True,
        },
        "fact_check": {
            "schema_version": "1.0", "policy_version": "double-fact-check@1", "content_kind": "daily_news",
            "human_review_required": True, "status": "review", "input_sha256": "a" * 64,
            "output_sha256": "b" * 64,
            "checks": [
                {"id": "source_evidence", "status": "pass", "label": "Source", "detail": "Recorded source anchors.", "metrics": {"source_characters": 150}},
                {"id": "output_claims", "status": "review", "label": "Output", "detail": "Human check required.", "metrics": {"artifact_count": 1, "brand_qa_status": None}},
            ],
        },
        "asset": {
            **binding, "client_id": client, "asset_id": _id(4), "asset_kind": "png", "mime_type": "image/png",
            "storage_bucket": "content-studio", "storage_path": f"{_id(1)}/{client}/{_id(4)}/news-card.png",
            "filename": "news-card.png", "sha256": "c" * 64, "byte_size": 4096,
            "width": 1080, "height": 1080, "asset_count": 1, "stored": True,
        },
        "source": {
            "workspace_id": _id(1), "client_id": client, "source_item_id": _id(6), "source_type": "tweet",
            "canonical_url": f"https://x.com/{CLIENT_TARGETS[client][2]}/status/123456789",
            "published_at": (NOW - timedelta(hours=2)).isoformat(), "position": 0,
        },
    }


class PublicationHandoffTests(unittest.TestCase):
    def test_timestamp_strings_reject_normalized_offsets_and_noniso_shapes(self):
        paths = (("observed_at",), ("version_created_at",),
                 ("latest_approval", "reviewed_at"), ("source", "published_at"))
        for path in paths:
            for stamp in ("2026-09-06T11:00:00+00:60", "2026-09-06T09:00:00-00:60",
                          "2026-09-07T10:00:00+24:00", "2026-09-06T10:00:00+00:00:00",
                          "2026-09-06 10:00:00+00:00"):
                snapshot = _snapshot()
                target = snapshot if len(path) == 1 else snapshot[path[0]]
                target[path[-1]] = stamp
                with self.subTest(path=path, stamp=stamp), self.assertRaisesRegex(
                    PublicationHandoffError, "handoff_timestamp_invalid"
                ):
                    build_publication_handoff(snapshot, now=NOW)

    def test_valid_timezone_offsets_z_and_aware_datetime_inputs_are_preserved(self):
        paths = (("observed_at",), ("version_created_at",),
                 ("latest_approval", "reviewed_at"), ("source", "published_at"))
        expected = build_publication_handoff(_snapshot(), now=NOW)
        for representation in ("offset", "z", "datetime"):
            snapshot = _snapshot()
            for path in paths:
                target = snapshot if len(path) == 1 else snapshot[path[0]]
                stamp = datetime.fromisoformat(target[path[-1]])
                target[path[-1]] = (stamp.astimezone(timezone(timedelta(hours=9))).isoformat()
                    if representation == "offset" else stamp.isoformat().replace("+00:00", "Z")
                    if representation == "z" else stamp)
            self.assertEqual(build_publication_handoff(snapshot, now=NOW), expected)

    def test_four_client_policy_has_no_execution_authority(self):
        for client in CLIENT_TARGETS:
            with self.subTest(client=client):
                packet = build_publication_handoff(_snapshot(client), now=NOW)["packet"]
                tg, x, _ = CLIENT_TARGETS[client]
                self.assertEqual(packet["targets"], {"telegram": tg, "x": x})
                cap = packet["capabilities"]
                self.assertEqual(cap["telegram"]["adapter"], "exact_version_implemented" if client == "squid" else "adapter_missing")
                self.assertEqual(cap["typefully"]["adapter"], "exact_version_adapter_missing")
                self.assertIsNone(cap["typefully"]["publish_at"])
                self.assertFalse(cap["telegram"]["execution_authorized"])
                self.assertFalse(cap["typefully"]["execution_authorized"])
                for flag in ("live_readback_verified", "asset_bytes_verified", "automatic_approval", "automatic_publication", "queue_created", "execution_authorized"):
                    self.assertFalse(packet["safety"][flag])
                self.assertTrue(packet["safety"]["final_human_publication_approval_required"])

    def test_fixed_public_targets_match_existing_policy_not_typefully_labels(self):
        root = Path(__file__).resolve().parents[1]
        sql = (root / "supabase/migrations/20260727143000_content_performance_promotions.sql").read_text()
        settings = (root / "core/publications/settings.py").read_text()
        for client, (telegram, x, _) in CLIENT_TARGETS.items():
            self.assertIn(f"when '{client}' then '{x}'", sql)
            self.assertIn(f'"{client}": "{telegram}"', settings)
        self.assertNotEqual(CLIENT_TARGETS["squid"][1], "squid_kr")

    def test_public_target_registry_cannot_be_mutated(self):
        with self.assertRaises(TypeError):
            CLIENT_TARGETS["squid"] = ("other", "other", "other")

    def test_exact_copy_is_never_rewritten_and_hash_is_reproducible(self):
        data = _snapshot()
        before = copy.deepcopy(data)
        result = build_publication_handoff(data, now=NOW)
        self.assertEqual(result["packet"]["channel_copy"], data["channel_copy"])
        encoded = json.dumps(result["packet"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertEqual(result["packet_sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(result, build_publication_handoff(dict(reversed(list(data.items()))), now=NOW + timedelta(minutes=1)))
        self.assertEqual(data, before)

    def test_package_builder_performs_no_io(self):
        with patch("builtins.open", side_effect=AssertionError("filesystem")), \
             patch("pathlib.Path.open", side_effect=AssertionError("filesystem")), \
             patch("socket.socket.connect", side_effect=AssertionError("network")), \
             patch("os.getenv", side_effect=AssertionError("environment")):
            build_publication_handoff(_snapshot(), now=NOW)

    def test_revision_and_cross_workspace_or_item_approval_is_rejected(self):
        for field in ("workspace_id", "content_item_id", "content_version_id"):
            value = _snapshot()
            value["latest_approval"][field] = _id(99)
            with self.subTest(field=field), self.assertRaisesRegex(PublicationHandoffError, "exact_version"):
                build_publication_handoff(value, now=NOW)
        value = _snapshot()
        value["current_version_id"] = _id(99)
        with self.assertRaisesRegex(PublicationHandoffError, "exact_version"):
            build_publication_handoff(value, now=NOW)

    def test_each_human_attestation_is_required_and_qa_cannot_substitute(self):
        for field, bad in (("source_facts_verified", False), ("output_claims_verified", None),
                           ("source_facts_verified", 1), ("decision", "PASS"),
                           ("decision", "rejected"), ("reviewer_source", "codex:content-qa"),
                           ("fact_check_policy_version", "other"), ("review_sequence", 0)):
            value = _snapshot()
            value["latest_approval"][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)
        for field in ("source_facts_verified", "output_claims_verified", "approval_id"):
            value = _snapshot()
            value["latest_approval"].pop(field)
            with self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)

    def test_fact_check_baseline_cannot_be_missing_blocked_or_mismatched(self):
        changes = [("status", "blocked"), ("status", "pass"), ("content_kind", "article"),
                   ("human_review_required", False), ("input_sha256", "bad"), ("checks", []),
                   ("policy_version", "other")]
        for field, bad in changes:
            value = _snapshot()
            value["fact_check"][field] = bad
            with self.subTest(field=field), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)
        for bad in (float("nan"), float("inf"), {}, [1]):
            value = _snapshot()
            value["fact_check"]["checks"][0]["metrics"]["bad_metric"] = bad
            with self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)

    def test_pending_mock_non_daily_inactive_and_existing_publication_rejected(self):
        for field, bad in (("content_status", "needs_review"), ("mock_mode", True), ("client_active", False),
                           ("content_kind", "article"), ("existing_publication_count", 1),
                           ("existing_publication_count", False), ("client_id", "unknown")):
            value = _snapshot()
            value[field] = bad
            with self.subTest(field=field), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)

    def test_canonical_exact_png_metadata_required(self):
        for field, bad in (("client_id", "yellow"), ("content_version_id", _id(99)),
                           ("asset_id", _id(99)), ("asset_count", 2), ("asset_count", True),
                           ("stored", False), ("sha256", "bad"), ("asset_kind", "svg"),
                           ("mime_type", "image/jpeg"), ("storage_bucket", "public"),
                           ("storage_path", "elsewhere/news-card.png"), ("filename", "other.png"),
                           ("byte_size", 10 * 1024 * 1024 + 1), ("width", 0), ("height", 10001)):
            value = _snapshot()
            value["asset"][field] = bad
            with self.subTest(field=field), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)

    def test_future_naive_or_out_of_order_timestamps_rejected(self):
        for field, bad in (("observed_at", NOW + timedelta(seconds=1)),
                           ("version_created_at", NOW.replace(tzinfo=None)),
                           ("version_created_at", NOW - timedelta(minutes=10))):
            value = _snapshot()
            value[field] = bad
            with self.subTest(field=field), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)
        value = _snapshot()
        value["latest_approval"]["reviewed_at"] = NOW + timedelta(seconds=1)
        with self.assertRaises(PublicationHandoffError):
            build_publication_handoff(value, now=NOW)
        for now in (None, NOW.isoformat(), NOW.replace(tzinfo=None)):
            with self.assertRaises(PublicationHandoffError):
                build_publication_handoff(_snapshot(), now=now)

    def test_approved_publication_does_not_inherit_intake_source_expiry(self):
        value = _snapshot()
        value["source"]["published_at"] = NOW - timedelta(days=7)
        packet = build_publication_handoff(value, now=NOW)["packet"]
        self.assertEqual(packet["safety"]["source_freshness_policy"], "human_approved_publication_no_fixed_age_limit")
        self.assertFalse(packet["safety"]["live_readback_verified"])

    def test_official_source_must_match_client_workspace_and_precede_version(self):
        for field, bad in (("canonical_url", "https://x.com/Yellow/status/123456789"),
                           ("canonical_url", "https://x.com/SquidRouter/status/123?token=secret"),
                           ("position", 1), ("source_type", "chat"), ("workspace_id", _id(99)),
                           ("client_id", "yellow"), ("published_at", NOW), ("source_item_id", "private")):
            value = _snapshot()
            value["source"][field] = bad
            with self.subTest(field=field), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)

    def test_destination_schedule_or_credentials_injection_rejected_everywhere(self):
        for field in ("destination", "chat_id", "bot_token", "social_set_id", "publish_at", "execution_authorized"):
            for nest in (None, "latest_approval", "asset", "source", "channel_copy", "fact_check"):
                value = _snapshot()
                target = value if nest is None else value[nest]
                target[field] = "synthetic-private-marker"
                with self.subTest(field=field, nest=nest), self.assertRaises(PublicationHandoffError) as error:
                    build_publication_handoff(value, now=NOW)
                self.assertNotIn("synthetic-private-marker", str(error.exception))

    def test_private_markers_controls_and_malformed_copy_are_rejected(self):
        for text in ("", " ", "t.me/+" + "synthetic_private_invite", "authorization: synthetic", "text\x00", "bad\ud800", "x" * 16001):
            value = _snapshot()
            value["channel_copy"]["telegram"] = text
            with self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)

    def test_long_captions_are_reported_not_truncated_or_authorized(self):
        value = _snapshot()
        value["channel_copy"]["telegram"] = "한" * 1025
        value["channel_copy"]["x"] = "검토된 원문 " * 100
        packet = build_publication_handoff(value, now=NOW)["packet"]
        self.assertEqual(packet["channel_copy"], value["channel_copy"])
        self.assertEqual(packet["capabilities"]["telegram"]["payload"], "caption_limit_exceeded")
        self.assertFalse(packet["capabilities"]["telegram"]["execution_authorized"])

    def test_hash_changes_with_exact_copy_banner_or_approval(self):
        baseline = build_publication_handoff(_snapshot(), now=NOW)["packet_sha256"]
        for mutation in (
            lambda data: data["channel_copy"].update(x="변경된 승인본"),
            lambda data: data["asset"].update(sha256="d" * 64),
            lambda data: data["latest_approval"].update(approval_id=_id(99)),
        ):
            value = _snapshot()
            mutation(value)
            self.assertNotEqual(build_publication_handoff(value, now=NOW)["packet_sha256"], baseline)

    def test_caption_count_matches_existing_python_and_sql_codepoint_policy(self):
        for length in (1024, 1025):
            value = _snapshot()
            value["channel_copy"]["telegram"] = "🚀" * length
            packet = build_publication_handoff(value, now=NOW)["packet"]
            self.assertEqual(packet["channel_copy"]["telegram"], value["channel_copy"]["telegram"])
            self.assertEqual(packet["capabilities"]["telegram"]["payload"],
                             "within_caption_limit" if length == 1024 else "caption_limit_exceeded")

    def test_missing_or_extra_root_and_nested_fields_fail_closed(self):
        for key in _snapshot():
            value = _snapshot()
            value.pop(key)
            with self.subTest(key=key), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(value, now=NOW)
        for bad in (None, [], "private"):
            with self.assertRaises(PublicationHandoffError) as error:
                build_publication_handoff(bad, now=NOW)
            self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
