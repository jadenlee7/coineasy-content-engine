// Opt-in real GoTrue + PostgREST tests. The ordinary unit suite starts no services.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac, createPublicKey, randomBytes, randomUUID, verify } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { startLocalManagedAuth } from '../scripts/test-managed-auth-local.mjs';
import { createUpstream, MANAGED_INSPECTOR_ROLE, verifyManagedJwt } from '../tools/managed-telegram-inspect/auth.mjs';
import { pgJsonbText, sha256, validateInspectResponse, validateRequest } from '../scripts/lib/telegram-resolution-inspect.mjs';

function totp(secret: string) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of secret.replace(/=+$/, '')) bits += alphabet.indexOf(char).toString(2).padStart(5, '0');
  const bytes = Buffer.from((bits.match(/.{8}/g) ?? []).map((byte) => parseInt(byte, 2)));
  const counter = Buffer.alloc(8); counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000)));
  const hash = createHmac('sha1', bytes).update(counter).digest();
  const offset = hash[hash.length - 1] & 15;
  return String((hash.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, '0');
}
function claims(token: string) { return JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()); }

test('real local Managed Auth and PostgREST contracts', { skip: process.env.COINEASY_MANAGED_AUTH_LIVE !== '1', timeout: 600_000 }, async (t) => {
  const stack = await startLocalManagedAuth();
  t.after(async () => { await stack.close(); });
  const config = { projectUrl: stack.issuer.replace(/\/auth\/v1$/, ''), publishableKey: 'sb_publishable_local_synthetic_only' };
  const allowedMessages = new Set([
    ...readFileSync(new URL('../supabase/migrations/20260831120000_exact_telegram_delivery_unknown_resolution.sql', import.meta.url), 'utf8').matchAll(/raise exception\s+'([^'%]+)'/g),
    ...readFileSync(new URL('../supabase/migrations/20260831180000_managed_auth_telegram_inspect.sql', import.meta.url), 'utf8').matchAll(/raise exception\s+'([^'%]+)'/g),
  ].map((match) => match[1]));
  let lastDbReason = 'unknown';
  const upstream = createUpstream(config, async (input: any, init: any) => {
    const url = new URL(input);
    assert.equal(url.origin, config.projectUrl, 'only synthetic project routing is permitted');
    const isAuth = url.pathname.startsWith('/auth/v1/'), isRest = url.pathname.startsWith('/rest/v1/');
    assert.ok(isAuth !== isRest, 'only exact Auth/REST route namespaces');
    // Real HTTP via an owned, egress-isolated Docker helper. No project DNS.
    const response = await stack.fetch(isAuth ? 'auth' : 'rest', url.pathname.slice(8) + url.search, init);
    if (isRest && !response.ok) {
      const failure = await response.clone().json();
      lastDbReason = `${/^[A-Z0-9]{5}$/.test(failure.code ?? '') ? failure.code : 'unknown'}:${allowedMessages.has(failure.message) ? failure.message : 'unclassified'}`;
    }
    return response;
  });
  const email = `local-${randomBytes(8).toString('hex')}@example.invalid`, password = randomBytes(24).toString('base64url');
  const created = await stack.localCreateInspector(email, password);
  assert.equal(created.status, 200, 'local Admin atomically creates the never-logged-in inspector');
  assert.equal(created.data.role, MANAGED_INSPECTOR_ROLE);
  assert.equal(await stack.sql(`select role from auth.users where id='${created.data.id}'::uuid;`), MANAGED_INSPECTOR_ROLE);
  const login = await stack.request('auth', '/token?grant_type=password', { method: 'POST', body: { email, password } });
  assert.equal(login.status, 200, 'real password grant');
  let session = login.data;
  await t.test('ordinary signup remains authenticated and cannot call any managed RPC', async () => {
    const ordinaryEmail = `local-${randomBytes(8).toString('hex')}@example.invalid`;
    const ordinaryPassword = randomBytes(24).toString('base64url');
    const ordinary = await stack.request('auth', '/signup', { method: 'POST', body: { email: ordinaryEmail, password: ordinaryPassword } });
    assert.equal(ordinary.status, 200); assert.equal(claims(ordinary.data.access_token).role, 'authenticated');
    const denied = [
      ['/rpc/managed_telegram_inspect_context', { target_workspace_id: randomUUID(), target_release_sha: 'a'.repeat(40) }],
      ['/rpc/register_managed_telegram_inspect_consent', { target_consent_id: randomUUID(), target_request: {}, target_request_sha256: 'a'.repeat(64) }],
      ['/rpc/inspect_managed_telegram_delivery_unknown', { target_consent_id: randomUUID() }],
    ];
    for (const [path, body] of denied) {
      const response = await stack.request('rest', path as string, { method: 'POST', token: ordinary.data.access_token, body });
      assert.ok(response.status >= 400, `${path} denies the ordinary authenticated role`);
    }
    const jwks = await stack.request('auth', '/.well-known/jwks.json');
    assert.throws(() => verifyManagedJwt(ordinary.data.access_token, jwks.data, config, new Date(), false), /authentication_rejected/);
  });
  await t.test('password JWT signature + live user + AAL1', async () => {
    const token = session.access_token, parts = token.split('.');
    const jwks = await stack.request('auth', '/.well-known/jwks.json');
    assert.equal(jwks.status, 200);
    const key = jwks.data.keys.find((item: any) => item.kid === JSON.parse(Buffer.from(parts[0], 'base64url').toString()).kid);
    assert.ok(key && !key.d && key.alg === 'ES256');
    assert.ok(verify('sha256', Buffer.from(`${parts[0]}.${parts[1]}`), { key: createPublicKey({ key, format: 'jwk' }), dsaEncoding: 'ieee-p1363' }, Buffer.from(parts[2], 'base64url')));
    const jwt = claims(token);
    assert.equal(jwt.aal, 'aal1'); assert.equal(jwt.iss, stack.issuer); assert.equal(jwt.aud, 'authenticated');
    assert.equal(jwt.role, MANAGED_INSPECTOR_ROLE, 'real GoTrue issues the persisted dedicated role');
    assert.ok(jwt.amr.some((entry: any) => entry.method === 'password' && Number.isInteger(entry.timestamp)));
    assert.equal(verifyManagedJwt(token, jwks.data, config, new Date(), false).aal, 'aal1');
    assert.throws(() => verifyManagedJwt(token, jwks.data, config, new Date()), /authentication_rejected/);
    const liveUser = await stack.request('auth', '/user', { token });
    assert.equal(liveUser.status, 200); assert.equal(liveUser.data.role, MANAGED_INSPECTOR_ROLE);
    const changed = `${parts[0]}.${Buffer.from(JSON.stringify({ ...jwt, aal: 'aal2' })).toString('base64url')}.${parts[2]}`;
    assert.equal((await stack.request('rest', '/', { token: changed })).status, 401, 'PostgREST rejects forged claims');
  });
  // Enrollment is a LOCAL fixture action; the production application has none.
  const enrolled = await stack.request('auth', '/factors', { method: 'POST', token: session.access_token, body: { factor_type: 'totp', friendly_name: 'local-test-only', issuer: 'CoinEasy Local Test' } });
  assert.equal(enrolled.status, 200, 'local TOTP fixture enrollment');
  const factorId = enrolled.data.id;
  await t.test('actual TOTP challenge/verify issues fresh AAL2 and DB-backed AMR', async () => {
    const challenged = await stack.request('auth', `/factors/${factorId}/challenge`, { method: 'POST', token: session.access_token, body: {} });
    assert.equal(challenged.status, 200); assert.ok(Number.isInteger(challenged.data.expires_at));
    const checked = await stack.request('auth', `/factors/${factorId}/verify`, { method: 'POST', token: session.access_token, body: { challenge_id: challenged.data.id, code: totp(enrolled.data.totp.secret) } });
    assert.equal(checked.status, 200, 'real local TOTP verification');
    session = checked.data;
    const jwt = claims(session.access_token), method = jwt.amr.find((entry: any) => entry.method === 'totp');
    assert.equal(jwt.aal, 'aal2'); assert.ok(method && Math.abs(Date.now() / 1000 - method.timestamp) < 15);
    const metadata = await stack.jsonSql(`select json_build_object('aal',s.aal,'factor_id',s.factor_id,'method',a.authentication_method,'timestamp',floor(extract(epoch from a.updated_at))) from auth.sessions s join auth.mfa_amr_claims a on a.session_id=s.id and a.authentication_method='totp' where s.id='${jwt.session_id}'::uuid;`);
    assert.equal(metadata.aal, 'aal2'); assert.equal(metadata.factor_id, factorId); assert.equal(metadata.timestamp, method.timestamp);
    assert.equal((await upstream.authenticate(session.access_token, new Date())).identity.aal, 'aal2', 'real application verifier accepts real GoTrue token');
  });
  await t.test('application password login and existing verified TOTP flow', async () => {
    session = await upstream.request('/auth/v1/token?grant_type=password', { method: 'POST', body: { email, password } });
    const pending = await upstream.authenticate(session.access_token, new Date(), false);
    assert.equal(pending.identity.aal, 'aal1'); assert.ok(pending.factors.includes(factorId));
    const challenge = await upstream.request(`/auth/v1/factors/${factorId}/challenge`, { method: 'POST', token: session.access_token, body: {} });
    session = await upstream.request(`/auth/v1/factors/${factorId}/verify`, { method: 'POST', token: session.access_token,
      body: { challenge_id: challenge.id, code: totp(enrolled.data.totp.secret) } });
    assert.equal((await upstream.authenticate(session.access_token, new Date())).identity.aal, 'aal2');
    const jwks = await upstream.request('/auth/v1/.well-known/jwks.json');
    assert.throws(() => verifyManagedJwt(session.access_token, jwks, config, new Date(claims(session.access_token).exp * 1000)), /authentication_rejected/, 'genuine issued token expires');
  });
  await t.test('live Auth role drift denies an otherwise valid dedicated-role JWT with no fallback', async () => {
    const jwt = claims(session.access_token);
    assert.equal(jwt.role, MANAGED_INSPECTOR_ROLE);
    await stack.sql(`update auth.users set role='authenticated' where id='${jwt.sub}';`);
    await assert.rejects(() => upstream.authenticate(session.access_token, new Date()), /authentication_rejected/);
    await stack.sql(`update auth.users set role='${MANAGED_INSPECTOR_ROLE}' where id='${jwt.sub}';`);
    assert.equal((await upstream.authenticate(session.access_token, new Date())).identity.userId, jwt.sub);
  });
  const fixture = await stack.jsonSql(`select managed_auth_live_test.fixture('${claims(session.access_token).sub}'::uuid);`);
  let consentId = fixture.consent_id;
  const request = fixture.request;
  const contextBody = { target_workspace_id: request.workspace_id, target_release_sha: request.release_sha };
  const inspect = () => upstream.rpc('inspect_managed_telegram_delivery_unknown', session.access_token, { target_consent_id: consentId });
  const sourceSnapshot = () => stack.jsonSql(`select jsonb_build_object(
    'items',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.content_items t),
    'versions',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.content_versions t),
    'jobs',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.jobs t),
    'publications',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.publications t),
    'assets',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.assets t),
    'approvals',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.approvals t),
    'events',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.event_log t),
    'consents',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by consent_id)::text,'')) from private.managed_telegram_inspect_consents t),
    'resolution_approvals',(select count(*) from private.exact_telegram_delivery_unknown_approvals),
    'resolutions',(select count(*) from private.exact_telegram_delivery_unknown_resolutions));`);
  await t.test('dedicated token cannot reach general tables, ordinary RPCs or old resolution phases', async () => {
    const boundarySnapshot = () => stack.jsonSql(`select jsonb_build_object(
      'workspaces',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.workspaces t),
      'members',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by workspace_id,user_id)::text,'')) from public.workspace_members t),
      'items',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.content_items t),
      'versions',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.content_versions t),
      'jobs',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.jobs t),
      'publications',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.publications t),
      'approvals',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by id)::text,'')) from public.approvals t),
      'work_orders',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by workspace_id,work_order_id)::text,'')) from agent_runtime.agent_work_orders t),
      'work_events',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by workspace_id,work_order_id,event_seq)::text,'')) from agent_runtime.agent_work_order_events t),
      'runs',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by workspace_id,run_id)::text,'')) from agent_runtime.agent_runs t),
      'dispatch',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by workspace_id,work_order_id)::text,'')) from agent_runtime.agent_dispatch_outbox t),
      'receipts',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by workspace_id,receipt_id)::text,'')) from agent_runtime.agent_action_receipts t),
      'incidents',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by workspace_id,incident_id)::text,'')) from agent_runtime.agent_incidents t));`);
    const target = randomUUID(), workOrder = randomUUID(), hash = 'a'.repeat(64);
    const ordinary = [
      ['authorize_agent_work_order', { target_workspace_id: target, target_work_order_id: workOrder, target_scope_sha256: hash, target_expected_status_version: 0 }],
      ['complete_agent_work_order', { target_workspace_id: target, target_work_order_id: workOrder, target_scope_sha256: hash, target_expected_status_version: 0 }],
      ['get_agent_company_dashboard', { target_workspace_id: target }],
      ['get_agent_work_order', { target_workspace_id: target, target_work_order_id: workOrder }],
      ['list_agent_operator_inbox', { target_workspace_id: target, target_limit: 1, target_before_updated_at: null, target_before_work_order_id: null }],
      ['propose_agent_work_order', { target_workspace_id: target, target_scope: {}, target_scope_sha256: hash }],
      ['record_agent_operator_decision', { target_workspace_id: target, target_work_order_id: workOrder, target_scope_sha256: hash, target_expected_status_version: 0, target_decision: 'approve', target_reason_code: 'local_test' }],
      ['queue_content_generation', { target_content_item_id: target, generation_options: {}, request_idempotency_key: 'local-role-boundary' }],
      ['record_approved_figma_link', { target_content_item_id: target, target_content_version_id: randomUUID(), target_file_key: 'local', target_node_id: '1:1', target_page_name: null, target_section_name: null, link_metadata: {} }],
      ['request_content_publication', { target_content_item_id: target, target_content_version_id: randomUUID(), target_channel: 'telegram', target_scheduled_for: null, request_idempotency_key: 'local-role-boundary' }],
    ];
    const audit = { schema_version: 'telegram-public-channel-audit@1', scan_source: 'public_telegram_web_history',
      public_channel: 'synthetic', first_message_id: '1', last_message_id: '1', message_count: 1,
      checked_at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'), caption_match_count: 0, png_match_count: 0,
      snapshot_sha256: hash };
    const phaseBase = { target_workspace_id: target, target_content_item_id: randomUUID(), target_content_version_id: randomUUID(),
      target_publication_id: randomUUID(), target_job_id: randomUUID(), target_resolution_id: randomUUID(),
      target_operator_approval_id: randomUUID(), target_release_sha: 'a'.repeat(40),
      target_public_audit: audit };
    const oldPhases = [
      ['inspect_exact_telegram_delivery_unknown_resolution', { ...phaseBase, target_inspected_by: `auth:${claims(session.access_token).sub}`, target_approved_by: 'operator:local-test', target_expires_at: new Date(Date.now() + 60_000).toISOString() }],
      ['approve_exact_telegram_delivery_unknown_resolution', { ...phaseBase, target_approved_by: 'operator:local-test', target_expires_at: new Date(Date.now() + 60_000).toISOString(), target_approval_subject_sha256: hash }],
      ['resolve_exact_telegram_delivery_unknown_without_resend', { ...phaseBase, target_resolved_by: `auth:${claims(session.access_token).sub}`, target_approval_subject_sha256: hash }],
    ];
    const before = await boundarySnapshot();
    assert.ok((await stack.request('rest', '/workspaces?select=id&limit=1', { token: session.access_token })).status >= 400);
    assert.ok((await stack.request('rest', '/workspaces', { method: 'POST', token: session.access_token,
      body: { id: randomUUID(), name: 'Denied local write', slug: `denied-${randomUUID()}`, created_by: claims(session.access_token).sub } })).status >= 400);
    for (const [name, body] of [...ordinary, ...oldPhases]) {
      const response = await stack.request('rest', `/rpc/${name}`, { method: 'POST', token: session.access_token, body });
      assert.ok(response.status >= 400, `${name} denies the dedicated inspector role`);
    }
    assert.deepEqual(await boundarySnapshot(), before, 'denial matrix leaves general tables and agent ledgers unchanged');
  });
  await t.test('actual authenticated consent + inspect, exact hash, no ledger writes', async () => {
    assert.ok(validateRequest(request), 'synthetic fixture passes the complete request contract');
    const keyOrdering = await stack.jsonSql(`select jsonb_build_object('locale_is_c_order', (select array_agg(k order by k)=array_agg(k order by k collate "C") from jsonb_object_keys('${JSON.stringify(request)}'::jsonb) k));`);
    console.log(`local request key collation matches C: ${keyOrdering.locale_is_c_order}`);
    assert.equal(sha256(pgJsonbText(request)), fixture.request_sha256, 'JS/PostgreSQL request hash parity');
    const context = await upstream.rpc('managed_telegram_inspect_context', session.access_token, contextBody);
    assert.equal(context.inspected_by, request.inspected_by);
    const deniedAal1 = await stack.request('rest', '/rpc/managed_telegram_inspect_context', { method: 'POST', token: login.data.access_token, body: contextBody });
    assert.ok(deniedAal1.status >= 400, 'AAL1 fails even after its session was upgraded');
    const consent = await upstream.rpc('register_managed_telegram_inspect_consent', session.access_token, { target_consent_id: consentId, target_request: request, target_request_sha256: fixture.request_sha256 })
      .catch(() => { throw new Error(`local_consent_registration:${lastDbReason}`); });
    assert.equal(consent.consent_id, consentId);
    const before = await sourceSnapshot(), result = await inspect(), after = await sourceSnapshot();
    assert.equal(result.eligible, true); assert.equal(result.resolved, false); assert.equal(result.resend_authorized, false);
    assert.equal(result.approval_subject_sha256, sha256(pgJsonbText(result.approval_subject)));
    assert.equal(validateInspectResponse(result, validateRequest(request)).eligible, true, 'complete production response validator');
    assert.deepEqual(after, before, 'inspect is genuinely read-only, including consent and events');
    assert.equal(after.resolution_approvals, 0); assert.equal(after.resolutions, 0);
    // A second raw API call is read-only too, not a claimed global once-only gate.
    assert.equal((await inspect()).approval_subject_sha256, result.approval_subject_sha256);
    assert.deepEqual(await sourceSnapshot(), before);
  });
  await t.test('live ban, session deadline and stale DB MFA deny an otherwise valid JWT', async () => {
    const jwt = claims(session.access_token);
    await stack.sql(`update auth.users set banned_until=clock_timestamp()+interval '1 hour' where id='${jwt.sub}';`);
    await assert.rejects(inspect, /upstream_rejected_or_unknown/);
    await assert.rejects(() => upstream.authenticate(session.access_token, new Date()), /authentication_rejected|upstream_rejected_or_unknown/);
    await stack.sql(`update auth.users set banned_until=null where id='${jwt.sub}'; update auth.sessions set not_after=clock_timestamp()-interval '1 second' where id='${jwt.session_id}';`);
    await assert.rejects(inspect, /upstream_rejected_or_unknown/);
    await stack.sql(`update auth.sessions set not_after=null where id='${jwt.session_id}'; update auth.mfa_amr_claims set updated_at=updated_at-interval '11 minutes' where session_id='${jwt.session_id}' and authentication_method='totp';`);
    await assert.rejects(inspect, /upstream_rejected_or_unknown/);
    await stack.sql(`update auth.mfa_amr_claims set updated_at=updated_at+interval '11 minutes' where session_id='${jwt.session_id}' and authentication_method='totp';`);
    assert.equal((await inspect()).eligible, true);
  });
  await t.test('real recovery link request invalidates existing consent; recovery JWT is not fresh MFA', async () => {
    const link = await stack.localRecoveryLink(email);
    assert.equal(link.status, 200, 'local admin generates recovery link without sending email');
    assert.ok(typeof link.data.hashed_token === 'string' && link.data.hashed_token.length > 20);
    await assert.rejects(inspect, /upstream_rejected_or_unknown/, 'recovery request changes live fingerprint');
    const recovered = await stack.request('auth', '/verify', { method: 'POST', body: { type: 'recovery', token_hash: link.data.hashed_token } });
    assert.equal(recovered.status, 200, 'real local recovery token verification');
    const recoveryClaims = claims(recovered.data.access_token);
    assert.equal(recoveryClaims.aal, 'aal1');
    // GoTrue v2.189.0 verifyPost deliberately records models.OTP for recovery.
    assert.ok(recoveryClaims.amr.some((method: any) => method.method === 'otp'));
    await assert.rejects(() => upstream.authenticate(recovered.data.access_token, new Date()), /authentication_rejected/);
    consentId = randomUUID();
    await upstream.rpc('register_managed_telegram_inspect_consent', session.access_token,
      { target_consent_id: consentId, target_request: request, target_request_sha256: fixture.request_sha256 });
    assert.equal((await inspect()).eligible, true, 'new separate consent binds the changed recovery fingerprint');
  });
  await t.test('refresh does not fabricate fresh MFA; logout invalidates live session', async () => {
    const before = claims(session.access_token);
    const refreshed = await stack.request('auth', '/token?grant_type=refresh_token', { method: 'POST', body: { refresh_token: session.refresh_token } });
    assert.equal(refreshed.status, 200);
    session = refreshed.data;
    const after = claims(session.access_token);
    assert.equal(after.session_id, before.session_id);
    assert.equal(after.amr.find((entry: any) => entry.method === 'totp').timestamp, before.amr.find((entry: any) => entry.method === 'totp').timestamp);
    assert.equal((await inspect()).eligible, true, 'refresh with same session preserves exact valid consent');
    const unenrolled = await stack.request('auth', `/factors/${factorId}`, { method: 'DELETE', token: session.access_token });
    assert.equal(unenrolled.status, 200, 'real local MFA reset fixture');
    assert.equal(await stack.sql(`select aal from auth.sessions where id='${after.session_id}'::uuid;`), 'aal1');
    await assert.rejects(inspect, /upstream_rejected_or_unknown/, 'stale AAL2 JWT cannot survive live MFA unenrollment');
    assert.equal((await stack.request('auth', '/logout?scope=local', { method: 'POST', token: session.access_token })).status, 204);
    assert.equal(await stack.sql(`select count(*) from auth.sessions where id='${after.session_id}'::uuid;`), '0');
    assert.ok((await stack.request('auth', '/user', { token: session.access_token })).status >= 400);
    await assert.rejects(inspect, /upstream_rejected_or_unknown/);
  });
});
