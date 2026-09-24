"""Pure exact-version Typefully draft preparation; no provider or DB calls."""

import copy
import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from core.publications.handoff import CLIENT_TARGETS, PublicationHandoffError
from core.publications.typefully_draft_preparation import (
    TypefullyDraftPreparationError,
    bind_typefully_media,
    prepare_typefully_draft,
)
from tests.test_publication_handoff import NOW, _id, _snapshot


MEDIA_ID = "99999999-9999-4999-8999-999999999999"


def evidence(client="squid"):
    provider_media = {
        "source": "typefully_media_get_v2", "media_id": MEDIA_ID,
        "social_set_id": 12345, "status": "ready", "observed_at": NOW.isoformat(),
    }
    owner_upload = {
        "source": "durable_typefully_media_owner_v1", "media_id": MEDIA_ID,
        "social_set_id": 12345, "content_version_id": _id(3),
        "asset_sha256": "c" * 64, "uploaded_bytes_sha256": "c" * 64,
    }
    return (
        {"source": "typefully_social_set_get_v2", "social_set_id": 12345,
         "x_username": CLIENT_TARGETS[client][1], "observed_at": NOW.isoformat()},
        bind_typefully_media(owner_upload, provider_media),
    )


class TypefullyDraftPreparationTests(unittest.TestCase):
    def test_provider_ready_does_not_override_wrong_owner_bytes(self):
        owner = {
            "source": "durable_typefully_media_owner_v1", "media_id": MEDIA_ID,
            "social_set_id": 12345, "content_version_id": _id(3),
            "asset_sha256": "c" * 64, "uploaded_bytes_sha256": "d" * 64,
        }
        provider = {
            "source": "typefully_media_get_v2", "media_id": MEDIA_ID,
            "social_set_id": 12345, "status": "ready", "observed_at": NOW.isoformat(),
        }
        with self.assertRaisesRegex(TypefullyDraftPreparationError, "media_owner_invalid"):
            bind_typefully_media(owner, provider)
        owner["uploaded_bytes_sha256"] = owner["asset_sha256"]
        provider["media_id"] = _id(99)
        with self.assertRaisesRegex(TypefullyDraftPreparationError, "media_readback_invalid"):
            bind_typefully_media(owner, provider)

    def test_all_clients_bind_exact_copy_banner_and_inert_draft(self):
        for client in CLIENT_TARGETS:
            with self.subTest(client=client):
                snapshot = _snapshot(client)
                original = copy.deepcopy(snapshot)
                account, media = evidence(client)
                result = prepare_typefully_draft(
                    snapshot, social_set=account, media=media, now=NOW,
                )
                prepared = result["preparation"]
                body = prepared["request_body"]
                self.assertNotIn("publish_at", body)
                self.assertNotIn("plan_at", body)
                self.assertEqual(body["platforms"], {"x": {
                    "enabled": True,
                    "posts": [{"text": snapshot["channel_copy"]["x"],
                               "media_ids": [MEDIA_ID]}],
                }})
                self.assertEqual(prepared["content_version_id"], _id(3))
                self.assertEqual(prepared["approval_id"], _id(5))
                self.assertEqual(prepared["asset_sha256"], "c" * 64)
                self.assertEqual(prepared["x_username"], CLIENT_TARGETS[client][1])
                self.assertFalse(prepared["execution_authorized"])
                self.assertFalse(prepared["provider_attempted"])
                self.assertTrue(prepared["durable_attempt_fence_required"])
                encoded = json.dumps(prepared, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()
                self.assertEqual(result["preparation_sha256"], hashlib.sha256(encoded).hexdigest())
                self.assertEqual(snapshot, original)

    def test_wrong_account_media_or_version_fails_closed(self):
        changes = (
            ("account", "x_username", "other_account", "account_mismatch"),
            ("account", "social_set_id", 999, "media_binding_mismatch"),
            ("media", "asset_sha256", "d" * 64, "media_binding_mismatch"),
            ("media", "content_version_id", _id(99), "media_binding_mismatch"),
            ("media", "status", "processing", "media_binding_mismatch"),
            ("media", "media_id", "not-a-uuid", "media_invalid"),
        )
        for target, key, value, code in changes:
            account, media = evidence()
            (account if target == "account" else media)[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                TypefullyDraftPreparationError, code,
            ):
                prepare_typefully_draft(_snapshot(), social_set=account,
                                        media=media, now=NOW)

    def test_stale_or_future_account_and_unknown_fields_fail_closed(self):
        for stamp in (NOW - timedelta(minutes=16), NOW + timedelta(seconds=1)):
            account, media = evidence()
            account["observed_at"] = stamp.isoformat()
            with self.subTest(stamp=stamp), self.assertRaisesRegex(
                TypefullyDraftPreparationError, "account_readback_stale",
            ):
                prepare_typefully_draft(_snapshot(), social_set=account,
                                        media=media, now=NOW)
        account, media = evidence()
        account["private_url"] = "not accepted"
        with self.assertRaisesRegex(TypefullyDraftPreparationError,
                                    "account_evidence_invalid"):
            prepare_typefully_draft(_snapshot(), social_set=account, media=media, now=NOW)
        for stamp in (NOW - timedelta(minutes=16), NOW + timedelta(seconds=1)):
            account, media = evidence()
            media["observed_at"] = stamp.isoformat()
            with self.subTest(media_stamp=stamp), self.assertRaisesRegex(
                TypefullyDraftPreparationError, "media_evidence_invalid",
            ):
                prepare_typefully_draft(_snapshot(), social_set=account,
                                        media=media, now=NOW)

    def test_revised_or_long_copy_requires_new_human_review(self):
        account, media = evidence()
        revised = _snapshot()
        revised["current_version_id"] = _id(99)
        with self.assertRaisesRegex(PublicationHandoffError, "exact_version"):
            prepare_typefully_draft(revised, social_set=account, media=media, now=NOW)
        long_copy = _snapshot()
        long_copy["channel_copy"]["x"] = "가" * 281
        with self.assertRaisesRegex(TypefullyDraftPreparationError,
                                    "exact_x_copy_requires_revision"):
            prepare_typefully_draft(long_copy, social_set=account, media=media, now=NOW)

    def test_no_filesystem_environment_or_network_io(self):
        account, media = evidence()
        with patch("builtins.open", side_effect=AssertionError("file")), \
             patch("pathlib.Path.open", side_effect=AssertionError("file")), \
             patch("socket.socket.connect", side_effect=AssertionError("network")), \
             patch("os.getenv", side_effect=AssertionError("environment")):
            prepare_typefully_draft(_snapshot(), social_set=account, media=media, now=NOW)


if __name__ == "__main__":
    unittest.main()
