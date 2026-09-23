"""Network-disabled exact-version Typefully draft owner tests."""

import hashlib
import json
import unittest

import httpx

from core.publications.typefully_draft_once import (
    SupabaseTypefullyDraftOwner,
    TypefullyDraftOnceSettings,
    TypefullyDraftOwnerError,
    run_typefully_draft_once,
)


WORKSPACE = "11111111-1111-4111-8111-111111111111"
ITEM = "22222222-2222-4222-8222-222222222222"
VERSION = "33333333-3333-4333-8333-333333333333"
APPROVAL = "44444444-4444-4444-8444-444444444444"
ASSET = "55555555-5555-4555-8555-555555555555"
RECEIPT = "66666666-6666-4666-8666-666666666666"
MEDIA = "77777777-7777-4777-8777-777777777777"
ATTEMPT = "88888888-8888-4888-8888-888888888888"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01" * 2 + b"synthetic"
SHA = hashlib.sha256(PNG).hexdigest()
KEY = "synthetic_typefully_write_key_00000001"


def settings():
    return TypefullyDraftOnceSettings(
        enabled=True,
        supabase_url="https://synthetic.supabase.co",
        service_role_key="synthetic_service_role_key_000000001",
        workspace_id=WORKSPACE, client_id="squid", content_item_id=ITEM,
        content_version_id=VERSION, approval_id=APPROVAL,
        social_set_id=12345, api_key=KEY,
        deployed_sha="a" * 40, authorized_sha="a" * 40,
    )


def candidate():
    return {
        "workspace_id": WORKSPACE, "client_id": "squid",
        "content_item_id": ITEM, "content_version_id": VERSION,
        "approval_id": APPROVAL, "version_created_at": "2026-09-23T10:00:00Z",
        "x_copy": "정확한 테스트 공지", "asset_id": ASSET,
        "asset_sha256": SHA, "asset_byte_size": len(PNG),
        "asset_width": 1, "asset_height": 1,
        "storage_bucket": "content-studio",
        "storage_path": f"{WORKSPACE}/squid/{ASSET}/news-card.png",
    }


def media():
    return {
        "media_receipt_id": RECEIPT, "content_version_id": VERSION,
        "asset_id": ASSET, "asset_sha256": SHA,
        "uploaded_bytes_sha256": SHA, "social_set_id": 12345,
        "media_id": MEDIA,
    }


def body():
    return {
        "platforms": {"x": {"enabled": True, "posts": [{
            "text": "정확한 테스트 공지", "media_ids": [MEDIA],
        }]}},
        "draft_title": f"CoinEasy squid {VERSION}", "publish_at": None,
    }


class FakeWorld:
    def __init__(self):
        self.attempt = None
        self.db_calls = []
        self.provider_calls = []
        self.override_account = None
        self.override_reservation = None
        self.override_png = None
        self.missing_media_receipt = False
        self.timeout_draft = False

    def db(self, request):
        self.db_calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=self.override_png if self.override_png is not None else PNG)
        name = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        if name == "get_typefully_draft_attempt":
            if self.attempt is None:
                return httpx.Response(200, content=b"null",
                                      headers={"content-type": "application/json"})
            return httpx.Response(200, json=self.attempt)
        if name == "get_typefully_draft_candidate":
            return httpx.Response(200, json=candidate())
        if name == "get_typefully_media_upload_receipt":
            if self.missing_media_receipt:
                return httpx.Response(200, content=b"null",
                                      headers={"content-type": "application/json"})
            return httpx.Response(200, json=media())
        if name == "reserve_typefully_draft_once":
            assert payload["target_content_version_id"] == VERSION
            assert payload["target_approval_id"] == APPROVAL
            assert payload["target_media_receipt_id"] == RECEIPT
            self.attempt = {"attempt_id": ATTEMPT,
                            "content_version_id": VERSION,
                            "status": "delivery_unknown"}
            reservation = {
                "attempt_id": ATTEMPT, "content_version_id": VERSION,
                "approval_id": APPROVAL, "social_set_id": 12345,
                "status": "delivery_unknown", "request_body": body(),
            }
            if self.override_reservation is not None:
                reservation.update(self.override_reservation)
            return httpx.Response(200, json=reservation)
        if name == "confirm_typefully_draft_once":
            assert payload["observed_status"] == "draft"
            self.attempt["status"] = "draft_created"
            return httpx.Response(200, json={
                "attempt_id": ATTEMPT, "status": "draft_created",
                "social_set_id": 12345, "provider_draft_id": 5678,
            })
        raise AssertionError(name)

    def provider(self, request):
        self.provider_calls.append(request)
        if request.method == "GET" and request.url.path.endswith("/12345/"):
            return httpx.Response(200, json=self.override_account or {
                "id": 12345, "username": "squidkorea"})
        if request.method == "GET" and request.url.path.endswith(f"/media/{MEDIA}"):
            return httpx.Response(200, json={"media_id": MEDIA, "status": "ready"})
        if request.method == "POST" and request.url.path.endswith("/drafts"):
            if self.timeout_draft:
                raise httpx.ReadTimeout("lost provider response")
            assert json.loads(request.content) == body()
            return httpx.Response(201, json={"id": 5678,
                                             "social_set_id": 12345,
                                             "status": "draft"})
        raise AssertionError((request.method, request.url.path))


class TypefullyDraftOnceTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_off_and_release_fence(self):
        self.assertIsNone(TypefullyDraftOnceSettings.from_env({}))
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_release_fence_mismatch"):
            TypefullyDraftOnceSettings.from_env({"TYPEFULLY_DRAFT_ENABLED": "true"})
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_enable_flag_invalid"):
            TypefullyDraftOnceSettings.from_env({"TYPEFULLY_DRAFT_ENABLED": "yes"})
        disabled = TypefullyDraftOnceSettings(**{
            **vars(settings()), "enabled": False,
        })
        world = FakeWorld()
        repo = SupabaseTypefullyDraftOwner(disabled,
                                          transport=httpx.MockTransport(world.db))
        with self.assertRaisesRegex(TypefullyDraftOwnerError, "typefully_disabled"):
            await run_typefully_draft_once(
                disabled, repository=repo,
                typefully_transport=httpx.MockTransport(world.provider),
            )
        self.assertEqual(world.db_calls, [])
        self.assertEqual(world.provider_calls, [])
        wrong = TypefullyDraftOnceSettings(**{
            **vars(settings()), "authorized_sha": "b" * 40,
        })
        world = FakeWorld()
        repo = SupabaseTypefullyDraftOwner(wrong,
                                          transport=httpx.MockTransport(world.db))
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_release_fence_mismatch"):
            await run_typefully_draft_once(
                wrong, repository=repo,
                typefully_transport=httpx.MockTransport(world.provider),
            )
        self.assertEqual(world.db_calls, [])
        self.assertEqual(world.provider_calls, [])

    async def test_exact_draft_once_and_replay_has_no_provider_call(self):
        world = FakeWorld()
        repo = SupabaseTypefullyDraftOwner(settings(),
                                          transport=httpx.MockTransport(world.db))
        result = await run_typefully_draft_once(
            settings(), repository=repo,
            typefully_transport=httpx.MockTransport(world.provider),
        )
        self.assertEqual(result, {"status": "draft_created", "attempt_id": ATTEMPT,
                                  "provider_draft_id": 5678, "social_set_id": 12345})
        self.assertEqual([r.method for r in world.provider_calls], ["GET", "GET", "POST"])
        self.assertEqual(len([r for r in world.db_calls
                              if r.url.path.endswith("reserve_typefully_draft_once")]), 1)
        self.assertEqual(len([r for r in world.db_calls
                              if r.url.path.endswith("confirm_typefully_draft_once")]), 1)
        self.assertEqual(len([r for r in world.db_calls if r.method == "GET"]), 1)
        self.assertEqual(world.db_calls[0].headers.get("authorization"),
                         f"Bearer {settings().service_role_key}")
        self.assertEqual(world.provider_calls[-1].headers.get("authorization"),
                         f"Bearer {KEY}")
        prior_calls = len(world.provider_calls)
        second = await run_typefully_draft_once(
            settings(), repository=repo,
            typefully_transport=httpx.MockTransport(world.provider),
        )
        self.assertEqual(second["status"], "already_reserved")
        self.assertEqual(len(world.provider_calls), prior_calls)

    async def test_wrong_account_or_reservation_body_never_posts(self):
        world = FakeWorld()
        world.override_account = {"id": 12345, "username": "other"}
        repo = SupabaseTypefullyDraftOwner(settings(),
                                          transport=httpx.MockTransport(world.db))
        with self.assertRaises(ValueError):
            await run_typefully_draft_once(
                settings(), repository=repo,
                typefully_transport=httpx.MockTransport(world.provider),
            )
        self.assertEqual([r.method for r in world.provider_calls], ["GET"])
        self.assertIsNone(world.attempt)

        world = FakeWorld()
        world.override_reservation = {"request_body": {
            **body(), "publish_at": "now",
        }}
        repo = SupabaseTypefullyDraftOwner(settings(),
                                          transport=httpx.MockTransport(world.db))
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_reservation_mismatch"):
            await run_typefully_draft_once(
                settings(), repository=repo,
                typefully_transport=httpx.MockTransport(world.provider),
            )
        self.assertEqual([r.method for r in world.provider_calls], ["GET", "GET"])
        self.assertEqual(world.attempt["status"], "delivery_unknown")

    async def test_lost_draft_response_leaves_unknown_and_no_retry(self):
        world = FakeWorld()
        world.timeout_draft = True
        repo = SupabaseTypefullyDraftOwner(settings(),
                                          transport=httpx.MockTransport(world.db))
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_draft_create_unknown"):
            await run_typefully_draft_once(
                settings(), repository=repo,
                typefully_transport=httpx.MockTransport(world.provider),
            )
        self.assertEqual(world.attempt["status"], "delivery_unknown")
        self.assertEqual([r.method for r in world.provider_calls], ["GET", "GET", "POST"])
        second = await run_typefully_draft_once(
            settings(), repository=repo,
            typefully_transport=httpx.MockTransport(world.provider),
        )
        self.assertEqual(second["status"], "already_reserved")
        self.assertEqual(len(world.provider_calls), 3)

    async def test_missing_receipt_or_changed_banner_never_reserves(self):
        for missing_receipt, changed_png in ((True, None), (False, PNG + b"changed")):
            with self.subTest(missing_receipt=missing_receipt):
                world = FakeWorld()
                world.missing_media_receipt = missing_receipt
                world.override_png = changed_png
                repo = SupabaseTypefullyDraftOwner(
                    settings(), transport=httpx.MockTransport(world.db),
                )
                with self.assertRaises(TypefullyDraftOwnerError):
                    await run_typefully_draft_once(
                        settings(), repository=repo,
                        typefully_transport=httpx.MockTransport(world.provider),
                    )
                self.assertIsNone(world.attempt)
                self.assertNotIn("POST", [request.method for request in world.provider_calls])


if __name__ == "__main__":
    unittest.main()
