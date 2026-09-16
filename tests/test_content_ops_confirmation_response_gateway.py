"""In-process HTTP/ASGI and synthetic store cursor; zero external network."""
import asyncio
from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from core.content_ops.confirmation_response_gateway import (
    PATH, MAX_BODY, ConfirmationGatewayError, ConfirmationSourceGateway,
    ConfirmationGatewaySink, encode_envelope, decode_envelope,
)
from core.content_ops.confirmation_response_store import PostgresConfirmationSourceStore
from test_content_ops_confirmation_response_store import auth, sealed, Connection
from test_content_ops_confirmation_response_capture import Rig, ACK, Stream


SHA = 'a'*40
TOKEN = 'synthetic_gateway_token_' + 'b'*32
ORIGIN = 'https://owner.example.invalid'


def wire(): return encode_envelope(sealed(), SHA)


def gateway(conn=None, **changes):
    calls = []
    def connect():
        calls.append(True)
        return conn if conn is not None else Connection()
    options = dict(enabled=True, token=TOKEN, release_sha=SHA, runtime_sha=SHA,
        authenticator=auth(), store=PostgresConfirmationSourceStore(connect, authenticator=auth()))
    options.update(changes)
    return ConfirmationSourceGateway(**options), calls


def sink(app=None, **changes):
    opts = dict(enabled=True, origin=ORIGIN, token=TOKEN, release_sha=SHA, runtime_sha=SHA,
        authenticator=auth(), transport=httpx.ASGITransport(app=app or gateway()[0]))
    opts.update(changes)
    return ConfirmationGatewaySink(**opts)


async def invoke(app, *, body=None, headers=None, events=None, **scope_changes):
    raw = wire() if body is None else body
    h = [(b'authorization', ('Bearer '+TOKEN).encode()), (b'x-content-ops-release-sha', SHA.encode()),
        (b'content-type', b'application/json'), (b'content-length', str(len(raw)).encode())]
    if headers is not None: h = headers
    scope = dict(type='http', path=PATH, method='POST', query_string=b'', headers=h)
    scope.update(scope_changes)
    queue = list(events if events is not None else [dict(type='http.request', body=raw)])
    reads, output = [], []
    async def receive(): reads.append(True); return queue.pop(0)
    async def send(value): output.append(value)
    await app(scope, receive, send)
    return output[0]['status'], json.loads(output[1]['body']), reads, output


def test_codec_preserves_original_bytes_time_and_seal():
    assert decode_envelope(wire(), SHA, auth()) == sealed()
    value = replace(sealed(), receipt=replace(sealed().receipt, raw_response=b'x'*32768))
    value = auth().seal(enabled=True, receipt=value.receipt)
    assert len(encode_envelope(value, SHA)) < MAX_BODY
    assert decode_envelope(encode_envelope(value, SHA), SHA, auth()) == value


@pytest.mark.parametrize('field,value', [('schema', 'publish'), ('release_sha', 'b'*40),
    ('delivery_id', 'bad'), ('request_sha256', 'b'*64), ('http_status', True),
    ('raw_response_b64', '!invalid'), ('observed_at', '2026-09-16T00:00:00'),
    ('relay_seal', 'b'*64), ('extra', 'approved')])
def test_wire_changes_cannot_access_store(field, value):
    data = json.loads(wire()); data[field] = value
    app, calls = gateway()
    status, result, _, _ = asyncio.run(invoke(app,
        body=json.dumps(data, sort_keys=True, separators=(',', ':')).encode()))
    assert status == 400 and result['execution_authorized'] is False and not calls


@pytest.mark.parametrize('body', [b'', b'[]', b'{}', b'\xff', b'x'*(MAX_BODY+1),
    wire()+b' ', b'{"schema":"confirmation-source@1",'+wire()[1:],
    wire().replace(b'"http_status":200', b'"http_status":NaN')])
def test_malformed_noncanonical_or_duplicate_json_refused(body):
    app, calls = gateway()
    status, _, _, _ = asyncio.run(invoke(app, body=body))
    assert status == 400 and not calls


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_disabled_server_and_sink_do_not_read_or_connect(enabled):
    app = ConfirmationSourceGateway(enabled=enabled)
    status, _, reads, _ = asyncio.run(invoke(app))
    assert status == 503 and not reads
    assert asyncio.run(ConfirmationGatewaySink(enabled=enabled).record(envelope=object())) is None


