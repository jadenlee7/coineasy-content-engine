"""Offline typed reservation readback, synthetic identities and keyed evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_receipt import PromptReceiptError
from core.content_ops.prompt_reservation import (
    canonical_timestamp, card_parent_binding, load_reserved_attempt,
)
from core.content_ops.review_edit_ingress import EditBindings


def uid(n):
    return f'10000000-0000-4000-8000-{n:012d}'


def fixture():
    binding = EditBindings(b'synthetic-reservation-binding-key' * 2)
    now = datetime(2026, 9, 15, 12, 0, 5, 123456, tzinfo=timezone.utc)
    review = dict(id=uid(1), workspace_id=uid(2), client_id='yellow',
                  content_item_id=uid(3), content_version_id=uid(4),
                  version_fingerprint='f'*64, epoch=1, state='edit_requested')
    card = dict(id=uid(5), review_id=review['id'], epoch=0, version_fingerprint='f'*64,
        active=True, delivered_at=(now-timedelta(seconds=5)).isoformat(),
        expires_at=(now+timedelta(minutes=10)).isoformat(),
        bindings=dict(bot=binding.digest('bot',101),room=binding.digest('room',101,-301),
            message=binding.digest('card-message@2',101,-301,10), packet_receipt='a'*64,
            card_receipt='b'*64, parent_binding='0'*64,thread_id=None),
        parts=[dict(kind=kind, message_binding=binding.digest('card-message@2',101,-301,n),
                    payload_sha256='d'*64,outcome='sent')
               for n,kind in enumerate(('image','telegram','x'),1)])
    parent = card_parent_binding(binding, review, card)
    card['bindings']['parent_binding'] = parent
    attempt = dict(id=uid(6),card_id=card['id'],review_id=review['id'],actor_id=uid(7),
        epoch=1,edit_action_key='e'*64,version_fingerprint='f'*64,
        bot_binding=card['bindings']['bot'],room_binding=card['bindings']['room'],
        human_binding=binding.digest('human',101,201),thread_id=None,
        parent_binding_sha256=parent,
        expected_text_sha256=hashlib.sha256(prompt_instruction('edit_x').encode()).hexdigest(),
        started_at=(now-timedelta(seconds=2)).isoformat(),expires_at=card['expires_at'])
    kwargs = dict(bindings=binding,bot_id=101,chat_id=-301,human_id=201,thread_id=None,
                  observed_at=now,db_now=now+timedelta(seconds=1))
    return review,card,attempt,kwargs


def test_readback_preserves_id_and_conservatively_maps_seconds():
    r,c,a,k = fixture()
    result = load_reserved_attempt(r,c,a,**k)
    assert result.attempt_id == a['id']
    assert result.started_at == int(datetime.fromisoformat(a['started_at']).timestamp()) + 1
    assert result.expires_at == int(datetime.fromisoformat(a['expires_at']).timestamp())
    assert result.packet_receipt_sha256 == a['parent_binding_sha256']


def test_canonical_time_preserves_microseconds_and_normalizes_zone():
    value = datetime(2026,9,15,12,0,0,123456,tzinfo=timezone.utc)
    assert canonical_timestamp(value) == '2026-09-15T12:00:00.123456Z'
    assert canonical_timestamp(value.astimezone(timezone(timedelta(hours=9))).isoformat()) == canonical_timestamp(value)
    assert canonical_timestamp(value+timedelta(microseconds=1)) != canonical_timestamp(value)


def test_parent_is_timezone_stable_but_not_microsecond_insensitive():
    r,c,a,k = fixture()
    original = card_parent_binding(k['bindings'],r,c)
    changed = deepcopy(c)
    changed['delivered_at'] = datetime.fromisoformat(c['delivered_at']).astimezone(timezone(timedelta(hours=9))).isoformat()
    assert card_parent_binding(k['bindings'],r,changed) == original
    changed['delivered_at'] = (datetime.fromisoformat(c['delivered_at'])+timedelta(microseconds=1)).isoformat()
    assert card_parent_binding(k['bindings'],r,changed) != original


@pytest.mark.parametrize('field,value', [
    ('card_id',uid(99)),('review_id',uid(99)),('epoch',2),('epoch',True),
    ('actor_id','bad'),('version_fingerprint','e'*64),('bot_binding','e'*64),
    ('room_binding','e'*64),('human_binding','e'*64),('parent_binding_sha256','0'*64),
    ('expected_text_sha256','0'*64),('thread_id',True),('thread_id',1),
    ('started_at','infinity'),('expires_at','-infinity'),
    ('started_at','2026-09-15T12:00:03'),('edit_action_key','bad'),
])
def test_tampered_attempt_fails_closed(field,value):
    r,c,a,k = fixture(); a[field] = value
    with pytest.raises(PromptReceiptError,match='^prompt_registration_unknown$'):
        load_reserved_attempt(r,c,a,**k)


@pytest.mark.parametrize('mutation', ['inactive','held','part','parent','recycle','legacy','time'])
def test_card_and_parent_changes_cannot_reuse_reservation(mutation):
    r,c,a,k = fixture()
    if mutation=='inactive': c['active']=False
    if mutation=='held': r['state']='held'
    if mutation=='part': c['parts'][0]['payload_sha256']='9'*64
    if mutation=='parent': c['bindings']['parent_binding']='9'*64
    if mutation=='recycle': c['parts'][0]['message_binding']=c['bindings']['message']
    if mutation=='legacy':
        legacy=k['bindings'].digest('prompt-parent@1',c['id'])
        c['bindings']['parent_binding']=a['parent_binding_sha256']=legacy
    if mutation=='time': c['expires_at']=(datetime.fromisoformat(c['expires_at'])+timedelta(microseconds=1)).isoformat()
    with pytest.raises(PromptReceiptError,match='^prompt_registration_unknown$'):
        load_reserved_attempt(r,c,a,**k)


@pytest.mark.parametrize('case',['pre_start','same_second','at_expiry','after_db','db_expired','naive'])
def test_precise_chronology_cannot_be_relaxed_by_second_mapping(case):
    r,c,a,k=fixture(); start=datetime.fromisoformat(a['started_at'])
    if case=='pre_start': k['observed_at']=start-timedelta(microseconds=1)
    if case=='same_second': k['observed_at']=start+timedelta(microseconds=1)
    if case=='at_expiry': k['observed_at']=datetime.fromisoformat(a['expires_at'])
    if case=='after_db': k['db_now']=k['observed_at']-timedelta(microseconds=1)
    if case=='db_expired': k['db_now']=datetime.fromisoformat(a['expires_at'])
    if case=='naive': k['observed_at']=k['observed_at'].replace(tzinfo=None)
    with pytest.raises(PromptReceiptError,match='^prompt_registration_unknown$'):
        load_reserved_attempt(r,c,a,**k)
