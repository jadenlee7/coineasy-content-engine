import { createServer } from 'node:http';
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { loadConfig, assertRuntimeFlags } from './config.mjs';
import { createUpstream } from './auth.mjs';
import { parseStrictJson, validateRequest, validateInspectResponse } from '../../scripts/lib/telegram-resolution-inspect.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const COOKIE = '__Host-managed-inspect';
const escape = (text) => String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const hidden = (name, value) => `<input type="hidden" name="${escape(name)}" value="${escape(value)}">`;
const csrfField = (session) => hidden('csrf', session.csrf);
const fail = (code = 'request_rejected') => { throw new Error(code); };
function exact(value, keys) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join(',') !== [...keys].sort().join(',')) fail();
}
function utcMillis(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)
      || !Number.isFinite(Date.parse(value))
      || new Date(value).toISOString().slice(0, 19) !== value.slice(0, 19)) fail();
  return Date.parse(value);
}
function futureUtc(value, now) {
  const stamp = utcMillis(value);
  if (stamp <= now.getTime()) fail();
  return stamp;
}
export function validateContext(value, identity, config, now) {
  exact(value, ['schema_version', 'user_id', 'workspace_id', 'inspected_by', 'approved_by',
    'project_ref', 'release_id', 'release_sha', 'migration_sha256', 'verified_deployment_reference', 'expires_at']);
  if (value.schema_version !== 'managed-telegram-inspect-context@1'
      || value.user_id !== identity.userId || value.workspace_id !== config.workspaceId
      || value.inspected_by !== `auth:${identity.userId}`
      || !/^[A-Za-z0-9@._:-]{3,120}$/.test(value.approved_by ?? '')
      || value.project_ref !== config.projectRef || value.release_sha !== config.buildSha
      || !UUID.test(value.release_id ?? '') || !HASH.test(value.migration_sha256 ?? '')
      || typeof value.verified_deployment_reference !== 'string'
      || !/^[\x20-\x7e]{1,500}$/.test(value.verified_deployment_reference)) fail();
  futureUtc(value.expires_at, now);
  return Object.freeze(value);
}
export function validateConsent(value, consentId, validated, context, now) {
  exact(value, ['schema_version', 'consent_id', 'request_sha256', 'public_audit_sha256',
    'consented_at', 'expires_at', 'reused']);
  const expires = futureUtc(value.expires_at, now);
  const consented = utcMillis(value.consented_at);
  if (value.schema_version !== 'managed-telegram-inspect-consent@1' || value.consent_id !== consentId
      || value.request_sha256 !== validated.request_sha256
      || value.public_audit_sha256 !== validated.public_audit_sha256 || value.reused !== false
      || consented > now.getTime()
      || consented < now.getTime() - 15000 || expires > consented + 600000
      || expires > Date.parse(validated.request.expires_at) || expires > Date.parse(context.expires_at)) fail();
  return Object.freeze(value);
}