@pytest.mark.parametrize('changes', [dict(token=None), dict(token='short'),
    dict(release_sha='short'), dict(runtime_sha='b'*40), dict(authenticator=None), dict(store=None)])
def test_bad_server_configuration_stops_before_body(changes):
    app, calls = gateway(**changes)
    status, _, reads, _ = asyncio.run(invoke(app))
    assert status == 503 and not reads and not calls


@pytest.mark.parametrize('changes', [dict(method='GET'), dict(method='OPTIONS'),
    dict(path=PATH+'/'), dict(query_string=b'action=publish')])
def test_endpoint_has_no_generic_action_or_cors_or_query_interface(changes):
    app, calls = gateway()
    status, _, reads, _ = asyncio.run(invoke(app, **changes))
    assert status == 400 and not reads and not calls


@pytest.mark.parametrize('kind', ['missing', 'wrong', 'cookie', 'duplicate', 'release',
    'duplicate-release', 'content-type', 'encoding', 'length'])
def test_auth_and_headers_checked_before_body_or_db(kind):
    headers = [(b'authorization', ('Bearer '+TOKEN).encode()),
        (b'x-content-ops-release-sha', SHA.encode()), (b'content-type', b'application/json')]
    if kind == 'missing': headers.pop(0)
    if kind == 'wrong': headers[0] = (b'authorization', b'Bearer wrong')
    if kind == 'cookie': headers.append((b'cookie', b'shared-session'))
    if kind == 'duplicate': headers.append((b'Authorization', ('Bearer '+TOKEN).encode()))
    if kind == 'release': headers[1] = (b'x-content-ops-release-sha', b'b'*40)
    if kind == 'duplicate-release': headers.append(headers[1])
    if kind == 'content-type': headers[2] = (b'content-type', b'text/plain')
    if kind == 'encoding': headers.append((b'content-encoding', b'gzip'))
    if kind == 'length': headers.append((b'content-length', str(MAX_BODY+1).encode()))
    app, calls = gateway()
    status, result, reads, _ = asyncio.run(invoke(app, headers=headers))
    assert status in (400, 401, 409) and not reads and not calls
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize('events', [
    [dict(type='http.disconnect')],
    [dict(type='http.request', body=b'x'*MAX_BODY, more_body=True), dict(type='http.request', body=b'x')],
    [dict(type='http.request', body=wire()[:-1])],
])
def test_disconnect_chunked_overflow_and_length_mismatch_do_not_write(events):
    app, calls = gateway()
    status, _, _, _ = asyncio.run(invoke(app, events=events))
    assert status == 400 and not calls


def test_server_bounded_body_timeout(monkeypatch):
    monkeypatch.setattr('core.content_ops.confirmation_response_gateway.TIMEOUT_SECONDS', .01)
    app, calls = gateway()
    scope = dict(type='http', path=PATH, method='POST', headers=[
        (b'authorization', ('Bearer '+TOKEN).encode()), (b'x-content-ops-release-sha', SHA.encode()),
        (b'content-type', b'application/json')])
    output = []
    async def receive(): await asyncio.Event().wait()
    async def send(value): output.append(value)
    asyncio.run(app(scope, receive, send))
    assert output[0]['status'] == 400 and not calls


@pytest.mark.parametrize('reused', [False, True])
def test_http_sink_to_asgi_to_store_returns_bound_minimal_ack(reused):
    conn = Connection(reused=reused)
    app, calls = gateway(conn)
    result = asyncio.run(sink(app).record(envelope=sealed()))
    assert result == dict(ACK, reused=reused) and len(calls) == 1 and conn.exits == [None]
    assert sum(q.strip().startswith('insert') for q, _ in conn.calls) == int(not reused)


def test_full_capture_http_gateway_store_chain_preserves_original_receipt():
    conn = Connection()
    app, calls = gateway(conn)
    r = Rig(sink=sink(app).record)
    asyncio.run(r.post())
    assert len(r.writes()) == len(calls) == 1 and conn.exits == [None]
    assert any(q.strip().startswith('insert') for q, _ in conn.calls)


def test_commit_ack_lost_never_retries_store_or_telegram():
    conn = Connection(commit_error=True)
    app, calls = gateway(conn)
    r = Rig(sink=sink(app).record)
    with pytest.raises(ValueError): asyncio.run(r.post())
    with pytest.raises(ValueError): asyncio.run(r.post())
    assert len(r.writes()) == len(calls) == len(conn.exits) == 1


