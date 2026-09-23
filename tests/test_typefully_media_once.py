"""Network-disabled, exact-version Typefully media allocation owner tests."""

import json
import unittest

import httpx

from core.publications.typefully_media_once import (
    SupabaseTypefullyMediaOwner,
    TypefullyMediaOnceSettings,
    run_typefully_media_once,
)
from core.publications.typefully_draft_once import TypefullyDraftOwnerError
from core.publications.typefully_media_upload import TypefullyMediaUploadError
from tests.test_typefully_draft_once import (
    APPROVAL, ASSET, ATTEMPT, ITEM, KEY, MEDIA, PNG, RECEIPT, SHA, VERSION,
    WORKSPACE, candidate,
)
from tests.test_typefully_media_upload import UPLOAD_URL


def settings():
    return TypefullyMediaOnceSettings(
        enabled=True, supabase_url="https://synthetic.supabase.co",
        service_role_key="synthetic_service_role_key_000000001",
        workspace_id=WORKSPACE, client_id="squid", content_item_id=ITEM,
        content_version_id=VERSION, approval_id=APPROVAL,
        social_set_id=12345, api_key=KEY,
        deployed_sha="a" * 40, authorized_sha="a" * 40,
    )


class MediaWorld:
    def __init__(self):
        self.events = []
        self.attempt = None
        self.receipt = None
        self.allocation_timeout = False
        self.intent_timeout = False
        self.put_timeout = False
        self.receipt_timeout = False
        self.bad_account = False

    def db(self, request):
        if request.method == "GET":
            self.events.append("storage_get")
            return httpx.Response(200, content=PNG,
                                  headers={"content-type": "image/png"})
        name = request.url.path.rsplit("/", 1)[-1]
        self.events.append(name)
        payload = json.loads(request.content)
        if name == "get_typefully_media_allocation_attempt":
            return (httpx.Response(200, json=self.attempt) if self.attempt is not None
                    else httpx.Response(200, content=b"null"))
        if name == "get_typefully_draft_candidate":
            return httpx.Response(200, json=candidate())
        if name == "reserve_typefully_media_allocation_once":
            assert payload["target_approval_id"] == APPROVAL
            self.attempt = {
                "attempt_id": ATTEMPT, "content_version_id": VERSION,
                "approval_id": APPROVAL, "asset_id": ASSET,
                "asset_sha256": SHA, "social_set_id": 12345,
                "status": "allocation_unknown", "media_id": None,
            }
            return httpx.Response(200, json=self.attempt)
        if name == "mark_typefully_media_upload_intent":
            assert payload["target_attempt_id"] == ATTEMPT
            assert payload["observed_media_id"] == MEDIA
            self.attempt["status"] = "upload_unknown"
            self.attempt["media_id"] = MEDIA
            if self.intent_timeout:
                raise httpx.ReadTimeout("lost intent acknowledgement")
            return httpx.Response(200, json=self.attempt)
        if name == "record_typefully_media_upload":
            assert payload["target_allocation_attempt_id"] == ATTEMPT
            assert payload["target_uploaded_bytes_sha256"] == SHA
            self.attempt["status"] = "uploaded"
            self.receipt = {
                "media_receipt_id": RECEIPT,
                "content_version_id": VERSION,
                "asset_id": ASSET, "asset_sha256": SHA,
                "uploaded_bytes_sha256": SHA,
                "social_set_id": 12345, "media_id": MEDIA,
            }
            if self.receipt_timeout:
                raise httpx.ReadTimeout("lost receipt acknowledgement")
            return httpx.Response(200, json=RECEIPT)
        if name == "get_typefully_media_upload_receipt":
            return httpx.Response(200, json=self.receipt)
        raise AssertionError(name)

    def provider(self, request):
        if request.method == "GET":
            self.events.append("account_get")
            return httpx.Response(200, json={
                "id": 12345, "username": "wrong" if self.bad_account else "squidkorea",
            })
        if request.method == "POST" and request.url.path.endswith("/media/upload"):
            self.events.append("allocation_post")
            if self.allocation_timeout:
                raise httpx.ReadTimeout("lost allocation response")
            return httpx.Response(201, json={
                "media_id": MEDIA, "upload_url": UPLOAD_URL,
            })
        raise AssertionError((request.method, request.url.path))

    def s3(self, request):
        self.events.append("s3_put")
        assert request.method == "PUT" and request.content == PNG
        assert "authorization" not in request.headers
        if self.put_timeout:
            raise httpx.ReadTimeout("lost PUT response")
        return httpx.Response(204)


