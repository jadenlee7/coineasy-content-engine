"""Synthetic read-only provider transport; no real credentials or network."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx

from core.content_ops.publication_route_readback import (
    PublicationRouteReadback, PublicationRouteReadbackError,
)
from core.content_ops.publication_route_verification import verify_publication_routes
from core.content_ops.typefully_route_reader import (
    TypefullyDraftTarget, TypefullySocialSetDetailReader,
)
from core.publishers.telegram_exact import (
    TelegramExactConfig, TelegramExactError, load_telegram_exact_config,
)
from tests.test_content_ops_publication_route_verification import fixture


TOKEN = "123457:" + "a" * 40


class FakeProvider:
    def __init__(self, values):
        self.values = values
        self.calls = []
        self.typefully_calls = []
        self.failure_method = None
        self.bad_json = False
        self.large_body = False
        self.wrong_channel = False
        self.revoke_bot = False
        self.mixed_social_set = False
        self.typefully_failure = False

    def telegram(self, request):
        self.calls.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if request.url.host != "api.telegram.org" or request.method != "POST":
            raise AssertionError("unexpected provider request")
        body = json.loads(request.content)
        if method == "getMe":
            assert body == {}
            result = self.values["telegram_bot"]
        elif method == "getChat":
            assert body == {"chat_id": self.values["expected"].telegram_channel_id}
            result = dict(self.values["telegram_channel"])
            if self.wrong_channel:
                result["id"] += 1
        elif method == "getChatMember":
            assert body == {"chat_id": self.values["expected"].telegram_channel_id,
                            "user_id": self.values["expected"].telegram_bot_id}
            result = dict(self.values["telegram_member"])
            if self.revoke_bot:
                result["can_post_messages"] = False
        else:
            raise AssertionError("write or intake method attempted")
        if method == self.failure_method:
            return httpx.Response(503, json={"ok": False,
                "description": "synthetic secret should never leak"})
        if self.bad_json and method == "getChat":
            return httpx.Response(200, content=b"<private provider error>")
        if self.large_body and method == "getChat":
            return httpx.Response(200, content=b" " * 32769)
        return httpx.Response(200, json={"ok": True, "result": result})

    async def typefully(self, social_set_id):
        self.typefully_calls.append(social_set_id)
        if self.typefully_failure:
            raise RuntimeError("private OAuth response must not leak")
        result = dict(self.values["typefully_social_set"])
        if self.mixed_social_set:
            result["platforms"] = {**result["platforms"],
                                   "linkedin": {"platform": "linkedin"}}
        return result

    def owner(self, *, token=TOKEN, expected=None, runtime_release_sha=None,
              publisher_config=None, typefully_target=None):
        route = expected or self.values["expected"]
        publisher_config = publisher_config or TelegramExactConfig(
            client_id=route.client_id,
            public_username=route.telegram_username,
            chat_id="@" + route.telegram_username,
            bot_token=token)
        return PublicationRouteReadback(expected or self.values["expected"],
            telegram_publisher_config=publisher_config,
            typefully_publisher_target=(typefully_target or TypefullyDraftTarget(
                route.client_id, route.typefully_social_set_id)),
            runtime_release_sha=(runtime_release_sha
                if runtime_release_sha is not None else self.values["expected"].release_sha),
            typefully_detail_reader=self.typefully,
            transport=httpx.MockTransport(self.telegram),
            clock=lambda: self.values["now"])


class PublicationRouteReadbackTest(unittest.TestCase):
    def test_existing_publisher_loader_rejects_inactive_client_routes(self):
        for client in ("yellow", "squid", "babylon", "origintrail"):
            with self.subTest(client=client):
                expected = fixture(client)["expected"]
                suffix = client.upper()
                env = {f"TELEGRAM_BOT_TOKEN_{suffix}":
                           str(expected.telegram_bot_id) + ":" + "a" * 40,
                       f"TELEGRAM_CHANNEL_{suffix}":
                           "@" + expected.telegram_username}
                if client in ("babylon", "origintrail"):
                    with self.assertRaisesRegex(TelegramExactError,
                                                "channel_inactive"):
                        load_telegram_exact_config(client,
                            clients_dir=Path("clients"), environ=env)
                else:
                    config = load_telegram_exact_config(client,
                        clients_dir=Path("clients"), environ=env)
                    self.assertEqual(config.client_id, client)
                    self.assertEqual(config.public_username,
                                     expected.telegram_username.lower())

    def test_default_off_and_bad_policy_have_zero_provider_io(self):
        provider = FakeProvider(fixture())
        with self.assertRaisesRegex(PublicationRouteReadbackError, "disabled"):
            asyncio.run(provider.owner().run())
        with self.assertRaisesRegex(PublicationRouteReadbackError,
                                    "configuration_invalid"):
            asyncio.run(provider.owner(token="bad").run(enabled=True))
        invalid = replace(provider.values["expected"], client_id="unknown")
        with self.assertRaisesRegex(PublicationRouteReadbackError,
                                    "configuration_invalid"):
            asyncio.run(provider.owner(expected=invalid).run(enabled=True))
        malformed = replace(provider.values["expected"], client_id=["yellow"])
        with self.assertRaisesRegex(PublicationRouteReadbackError,
                                    "configuration_invalid"):
            asyncio.run(provider.owner(expected=malformed).run(enabled=True))
        with self.assertRaisesRegex(PublicationRouteReadbackError,
                                    "configuration_invalid"):
            asyncio.run(provider.owner(runtime_release_sha="d" * 40).run(enabled=True))
        for changed in (
                {"client_id": "squid"},
                {"public_username": "otherchannel"},
                {"chat_id": "@otherchannel"},
                {"chat_id": "-9999999999999"},
        ):
            config = TelegramExactConfig(
                client_id=provider.values["expected"].client_id,
                public_username=provider.values["expected"].telegram_username,
                chat_id="@" + provider.values["expected"].telegram_username,
                bot_token=TOKEN)
            with self.subTest(changed=changed), self.assertRaisesRegex(
                    PublicationRouteReadbackError, "configuration_invalid"):
                asyncio.run(provider.owner(publisher_config=replace(config,
                    **changed)).run(enabled=True))
        for target in (TypefullyDraftTarget("squid",
                           provider.values["expected"].typefully_social_set_id),
                       TypefullyDraftTarget("yellow", 999)):
            with self.subTest(target=target), self.assertRaisesRegex(
                    PublicationRouteReadbackError, "configuration_invalid"):
                asyncio.run(provider.owner(typefully_target=target).run(enabled=True))
        self.assertEqual((provider.calls, provider.typefully_calls), ([], []))

    def test_exact_three_telegram_reads_then_one_typefully_read(self):
        for client in ("yellow", "babylon", "squid", "origintrail"):
            with self.subTest(client=client):
                provider = FakeProvider(fixture(client))
                token = str(provider.values["expected"].telegram_bot_id) + ":" + "a" * 40
                actual = asyncio.run(provider.owner(token=token).run(enabled=True))
                expected = verify_publication_routes(**{
                    **provider.values, "observed_at": provider.values["now"]})
                self.assertEqual(actual, expected)
                self.assertEqual([request.url.path.rsplit("/", 1)[-1]
                                  for request in provider.calls],
                                 ["getMe", "getChat", "getChatMember"])
                self.assertEqual(provider.typefully_calls,
                                 [provider.values["expected"].typefully_social_set_id])

    def test_wrong_channel_or_permission_never_reaches_typefully(self):
        for field in ("wrong_channel", "revoke_bot"):
            provider = FakeProvider(fixture())
            setattr(provider, field, True)
            with self.subTest(field=field), self.assertRaisesRegex(
                    PublicationRouteReadbackError, "unverified"):
                asyncio.run(provider.owner().run(enabled=True))
            self.assertEqual(provider.typefully_calls, [])

    def test_provider_errors_are_fixed_and_never_send(self):
        for field, value in (("failure_method", "getMe"),
                             ("failure_method", "getChat"),
                             ("failure_method", "getChatMember"),
                             ("bad_json", True), ("large_body", True),
                             ("mixed_social_set", True),
                             ("typefully_failure", True)):
            provider = FakeProvider(fixture())
            setattr(provider, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(
                    PublicationRouteReadbackError) as caught:
                asyncio.run(provider.owner().run(enabled=True))
            self.assertNotIn("synthetic secret", str(caught.exception))
            self.assertNotIn("private OAuth", str(caught.exception))
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertTrue(all(request.url.path.rsplit("/", 1)[-1] in
                {"getMe", "getChat", "getChatMember"} for request in provider.calls))

    def test_slow_provider_observation_is_not_backdated_as_fresh(self):
        provider = FakeProvider(fixture())
        moments = iter((provider.values["now"],
                        provider.values["now"] + timedelta(minutes=16)))
        owner = PublicationRouteReadback(provider.values["expected"],
            telegram_publisher_config=TelegramExactConfig(
                client_id="yellow", public_username="yellowkorea_ann",
                chat_id="@yellowkorea_ann", bot_token=TOKEN),
            typefully_publisher_target=TypefullyDraftTarget("yellow",
                provider.values["expected"].typefully_social_set_id),
            runtime_release_sha=provider.values["expected"].release_sha,
            typefully_detail_reader=provider.typefully,
            transport=httpx.MockTransport(provider.telegram), clock=lambda: next(moments))
        with self.assertRaisesRegex(PublicationRouteReadbackError, "unverified"):
            asyncio.run(owner.run(enabled=True))
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(len(provider.typefully_calls), 1)

    def test_http_readers_compose_without_draft_or_send(self):
        provider = FakeProvider(fixture())
        typefully_requests = []
        def typefully_detail(request):
            typefully_requests.append(request)
            return httpx.Response(200, json=provider.values["typefully_social_set"])
        detail_reader = TypefullySocialSetDetailReader(
            bearer_token="synthetic_read_only_bearer", enabled=True,
            transport=httpx.MockTransport(typefully_detail))
        owner = PublicationRouteReadback(provider.values["expected"],
            telegram_publisher_config=TelegramExactConfig(
                client_id="yellow", public_username="yellowkorea_ann",
                chat_id="@yellowkorea_ann", bot_token=TOKEN),
            typefully_publisher_target=TypefullyDraftTarget("yellow",
                provider.values["expected"].typefully_social_set_id),
            runtime_release_sha=provider.values["expected"].release_sha,
            typefully_detail_reader=detail_reader,
            transport=httpx.MockTransport(provider.telegram),
            clock=lambda: provider.values["now"])
        result = asyncio.run(owner.run(enabled=True))
        self.assertEqual(result.client_id, "yellow")
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual([request.method for request in provider.calls],
                         ["POST", "POST", "POST"])
        self.assertEqual(len(typefully_requests), 1)
        self.assertEqual(typefully_requests[0].method, "GET")


if __name__ == "__main__":
    unittest.main()
