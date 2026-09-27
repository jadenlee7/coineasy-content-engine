"""Private callback -> existing PostgreSQL owner, one transaction per update.

No credentials, grants, delivery, startup or retry. Inject a NEW connection per
call; do not share session state between polling threads. The existing card
ledger must already contain authenticated complete-packet delivery evidence.
This does not register cards or prove that Telegram delivered them.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.prompt_reservation import card_parent_binding, canonical_timestamp, _time
from core.content_ops.review_buttons import ButtonSigner, ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import ReviewIngressError, _parse_callback, _positive_int


def _check(ok):
    if not ok:
        raise ReviewIngressError("review_private_owner_outcome_unknown")


def private_snapshot(review, version, source, asset):
    """Canonical DB projection shared by card preparation and callback readback.

    `blocked` is deliberate: these checks never establish public eligibility.
    A source's official URL is checked by ReviewSnapshot.validate; source text,
    private storage URLs, credentials and raw provider responses are not loaded.
    """
    snapshot = ReviewSnapshot(review['workspace_id'], review['client_id'],
        review['content_item_id'], review['content_version_id'],
        source['canonical_url'], canonical_timestamp(source['published_at']),
        version['channel_copy']['telegram'], version['channel_copy']['x'],
        asset['sha256'], 'blocked')
    snapshot.validate()
    return snapshot


class PostgresPrivateReviewOwner:
    def __init__(self, connection_factory, bindings):
        _check(callable(connection_factory) and type(bindings) is EditBindings)
        self._connect, self._bindings = connection_factory, bindings

    def handle_update(self, *, enabled=False, raw_body=None, headers=(),
                      policy=None, signer=None, now=None):
        if enabled is not True:
            return {'status': 'disabled', 'public_send_attempted': False}
        # Authenticate before connecting. Usernames are never consulted.
        query, actor = _parse_callback(raw_body, headers, policy, now, private_only=True)
        _check(canonical_uuid(actor) and type(signer) is ButtonSigner)
        message = query['message']
        topic = message.get('message_thread_id')
        _check(topic is None or _positive_int(topic))
        b = self._bindings
        bot = b.digest('bot', policy.bot_id)
        room = b.digest('room', policy.bot_id, policy.chat_id)
        card_message = b.digest('card-message@2', policy.bot_id, policy.chat_id, message['message_id'])
        human = b.digest('human', policy.bot_id, query['from']['id'])
        operation = hashlib.sha256(query['id'].encode()).hexdigest()
        try:
            with self._connect() as connection:
                _check(connection.autocommit is False)
                with connection.cursor() as cur:
                    # Shared lock ordering with edit/revoke: item -> review ->
                    # card -> identity -> reviewer -> active client.
                    cur.execute("""select i.id, c.id, r.id
                        from public.content_items i
                        join private.content_ops_button_reviews r
                          on r.workspace_id=i.workspace_id and r.content_item_id=i.id
                        join private.content_ops_button_cards c on c.review_id=r.id
                        where c.bindings->>'bot'=%s and c.bindings->>'room'=%s
                          and c.bindings->>'message'=%s for update of i""",
                        (bot, room, card_message))
                    rows = cur.fetchall()
                    _check(len(rows) == 1)
                    item, card_id, review_id = map(str, rows[0])
                    cur.execute("""select to_jsonb(r) from private.content_ops_button_reviews r
                        where r.id=%s::uuid and r.content_item_id=%s::uuid for update""", (review_id, item))
                    review = cur.fetchone()[0]
                    cur.execute("""select to_jsonb(c) from private.content_ops_button_cards c
                        where c.id=%s::uuid and c.review_id=%s::uuid for share""", (card_id, review_id))
                    card = cur.fetchone()[0]
                    _check(card['active'] is True and card['bindings']['thread_id'] == topic
                        and card['bindings']['bot'] == bot and card['bindings']['room'] == room
                        and card['bindings']['message'] == card_message
                        and card_parent_binding(b, review, card) == card['bindings']['parent_binding']
                        and review['epoch'] in (card['epoch'], card['epoch'] + 1))
                    cur.execute("""select actor_id from private.content_ops_button_identities
                        where workspace_id=%s::uuid and bot_binding=%s and human_binding=%s
                          and active for share""", (review['workspace_id'], bot, human))
                    identity = cur.fetchone()
                    _check(identity is not None and str(identity[0]) == actor)
                    cur.execute("""select actor_id from private.content_ops_button_reviewers
                        where workspace_id=%s::uuid and client_id=%s and actor_id=%s::uuid
                          and active for share""", (review['workspace_id'], review['client_id'], actor))
                    _check(cur.fetchone() is not None)
                    cur.execute("""select client_id from public.workspace_clients
                        where workspace_id=%s::uuid and client_id=%s and active for share""",
                        (review['workspace_id'], review['client_id']))
                    _check(cur.fetchone() is not None)
                    cur.execute("""select jsonb_build_object('channel_copy',v.channel_copy),
                            jsonb_build_object('id',s.id,'source_feed_id',s.source_feed_id,
                                'canonical_url',s.canonical_url,'published_at',s.published_at),
                            jsonb_build_object('sha256',a.sha256)
                        from public.content_versions v
                        join public.content_source_links l on l.workspace_id=v.workspace_id
                          and l.content_item_id=v.content_item_id and l.position=0 and l.client_id=%s
                        join public.source_items s on s.workspace_id=l.workspace_id
                          and s.client_id=l.client_id and s.id=l.source_item_id
                        join public.assets a on a.workspace_id=v.workspace_id
                          and a.content_item_id=v.content_item_id and a.content_version_id=v.id
                          and a.id::text=v.deliverables->>'primary_asset_id'
                        where v.id=%s::uuid and v.workspace_id=%s::uuid
                          and v.content_item_id=%s::uuid and a.asset_kind='png'
                          and a.mime_type='image/png' and s.source_type='tweet'
                        for share of v,l,s,a""",
                        (review['client_id'], review['content_version_id'], review['workspace_id'], item))
                    rows = cur.fetchall()
                    _check(len(rows) == 1)
                    version, source, asset = rows[0]
                    _check(canonical_uuid(source['id']) and canonical_uuid(source['source_feed_id']))
                    snapshot = private_snapshot(review, version, source, asset)

                    def verify_clock():
                        cur.execute('select clock_timestamp()')
                        current = _time(cur.fetchone()[0])
                        _check(_time(card['delivered_at']) <= current < _time(card['expires_at'])
                            and current < _time(review['expires_at'])
                            and current - timedelta(hours=24) < _time(source['published_at']) <= current)
                        # A newer official-feed tweet invalidates this private
                        # review even if the linked source and signed snapshot
                        # themselves have not changed. The restricted role has
                        # source SELECT but no feed SELECT; feed/poll readiness
                        # remains a separate owner-side release gate.
                        cur.execute("""select s.id from public.source_items s
                            where s.workspace_id=%s::uuid and s.client_id=%s
                              and s.source_feed_id=%s::uuid and s.source_type='tweet'
                            order by s.published_at desc nulls last, s.id desc limit 1""",
                            (review['workspace_id'], review['client_id'], source['source_feed_id']))
                        latest = cur.fetchone()
                        _check(latest is not None and str(latest[0]) == source['id'])
                        return signer.verify(query['data'], snapshot, policy.room_binding,
                                             now=int(current.timestamp()))

                    action = verify_clock()
                    _check(action in {'source_checked', 'claims_checked', 'hold',
                                      'edit_telegram', 'edit_x', 'edit_banner'})
                    cur.execute('select private.record_content_ops_button_action(%s::uuid,%s::uuid,%s,%s,%s)',
                        (review_id, actor, review['version_fingerprint'], action, operation))
                    receipt = cur.fetchone()[0]
                    _check(type(receipt) is dict and receipt.get('execution_authorized') is False
                        and receipt.get('status') in {'checked', 'held', 'edit_requested'}
                        and type(receipt.get('reused')) is bool)
                    # Revalidate AFTER permission/SQL lock waits. Failure rolls
                    # back the action. Never acknowledge a commit before exit.
                    _check(verify_clock() == action)
                    result = {'status': receipt['status'], 'reused': receipt['reused'],
                              'public_send_attempted': False}
            return result
        except Exception:
            # Includes commit ACK loss: outcome UNKNOWN, never an auto-retry.
            raise ReviewIngressError('review_private_owner_outcome_unknown') from None