export function createManagedInspectServer({ config = loadConfig(), fetchImpl = fetch,
  clock = () => new Date() } = {}) {
  const sessions = new Map();
  const limits = new Map();
  const upstream = config.enabled ? createUpstream(config, fetchImpl) : null;
  const token = () => randomBytes(32).toString('hex');
  const page = (title, content) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${escape(title)}</title></head><body><main><h1>${escape(title)}</h1><p>Private inspection only. No send, claim, approval or resolution. A user session is not an inspect-only token.</p>${content}</main></body></html>`;
  function reply(response, status, title, content) {
    response.writeHead(status, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(page(title, content));
  }
  function setCookie(response, id) {
    response.setHeader('Set-Cookie', `${COOKIE}=${id}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=1800`);
  }
  function newSession(response, data = {}) {
    if (sessions.size >= 256) fail('capacity_rejected');
    const id = token();
    const session = { csrf: token(), expires: clock().getTime() + 600000, views: new Map(), ...data };
    sessions.set(id, session); setCookie(response, id);
    return { id, session };
  }
  function sessionOf(request) {
    const values = (request.headers.cookie ?? '').split(';').map((part) => part.trim()).filter((part) => part.startsWith(`${COOKIE}=`));
    if (values.length !== 1) return null;
    const id = values[0].slice(COOKIE.length + 1);
    if (!/^[0-9a-f]{64}$/.test(id)) return null;
    const session = sessions.get(id);
    if (!session || session.expires <= clock().getTime()) return null;
    return { id, session };
  }
  function rateLimit(request) {
    const now = clock().getTime();
    for (const [key, value] of limits) if (value.until <= now) limits.delete(key);
    // Do not trust X-Forwarded-For. Shared proxy IPs are conservatively shared.
    const key = request.socket.remoteAddress ?? 'unknown';
    const entry = limits.get(key) ?? { count: 0, until: now + 60000 };
    if (++entry.count > 30 || limits.size >= 1024) fail('rate_limited');
    limits.set(key, entry);
  }
  async function formBody(request, session) {
    if (request.headers.origin !== config.origin || request.headers['sec-fetch-site'] === 'cross-site'
        || !/^application\/x-www-form-urlencoded(?:;\s*charset=utf-8)?$/i.test(request.headers['content-type'] ?? '')) fail();
    let length = 0; const parts = [];
    for await (const part of request) {
      length += part.length;
      if (length > 100000) fail();
      parts.push(part);
    }
    const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(parts));
    const params = new URLSearchParams(text); const result = {};
    for (const [key, value] of params) {
      if (Object.hasOwn(result, key)) fail();
      Object.defineProperty(result, key, { value, enumerable: true });
    }
    if (typeof result.csrf !== 'string' || !/^[0-9a-f]{64}$/.test(result.csrf)
        || !timingSafeEqual(Buffer.from(result.csrf), Buffer.from(session.csrf))) fail();
    return result;
  }
  async function protectedSession(found) {
    if (!found?.session.accessToken || !found.session.mfa) fail();
    const { identity, factors } = await upstream.authenticate(found.session.accessToken, clock());
    if (identity.userId !== found.session.identity.userId || identity.sessionId !== found.session.identity.sessionId
        || !factors.includes(found.session.factorId)) fail();
    const context = validateContext(await upstream.rpc('managed_telegram_inspect_context', found.session.accessToken,
      { target_workspace_id: config.workspaceId, target_release_sha: config.buildSha }), identity, config, clock());
    return { identity, context };
  }
  function checkBinding(validated, context) {
    const req = validated.request;
    if (req.workspace_id !== config.workspaceId || req.release_sha !== config.buildSha
        || req.project_ref !== config.projectRef || req.inspected_by !== context.inspected_by
        || req.approved_by !== context.approved_by) fail();
  }
  function requestForm(session, context) {
    return `<p>Server inspector: <code>${escape(context.inspected_by)}</code><br>Intended approver: <code>${escape(context.approved_by)}</code><br>Build: <code>${escape(config.buildSha)}</code></p><p>Supply only the bounded exact request and an actual human public audit. No draft text, images, media URLs or credentials.</p><form method="post" action="/review">${csrfField(session)}<label>Exact request JSON<textarea name="request" required rows="24" cols="100" maxlength="32768"></textarea></label><button>Review exact request (no consent write)</button></form><form method="post" action="/logout">${csrfField(session)}<button>Log out</button></form>`;
  }
  const server = createServer({ maxHeaderSize: 16384, requestTimeout: 15000, headersTimeout: 10000 }, async (request, response) => {
    response.setHeader('Cache-Control', 'no-store, max-age=0');
    response.setHeader('Pragma', 'no-cache');
    response.setHeader('Referrer-Policy', 'no-referrer');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Content-Security-Policy', "default-src 'none'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'");
    response.setHeader('Strict-Transport-Security', 'max-age=31536000');
    response.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    try {
      if (!config.enabled) { reply(response, 503, 'Disabled', '<p>Default disabled. No Auth or database request was made.</p>'); return; }
      if (request.headers.host !== new URL(config.origin).host || request.headers['sec-fetch-site'] === 'cross-site') fail();
      for (const [id, session] of sessions) if (session.expires <= clock().getTime()) sessions.delete(id);
      rateLimit(request);
      const route = request.url;
      let found = sessionOf(request);
      if (request.method === 'GET' && route === '/guard.mjs') {
        response.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' });
        response.end(await readFile(new URL('./browser-guard.mjs', import.meta.url), 'utf8')); return;
      }
      if (request.method === 'GET' && route === '/') {
        if (!found) found = newSession(response);
        if (found.session.mfa) {
          const { context } = await protectedSession(found);
          reply(response, 200, 'Exact private inspection', requestForm(found.session, context)); return;
        }
        reply(response, 200, 'Dedicated operator login', `<p>Use only the separately authorized minimal account with an existing verified TOTP factor. No signup, enrollment or recovery here.</p><form method="post" action="/login">${csrfField(found.session)}<label>Email<input type="email" name="email" required maxlength="254" autocomplete="username"></label><label>Password<input type="password" name="password" required maxlength="256" autocomplete="current-password"></label><button>Log in</button></form>`); return;
      }
      if (request.method !== 'POST' || !found) fail();
      if (!['/login', '/mfa', '/logout', '/review', '/consent', '/inspect'].includes(route)) fail();
      const form = await formBody(request, found.session);
      if (route === '/login') {
        exact(form, ['csrf', 'email', 'password']);
        if (!/^[^\s@]{1,128}@[^\s@]{1,125}$/.test(form.email) || form.email.length > 254
            || form.password.length < 1 || form.password.length > 256) fail();
        const auth = await upstream.request('/auth/v1/token?grant_type=password', { method: 'POST',
          body: { email: form.email, password: form.password } });
        const { identity, factors } = await upstream.authenticate(auth.access_token, clock(), false);
        sessions.delete(found.id);
        found = newSession(response, { accessToken: auth.access_token, identity,
          expires: Math.min(identity.expiresAt, clock().getTime() + 600000) });
        // Refresh tokens and submitted password are deliberately not retained.
        reply(response, 200, 'Verify existing TOTP', `<form method="post" action="/mfa">${csrfField(found.session)}<label>Verified factor<select name="factor_id">${factors.map((id) => `<option value="${id}">${id}</option>`).join('')}</select></label><label>Current TOTP<input name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="one-time-code"></label><button>Verify TOTP (no consent write)</button></form>`); return;
      }
      if (route === '/mfa') {
        exact(form, ['csrf', 'factor_id', 'code']);
        if (!found.session.accessToken || !UUID.test(form.factor_id) || !/^[0-9]{6}$/.test(form.code)) fail();
        const before = await upstream.authenticate(found.session.accessToken, clock(), false);
        if (!before.factors.includes(form.factor_id) || before.identity.sessionId !== found.session.identity.sessionId) fail();
        const challenge = await upstream.request(`/auth/v1/factors/${form.factor_id}/challenge`, { method: 'POST', token: found.session.accessToken, body: {} });
        if (!UUID.test(challenge.id ?? '') || !Number.isSafeInteger(challenge.expires_at)
            || challenge.expires_at <= Math.floor(clock().getTime() / 1000)) fail();
        const verified = await upstream.request(`/auth/v1/factors/${form.factor_id}/verify`, { method: 'POST', token: found.session.accessToken,
          body: { challenge_id: challenge.id, code: form.code } });
        const after = await upstream.authenticate(verified.access_token, clock());
        if (after.identity.userId !== before.identity.userId || after.identity.sessionId !== before.identity.sessionId
            || !after.factors.includes(form.factor_id)) fail();
        sessions.delete(found.id);
        found = newSession(response, { accessToken: verified.access_token, identity: after.identity,
          mfa: true, factorId: form.factor_id, expires: Math.min(after.identity.expiresAt, clock().getTime() + 600000) });
        const { context } = await protectedSession(found);
        reply(response, 200, 'Exact private inspection', requestForm(found.session, context)); return;
      }
      if (route === '/logout') {
        exact(form, ['csrf']);
        sessions.delete(found.id);
        response.setHeader('Set-Cookie', `${COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0`);
        if (found.session.accessToken) await upstream.request('/auth/v1/logout?scope=local', { method: 'POST', token: found.session.accessToken });
        reply(response, 200, 'Logged out', '<p>Local session removed.</p>'); return;
      }
      const { context } = await protectedSession(found);
      if (route === '/review') {
        exact(form, ['csrf', 'request']);
        if (found.session.views.size >= 8) fail();
        const validated = validateRequest(parseStrictJson(form.request), clock());
        checkBinding(validated, context);
        const reviewId = token(); const consentId = randomUUID();
        found.session.views.set(reviewId, { validated, consentId });
        reply(response, 200, 'Confirm exact inspect consent', `<p>Request SHA-256: <code>${validated.request_sha256}</code></p><pre>${escape(JSON.stringify(validated.request, null, 2))}</pre><p>This separate consent registration writes a new authorization record. It is not delivery approval or resolution. Inspect itself does not change DB records.</p><form method="post" action="/consent">${csrfField(found.session)}${hidden('review_id', reviewId)}<label><input type="checkbox" name="confirm" value="inspect-only" required>I explicitly consent to inspecting this exact request only.</label><button>Register exact consent (authorization write)</button></form>`); return;
      }
      if (route === '/consent') {
        exact(form, ['csrf', 'review_id', 'confirm']);
        const view = found.session.views.get(form.review_id);
        if (!view || view.consentAttempted || form.confirm !== 'inspect-only') fail();
        const validated = validateRequest(view.validated.request, clock()); checkBinding(validated, context);
        // An uncertain response does not permit silently reissuing a new consent.
        view.consentAttempted = true;
        const raw = await upstream.rpc('register_managed_telegram_inspect_consent', found.session.accessToken,
          { target_consent_id: view.consentId, target_request: validated.request, target_request_sha256: validated.request_sha256 });
        view.consent = validateConsent(raw, view.consentId, validated, context, clock());
        reply(response, 200, 'Exact consent registered', `<p>Consent: <code>${view.consentId}</code><br>Request hash: <code>${validated.request_sha256}</code><br>Expires: ${escape(view.consent.expires_at)}</p><p>One application attempt per origin/browser-profile storage, not global exactly-once. Browser transport may replay HTTP POST; the live server-session gate permits only one upstream inspect RPC attempt. No application retry after failure, timeout or refresh.</p><noscript>BLOCK: JavaScript is required to commit the local attempt marker. No inspect submission is enabled.</noscript><p data-attempt-status>Local marker must commit before submission.</p><form data-inspect-form data-consent-id="${view.consentId}" data-request-hash="${validated.request_sha256}" method="post" action="/inspect">${csrfField(found.session)}${hidden('consent_id', view.consentId)}${hidden('attempt_marker_committed', '0')}<button disabled>Inspect once (DB read-only; no send)</button></form><script type="module" src="/guard.mjs"></script>`); return;
      }
      if (route === '/inspect') {
        exact(form, ['csrf', 'consent_id', 'attempt_marker_committed']);
        const view = [...found.session.views.values()].find((entry) => entry.consentId === form.consent_id);
        if (!view?.consent || view.inspectAttempted || form.attempt_marker_committed !== '1') fail();
        futureUtc(view.consent.expires_at, clock());
        const validated = validateRequest(view.validated.request, clock()); checkBinding(validated, context);
        view.inspectAttempted = true;
        const raw = await upstream.rpc('inspect_managed_telegram_delivery_unknown', found.session.accessToken,
          { target_consent_id: view.consentId });
        const bounded = validateInspectResponse(raw, validated, clock());
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        response.end(JSON.stringify(bounded)); return;
      }
      fail();
    } catch (error) {
      // No raw request, Auth/DB payload, cookies or error causes reach logs/UI.
      if (!response.headersSent) reply(response, error.message === 'rate_limited' ? 429 : 400, 'BLOCK', '<p>Request rejected or result unknown. No automatic retry. Recheck authorization before proceeding.</p>');
      else response.end();
    }
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    assertRuntimeFlags();
    const port = Number(process.env.PORT ?? '8787');
    const host = process.env.MANAGED_INSPECT_BIND_HOST ?? '127.0.0.1';
    if (!Number.isInteger(port) || port < 1024 || port > 65535 || !['127.0.0.1', '0.0.0.0'].includes(host)) throw new Error();
    const stamp = process.env.MANAGED_INSPECT_ENABLED === 'true'
      ? (await readFile(new URL('./build-sha.txt', import.meta.url), 'utf8')).trim() : undefined;
    createManagedInspectServer({ config: loadConfig(process.env, stamp) })
      .on('error', () => { process.stderr.write('managed_inspect_listen_blocked\n'); process.exitCode = 1; })
      .listen(port, host);
  } catch { process.stderr.write('managed_inspect_startup_blocked\n'); process.exitCode = 1; }
}
