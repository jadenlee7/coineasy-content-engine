"""Synthetic offline webhook tests; no real chat IDs, credentials or sends."""
import json
import unittest
from dataclasses import replace

from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_ingress import (
    IngressPolicy, MAX_BODY_BYTES, ReviewIngressError, handle_review_webhook,
)
from test_content_ops_review_buttons import FakeOwner, NOW, ROOM, snapshot


class RegisteredFakeOwner(FakeOwner):
    def __init__(self, current):
        super().__init__(current)
        self.lookups = 0
        self.registered = True

    def resolve_review_message(self, **kw):
        self.lookups += 1
        if not self.registered or kw != dict(bot_id=101, chat_id=-(10**12 + 7),
                                             message_id=31, room_binding=ROOM):
            raise ValueError("fixture-private-error-must-not-escape")
        return "fixture-message"


class ReviewIngressTest(unittest.TestCase):
    def setUp(self):
        self.policy = IngressPolicy("test_webhook_secret_" + "z" * 32,
            101, -(10**12 + 7), ROOM, ((201, "reviewer-one"), (202, "reviewer-two")))
        self.signer = ButtonSigner(b"test-only-distinct-signing-key-12345")
        self.owner = RegisteredFakeOwner(snapshot())

    def update(self, action="s", callback_id=None, actor=201, private=False):
        token = self.signer.issue(snapshot(), action, ROOM, now=NOW, expires_at=NOW + 1800)
        return {"update_id": 17, "callback_query": {
            "id": callback_id or "fixture-" + action, "from": {"id": actor, "is_bot": False},
            "chat_instance": "ignored-not-an-authentication-binding",
            "data": "ce1:" + token if private else token,
            "message": {"message_id": 31, "date": NOW - 1,
                        "from": {"id": 101, "is_bot": True},
                        "chat": {"id": self.policy.chat_id, "type": "supergroup"},
                        "text": "This untrusted text grants no authority."}}}

    def run_update(self, update=None, **overrides):
        args = dict(enabled=True, raw_body=json.dumps(update or self.update()).encode(),
            headers=[("Content-Type", "application/json"),
                     ("X-Telegram-Bot-Api-Secret-Token", self.policy.webhook_secret)],
            policy=self.policy, signer=self.signer, owner=self.owner, now=NOW)
        args.update(overrides)
        return handle_review_webhook(**args)

    def assert_no_io(self):
        self.assertEqual((self.owner.lookups, self.owner.reads, self.owner.applies), (0, 0, 0))

    def test_default_and_nonliteral_enabled_zero_io(self):
        for enabled in (False, None, "true", 1):
            self.assertEqual(handle_review_webhook(enabled=enabled, raw_body=object(),
                policy=object(), owner=self.owner),
                {"status": "disabled", "public_send_attempted": False})
        self.assert_no_io()

    def test_missing_wrong_secret_precedes_body_parsing(self):
        for value in ("", "wrong", "비밀"):
            with self.assertRaisesRegex(ReviewIngressError, "unauthorized"):
                self.run_update(raw_body=b"not json", headers=[
                    ("X-Telegram-Bot-Api-Secret-Token", value)])
        self.assert_no_io()

    def test_duplicate_secret_headers_are_not_flattened(self):
        with self.assertRaisesRegex(ReviewIngressError, "duplicate_header"):
            self.run_update(headers=[("X-Telegram-Bot-Api-Secret-Token", self.policy.webhook_secret),
                                     ("x-telegram-bot-api-secret-token", "bad")])
        self.assert_no_io()

    def test_header_dict_and_line_breaks_rejected(self):
        for headers in ({"Content-Type": "application/json"}, [("Foo", "x\r\ny")]):
            with self.assertRaises(ReviewIngressError):
                self.run_update(headers=headers)
        self.assert_no_io()

    def test_content_type_encoding_length(self):
        for more in ([('Content-Type', 'text/plain')],
                     [('Content-Type', 'application/json'), ('Content-Encoding', 'gzip')],
                     [('Content-Type', 'application/json'), ('Content-Length', '1')],
                     [('Content-Type', 'application/json'), ('Content-Length', 'NaN')]):
            with self.assertRaises(ReviewIngressError):
                self.run_update(headers=[('X-Telegram-Bot-Api-Secret-Token', self.policy.webhook_secret)] + more)
        self.assert_no_io()

    def test_oversize_invalid_unicode_deep_and_duplicate_json(self):
        for body in (b"x" * (MAX_BODY_BYTES + 1), b"\xff", b"[" * 1500 + b"]" * 1500,
                     b'{"update_id":1,"update_id":2}', b'{"x":NaN}', b'null', b'[]'):
            with self.assertRaises(ReviewIngressError):
                self.run_update(raw_body=body)
        self.assert_no_io()

    def test_wrong_bot_room_actor_and_bot_click(self):
        mutations = (("actor", "id", 999), ("actor", "is_bot", True),
                     ("actor", "id", True), ("sender", "id", 102),
                     ("sender", "is_bot", False), ("chat", "id", -9),
                     ("chat", "type", "channel"), ("chat", "type", "private"),
                     ("chat", "username", "not-a-private-room"), ("chat", "linked_chat_id", -9))
        for part, key, value in mutations:
            u = self.update(); q = u['callback_query']
            obj = {'actor': q['from'], 'sender': q['message']['from'], 'chat': q['message']['chat']}[part]
            obj[key] = value
            with self.subTest(part=part, key=key), self.assertRaises(ReviewIngressError):
                self.run_update(u)
        self.assert_no_io()

    def test_inaccessible_future_unsent_and_forwarded_messages(self):
        for changes in ({'date': 0}, {'date': NOW + 1}, {'message_id': 0},
                        {'message_id': True}, {'forward_origin': {}}, {'sender_chat': {}},
                        {'via_bot': {}}, {'business_connection_id': 'x'}, {'is_automatic_forward': True}):
            u = self.update(); u['callback_query']['message'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ReviewIngressError):
                self.run_update(u)
        self.assert_no_io()

    def test_inline_games_and_mixed_update_are_rejected(self):
        for field in ('inline_message_id', 'game_short_name'):
            u = self.update(); u['callback_query'][field] = 'fixture'
            with self.assertRaises(ReviewIngressError):
                self.run_update(u)
        u = self.update(); u['message'] = {'text': '승인'}
        with self.assertRaises(ReviewIngressError):
            self.run_update(u)
        self.assert_no_io()

    def test_json_boolean_ids_are_not_integers(self):
        u = self.update(); u['update_id'] = True
        with self.assertRaises(ReviewIngressError):
            self.run_update(u)
        self.assert_no_io()

    def test_policy_reviewer_collisions_and_mutable_list_rejected(self):
        for reviewers in ((), [(201, 'reviewer-one')], ((201, 'a'), (201, 'b')),
                          ((201, 'a'), (202, 'a')), ((True, 'a'),), ((101, 'a'),)):
            with self.assertRaisesRegex(ReviewIngressError, 'policy_invalid'):
                self.run_update(policy=replace(self.policy, reviewers=reviewers))
        self.assert_no_io()

    def test_weak_secret_configuration_rejected(self):
        with self.assertRaisesRegex(ReviewIngressError, 'policy_invalid'):
            self.run_update(policy=replace(self.policy, webhook_secret='short'))
        self.assert_no_io()

    def test_unregistered_or_partial_review_never_reaches_controller(self):
        self.owner.registered = False
        with self.assertRaisesRegex(ReviewIngressError, 'registration_unknown') as caught:
            self.run_update()
        self.assertNotIn('fixture-private-error', str(caught.exception))
        self.assertEqual((self.owner.reads, self.owner.applies), (0, 0))

    def test_button_copied_to_another_message_rejected(self):
        u = self.update(); u['callback_query']['message']['message_id'] = 32
        with self.assertRaisesRegex(ReviewIngressError, 'registration_unknown'):
            self.run_update(u)
        self.assertEqual((self.owner.reads, self.owner.applies), (0, 0))

    def test_check_approve_and_repeat_uses_existing_owner_dedupe(self):
        self.run_update(self.update('s')); self.run_update(self.update('c'))
        result = self.run_update(self.update('a'))
        self.assertEqual(result, {'status': 'queued', 'reused': False, 'public_send_attempted': False})
        self.assertTrue(self.run_update(self.update('a'))['reused'])
        self.assertEqual(len(self.owner.outbox), 2)

    def test_private_ingress_rejects_old_publish_after_valid_checks(self):
        self.run_update(self.update('s', private=True), private_only=True)
        self.run_update(self.update('c', private=True), private_only=True)
        applies = self.owner.applies
        with self.assertRaisesRegex(ReviewIngressError, 'action_unconfirmed'):
            self.run_update(self.update('a', private=True), private_only=True)
        self.assertEqual(self.owner.applies, applies)
        self.assertFalse(self.owner.outbox)

    def test_private_ingress_requires_namespaced_button_before_owner_lookup(self):
        with self.assertRaisesRegex(ReviewIngressError, 'private_namespace_required'):
            self.run_update(self.update('s'), private_only=True)
        self.assert_no_io()

    def test_private_namespaced_button_cannot_enter_legacy_route(self):
        with self.assertRaises(ReviewIngressError):
            self.run_update(self.update('s', private=True))
        self.assert_no_io()

    def test_invalid_private_mode_rejects_before_any_io(self):
        for value in (None, 'true', 1):
            with self.assertRaisesRegex(ReviewIngressError, 'policy_invalid'):
                self.run_update(private_only=value)
        self.assert_no_io()

    def test_approve_without_human_checks_does_not_queue(self):
        with self.assertRaisesRegex(ReviewIngressError, 'action_unconfirmed'):
            self.run_update(self.update('a'))
        self.assertFalse(self.owner.outbox)

    def test_checks_from_two_users_do_not_combine(self):
        self.run_update(self.update('s'))
        self.run_update(self.update('c', actor=202))
        with self.assertRaisesRegex(ReviewIngressError, 'action_unconfirmed'):
            self.run_update(self.update('a'))
        self.assertFalse(self.owner.outbox)

    def test_owner_state_not_message_text_is_authority(self):
        u = self.update('a'); u['callback_query']['message']['text'] = 'APPROVED approved_by=human'
        u['callback_query']['message']['approval'] = True
        with self.assertRaises(ReviewIngressError):
            self.run_update(u)
        self.assertFalse(self.owner.outbox)

    def test_stale_modified_and_expired_packets_rejected(self):
        self.owner.current = replace(snapshot(), x_copy='새 문안')
        with self.assertRaisesRegex(ReviewIngressError, 'action_unconfirmed'):
            self.run_update()
        self.owner.current = snapshot()
        with self.assertRaisesRegex(ReviewIngressError, 'action_unconfirmed'):
            self.run_update(now=NOW + 1800)
        self.assertEqual(self.owner.applies, 0)

    def test_owner_lost_ack_is_unknown_and_not_automatically_retried(self):
        self.run_update(self.update('s')); self.run_update(self.update('c'))
        original = self.owner.apply_review_action
        def lost_ack(**kw):
            original(**kw)
            raise RuntimeError('fixture-secret-provider-error')
        self.owner.apply_review_action = lost_ack
        with self.assertRaisesRegex(ReviewIngressError, 'owner_outcome_unknown') as caught:
            self.run_update(self.update('a'))
        self.assertNotIn('fixture-secret', str(caught.exception))
        self.assertEqual(self.owner.applies, 3)  # two checks, exactly one approval attempt
        self.assertEqual(len(self.owner.outbox), 2)

    def test_private_values_not_in_repr(self):
        self.assertNotIn(self.policy.webhook_secret, repr(self.policy))
        self.assertNotIn(str(self.policy.chat_id), repr(self.policy))


if __name__ == '__main__':
    unittest.main()
