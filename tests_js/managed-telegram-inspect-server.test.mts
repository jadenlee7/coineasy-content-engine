import assert from 'node:assert/strict';
import test from 'node:test';
import { generateKeyPairSync, sign } from 'node:crypto';
import { request as httpRequest } from 'node:http';
import { loadConfig, assertRuntimeFlags } from '../tools/managed-telegram-inspect/config.mjs';
import { createUpstream, MANAGED_INSPECTOR_ROLE, verifyManagedJwt } from '../tools/managed-telegram-inspect/auth.mjs';
import { createManagedInspectServer, validateContext, validateConsent } from '../tools/managed-telegram-inspect/server.mjs';
import { pgJsonbText, sha256, validateRequest } from '../scripts/lib/telegram-resolution-inspect.mjs';

// Synthetic credentials and IDs only. No Auth, DB, provider, or Telegram network.
const NOW = new Date('2026-08-31T12:00:00Z');
const id = (n: number) => `20000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
const config: any = Object.freeze({ enabled: true, origin: 'https://inspect.example',
  projectUrl: 'https://abcdefghijklmnopqrst.supabase.co', projectRef: 'abcdefghijklmnopqrst',
  workspaceId: id(1), buildSha: 'a'.repeat(40), publishableKey: 'sb_publishable_synthetic_no_privilege' });
const pair = generateKeyPairSync('ec', { namedCurve: 'P-256' });
const jwk = { ...pair.publicKey.export({ format: 'jwk' }), alg: 'ES256', kid: 'synthetic', use: 'sig' };
const jwks = { keys: [jwk] };
function claims(aal = 'aal2'): any {
  return { iss: `${config.projectUrl}/auth/v1`, aud: 'authenticated', role: MANAGED_INSPECTOR_ROLE,
    sub: id(20), session_id: id(21), is_anonymous: false, aal,
    iat: NOW.getTime() / 1000 - 60, exp: NOW.getTime() / 1000 + 3600,
    amr: [{ method: 'password', timestamp: NOW.getTime() / 1000 - 60 },
      ...(aal === 'aal2' ? [{ method: 'totp', timestamp: NOW.getTime() / 1000 }] : [])] };
}
function jwt(value = claims(), header: any = { alg: 'ES256', typ: 'JWT', kid: 'synthetic' }) {
  const body = `${Buffer.from(JSON.stringify(header)).toString('base64url')}.${Buffer.from(JSON.stringify(value)).toString('base64url')}`;
  return `${body}.${sign('sha256', Buffer.from(body), { key: pair.privateKey, dsaEncoding: 'ieee-p1363' }).toString('base64url')}`;
}
function context(): any {
  return { schema_version: 'managed-telegram-inspect-context@1', user_id: id(20), workspace_id: config.workspaceId,
    inspected_by: `auth:${id(20)}`, approved_by: 'operator:synthetic-approver', project_ref: config.projectRef,
    release_id: id(22), release_sha: config.buildSha, migration_sha256: 'd'.repeat(64),
    verified_deployment_reference: 'synthetic-offline-release', expires_at: '2026-08-31T12:09:00+00:00' };
}
function exactRequest(): any {
  return { schema_version: 'telegram-resolution-inspect-request@1', project_ref: config.projectRef,
    environment: 'production', client_id: 'squid', release_sha: config.buildSha,
    workspace_id: id(1), content_item_id: id(2), content_version_id: id(3), publication_id: id(4), job_id: id(5),
    resolution_id: id(6), operator_approval_id: id(7), inspected_by: `auth:${id(20)}`,
    approved_by: 'operator:synthetic-approver', expires_at: '2026-08-31T12:05:00Z', public_audit: {
      schema_version: 'telegram-public-channel-audit@1', scan_source: 'public_telegram_web_history',
      public_channel: 'squid_kor_update', first_message_id: '9007199254740993', last_message_id: '9007199254741002',
      message_count: 10, checked_at: '2026-08-31T11:59:00Z', caption_match_count: 0, png_match_count: 0,
      snapshot_sha256: 'b'.repeat(64) } };
}
function result(req: any): any {
  const validated = validateRequest(req, NOW);
  const subject: any = {
    schema_version: 'exact-telegram-delivery-resolution@1', action: 'resolve_delivery_unknown_without_resend',
    client_id: 'squid', publication_approval_id: id(10), asset_id: id(11), delivery_attempt_id: id(12),
    delivery_started_at: '2026-08-31T11:40:00.123456+00:00', publication_status: 'delivery_unknown', job_status: 'failed',
    delivery_outcome: 'unknown', disposition: 'operator_closed_without_resend', public_observation: 'not_observed_at_checked_at',
    public_audit: req.public_audit, public_audit_sha256: validated.public_audit_sha256, approved_by: req.approved_by,
    expires_at: '2026-08-31T12:05:00+00:00', approved_release_sha: req.release_sha, resend_authorized: false,
    provider_calls: 0, database_claims: 0, publication_state_changed: false, job_state_changed: false,
    forbidden_actions: ['provider_call', 'claim', 'requeue', 'resend', 'mark_published', 'create_publication', 'create_job'] };
  for (const key of ['workspace_id', 'content_item_id', 'content_version_id', 'publication_id', 'job_id', 'resolution_id', 'operator_approval_id']) subject[key] = req[key];
  for (const key of ['delivery_request_sha256', 'publication_request_sha256', 'publication_response_sha256',
    'job_input_sha256', 'job_output_sha256', 'content_item_row_sha256', 'content_version_row_sha256', 'publication_row_sha256',
    'job_row_sha256', 'publication_approval_row_sha256', 'asset_row_sha256', 'caption_sha256', 'asset_sha256']) subject[key] = 'c'.repeat(64);
  return { eligible: true, resolved: false, reused: false, resolution_id: req.resolution_id, publication_id: req.publication_id,
    job_id: req.job_id, content_item_id: req.content_item_id, content_version_id: req.content_version_id,
    delivery_outcome: 'unknown', disposition: 'operator_closed_without_resend', public_observation: 'not_observed_at_checked_at',
    approval_subject: subject, approval_subject_sha256: sha256(pgJsonbText(subject)), approved: false, approved_at: null, resend_authorized: false };
}
function mockUpstream() {
  const calls: { path: string; body: any; options: any }[] = [];
  const state: any = { context: context(), user: { id: id(20), role: MANAGED_INSPECTOR_ROLE, is_anonymous: false,
    factors: [{ id: id(23), factor_type: 'totp', status: 'verified' }] }, request: null, rejectPath: null, resultExtra: false };
  const fetchImpl = async (url: string, options: any) => {
    assert.ok(url.startsWith(config.projectUrl));
    const path = url.slice(config.projectUrl.length);
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ path, body, options });
    if (path === state.rejectPath) return new Response('synthetic secret provider response', { status: 403 });
    let output: any;
    if (path === '/auth/v1/.well-known/jwks.json') output = jwks;
    else if (path === '/auth/v1/token?grant_type=password') output = { access_token: jwt(claims('aal1')), refresh_token: 'synthetic-refresh-never-retained' };
    else if (path === '/auth/v1/user') output = state.user;
    else if (path === `/auth/v1/factors/${id(23)}/challenge`) { assert.deepEqual(body, {}); output = { id: id(24), expires_at: NOW.getTime() / 1000 + 60 }; }
    else if (path === `/auth/v1/factors/${id(23)}/verify`) output = { access_token: jwt(), refresh_token: 'synthetic-refresh-never-retained' };
    else if (path === '/rest/v1/rpc/managed_telegram_inspect_context') output = state.context;
    else if (path === '/rest/v1/rpc/register_managed_telegram_inspect_consent') {
      state.request = body.target_request;
      const validated = validateRequest(body.target_request, NOW);
      output = { schema_version: 'managed-telegram-inspect-consent@1', consent_id: body.target_consent_id,
        request_sha256: validated.request_sha256, public_audit_sha256: validated.public_audit_sha256,
        consented_at: NOW.toISOString(), expires_at: '2026-08-31T12:05:00+00:00', reused: false };
    } else if (path === '/rest/v1/rpc/inspect_managed_telegram_delivery_unknown') {
      assert.deepEqual(Object.keys(body), ['target_consent_id']);
      output = result(state.request); if (state.resultExtra) output.raw_provider_response = 'SENSITIVE_SYNTHETIC';
    } else if (path === '/auth/v1/logout?scope=local') return new Response(null, { status: 204 });
    else throw new Error('Unexpected synthetic endpoint');
    return Response.json(output);
  };
  return { fetchImpl, calls, state };
}
async function harness(t: any, enabled = true) {
  const mock = mockUpstream();
  const time = { now: NOW };
  const server = createManagedInspectServer({ config: enabled ? config : { enabled: false }, fetchImpl: mock.fetchImpl, clock: () => time.now });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
  const port = (server.address() as any).port; let cookie = '';
  async function call(path = '/', fields?: any, extraHeaders: any = {}) {
    const body = fields ? new URLSearchParams(fields).toString() : null;
    return new Promise<any>((resolve, reject) => {
      const req = httpRequest({ hostname: '127.0.0.1', port, path, method: body === null ? 'GET' : 'POST',
        headers: { Host: 'inspect.example', Cookie: cookie, ...(body === null ? {} : { Origin: config.origin,
          'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': Buffer.byteLength(body) }), ...extraHeaders } }, (res) => {
        let text = ''; res.setEncoding('utf8'); res.on('data', (chunk) => { text += chunk; }); res.on('end', () => {
          if (res.headers['set-cookie']) cookie = res.headers['set-cookie'][0].split(';')[0];
          resolve({ status: res.statusCode, headers: res.headers, text });
        });
      }); req.on('error', reject); if (body) req.write(body); req.end();
    });
  }
  const field = (response: any, name: string) => {
    const match = response.text.match(new RegExp(`name="${name}" value="([^"]+)"`));
    assert.ok(match, `missing form field ${name}`); return match[1];
  };
  async function login() {
    let response = await call();
    response = await call('/login', { csrf: field(response, 'csrf'), email: 'synthetic@example.invalid', password: 'synthetic-no-account' });
    assert.equal(response.status, 200);
    response = await call('/mfa', { csrf: field(response, 'csrf'), factor_id: id(23), code: '123456' });
    assert.equal(response.status, 200); return response;
  }
  async function consent() {
    let response = await login();
    response = await call('/review', { csrf: field(response, 'csrf'), request: JSON.stringify(exactRequest()) });
    assert.equal(response.status, 200);
    response = await call('/consent', { csrf: field(response, 'csrf'), review_id: field(response, 'review_id'), confirm: 'inspect-only' });
    assert.equal(response.status, 200); return response;
  }
  return { ...mock, call, field, login, consent, time };
}