def test_success_ack_binds_exact_body_and_release_and_has_no_private_payload():
    app, _ = gateway()
    status, result, _, output = asyncio.run(invoke(app))
    assert status == 200 and result == dict(ACK, release_sha=SHA,
        envelope_sha256=hashlib.sha256(wire()).hexdigest())
    assert (b'cache-control', b'no-store') in output[0]['headers']
    assert not set(result) & {'delivery_id', 'raw_response_b64', 'relay_seal', 'observed_at'}


@pytest.mark.parametrize('origin', ['http://owner.example.invalid', 'https://owner.example.invalid/',
    'https://user:password@owner.example.invalid', 'https://owner.example.invalid?secret=x',
    'https://owner.example.invalid/#x', 'https://owner.example.invalid:443',
    'https://127.0.0.1', 'https://localhost', None])
def test_bad_sink_origin_fails_before_request(origin):
    calls = []
    def handler(request): calls.append(request); raise AssertionError()
    client = sink(origin=origin, transport=httpx.MockTransport(handler))
    with pytest.raises(ConfirmationGatewayError): asyncio.run(client.record(envelope=sealed()))
    assert not calls


@pytest.mark.parametrize('change', ['sha', 'hash', 'authority', 'reused', 'extra', 'duplicate', 'oversized', 'redirect'])
def test_invalid_gateway_response_never_falls_back_or_retries(change):
    calls = []
    value = dict(ACK, release_sha=SHA, envelope_sha256=hashlib.sha256(wire()).hexdigest())
    if change == 'sha': value['release_sha'] = 'b'*40
    if change == 'hash': value['envelope_sha256'] = 'b'*64
    if change == 'authority': value['execution_authorized'] = True
    if change == 'reused': value['reused'] = 1
    if change == 'extra': value['private_body'] = 'secret'
    raw = json.dumps(value).encode()
    if change == 'duplicate': raw = b'{"status":"other",' + raw[1:]
    if change == 'oversized': raw = b'x'*2049
    stream = Stream([raw])
    def handler(request):
        calls.append(request)
        return httpx.Response(302 if change == 'redirect' else 200, stream=stream,
            headers={'Content-Type':'application/json', 'Location':'https://other.example.invalid'})
    with pytest.raises(ConfirmationGatewayError, match='^confirmation_gateway_unconfirmed$'):
        asyncio.run(sink(transport=httpx.MockTransport(handler)).record(envelope=sealed()))
    assert len(calls) == 1 and stream.closed


def test_sink_refuses_invalid_seal_before_http():
    calls = []
    def handler(request): calls.append(request); raise AssertionError()
    bad = replace(sealed(), relay_seal='e'*64)
    with pytest.raises(ConfirmationGatewayError):
        asyncio.run(sink(transport=httpx.MockTransport(handler)).record(envelope=bad))
    assert not calls


def test_server_checks_mac_with_its_own_key_before_store():
    from core.content_ops.confirmation_response_store import ConfirmationResponseAuthenticator
    app, calls = gateway(authenticator=ConfirmationResponseAuthenticator(b'another-dedicated-test-key'*2))
    status, _, _, _ = asyncio.run(invoke(app))
    assert status == 400 and not calls


@pytest.mark.parametrize('failure', ['timeout', 'lost-response'])
def test_sink_deadline_and_lost_response_never_retry(failure, monkeypatch):
    monkeypatch.setattr('core.content_ops.confirmation_response_gateway.TIMEOUT_SECONDS', .01)
    calls = []
    async def handler(request):
        calls.append(request)
        if failure == 'timeout': await asyncio.Event().wait()
        raise httpx.ReadError('private unavailable response')
    with pytest.raises(ConfirmationGatewayError, match='^confirmation_gateway_unconfirmed$'):
        asyncio.run(sink(transport=httpx.MockTransport(handler)).record(envelope=sealed()))
    assert len(calls) == 1


def test_no_private_values_logged_or_in_repr(caplog):
    caplog.set_level('INFO', logger='httpx')
    app, _ = gateway()
    client = sink(app)
    asyncio.run(client.record(envelope=sealed()))
    assert TOKEN not in caplog.text and sealed().relay_seal not in caplog.text
    assert TOKEN not in repr(client) and TOKEN not in repr(app)