async def run(world, selected=None):
    config = settings() if selected is None else selected
    repo = SupabaseTypefullyMediaOwner(config, transport=httpx.MockTransport(world.db))
    return await run_typefully_media_once(
        config, repository=repo,
        typefully_transport=httpx.MockTransport(world.provider),
        upload_transport=httpx.MockTransport(world.s3),
    )


class TypefullyMediaOnceTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_off_and_release_fence_have_zero_io(self):
        self.assertIsNone(TypefullyMediaOnceSettings.from_env({}))
        world = MediaWorld()
        disabled = TypefullyMediaOnceSettings(**{**vars(settings()), "enabled": False})
        with self.assertRaisesRegex(TypefullyDraftOwnerError, "typefully_media_disabled"):
            await run(world, disabled)
        mismatch = TypefullyMediaOnceSettings(**{
            **vars(settings()), "authorized_sha": "b" * 40,
        })
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_media_release_fence_mismatch"):
            await run(world, mismatch)
        self.assertEqual(world.events, [])

    async def test_one_allocation_one_put_and_replay_inert(self):
        world = MediaWorld()
        result = await run(world)
        self.assertEqual(result, {
            "status": "uploaded", "attempt_id": ATTEMPT,
            "media_receipt_id": RECEIPT, "media_id": MEDIA,
        })
        self.assertLess(world.events.index("reserve_typefully_media_allocation_once"),
                        world.events.index("allocation_post"))
        self.assertLess(world.events.index("mark_typefully_media_upload_intent"),
                        world.events.index("s3_put"))
        self.assertLess(world.events.index("s3_put"),
                        world.events.index("record_typefully_media_upload"))
        replay = await run(world)
        self.assertEqual(replay["status"], "already_reserved")
        self.assertEqual(world.events.count("allocation_post"), 1)
        self.assertEqual(world.events.count("s3_put"), 1)

    async def test_wrong_account_stops_before_reservation(self):
        world = MediaWorld()
        world.bad_account = True
        with self.assertRaises(ValueError):
            await run(world)
        self.assertIsNone(world.attempt)
        self.assertNotIn("allocation_post", world.events)

    async def test_ambiguous_allocation_intent_put_or_receipt_never_retries(self):
        for lost, expected_status, expected_put in (
            ("allocation_timeout", "allocation_unknown", False),
            ("intent_timeout", "upload_unknown", False),
            ("put_timeout", "upload_unknown", True),
            ("receipt_timeout", "uploaded", True),
        ):
            with self.subTest(lost=lost):
                world = MediaWorld()
                setattr(world, lost, True)
                with self.assertRaises((TypefullyMediaUploadError,
                                        TypefullyDraftOwnerError)):
                    await run(world)
                self.assertEqual(world.attempt["status"], expected_status)
                self.assertEqual("s3_put" in world.events, expected_put)
                prior_posts = world.events.count("allocation_post")
                prior_puts = world.events.count("s3_put")
                replay = await run(world)
                self.assertEqual(replay["status"], "already_reserved")
                self.assertEqual(world.events.count("allocation_post"), prior_posts)
                self.assertEqual(world.events.count("s3_put"), prior_puts)


if __name__ == "__main__":
    unittest.main()