test('configuration is disabled by default and requires isolated environment plus actual build stamp', () => {
  assert.deepEqual(loadConfig({}), { enabled: false });
  for (const name of ['SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_KEY', 'STUDIO_AUTOMATION_TOKEN', 'TELEGRAM_CHAT_ID',
    'NETLIFY_AUTH_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY', 'NODE_OPTIONS', 'PGPASSWORD',
    'TOKEN', 'JOSE', 'JOSE_SIGNING_KEY', 'JWT_SIGNING_KEY', 'JWT_KEY', 'SIGNING_KEY']) {
    assert.throws(() => loadConfig({ [name]: 'synthetic' }), /isolated_runtime_required/);
  }
  const env = { MANAGED_INSPECT_ENABLED: 'true', MANAGED_INSPECT_ORIGIN: config.origin,
    MANAGED_INSPECT_PROJECT_URL: config.projectUrl, MANAGED_INSPECT_PUBLISHABLE_KEY: config.publishableKey,
    MANAGED_INSPECT_WORKSPACE_ID: config.workspaceId };
  assert.throws(() => loadConfig(env));
  assert.throws(() => loadConfig(env, config.buildSha));
  assert.throws(() => loadConfig({ ...env, MANAGED_INSPECT_SOURCE_SHA: config.buildSha }, config.buildSha));
  const githubEnv = { ...env, RAILWAY_GIT_COMMIT_SHA: config.buildSha };
  assert.equal(loadConfig(githubEnv, config.buildSha).buildSha, config.buildSha);
  for (const value of ['', config.buildSha.toUpperCase(), ` ${config.buildSha}`, `${config.buildSha} `]) {
    assert.throws(() => loadConfig({ ...env, RAILWAY_GIT_COMMIT_SHA: value }, config.buildSha));
  }
  assert.throws(() => loadConfig({ ...env, RAILWAY_GIT_COMMIT_SHA: 'b'.repeat(40) }, config.buildSha));
  assert.throws(() => loadConfig({ ...githubEnv, MANAGED_INSPECT_PROJECT_URL: 'http://localhost:54321' }, config.buildSha));
  assert.throws(() => loadConfig({ ...githubEnv, MANAGED_INSPECT_PUBLISHABLE_KEY: jwt() }, config.buildSha));
  for (const flag of ['--inspect', '--inspect-brk=0', '--require=x', '--import=x', '--tls-keylog=x', '--heap-prof', '--report-on-fatalerror']) {
    assert.throws(() => assertRuntimeFlags([flag]), /unsafe_runtime_diagnostics/);
  }
});

