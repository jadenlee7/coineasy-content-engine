"""Synthetic HTTP streams through the existing relay; no external sends."""
import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from core.content_ops.confirmation_response_capture import (
    ConfirmationCaptureError, ConfirmationResponseCapture,
)
from core.content_ops.confirmation_delivery_owner import confirmation_send_request
from core.content_ops.confirmation_response_store import PostgresConfirmationSourceStore
from core.content_ops.worker import TelegramReviewRelay, ReviewError
from test_content_ops_worker import settings
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_markup_confirmation import target, moment
from test_content_ops_confirmation_delivery_owner import receipt, DELIVERY
from test_content_ops_confirmation_response_store import auth, Connection
from test_content_ops_review_cancellation import BOT, ROOM, NOW, uid


ACK = dict(status='confirmation_source_recorded', reused=False, execution_authorized=False)


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def aclose(self):
        self.closed = True


class Rig:
    def __init__(self, *, enabled=True, chunks=None, status=200, headers=None,
                 sink=None, times=None, capture_changes=None):
        self.identity = fixture()[0]
        self.settings = replace(settings(), relay_bot_token=f'{BOT}:'+'a'*35, relay_chat_id=str(ROOM))
        self.body = json.loads(confirmation_send_request(target(), self.identity['plan'],
            self.identity['bindings']))['body']
        self.requests, self.envelopes = [], []
        self.stream = Stream(chunks if chunks is not None else [receipt().raw_response[:7], receipt().raw_response[7:]])
        self.times = iter(times if times is not None else [moment(), receipt().observed_at])

        async def record(*, envelope):
            self.envelopes.append(envelope)
            return dict(ACK)

        opts = dict(enabled=enabled, target=target(), delivery_id=DELIVERY,
            authenticator=auth(), sink=sink or record, clock=lambda:next(self.times), **self.identity)
        opts.update(capture_changes or {})
        self.capture = ConfirmationResponseCapture(**opts)

        async def handler(request):
            self.requests.append(request)
            method = request.url.path.rsplit('/', 1)[-1]
            if method == 'getMe':
                result = dict(id=BOT, is_bot=True)
            elif method == 'getChat':
                result = dict(id=ROOM, type='supergroup')
            elif method == 'getChatMember':
                result = dict(status='member', user=dict(id=BOT, is_bot=True))
            else:
                return httpx.Response(status, headers=headers, stream=self.stream)
            return httpx.Response(200, json=dict(ok=True, result=result))

        self.relay = TelegramReviewRelay(self.settings, transport=httpx.MockTransport(handler),
            response_capture=self.capture)

    async def post(self, *, preflight=True, method='sendMessage', body=None):
        if preflight:
            await self.relay.preflight()
        # Internal seam only: no production owner/runner calls this prompt path.
        return await self.relay._post(method, self.body if body is None else body)

    def writes(self):
        return [r for r in self.requests if r.url.path.rsplit('/', 1)[-1] not in
                {'getMe', 'getChat', 'getChatMember'}]


def test_original_stream_bytes_and_time_are_sealed_once_and_returned_unchanged():
    r = Rig()
    response = asyncio.run(r.post())
    assert response.status_code == 200 and response.content == receipt().raw_response
    assert len(r.writes()) == len(r.envelopes) == 1 and r.stream.closed
    assert auth().verify(r.envelopes[0]) == receipt()
    assert r.writes()[0].headers['accept-encoding'] == 'identity'
    assert json.loads(r.writes()[0].content) == r.body
    with pytest.raises(ConfirmationCaptureError):
        asyncio.run(r.post())
    assert len(r.writes()) == len(r.envelopes) == 1


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_capture_off_preserves_existing_transport_without_sink_or_clock(enabled):
    r = Rig(enabled=enabled, times=[])
    assert asyncio.run(r.post()).content == receipt().raw_response
    assert len(r.writes()) == 1 and not r.envelopes


def test_disabled_observer_can_be_constructed_without_dependencies():
    c = ConfirmationResponseCapture()
    assert c.begin(settings=None, method=None, body=None, verified=None) is None
    value = object()
    assert asyncio.run(c.collect(value)) is value


@pytest.mark.parametrize('changes', [dict(delivery_id='bad'), dict(authenticator=None),
    dict(sink=lambda **kw:ACK), dict(clock=None), dict(actor_id=uid(888)),
    dict(target=replace(target(), plan_seal='e'*64)),
    dict(plan=replace(fixture()[0]['plan'], seal='e'*64))])
def test_invalid_capture_fails_before_write(changes):
    r = Rig(capture_changes=changes)
    with pytest.raises(ConfirmationCaptureError, match='^confirmation_capture_unconfirmed$'):
        asyncio.run(r.post())
    assert not r.writes() and not r.envelopes


@pytest.mark.parametrize('change', ['chat', 'text', 'keyboard', 'extra', 'thread', 'method', 'bot'])
def test_exact_request_and_relay_identity_required(change):
    r = Rig()
    method = 'sendMessage'
    if change == 'chat': r.body['chat_id'] = ROOM-1
    if change == 'text': r.body['text'] += 'changed'
    if change == 'keyboard': r.body['reply_markup'] = {}
    if change == 'extra': r.body['protect_content'] = True
    if change == 'thread': r.body['message_thread_id'] = 4
    if change == 'method': method = 'editMessageReplyMarkup'
    if change == 'bot': r.settings = replace(r.settings, relay_bot_token='99999:'+'b'*35); r.relay._settings = r.settings
    if change == 'bot':
        # Mark the synthetic preflight true to isolate capture's identity check.
        r.relay._verified = True
    with pytest.raises((ConfirmationCaptureError, ReviewError)):
        asyncio.run(r.post(method=method, preflight=change != 'bot'))
    assert not r.writes() and not r.envelopes


