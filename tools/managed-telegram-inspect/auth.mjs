import { createPublicKey, verify } from 'node:crypto';
import { parseStrictJson } from '../../scripts/lib/telegram-resolution-inspect.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const reject = () => { throw new Error('authentication_rejected'); };
const record = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
function segment(text) {
  if (!/^[A-Za-z0-9_-]+$/.test(text)) reject();
  const bytes = Buffer.from(text, 'base64url');
  if (bytes.toString('base64url') !== text) reject();
  return bytes;
}

export function verifyManagedJwt(token, jwks, config, now, requireMfa = true) {
  try {
    if (typeof token !== 'string' || token.length > 16384) reject();
    const parts = token.split('.');
    if (parts.length !== 3) reject();
    const header = parseStrictJson(segment(parts[0]).toString('utf8'), 2048);
    const claims = parseStrictJson(segment(parts[1]).toString('utf8'), 12288);
    if (!record(header) || Object.keys(header).some((k) => !['alg', 'kid', 'typ'].includes(k))
        || !['ES256', 'RS256'].includes(header.alg) || header.typ !== 'JWT'
        || typeof header.kid !== 'string' || header.kid.length < 1 || header.kid.length > 128
        || !record(jwks) || !Array.isArray(jwks.keys) || jwks.keys.length > 16) reject();
    const keys = jwks.keys.filter((key) => record(key) && key.kid === header.kid);
    if (keys.length !== 1) reject();
    const jwk = keys[0];
    if (jwk.alg !== header.alg || (jwk.use !== undefined && jwk.use !== 'sig')
        || ['d', 'p', 'q', 'dp', 'dq', 'qi', 'oth', 'k'].some((key) => key in jwk)
        || (jwk.key_ops !== undefined && (!Array.isArray(jwk.key_ops)
          || jwk.key_ops.length !== 1 || jwk.key_ops[0] !== 'verify'))
        || (header.alg === 'ES256' && (jwk.kty !== 'EC' || jwk.crv !== 'P-256'))
        || (header.alg === 'RS256' && jwk.kty !== 'RSA')) reject();
    const key = createPublicKey({ key: jwk, format: 'jwk' });
    if (header.alg === 'RS256' && key.asymmetricKeyDetails.modulusLength < 2048) reject();
    if (!verify('sha256', Buffer.from(`${parts[0]}.${parts[1]}`),
      { key, dsaEncoding: 'ieee-p1363' }, segment(parts[2]))) reject();
    const seconds = Math.floor(now.getTime() / 1000);
    if (!record(claims) || claims.iss !== `${config.projectUrl}/auth/v1`
        || claims.aud !== 'authenticated' || claims.role !== 'authenticated'
        || !UUID.test(claims.sub ?? '') || !UUID.test(claims.session_id ?? '')
        || claims.sub === '00000000-0000-0000-0000-000000000000'
        || claims.session_id === '00000000-0000-0000-0000-000000000000'
        || claims.is_anonymous !== false || !['aal1', 'aal2'].includes(claims.aal)
        || !Number.isSafeInteger(claims.exp) || claims.exp <= seconds
        || !Number.isSafeInteger(claims.iat) || claims.iat > seconds
        || (claims.nbf !== undefined && (!Number.isSafeInteger(claims.nbf) || claims.nbf > seconds))) reject();
    let mfaAt = null;
    if (Array.isArray(claims.amr)) {
      for (const method of claims.amr) {
        if (record(method) && method.method === 'totp' && Number.isSafeInteger(method.timestamp)
            && method.timestamp <= seconds) mfaAt = Math.max(mfaAt ?? 0, method.timestamp);
      }
    }
    if (requireMfa && (claims.aal !== 'aal2' || mfaAt === null || seconds - mfaAt > 600)) reject();
    return Object.freeze({ userId: claims.sub, sessionId: claims.session_id,
      aal: claims.aal, mfaAt, expiresAt: claims.exp * 1000 });
  } catch { reject(); }
}

export function createUpstream(config, fetchImpl = fetch) {
  async function request(path, { method = 'GET', token, body } = {}) {
    try {
      // Fixed allowlisted same-project paths only; no redirects or retry loop.
      if (!/^\/auth\/v1\/(?:token\?grant_type=password|user|\.well-known\/jwks\.json|factors\/[0-9a-f-]{36}\/(?:challenge|verify)|logout\?scope=local)$/.test(path)
          && !/^\/rest\/v1\/rpc\/(?:managed_telegram_inspect_context|register_managed_telegram_inspect_consent|inspect_managed_telegram_delivery_unknown)$/.test(path)) throw new Error();
      const response = await fetchImpl(`${config.projectUrl}${path}`, {
        method, redirect: 'error', signal: AbortSignal.timeout(10000),
        headers: { apikey: config.publishableKey, Accept: 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) throw new Error();
      if (response.status === 204) return null;
      if (!(response.headers.get('content-type') ?? '').startsWith('application/json')) throw new Error();
      const reader = response.body.getReader();
      const chunks = []; let length = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > 65536) { await reader.cancel(); throw new Error(); }
        chunks.push(Buffer.from(value));
      }
      return parseStrictJson(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)), 65536);
    } catch { throw new Error('upstream_rejected_or_unknown'); }
  }
  async function authenticate(token, now, requireMfa = true) {
    const jwks = await request('/auth/v1/.well-known/jwks.json');
    const identity = verifyManagedJwt(token, jwks, config, now, requireMfa);
    const user = await request('/auth/v1/user', { token });
    if (!record(user) || user.id !== identity.userId || user.is_anonymous !== false
        || (user.banned_until && (!Number.isFinite(Date.parse(user.banned_until))
          || Date.parse(user.banned_until) > now.getTime()))
        || user.deleted_at) reject();
    const factors = Array.isArray(user.factors) ? user.factors.filter((factor) => record(factor)
      && factor.factor_type === 'totp' && factor.status === 'verified' && UUID.test(factor.id ?? '')) : [];
    if (factors.length < 1 || factors.length > 5) reject();
    return { identity, factors: factors.map((factor) => factor.id) };
  }
  return Object.freeze({ request, authenticate,
    rpc(name, token, body) { return request(`/rest/v1/rpc/${name}`, { method: 'POST', token, body }); } });
}