test('JWT cryptographic verification pins key, algorithm, project, identity, role, session and fresh TOTP', () => {
  assert.equal(verifyManagedJwt(jwt(), jwks, config, NOW).userId, id(20));
  assert.equal(verifyManagedJwt(jwt(claims('aal1')), jwks, config, NOW, false).aal, 'aal1');
  const mutations = [
    (c: any) => { c.iss = 'https://other.invalid/auth/v1'; }, (c: any) => { c.aud = 'service_role'; },
    (c: any) => { c.role = 'authenticated'; }, (c: any) => { c.role = 'service_role'; },
    (c: any) => { delete c.role; }, (c: any) => { c.is_anonymous = true; },
    (c: any) => { c.sub = 'not-uuid'; }, (c: any) => { c.session_id = ''; },
    (c: any) => { c.aal = 'aal1'; }, (c: any) => { c.exp = NOW.getTime() / 1000; },
    (c: any) => { c.iat = NOW.getTime() / 1000 + 1; }, (c: any) => { c.nbf = NOW.getTime() / 1000 + 1; },
    (c: any) => { c.amr[1].timestamp -= 601; }, (c: any) => { c.amr[1].timestamp += 1; },
    (c: any) => { c.amr = [{ method: 'password', timestamp: NOW.getTime() / 1000 }]; },
  ];
  for (const mutate of mutations) { const value = claims(); mutate(value); assert.throws(() => verifyManagedJwt(jwt(value), jwks, config, NOW), /authentication_rejected/); }
  for (const header of [{ alg: 'none', kid: 'synthetic', typ: 'JWT' }, { alg: 'HS256', kid: 'synthetic', typ: 'JWT' },
    { alg: 'ES256', kid: 'missing', typ: 'JWT' }, { alg: 'ES256', kid: 'synthetic', typ: 'JWT', jku: 'https://invalid' }]) {
    assert.throws(() => verifyManagedJwt(jwt(claims(), header), jwks, config, NOW));
  }
  assert.throws(() => verifyManagedJwt(jwt(), { keys: [jwk, jwk] }, config, NOW));
  assert.throws(() => verifyManagedJwt(`${jwt().slice(0, -6)}AAAAAA`, jwks, config, NOW));
  assert.throws(() => verifyManagedJwt('short-synthetic-secret', jwks, config, NOW), (e: any) => e.message === 'authentication_rejected');
});

