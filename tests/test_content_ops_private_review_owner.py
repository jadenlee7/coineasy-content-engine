"""Cheap boundary checks complement disposable PostgreSQL behavioral tests."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4
from unittest.mock import Mock

import pytest

from core.content_ops.private_review_owner import PostgresPrivateReviewOwner, private_snapshot
from core.content_ops.polling_review_adapter import PollingReviewAdapter
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.prompt_reservation import card_parent_binding
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_ingress import ReviewIngressError
from test_content_ops_review_edit_ingress import POLICY


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_disabled_owner_does_not_connect_or_parse(enabled):
    connect=Mock(side_effect=AssertionError('unexpected I/O'))
    owner=PostgresPrivateReviewOwner(connect,EditBindings(b'synthetic-binding-key-1234567890123456'))
    assert owner.handle_update(enabled=enabled,raw_body=object())['status']=='disabled'
    connect.assert_not_called()


def test_unauthenticated_request_cannot_connect():
    connect=Mock(side_effect=AssertionError('unexpected I/O'))
    owner=PostgresPrivateReviewOwner(connect,EditBindings(b'synthetic-binding-key-1234567890123456'))
    with pytest.raises(ReviewIngressError):
        owner.handle_update(enabled=True,raw_body=b'{}',policy=POLICY,now=123)
    connect.assert_not_called()


def test_no_legacy_owner_fallback_configuration():
    owner=PostgresPrivateReviewOwner(Mock(),EditBindings(b'synthetic-binding-key-1234567890123456'))
    with pytest.raises(ReviewIngressError):
        PollingReviewAdapter(enabled=True,policy=POLICY,review_owner=Mock(),transactional_review_owner=owner)


def test_newer_feed_source_denies_private_callback_before_durable_action():
    """A linked, unchanged source is not enough when a newer tweet arrived."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ids = {name: str(uuid4()) for name in
        ('workspace','item','version','review','card','source','feed','newer','asset')}
    bindings = EditBindings(b'synthetic-binding-key-1234567890123456')
    signer = ButtonSigner(b'synthetic-private-review-signing-key-123456')
    review = dict(id=ids['review'],workspace_id=ids['workspace'],client_id='squid',
        content_item_id=ids['item'],content_version_id=ids['version'],
        version_fingerprint='a'*64,epoch=0,expires_at=now+timedelta(minutes=10))
    parts = [dict(kind=kind,message_binding=char*64,payload_sha256=hash_char*64,
                  outcome='sent') for kind,char,hash_char in
             (('image','1','4'),('telegram','2','5'),('x','3','6'))]
    card = dict(id=ids['card'],review_id=ids['review'],epoch=0,
        version_fingerprint=review['version_fingerprint'],active=True,parts=parts,
        delivered_at=now-timedelta(seconds=10),expires_at=now+timedelta(minutes=10),
        bindings=dict(bot=bindings.digest('bot',POLICY.bot_id),
            room=bindings.digest('room',POLICY.bot_id,POLICY.chat_id),
            message=bindings.digest('card-message@2',POLICY.bot_id,POLICY.chat_id,31),
            packet_receipt='7'*64,card_receipt='8'*64,
            parent_binding='9'*64,thread_id=None))
    card['bindings']['parent_binding'] = card_parent_binding(bindings,review,card)
    version = {'channel_copy': {'telegram':'Synthetic Telegram','x':'Synthetic X'}}
    source = dict(id=ids['source'],source_feed_id=ids['feed'],
        canonical_url='https://x.com/SquidRouter/status/123',
        published_at=now-timedelta(hours=1))
    asset = {'sha256':'b'*64}
    snapshot = private_snapshot(review,version,source,asset)
    token = signer.issue(snapshot,'s',POLICY.room_binding,
        now=int(now.timestamp()),expires_at=int(now.timestamp())+600)
    update = {'update_id':7,'callback_query':{'id':'synthetic-source-click',
        'from':{'id':201,'is_bot':False},'data':'ce1:'+token,
        'message':{'message_id':31,'date':int(now.timestamp()),
            'from':{'id':POLICY.bot_id,'is_bot':True},
            'chat':{'id':POLICY.chat_id,'type':'supergroup'}}}}

    class Connection:
        autocommit = False
        def __init__(self, latest):
            self.latest = latest
            self.statements = []
            self.actions = 0
            self.exit_kind = None
        def __enter__(self): return self
        def __exit__(self, kind, _value, _tb): self.exit_kind = kind
        def cursor(self):
            connection = self
            class Cursor:
                def __enter__(self): return self
                def __exit__(self, *_args): pass
                def execute(self, sql, params=()):
                    connection.statements.append((sql,params))
                    if 'from public.content_items i' in sql:
                        self.rows = [(ids['item'],ids['card'],ids['review'])]
                    elif 'to_jsonb(r) from private.content_ops_button_reviews' in sql:
                        self.rows = [(review,)]
                    elif 'to_jsonb(c) from private.content_ops_button_cards' in sql:
                        self.rows = [(card,)]
                    elif 'from private.content_ops_button_identities' in sql:
                        self.rows = [(POLICY.reviewers[0][1],)]
                    elif 'from private.content_ops_button_reviewers' in sql:
                        self.rows = [(POLICY.reviewers[0][1],)]
                    elif 'from public.workspace_clients' in sql:
                        self.rows = [('squid',)]
                    elif 'from public.content_versions v' in sql:
                        self.rows = [(version,source,asset)]
                    elif 'select clock_timestamp()' in sql:
                        self.rows = [(now,)]
                    elif 'from public.source_items s' in sql:
                        self.rows = [(connection.latest,)]
                    elif 'record_content_ops_button_action' in sql:
                        connection.actions += 1
                        self.rows = [({'status':'checked','reused':False,
                            'execution_authorized':False},)]
                    else:
                        raise AssertionError('unexpected SQL')
                def fetchone(self): return self.rows[0] if self.rows else None
                def fetchall(self): return self.rows
            return Cursor()

    def call(connection):
        return PostgresPrivateReviewOwner(lambda:connection,bindings).handle_update(
            enabled=True,raw_body=json.dumps(update).encode(),
            headers=[('Content-Type','application/json'),
                ('X-Telegram-Bot-Api-Secret-Token',POLICY.webhook_secret)],
            policy=POLICY,signer=signer,now=int(now.timestamp()))

    old = Connection(ids['source'])
    assert call(old) == {'status':'checked','reused':False,'public_send_attempted':False}
    assert old.actions == 1 and old.exit_kind is None
    assert sum('from public.source_items s' in sql for sql,_ in old.statements) == 2
    newer = Connection(ids['newer'])
    with pytest.raises(ReviewIngressError,match='^review_private_owner_outcome_unknown$'):
        call(newer)
    assert newer.actions == 0 and newer.exit_kind is ReviewIngressError
    latest_sql,latest_params = next((sql,params) for sql,params in newer.statements
        if 'from public.source_items s' in sql)
    assert 'order by s.published_at desc nulls last, s.id desc limit 1' in latest_sql
    assert latest_params == (ids['workspace'],'squid',ids['feed'])