def test_preflight_required_and_bundle_cannot_bypass_capture_scope():
    r = Rig()
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post(preflight=False))
    with pytest.raises(ReviewError): asyncio.run(r.relay.send_bundle_part(None, b'anything'))
    assert not r.requests


@pytest.mark.parametrize('times', [[moment(NOW-1)], [moment(NOW+300)],
    [moment().replace(tzinfo=None)], [moment(), moment(NOW-1)],
    [moment(), moment(NOW+300)], [moment(), moment().replace(tzinfo=None)]])
def test_invalid_or_rewound_or_expired_time_never_reaches_sink(times):
    r = Rig(times=times)
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert not r.envelopes
    writes = len(r.writes())
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert len(r.writes()) == writes


@pytest.mark.parametrize('chunks,headers', [
    ([], None), ([b'x'*32768, b'x'], None), ([b'x'*32769], None),
    ([b'{}'], {'Content-Length':'32769'}), ([b'{}'], {'Content-Length':'3'}),
    ([b'{}'], {'Content-Length':'0'}), ([b'{}'], {'Content-Length':'-1'}),
    ([b'{}'], {'Content-Encoding':'gzip'}),
    ([b'{', httpx.ReadError('private provider error')], None),
])
def test_unbounded_partial_compressed_and_invalid_length_responses_are_unknown(chunks, headers):
    r = Rig(chunks=chunks, headers=headers)
    with pytest.raises(ConfirmationCaptureError, match='^confirmation_capture_unconfirmed$'):
        asyncio.run(r.post())
    assert len(r.writes()) == 1 and not r.envelopes and r.stream.closed
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert len(r.writes()) == 1


@pytest.mark.parametrize('status', [200, 302, 400, 429, 500])
def test_capture_does_not_mislabel_failure_or_redirect_as_success(status):
    r = Rig(status=status, chunks=[b'{"ok":false}'], headers={'Location':'https://example.invalid/'})
    response = asyncio.run(r.post())
    assert response.status_code == status
    assert auth().verify(r.envelopes[0]).http_status == status
    assert len(r.writes()) == 1  # No redirect or provider retry.


@pytest.mark.parametrize('ack', [None, {}, True, dict(ACK, execution_authorized=True),
    dict(ACK, reused=1), dict(ACK, status='sent'), dict(ACK, private_body='secret')])
def test_invalid_sink_ack_cannot_cause_resend(ack):
    calls = []
    async def sink(**kwargs): calls.append(kwargs); return ack
    r = Rig(sink=sink)
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert len(calls) == len(r.writes()) == 1


@pytest.mark.parametrize('commit_error', [False, True])
def test_existing_store_receives_actual_mock_transport_envelope_once(commit_error):
    conn = Connection(commit_error=commit_error)
    store = PostgresConfirmationSourceStore(lambda:conn, authenticator=auth())
    async def sink(*, envelope): return store.record(enabled=True, envelope=envelope)
    r = Rig(sink=sink)
    if commit_error:
        with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    else:
        assert asyncio.run(r.post()).content == receipt().raw_response
    assert len(conn.exits) == 1
    assert sum(sql.strip().startswith('insert') for sql, _ in conn.calls) == 1
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert len(r.writes()) == 1 and len(conn.exits) == 1


@pytest.mark.parametrize('where', ['read', 'sink'])
def test_task_cancellation_closes_stream_and_permanently_consumes_capture(where):
    async def sink(**kwargs): raise asyncio.CancelledError()
    r = Rig(chunks=[asyncio.CancelledError()] if where == 'read' else None,
            sink=sink if where == 'sink' else None)
    with pytest.raises(asyncio.CancelledError): asyncio.run(r.post())
    assert r.stream.closed
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert len(r.writes()) == 1


def test_concurrent_posts_consume_one_local_capture_not_two():
    r = Rig()
    async def run():
        await r.relay.preflight()
        return await asyncio.gather(r.post(preflight=False), r.post(preflight=False), return_exceptions=True)
    results = asyncio.run(run())
    assert sum(isinstance(x, httpx.Response) for x in results) == 1
    assert sum(isinstance(x, ConfirmationCaptureError) for x in results) == 1
    assert len(r.writes()) == len(r.envelopes) == 1


def test_slow_sink_is_bounded_without_retry(monkeypatch):
    monkeypatch.setattr('core.content_ops.worker.CONFIRMATION_CAPTURE_TIMEOUT_SECONDS', .01)
    calls = []
    async def sink(**kwargs):
        calls.append(kwargs)
        await asyncio.Event().wait()
    r = Rig(sink=sink)
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert r.stream.closed
    with pytest.raises(ConfirmationCaptureError): asyncio.run(r.post())
    assert len(calls) == len(r.writes()) == 1


def test_maximum_response_is_preserved_as_evidence_not_parsed_as_success():
    r = Rig(chunks=[b'x'*16384, b'x'*16384], headers={'Content-Length':'32768'})
    assert len(asyncio.run(r.post()).content) == 32768
    assert auth().verify(r.envelopes[0]).raw_response == b'x'*32768


def test_private_request_and_response_do_not_appear_in_logs(caplog):
    caplog.set_level('INFO', logger='httpx')
    r = Rig()
    asyncio.run(r.post())
    assert r.settings.relay_bot_token not in caplog.text
    assert str(ROOM) not in caplog.text and r.body['text'] not in caplog.text
    assert DELIVERY not in repr(r.capture)