test('disabled server makes zero upstream calls for every route', async (t) => {
  const h = await harness(t, false);
  for (const path of ['/', '/review', '/consent', '/inspect', '/login', '/guard.mjs']) assert.equal((await h.call(path)).status, 503);
  assert.equal(h.calls.length, 0);
});

test('HTTP response has secure cookie/CSP/no-store and cross-origin or wrong CSRF fails before Auth', async (t) => {
  const h = await harness(t); const first = await h.call();
  assert.match(first.headers['set-cookie'][0], /__Host-.*; Path=\/; Secure; HttpOnly; SameSite=Strict/);
  assert.match(first.headers['content-security-policy'], /frame-ancestors 'none'/);
  assert.match(first.headers['cache-control'], /no-store/);
  const form = { csrf: h.field(first, 'csrf'), email: 'synthetic@example.invalid', password: 'SYNTHETIC_PRIVATE' };
  assert.equal((await h.call('/login', form, { Origin: 'https://evil.invalid' })).status, 400);
  assert.equal((await h.call('/login', { ...form, csrf: '0'.repeat(64) })).status, 400);
  assert.equal((await h.call('/', undefined, { Host: 'evil.invalid' })).status, 400);
  assert.equal(h.calls.length, 0);
});

test('separate login/MFA/review/consent/inspect gates expose no tokens and issue only exact approved RPC names', async (t) => {
  const h = await harness(t); const consent = await h.consent();
  assert.match(consent.text, /<button disabled>/);
  assert.match(consent.text, /not global exactly-once/);
  assert.match(consent.text, /Browser transport may replay HTTP POST/);
  assert.match(consent.text, /live server-session gate permits only one upstream inspect RPC attempt/);
  assert.doesNotMatch(consent.text, /access_token|refresh_token|synthetic-refresh|synthetic-no-account|Bearer /);
  assert.equal(h.calls.filter((c) => c.path.includes('register_managed')).length, 1);
  assert.equal(h.calls.filter((c) => c.path.includes('inspect_managed_telegram_delivery_unknown')).length, 0);
  const fields = { csrf: h.field(consent, 'csrf'), consent_id: h.field(consent, 'consent_id'), attempt_marker_committed: '1' };
  const output = await h.call('/inspect', fields, { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' });
  assert.equal(output.status, 200); assert.equal(JSON.parse(output.text).eligible, true);
  assert.match(output.headers['content-type'], /application\/json/);
  assert.equal((await h.call('/inspect', fields)).status, 400);
  assert.equal(h.calls.filter((c) => c.path.includes('inspect_managed_telegram_delivery_unknown')).length, 1);
  for (const call of h.calls) {
    assert.equal(call.options.redirect, 'error');
    assert.doesNotMatch(call.path, /approve|resolve|send|claim|refresh/);
  }
});

test('actor/build fence mismatches and session removal stop before consent/inspect', async (t) => {
  const h = await harness(t); const ready = await h.login();
  const req = exactRequest(); req.inspected_by = 'auth:other';
  assert.equal((await h.call('/review', { csrf: h.field(ready, 'csrf'), request: JSON.stringify(req) })).status, 400);
  h.state.context.release_sha = 'e'.repeat(40);
  assert.equal((await h.call('/review', { csrf: h.field(ready, 'csrf'), request: JSON.stringify(exactRequest()) })).status, 400);
  assert.equal(h.calls.filter((c) => c.path.includes('register_managed')).length, 0);
});

test('unknown consent outcome has no automatic retry and raw provider errors are never rendered', async (t) => {
  const h = await harness(t); const ready = await h.login();
  const review = await h.call('/review', { csrf: h.field(ready, 'csrf'), request: JSON.stringify(exactRequest()) });
  const fields = { csrf: h.field(review, 'csrf'), review_id: h.field(review, 'review_id'), confirm: 'inspect-only' };
  h.state.rejectPath = '/rest/v1/rpc/register_managed_telegram_inspect_consent';
  const first = await h.call('/consent', fields);
  assert.equal(first.status, 400); assert.doesNotMatch(first.text, /synthetic secret provider response/);
  assert.equal((await h.call('/consent', fields)).status, 400);
  assert.equal(h.calls.filter((c) => c.path.includes('register_managed')).length, 1);
});

test('unknown inspect outcome/extra response fields remain non-retryable and no raw response leaks', async (t) => {
  const h = await harness(t); const consent = await h.consent(); h.state.resultExtra = true;
  const fields = { csrf: h.field(consent, 'csrf'), consent_id: h.field(consent, 'consent_id'), attempt_marker_committed: '1' };
  const output = await h.call('/inspect', fields);
  assert.equal(output.status, 400); assert.doesNotMatch(output.text, /SENSITIVE_SYNTHETIC|raw_provider_response/);
  assert.equal((await h.call('/inspect', fields)).status, 400);
  assert.equal(h.calls.filter((c) => c.path.includes('inspect_managed_telegram_delivery_unknown')).length, 1);
});

test('browser marker flag alone grants no scope, extra target overrides fail, revoked Auth user stops before inspect', async (t) => {
  const h = await harness(t); const consent = await h.consent();
  const fields = { csrf: h.field(consent, 'csrf'), consent_id: h.field(consent, 'consent_id'), attempt_marker_committed: '1' };
  assert.equal((await h.call('/inspect', { ...fields, target_job_id: id(99) })).status, 400);
  assert.equal((await h.call('/inspect', { ...fields, attempt_marker_committed: '0' })).status, 400);
  h.state.rejectPath = '/auth/v1/user';
  assert.equal((await h.call('/inspect', fields)).status, 400);
  assert.equal(h.calls.filter((c) => c.path.includes('inspect_managed_telegram_delivery_unknown')).length, 0);
});

test('upstream is fixed-path/no redirect/no retry with bounded JSON responses', async () => {
  let count = 0;
  const client = createUpstream(config, async () => { count++; return new Response('x'.repeat(65537), { headers: { 'Content-Type': 'application/json' } }); });
  await assert.rejects(() => client.request('/rest/v1/rpc/approve_exact_telegram_delivery_unknown'), /upstream_rejected_or_unknown/);
  assert.equal(count, 0);
  await assert.rejects(() => client.request('/auth/v1/user'), /upstream_rejected_or_unknown/); assert.equal(count, 1);
});

test('context and consent response allowlists enforce exact actor/release/hash and bounded expiry', () => {
  const identity = verifyManagedJwt(jwt(), jwks, config, NOW);
  const ctx = validateContext(context(), identity, config, NOW);
  assert.throws(() => validateContext({ ...context(), extra: 'raw' }, identity, config, NOW));
  assert.throws(() => validateContext({ ...context(), user_id: id(99) }, identity, config, NOW));
  const validated = validateRequest(exactRequest(), NOW);
  const consent = { schema_version: 'managed-telegram-inspect-consent@1', consent_id: id(30),
    request_sha256: validated.request_sha256, public_audit_sha256: validated.public_audit_sha256,
    consented_at: NOW.toISOString(), expires_at: '2026-08-31T12:05:00+00:00', reused: false };
  assert.equal(validateConsent(consent, id(30), validated, ctx, NOW).consent_id, id(30));
  for (const change of [{ expires_at: '2026-08-31T12:05:01+00:00' }, { expires_at: '2026-08-31T12:00:00Z' },
    { reused: true }, { request_sha256: 'f'.repeat(64) }, { consented_at: '2026-08-31T12:00:02Z' }]) {
    assert.throws(() => validateConsent({ ...consent, ...change }, id(30), validated, ctx, NOW));
  }
});

test('missing session or MFA cannot reach context, consent or inspect and unsupported routes have zero upstream calls', async (t) => {
  const h = await harness(t);
  assert.equal((await h.call('/inspect', { consent_id: id(30), csrf: '0'.repeat(64), attempt_marker_committed: '1' })).status, 400);
  let response = await h.call();
  const csrf = h.field(response, 'csrf');
  assert.equal((await h.call('/approve', { csrf })).status, 400);
  assert.equal((await h.call('/review', { csrf, request: JSON.stringify(exactRequest()) })).status, 400);
  assert.equal(h.calls.length, 0);
  response = await h.call('/login', { csrf, email: 'synthetic@example.invalid', password: 'synthetic' });
  assert.equal((await h.call('/review', { csrf: h.field(response, 'csrf'), request: JSON.stringify(exactRequest()) })).status, 400);
  assert.equal(h.calls.filter((c) => c.path.startsWith('/rest/')).length, 0);
});

test('concurrent same-consent requests issue at most one upstream inspect in this runtime', async (t) => {
  const h = await harness(t); const consent = await h.consent();
  const fields = { csrf: h.field(consent, 'csrf'), consent_id: h.field(consent, 'consent_id'), attempt_marker_committed: '1' };
  const responses = await Promise.all([h.call('/inspect', fields), h.call('/inspect', fields)]);
  assert.deepEqual(responses.map((r) => r.status).sort(), [200, 400]);
  assert.equal(h.calls.filter((c) => c.path.endsWith('/inspect_managed_telegram_delivery_unknown')).length, 1);
});

test('consent expiry and stale in-memory session deny without reissuing consent or inspect', async (t) => {
  const h = await harness(t); const consent = await h.consent();
  const fields = { csrf: h.field(consent, 'csrf'), consent_id: h.field(consent, 'consent_id'), attempt_marker_committed: '1' };
  h.time.now = new Date('2026-08-31T12:05:01Z');
  assert.equal((await h.call('/inspect', fields)).status, 400);
  assert.equal(h.calls.filter((c) => c.path.endsWith('/inspect_managed_telegram_delivery_unknown')).length, 0);
  const before = h.calls.length;
  h.time.now = new Date('2026-08-31T12:10:01Z');
  assert.equal((await h.call('/inspect', fields)).status, 400);
  assert.equal(h.calls.length, before);
});

test('logout removes opaque local session and only requests local Auth logout', async (t) => {
  const h = await harness(t); const ready = await h.login();
  const csrf = h.field(ready, 'csrf');
  const response = await h.call('/logout', { csrf });
  assert.equal(response.status, 200); assert.match(response.headers['set-cookie'][0], /Max-Age=0/);
  const before = h.calls.length;
  assert.equal((await h.call('/review', { csrf, request: JSON.stringify(exactRequest()) })).status, 400);
  assert.equal(h.calls.length, before);
  assert.equal(h.calls.filter((c) => c.path === '/auth/v1/logout?scope=local').length, 1);
});

test('fixed remote-IP request bound rejects excess traffic without trusting forwarded client identity', async (t) => {
  const h = await harness(t);
  for (let i = 0; i < 30; i++) assert.equal((await h.call('/', undefined, { 'X-Forwarded-For': `198.51.100.${i}` })).status, 200);
  assert.equal((await h.call()).status, 429);
  assert.equal(h.calls.length, 0);
});

test('GET Auth user must match signed identity, exact live role and the same verified factor', async () => {
  for (const patch of [{ id: id(99) }, { role: 'authenticated' }, { role: 'service_role' },
    { role: undefined }, { is_anonymous: true }, { factors: [] },
    { banned_until: '2026-08-31T12:01:00Z' }, { banned_until: 'invalid' }, { deleted_at: '2026-08-31T11:59:00Z' }]) {
    const h = mockUpstream(); Object.assign(h.state.user, patch);
    await assert.rejects(() => createUpstream(config, h.fetchImpl).authenticate(jwt(), NOW), /authentication_rejected/);
  }
});
