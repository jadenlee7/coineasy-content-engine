import assert from 'node:assert/strict';
import { generateKeyPairSync, verify } from 'node:crypto';
import test from 'node:test';
import {
  InspectError,
  parseStrictJson,
  pgJsonbText,
  sha256,
  validateRequest,
  validateAuthorization,
  issueInspectJwt,
  validateInspectResponse,
} from '../scripts/lib/telegram-resolution-inspect.mjs';

// Entirely synthetic fixtures; no credentials, provider calls, or production IDs.
const NOW = new Date('2026-08-31T12:00:00Z');
const id = (n: number) => `10000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
const HASH_KEYS = [
  'delivery_request_sha256', 'publication_request_sha256', 'publication_response_sha256',
  'job_input_sha256', 'job_output_sha256', 'content_item_row_sha256',
  'content_version_row_sha256', 'publication_row_sha256', 'job_row_sha256',
  'publication_approval_row_sha256', 'asset_row_sha256', 'caption_sha256', 'asset_sha256',
];

function request(): any {
  return {
    schema_version: 'telegram-resolution-inspect-request@1',
    project_ref: 'abcdefghijklmnopqrst', environment: 'production', client_id: 'squid',
    release_sha: 'a'.repeat(40), workspace_id: id(1), content_item_id: id(2),
    content_version_id: id(3), publication_id: id(4), job_id: id(5),
    resolution_id: id(6), operator_approval_id: id(7),
    inspected_by: 'codex:synthetic-inspector', approved_by: 'operator:synthetic-approver',
    expires_at: '2026-08-31T13:00:00Z',
    public_audit: {
      schema_version: 'telegram-public-channel-audit@1',
      scan_source: 'public_telegram_web_history', public_channel: 'squid_kor_update',
      first_message_id: '9007199254740993', last_message_id: '9007199254741002',
      message_count: 10, checked_at: '2026-08-31T11:59:00Z',
      caption_match_count: 0, png_match_count: 0, snapshot_sha256: 'b'.repeat(64),
    },
  };
}

function authorization(validated = validateRequest(request(), NOW)): any {
  return {
    schema_version: 'telegram-resolution-inspect-authorization@1',
    authorization_id: id(14), request_sha256: validated.request_sha256,
    authorized_by: 'operator:synthetic-authorizer',
    authorized_at: '2026-08-31T11:59:50Z', expires_at: validated.request.expires_at,
    scope: 'issue_inspect_jwt_and_call_once', signing_key_id: id(15), max_rpc_calls: 1,
    resend_authorized: false, automatic_publication: false,
  };
}

function response(validated = validateRequest(request(), NOW)): any {
  const req = validated.request;
  const subject: any = {
    schema_version: 'exact-telegram-delivery-resolution@1',
    action: 'resolve_delivery_unknown_without_resend', client_id: 'squid',
    publication_approval_id: id(10), asset_id: id(11), delivery_attempt_id: id(12),
    delivery_started_at: '2026-08-31T11:40:00.123456+00:00',
    publication_status: 'delivery_unknown', job_status: 'failed',
    delivery_outcome: 'unknown', disposition: 'operator_closed_without_resend',
    public_observation: 'not_observed_at_checked_at', public_audit: req.public_audit,
    public_audit_sha256: validated.public_audit_sha256,
    approved_by: req.approved_by, expires_at: '2026-08-31T13:00:00+00:00',
    approved_release_sha: req.release_sha, resend_authorized: false,
    provider_calls: 0, database_claims: 0, publication_state_changed: false,
    job_state_changed: false,
    forbidden_actions: [
      'provider_call', 'claim', 'requeue', 'resend', 'mark_published',
      'create_publication', 'create_job',
    ],
  };
  for (const key of [
    'workspace_id', 'content_item_id', 'content_version_id', 'publication_id',
    'job_id', 'resolution_id', 'operator_approval_id',
  ]) subject[key] = req[key];
  for (const key of HASH_KEYS) subject[key] = 'c'.repeat(64);
  return {
    eligible: true, resolved: false, reused: false, resolution_id: req.resolution_id,
    publication_id: req.publication_id, job_id: req.job_id,
    content_item_id: req.content_item_id, content_version_id: req.content_version_id,
    delivery_outcome: 'unknown', disposition: 'operator_closed_without_resend',
    public_observation: 'not_observed_at_checked_at', approval_subject: subject,
    approval_subject_sha256: sha256(pgJsonbText(subject)), approved: false,
    approved_at: null, resend_authorized: false,
  };
}

function rejectsSafe(action: () => unknown, code?: string) {
  assert.throws(action, (error: any) => error instanceof InspectError
    && (!code || error.code === code) && error.message === error.code
    && !Object.hasOwn(error, 'cause'));
}

test('strict JSON detects duplicate keys after escape decoding and at every depth', () => {
  for (const raw of [
    '{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"nested":{"a":1,"a":1}}',
    '[{"nested":[{"x":null,"x":true}]}]',
  ]) rejectsSafe(() => parseStrictJson(raw), 'invalid_json');
  assert.deepEqual(parseStrictJson('{"a":[1,true,null,"escaped\\nline"]}'), {
    a: [1, true, null, 'escaped\nline'],
  });
});

test('strict JSON rejects invalid Unicode, NUL and malformed grammar', () => {
  for (const raw of [
    '"\\ud800"', '"\\udc00"', '"\ud800"', '"\\u0000"', '"\0"',
    '{"a":1,}', '[1,]', '01', 'true false', '{a:1}', '"\\x41"',
    '"raw\nline"', '"unterminated', '\ufeff{}', '{}/*comment*/',
  ]) rejectsSafe(() => parseStrictJson(raw), 'invalid_json');
  assert.equal(parseStrictJson('"\\ud83d\\ude00"'), '😀');
  assert.equal(parseStrictJson('"😀"'), '😀');
});

test('strict JSON enforces byte and depth bounds with safe integer literals only', () => {
  rejectsSafe(() => parseStrictJson('"é"', 3), 'json_too_large');
  assert.equal(parseStrictJson('"é"', 4), 'é');
  for (const raw of ['9007199254740992', '-9007199254740992', '9007199254740990.5',
    '1.0', '1e0', '1e309', '0.1', '-0', 'NaN', 'Infinity']) {
    rejectsSafe(() => parseStrictJson(raw), 'invalid_json');
  }
  assert.equal(parseStrictJson('9007199254740991'), Number.MAX_SAFE_INTEGER);
  assert.doesNotThrow(() => parseStrictJson(`${'['.repeat(8)}0${']'.repeat(8)}`));
  rejectsSafe(() => parseStrictJson(`${'['.repeat(9)}0${']'.repeat(9)}`), 'invalid_json');
});

test('strict JSON never assigns prototype setters', () => {
  const parsed = parseStrictJson('{"__proto__":{"polluted":true}}');
  assert.equal(Object.getPrototypeOf(parsed), Object.prototype);
  assert.equal(Object.hasOwn(parsed, '__proto__'), true);
  assert.equal(({} as any).polluted, undefined);
});

test('PostgreSQL canonical JSONB orders keys by byte length then byte value', () => {
  assert.equal(pgJsonbText({ b: 2, aa: 3, A: 4, a: 1, xyz: false }),
    '{"A": 4, "a": 1, "b": 2, "aa": 3, "xyz": false}');
  assert.equal(pgJsonbText({ items: [null, true, false, -7, 'a\n\t"\\/\b\f\r\x01'] }),
    '{"items": [null, true, false, -7, "a\\n\\t\\\"\\\\/\\b\\f\\r\\u0001"]}');
  assert.equal(sha256('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});

test('canonical encoder rejects unsupported values and accessors without evaluating them', () => {
  const cycle: any = {}; cycle.self = cycle;
  const getter = Object.defineProperty({}, 'secret', {
    enumerable: true, get() { throw new Error('getter-must-not-run'); },
  });
  const hidden = Object.defineProperty({}, 'hidden', { value: 1 });
  const sparse = new Array(1);
  const decorated: any = [1]; decorated.extra = 2;
  for (const value of [undefined, 1n, () => 1, Symbol(), NaN, Infinity, 0.1, -0,
    Number.MAX_SAFE_INTEGER + 1, '한글', '\0', '\ud800', { 'é': 1 }, new Date(),
    cycle, getter, hidden, sparse, decorated]) {
    rejectsSafe(() => pgJsonbText(value), 'invalid_canonical_value');
  }
});

test('request validator derives stable hashes, exact RPC payload and frozen copies', () => {
  const original = request();
  const validated = validateRequest(original, NOW);
  assert.equal(validated.request_sha256, sha256(pgJsonbText(original)));
  assert.equal(validated.public_audit_sha256, sha256(pgJsonbText(original.public_audit)));
  assert.equal(Object.keys(validated.rpc_payload).length, 12);
  assert.equal(validated.rpc_payload.target_publication_id, original.publication_id);
  assert.equal(Object.hasOwn(validated.rpc_payload, 'project_ref'), false);
  assert.equal(Object.hasOwn(validated.rpc_payload, 'target_approved_at'), false);
  assert.equal(Object.isFrozen(validated.request.public_audit), true);
  original.public_audit.message_count = 9;
  assert.equal(validated.request.public_audit.message_count, 10);
});

test('request schemas reject missing/extra keys, wrong client and unsafe identities', () => {
  const mutations = [
    (r: any) => { r.extra = true; }, (r: any) => { delete r.approved_by; },
    (r: any) => { r.schema_version = 'other'; }, (r: any) => { r.client_id = 'yellow'; },
    (r: any) => { r.environment = 'preview'; }, (r: any) => { r.project_ref = '../example'; },
    (r: any) => { r.project_ref = 'A'.repeat(20); }, (r: any) => { r.release_sha = 'A'.repeat(40); },
    (r: any) => { r.resolution_id = r.job_id; },
    (r: any) => { r.job_id = '00000000-0000-0000-0000-000000000000'; },
    (r: any) => { r.job_id = id(0xff).toUpperCase(); },
    (r: any) => { r.inspected_by = 'operator\nsecret'; },
    (r: any) => { r.approved_by = 'https://unsafe.example'; },
  ];
  for (const change of mutations) {
    const value = request(); change(value);
    rejectsSafe(() => validateRequest(value, NOW), 'invalid_request');
  }
});

test('request expiry and audit timestamps are valid canonical UTC with bounded age', () => {
  for (const expires of ['2026-08-31T12:00:00Z', '2026-08-31T14:00:01Z',
    '2026-08-31T13:00:00+00:00', '2026-08-31T13:00:00.000Z',
    '2026-02-30T13:00:00Z', '2026-08-31T24:00:00Z']) {
    const value = request(); value.expires_at = expires;
    rejectsSafe(() => validateRequest(value, NOW), 'invalid_request');
  }
  for (const checked of ['2026-08-31T12:00:01Z', '2026-08-31T11:29:59Z',
    '2026-02-30T11:59:00Z', '2026-08-31T11:59:60Z']) {
    const value = request(); value.public_audit.checked_at = checked;
    rejectsSafe(() => validateRequest(value, NOW), 'invalid_request');
  }
  const boundary = request(); boundary.expires_at = '2026-08-31T14:00:00Z';
  boundary.public_audit.checked_at = '2026-08-31T11:30:00Z';
  assert.doesNotThrow(() => validateRequest(boundary, NOW));
});

test('public audit requires explicit zero matches and exact canonical bigint strings', () => {
  const mutations = [
    (a: any) => { a.message_count = true; }, (a: any) => { a.message_count = 1001; },
    (a: any) => { a.message_count = 11; }, (a: any) => { a.message_count = 0; },
    (a: any) => { a.caption_match_count = false; }, (a: any) => { a.png_match_count = 1; },
    (a: any) => { delete a.caption_match_count; }, (a: any) => { a.first_message_id = 100; },
    (a: any) => { a.first_message_id = '01'; }, (a: any) => { a.first_message_id = '0'; },
    (a: any) => { a.first_message_id = '9007199254741003'; },
    (a: any) => { a.last_message_id = '9223372036854775808'; },
    (a: any) => { a.snapshot_sha256 = 'not-a-hash'; },
    (a: any) => { a.public_channel = 'unapproved_channel'; },
    (a: any) => { a.extra = 'provider-payload'; },
  ];
  for (const change of mutations) {
    const value = request(); change(value.public_audit);
    rejectsSafe(() => validateRequest(value, NOW), 'invalid_request');
  }
});

test('authorization binds exact request hash, scope, issuer, expiry and signing kid', () => {
  const validated = validateRequest(request(), NOW);
  const valid = authorization(validated);
  assert.deepEqual(validateAuthorization(valid, validated, NOW), valid);
  const mutations = [
    (a: any) => { a.extra = true; }, (a: any) => { a.request_sha256 = 'd'.repeat(64); },
    (a: any) => { a.max_rpc_calls = 2; }, (a: any) => { a.max_rpc_calls = true; },
    (a: any) => { a.scope = 'approve_and_resolve'; },
    (a: any) => { a.resend_authorized = true; },
    (a: any) => { a.automatic_publication = true; },
    (a: any) => { a.authorized_at = '2026-08-31T12:00:01Z'; },
    (a: any) => { a.authorized_at = '2026-08-31T11:29:59Z'; },
    (a: any) => { a.expires_at = '2026-08-31T13:00:01Z'; },
    (a: any) => { a.signing_key_id = 'arbitrary-key'; },
    (a: any) => { a.authorization_id = '00000000-0000-0000-0000-000000000000'; },
  ];
  for (const change of mutations) {
    const value = authorization(validated); change(value);
    rejectsSafe(() => validateAuthorization(value, validated, NOW), 'invalid_authorization');
  }
  rejectsSafe(() => validateAuthorization(valid, {
    ...validated, request_sha256: 'e'.repeat(64),
  }, NOW), 'invalid_authorization');
});

test('ES256 signer emits only exact inspect authority and a verifiable P1363 signature', () => {
  const { privateKey, publicKey } = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const validated = validateRequest(request(), NOW);
  const auth = authorization(validated);
  const jwk = { ...privateKey.export({ format: 'jwk' }), alg: 'ES256', kid: auth.signing_key_id, use: 'sig' };
  const token = issueInspectJwt(validated, auth, jwk, NOW);
  const [headerText, payloadText, signatureText] = token.split('.');
  const header = JSON.parse(Buffer.from(headerText, 'base64url').toString());
  const claims = JSON.parse(Buffer.from(payloadText, 'base64url').toString());
  assert.deepEqual(header, { alg: 'ES256', typ: 'JWT', kid: auth.signing_key_id });
  const signature = Buffer.from(signatureText, 'base64url');
  assert.equal(signature.length, 64);
  assert.equal(verify('sha256', Buffer.from(`${headerText}.${payloadText}`), {
    key: publicKey, dsaEncoding: 'ieee-p1363',
  }, signature), true);
  assert.equal(claims.capability, 'telegram_delivery_unknown_inspect');
  assert.equal(claims.role, 'coineasy_telegram_resolution');
  assert.equal(claims.iss, 'https://abcdefghijklmnopqrst.supabase.co/auth/v1');
  assert.equal(claims.aud, 'authenticated');
  assert.equal(claims.sub, validated.request.inspected_by);
  assert.equal(claims.jti, validated.request.resolution_id);
  assert.equal(claims.public_audit_sha256, validated.public_audit_sha256);
  assert.equal(claims.approved_by, validated.request.approved_by);
  assert.equal(claims.expires_at, validated.request.expires_at);
  assert.equal(claims.max_external_actions, 0);
  assert.equal(claims.resend_authorized, false);
  assert.equal(claims.automatic_publication, false);
  assert.equal(claims.iat, NOW.getTime() / 1000);
  assert.equal(claims.nbf, claims.iat);
  assert.equal(claims.exp, claims.iat + 3600);
  assert.equal(Object.hasOwn(claims, 'approval_subject_sha256'), false);
  assert.equal(Object.hasOwn(claims, 'public_audit'), false);
});

test('signer refuses shared secrets, public-only/wrong-key JWK and non-inspect authorization', () => {
  const { privateKey } = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const validated = validateRequest(request(), NOW);
  const auth = authorization(validated);
  const base: any = { ...privateKey.export({ format: 'jwk' }), alg: 'ES256', kid: auth.signing_key_id };
  const publicOnly = { ...base }; delete publicOnly.d;
  for (const value of [
    { kty: 'oct', alg: 'HS256', k: 'synthetic-secret', kid: auth.signing_key_id },
    publicOnly, { ...base, alg: 'HS256' }, { ...base, crv: 'P-384' },
    { ...base, kid: id(16) }, { ...base, use: 'enc' }, { ...base, d: 'bad' },
    { ...base, unexpected: 'must-not-propagate' },
  ]) rejectsSafe(() => issueInspectJwt(validated, auth, value, NOW), 'invalid_signing_key');
  rejectsSafe(() => issueInspectJwt(validated, { ...auth, scope: 'resolve' }, base, NOW), 'invalid_authorization');
});

test('response accepts exact complete fresh subject, preserves DB timestamp spelling and freezes it', () => {
  const validated = validateRequest(request(), NOW);
  const raw = response(validated);
  const result = validateInspectResponse(JSON.stringify(raw), validated, NOW);
  assert.deepEqual(result, raw);
  assert.equal(result.approval_subject.delivery_started_at, '2026-08-31T11:40:00.123456+00:00');
  assert.equal(Object.isFrozen(result.approval_subject.public_audit), true);
  assert.equal(Object.isFrozen(result.approval_subject.forbidden_actions), true);
});

test('response refuses missing or extra fields, prior approval/resolution and raw provider errors', () => {
  const validated = validateRequest(request(), NOW);
  for (const change of [
    (r: any) => { r.extra = 'secret-payload'; }, (r: any) => { delete r.approved; },
    (r: any) => { r.resolved = true; }, (r: any) => { r.reused = true; },
    (r: any) => { r.approved = true; }, (r: any) => { r.approved_at = NOW.toISOString(); },
    (r: any) => { r.eligible = false; }, (r: any) => { r.resend_authorized = true; },
    (r: any) => { r.publication_id = id(99); },
    (r: any) => { r.approval_subject_sha256 = 'f'.repeat(64); },
  ]) {
    const raw = response(validated); change(raw);
    rejectsSafe(() => validateInspectResponse(raw, validated, NOW), 'invalid_inspect_response');
  }
  rejectsSafe(() => validateInspectResponse('{"message":"synthetic-provider-error"}', validated, NOW), 'invalid_inspect_response');
});

test('response checks every subject pin and no-authority promise even with recomputed hash', () => {
  const validated = validateRequest(request(), NOW);
  const changes = [
    (s: any) => { s.extra = true; }, (s: any) => { delete s.asset_sha256; },
    (s: any) => { s.job_id = id(99); }, (s: any) => { s.workspace_id = id(98); },
    (s: any) => { s.operator_approval_id = id(97); },
    (s: any) => { s.approved_by = 'operator:other'; },
    (s: any) => { s.approved_release_sha = 'b'.repeat(40); },
    (s: any) => { s.publication_status = 'published'; },
    (s: any) => { s.disposition = 'not_delivered'; },
    (s: any) => { s.provider_calls = 1; }, (s: any) => { s.database_claims = 1; },
    (s: any) => { s.job_state_changed = true; },
    (s: any) => { s.forbidden_actions = s.forbidden_actions.slice(1); },
    (s: any) => { s.asset_id = s.job_id; },
    (s: any) => { s.delivery_attempt_id = '00000000-0000-0000-0000-000000000000'; },
    (s: any) => { s.public_audit_sha256 = 'e'.repeat(64); },
    (s: any) => { s.public_audit = { ...s.public_audit, snapshot_sha256: 'e'.repeat(64) }; },
  ];
  for (const change of changes) {
    const raw = response(validated); change(raw.approval_subject);
    raw.approval_subject_sha256 = sha256(pgJsonbText(raw.approval_subject));
    rejectsSafe(() => validateInspectResponse(raw, validated, NOW), 'invalid_inspect_response');
  }
});

test('response verifies timestamp validity, exact expiry and at least ten minutes after attempt', () => {
  const validated = validateRequest(request(), NOW);
  for (const [key, value] of [
    ['expires_at', '2026-08-31T13:00:00.000001+00:00'],
    ['expires_at', '2026-08-31T14:00:00+01:00'],
    ['delivery_started_at', '2026-08-31T11:49:00.000001+00:00'],
    ['delivery_started_at', '2026-02-30T11:40:00+00:00'],
    ['delivery_started_at', '2026-08-31T11:40:00.1234567+00:00'],
  ]) {
    const raw = response(validated); raw.approval_subject[key] = value;
    raw.approval_subject_sha256 = sha256(pgJsonbText(raw.approval_subject));
    rejectsSafe(() => validateInspectResponse(raw, validated, NOW), 'invalid_inspect_response');
  }
  const boundary = response(validated);
  boundary.approval_subject.delivery_started_at = '2026-08-31T11:49:00Z';
  boundary.approval_subject.expires_at = '2026-08-31T13:00:00.000000+00:00';
  boundary.approval_subject_sha256 = sha256(pgJsonbText(boundary.approval_subject));
  assert.doesNotThrow(() => validateInspectResponse(boundary, validated, NOW));
});

test('errors never echo arbitrary code strings or attach underlying errors', () => {
  const error = new InspectError('synthetic-sensitive-input');
  assert.equal(error.code, 'invalid_request');
  assert.equal(error.message, 'invalid_request');
  assert.equal(error.stack?.includes('synthetic-sensitive-input'), false);
  for (const code of [
    'arguments_invalid', 'inspect_arguments_required', 'secret_descriptors_invalid',
    'inspect_transport_unknown', 'authorization_already_attempted',
    'attempt_ledger_close_failed', 'inspect_http_status_rejected',
  ]) assert.equal(new InspectError(code).code, code);
});
