"""Typefully GET projections with synthetic responses and no network access."""

import unittest
from datetime import datetime, timezone

from core.publications.handoff import CLIENT_TARGETS
from core.publications.typefully_draft_preparation import (
    bind_typefully_media,
    prepare_typefully_draft,
)
from core.publications.typefully_readback import (
    TypefullyReadbackError,
    read_typefully_media,
    read_typefully_social_set,
)
from tests.test_publication_handoff import _id, _snapshot


NOW = datetime(2026, 9, 23, 15, tzinfo=timezone.utc)
KEY = "synthetic_typefully_read_only_key_0001"
MEDIA_ID = "99999999-9999-4999-8999-999999999999"


class Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get(self, url, *, headers):
        self.calls.append((url, headers))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TypefullyReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_readbacks_bind_to_exact_approved_draft_without_post(self):
        account_client = FakeClient(Response(200, {
            "id": 12345, "username": "squidkorea",
        }))
        media_client = FakeClient(Response(200, {
            "media_id": MEDIA_ID, "status": "ready",
        }))
        account = await read_typefully_social_set(
            social_set_id=12345, client_id="squid", api_key=KEY,
            client=account_client, clock=lambda: NOW,
        )
        media_status = await read_typefully_media(
            social_set_id=12345, media_id=MEDIA_ID, api_key=KEY,
            client=media_client, clock=lambda: NOW,
        )
        bound_media = bind_typefully_media({
            "source": "durable_typefully_media_owner_v1",
            "media_id": MEDIA_ID, "social_set_id": 12345,
            "content_version_id": _id(3), "asset_sha256": "c" * 64,
            "uploaded_bytes_sha256": "c" * 64,
        }, media_status)
        prepared = prepare_typefully_draft(
            _snapshot(), social_set=account, media=bound_media, now=NOW,
        )["preparation"]
        self.assertEqual(prepared["request_body"]["publish_at"], None)
        self.assertEqual(prepared["request_body"]["platforms"]["x"]["posts"][0]["media_ids"],
                         [MEDIA_ID])
        self.assertFalse(prepared["execution_authorized"])
        self.assertEqual(len(account_client.calls), 1)
        self.assertEqual(len(media_client.calls), 1)

    async def test_all_client_accounts_require_expected_x_username(self):
        for client_id, (_, x_username, _) in CLIENT_TARGETS.items():
            with self.subTest(client_id=client_id):
                fake = FakeClient(Response(200, {
                    "id": 12345, "username": x_username.upper(),
                    "private_url": "never echo this", "platforms": {"other": {}},
                }))
                result = await read_typefully_social_set(
                    social_set_id=12345, client_id=client_id, api_key=KEY,
                    client=fake, clock=lambda: NOW,
                )
                self.assertEqual(result["source"], "typefully_social_set_get_v2")
                self.assertEqual(result["social_set_id"], 12345)
                self.assertEqual(result["x_username"], x_username.upper())
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(fake.calls[0][0],
                                 "https://api.typefully.com/v2/social-sets/12345/")
                self.assertEqual(fake.calls[0][1], {"Authorization": f"Bearer {KEY}"})
                self.assertNotIn("private_url", str(result))

    async def test_account_mismatch_and_unknown_response_are_fixed_errors(self):
        for payload, code in (
            ({"id": 12345, "username": "wrong"}, "x_account_mismatch"),
            ({"id": 999, "username": "squidkorea"}, "x_account_mismatch"),
            ({"id": 12345, "username": 42}, "account_readback_invalid"),
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                TypefullyReadbackError, code,
            ):
                await read_typefully_social_set(
                    social_set_id=12345, client_id="squid", api_key=KEY,
                    client=FakeClient(Response(200, payload)), clock=lambda: NOW,
                )
        for response in (Response(429, {"secret": "PRIVATE"}),
                         RuntimeError("PRIVATE")):
            with self.subTest(response=response), self.assertRaisesRegex(
                TypefullyReadbackError, "account_readback_unavailable",
            ) as caught:
                await read_typefully_social_set(
                    social_set_id=12345, client_id="squid", api_key=KEY,
                    client=FakeClient(response), clock=lambda: NOW,
                )
            self.assertNotIn("PRIVATE", str(caught.exception))

    async def test_media_readback_requires_exact_ready_id(self):
        fake = FakeClient(Response(200, {
            "media_id": MEDIA_ID, "status": "ready", "media_urls": {"original": "private"},
        }))
        result = await read_typefully_media(
            social_set_id=12345, media_id=MEDIA_ID, api_key=KEY,
            client=fake, clock=lambda: NOW,
        )
        self.assertEqual(result, {
            "source": "typefully_media_get_v2", "social_set_id": 12345,
            "media_id": MEDIA_ID, "status": "ready", "observed_at": NOW.isoformat(),
        })
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][0],
                         f"https://api.typefully.com/v2/social-sets/12345/media/{MEDIA_ID}")
        self.assertNotIn("private", str(result))
        for payload, code in (
            ({"media_id": MEDIA_ID, "status": "processing"}, "media_not_ready"),
            ({"media_id": "other", "status": "ready"}, "media_readback_invalid"),
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                TypefullyReadbackError, code,
            ):
                await read_typefully_media(
                    social_set_id=12345, media_id=MEDIA_ID, api_key=KEY,
                    client=FakeClient(Response(200, payload)), clock=lambda: NOW,
                )

    async def test_invalid_identity_or_key_stops_before_transport(self):
        for kwargs in (
            {"social_set_id": True}, {"client_id": "unknown"},
            {"api_key": "short"}, {"clock": 123},
        ):
            fake = FakeClient(Response(200, {}))
            inputs = {"social_set_id": 12345, "client_id": "squid",
                      "api_key": KEY, "clock": lambda: NOW, "client": fake}
            inputs.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(TypefullyReadbackError):
                await read_typefully_social_set(**inputs)
            self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
