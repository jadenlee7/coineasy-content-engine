"""Network-disabled daily Typefully slot and orchestration tests."""

import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from core.publications.typefully_daily import (
    SupabaseTypefullyDailySelector,
    TypefullyDailySettings,
    run_typefully_daily,
)
from core.publications.typefully_draft_once import TypefullyDraftOwnerError
from tests.test_typefully_draft_once import APPROVAL, ITEM, KEY, VERSION, WORKSPACE


SLOT = "99999999-9999-4999-8999-999999999999"


def settings(clients=("yellow", "babylon")):
    return TypefullyDailySettings(
        enabled=True, supabase_url="https://synthetic.supabase.co",
        service_role_key="synthetic_service_role_key_000000001",
        workspace_id=WORKSPACE, api_key=KEY, clients=clients,
        social_sets={"yellow": 12345, "babylon": 67890},
        deployed_sha="a" * 40, authorized_sha="a" * 40,
    )


def slot(client_id):
    return {
        "slot_id": SLOT, "workspace_id": WORKSPACE,
        "client_id": client_id,
        "kst_date": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "content_item_id": ITEM, "content_version_id": VERSION,
        "approval_id": APPROVAL, "reused": False,
    }


class FakeSelector:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    async def claim(self, client_id):
        self.calls.append(client_id)
        return self.candidates.get(client_id)


class TypefullyDailyTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_off_and_release_mismatch_have_zero_io(self):
        self.assertIsNone(TypefullyDailySettings.from_env({}))
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_daily_release_fence_mismatch"):
            TypefullyDailySettings.from_env({"TYPEFULLY_DAILY_ENABLED": "true"})
        selector = FakeSelector({"yellow": slot("yellow")})
        disabled = TypefullyDailySettings(**{**vars(settings()), "enabled": False})
        with self.assertRaisesRegex(TypefullyDraftOwnerError, "typefully_daily_disabled"):
            await run_typefully_daily(disabled, selector=selector)
        mismatch = TypefullyDailySettings(**{
            **vars(settings()), "authorized_sha": "b" * 40,
        })
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_daily_release_fence_mismatch"):
            await run_typefully_daily(mismatch, selector=selector)
        self.assertEqual(selector.calls, [])

    async def test_unsafe_url_and_credentials_have_zero_io(self):
        selector = FakeSelector({"yellow": slot("yellow")})
        unsafe = TypefullyDailySettings(**{
            **vars(settings()), "supabase_url": "https://untrusted.example",
        })
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_daily_settings_invalid"):
            await run_typefully_daily(unsafe, selector=selector)
        missing_key = TypefullyDailySettings(**{
            **vars(settings()), "service_role_key": "",
        })
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_daily_credentials_invalid"):
            await run_typefully_daily(missing_key, selector=selector)
        self.assertEqual(selector.calls, [])

    async def test_selected_clients_flow_into_exact_media_then_draft(self):
        selector = FakeSelector({"yellow": slot("yellow"), "babylon": None})
        events = []

        async def media(config):
            events.append(("media", config.client_id, config.content_version_id,
                           config.social_set_id))
            return {"status": "uploaded"}

        async def draft(config):
            events.append(("draft", config.client_id, config.content_version_id,
                           config.social_set_id))
            return {"status": "draft_created"}

        result = await run_typefully_daily(
            settings(), selector=selector, media_runner=media, draft_runner=draft,
        )
        self.assertEqual(selector.calls, ["yellow", "babylon"])
        self.assertEqual(events, [("media", "yellow", VERSION, 12345),
                                  ("draft", "yellow", VERSION, 12345)])
        self.assertEqual([item["status"] for item in result["outcomes"]],
                         ["draft_created", "no_candidate"])
        self.assertNotIn("x_copy", json.dumps(result))

    async def test_unknown_media_attempt_never_calls_draft(self):
        selector = FakeSelector({"yellow": slot("yellow")})
        draft_calls = []

        async def media(_config):
            return {"status": "already_reserved", "attempt_status": "upload_unknown"}

        async def draft(_config):
            draft_calls.append(True)
            return {"status": "draft_created"}

        result = await run_typefully_daily(
            settings(("yellow",)), selector=selector,
            media_runner=media, draft_runner=draft,
        )
        self.assertEqual(result["outcomes"][0]["status"], "media_unknown")
        self.assertEqual(draft_calls, [])

    async def test_completed_media_may_resume_to_draft(self):
        selector = FakeSelector({"yellow": slot("yellow")})
        draft_calls = []

        async def media(_config):
            return {"status": "already_reserved", "attempt_status": "uploaded"}

        async def draft(config):
            draft_calls.append(config.approval_id)
            return {"status": "already_reserved", "attempt_status": "draft_created"}

        result = await run_typefully_daily(
            settings(("yellow",)), selector=selector,
            media_runner=media, draft_runner=draft,
        )
        self.assertEqual(draft_calls, [APPROVAL])
        self.assertEqual(result["outcomes"][0]["status"], "already_reserved")

    async def test_unknown_draft_attempt_is_not_reported_as_complete(self):
        selector = FakeSelector({"yellow": slot("yellow")})

        async def media(_config):
            return {"status": "uploaded"}

        async def draft(_config):
            return {"status": "already_reserved", "attempt_status": "delivery_unknown"}

        result = await run_typefully_daily(
            settings(("yellow",)), selector=selector,
            media_runner=media, draft_runner=draft,
        )
        self.assertEqual(result["outcomes"][0]["status"], "draft_unknown")

    async def test_selector_rejects_mismatched_slot_and_does_not_leak_body(self):
        calls = []

        def backend(request):
            calls.append(request)
            assert json.loads(request.content) == {
                "target_workspace_id": WORKSPACE, "target_client_id": "yellow",
            }
            return httpx.Response(200, json={**slot("yellow"),
                                             "content_version_id": "wrong",
                                             "secret": "do not echo"})

        selector = SupabaseTypefullyDailySelector(
            settings(), transport=httpx.MockTransport(backend),
        )
        with self.assertRaisesRegex(TypefullyDraftOwnerError,
                                    "typefully_identifier_invalid") as caught:
            await selector.claim("yellow")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("do not echo", str(caught.exception))

    async def test_selector_accepts_server_kst_date_at_local_midnight_boundary(self):
        previous = "2026-09-22"

        def backend(_request):
            return httpx.Response(200, json={**slot("yellow"), "kst_date": previous})

        selector = SupabaseTypefullyDailySelector(
            settings(), transport=httpx.MockTransport(backend),
        )
        self.assertEqual((await selector.claim("yellow"))["kst_date"], previous)


if __name__ == "__main__":
    unittest.main()
