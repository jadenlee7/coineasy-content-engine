"""Synthetic provider readback only; no Telegram, Typefully or database I/O."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from core.content_ops.publication_route_verification import (
    ExpectedPublicationRoutes, PublicationRouteError, verify_publication_routes,
)
from core.content_ops.final_publication_confirmation import (
    FinalConfirmationError, FinalConfirmationSigner,
    final_confirmation_messages, pin_verified_routes,
)
from core.publications.handoff import CLIENT_TARGETS
from tests.test_content_ops_final_publication_confirmation import snapshot


NOW = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)
W = "11111111-1111-4111-8111-111111111111"
RELEASE = "c" * 40


def fixture(client="yellow"):
    index = ("yellow", "babylon", "squid", "origintrail").index(client) + 1
    tg, x, _ = CLIENT_TARGETS[client]
    expected = ExpectedPublicationRoutes(W, client, RELEASE,
        -(1000000000000 + index), tg, 123456 + index, 200000 + index, x)
    bot = {"id": expected.telegram_bot_id, "is_bot": True}
    chat = {"id": expected.telegram_channel_id, "type": "channel",
            "username": tg}
    member = {"status": "administrator", "can_post_messages": True,
              "user": {"id": expected.telegram_bot_id, "is_bot": True}}
    set_details = {"id": expected.typefully_social_set_id,
                   "platforms": {"x": {"platform": "x", "username": x,
                                       "profile_url": "https://x.com/" + x},
                                 "linkedin": None, "mastodon": None,
                                 "threads": None, "bluesky": None,
                                 "substack": None}}
    return dict(expected=expected, telegram_bot=bot, telegram_channel=chat,
                telegram_member=member, typefully_social_set=set_details,
                observed_at=NOW-timedelta(minutes=1), now=NOW)


class PublicationRouteVerificationTest(unittest.TestCase):
    def verify(self, values):
        return verify_publication_routes(**values)

    def test_all_four_clients_bind_exact_provider_identities(self):
        signer = FinalConfirmationSigner(b"f" * 32)
        for client in ("yellow", "babylon", "squid", "origintrail"):
            with self.subTest(client=client):
                values = fixture(client)
                routes = self.verify(values)
                self.assertEqual(len(routes.telegram_route_binding), 64)
                self.assertEqual(len(routes.typefully_route_binding), 64)
                self.assertNotEqual(routes.telegram_route_binding,
                                    routes.typefully_route_binding)
                base = replace(snapshot(client), release_sha=RELEASE)
                pinned = pin_verified_routes(base, routes, now=NOW)
                card = final_confirmation_messages(pinned, signer, "private-room",
                    now=int(NOW.timestamp()))
                self.assertIn(routes.telegram_destination_label,
                              card["controls"]["text"])
                self.assertIn(routes.typefully_x_destination_label,
                              card["controls"]["text"])
                self.assertNotIn(str(values["expected"].telegram_channel_id),
                                 card["controls"]["text"])

    def test_policy_or_provider_identity_change_fails_closed(self):
        good = fixture()
        changed = [
            {**good, "expected": replace(good["expected"], telegram_channel_id=-999)},
            {**good, "expected": replace(good["expected"], typefully_social_set_id=999)},
            {**good, "expected": replace(good["expected"], telegram_username="otherchannel")},
            {**good, "expected": replace(good["expected"], x_username="otherxhandle")},
            {**good, "telegram_bot": {**good["telegram_bot"], "id": 777}},
            {**good, "telegram_channel": {**good["telegram_channel"], "type": "supergroup"}},
            {**good, "telegram_channel": {**good["telegram_channel"], "username": "different"}},
            {**good, "telegram_member": {**good["telegram_member"], "status": "member"}},
            {**good, "telegram_member": {**good["telegram_member"], "can_post_messages": False}},
            {**good, "typefully_social_set": {**good["typefully_social_set"], "id": 999}},
            {**good, "typefully_social_set": {**good["typefully_social_set"],
                "platforms": {**good["typefully_social_set"]["platforms"],
                              "linkedin": {"platform": "linkedin"}}}},
            {**good, "typefully_social_set": {**good["typefully_social_set"],
                "platforms": {**good["typefully_social_set"]["platforms"],
                              "x": None}}},
        ]
        for values in changed:
            with self.subTest(index=changed.index(values)):
                with self.assertRaises(PublicationRouteError) as caught:
                    self.verify(values)
                self.assertNotIn("999", str(caught.exception))

    def test_stale_future_or_naive_observation_fails_closed(self):
        for observed in (NOW-timedelta(minutes=15, seconds=1),
                         NOW+timedelta(seconds=1),
                         NOW.replace(tzinfo=None)):
            with self.subTest(observed=observed), self.assertRaisesRegex(
                    PublicationRouteError, "observation_stale"):
                self.verify({**fixture(), "observed_at": observed})

    def test_refresh_stable_but_identity_and_release_change_binding(self):
        good = fixture()
        routes = self.verify(good)
        refreshed = self.verify({**good, "observed_at": NOW})
        self.assertEqual(routes.telegram_route_binding,
                         refreshed.telegram_route_binding)
        self.assertEqual(routes.typefully_route_binding,
                         refreshed.typefully_route_binding)
        other_release = self.verify({**good, "expected": replace(good["expected"],
            release_sha="d" * 40)})
        self.assertNotEqual(routes.telegram_route_binding,
                            other_release.telegram_route_binding)
        self.assertNotEqual(routes.typefully_route_binding,
                            other_release.typefully_route_binding)

    def test_snapshot_pinning_rejects_mismatched_client_release_and_staleness(self):
        routes = self.verify(fixture())
        base = replace(snapshot(), release_sha=RELEASE)
        for changed, now in ((replace(routes, client_id="squid"), NOW),
                             (replace(routes, release_sha="d" * 40), NOW),
                             (routes, NOW+timedelta(minutes=15))):
            with self.subTest(changed=changed.client_id, now=now), self.assertRaisesRegex(
                    FinalConfirmationError, "destination_unverified"):
                pin_verified_routes(base, changed, now=now)


if __name__ == "__main__":
    unittest.main()
