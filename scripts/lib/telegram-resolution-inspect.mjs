import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign,
  verify,
} from 'node:crypto';

const ERROR_CODES = new Set([
  'invalid_json', 'json_too_large', 'invalid_canonical_value',
  'invalid_request', 'invalid_authorization', 'invalid_signing_key',
  'invalid_inspect_response',
  'arguments_invalid', 'inspect_arguments_required',
  'validate_only_rejects_credentials', 'operator_input_failed',
  'input_encoding_invalid', 'intent_file_invalid',
  'secret_descriptors_invalid', 'secret_descriptor_invalid',
  'secret_descriptor_permissions', 'secret_descriptor_timeout',
  'secret_descriptor_too_large', 'secret_descriptor_empty',
  'secret_descriptor_encoding', 'secret_descriptor_read_failed',
  'publishable_key_invalid', 'attempt_directory_invalid',
  'attempt_directory_permissions', 'authorization_already_attempted',
  'attempt_ledger_failed', 'attempt_ledger_close_failed',
  'project_ref_invalid', 'inspect_transport_unknown',
  'inspect_http_status_rejected', 'inspect_response_type_invalid',
  'inspect_response_too_large', 'inspect_response_invalid',
  'insecure_tls_environment', 'unsafe_runtime_diagnostics', 'inspect_operator_io_failed',
]);

/** Errors intentionally never include request, provider, or key material. */
export class InspectError extends Error {
  constructor(code) {
    const safeCode = ERROR_CODES.has(code) ? code : 'invalid_request';
    super(safeCode);
    this.name = 'InspectError';
    this.code = safeCode;
  }
}

function fail(code) { throw new InspectError(code); }

function unicodeOkay(text) {
  for (let i = 0; i < text.length; i += 1) {
    const unit = text.charCodeAt(i);
    if (unit === 0) return false;
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = text.charCodeAt(i + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      i += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

/** JSON.parse alone silently accepts duplicate keys and rounds large numbers. */
export function parseStrictJson(text, maxBytes = 32768) {
  if (typeof text !== 'string' || !Number.isSafeInteger(maxBytes)
      || maxBytes < 1 || !unicodeOkay(text)) fail('invalid_json');
  if (Buffer.byteLength(text, 'utf8') > maxBytes) fail('json_too_large');
  let cursor = 0;
  const whitespace = () => {
    while (cursor < text.length && /[\x20\x09\x0a\x0d]/.test(text[cursor])) cursor += 1;
  };
  function string() {
    const start = cursor;
    if (text[cursor++] !== '"') fail('invalid_json');
    while (cursor < text.length) {
      const unit = text[cursor++];
      if (unit === '"') {
        let decoded;
        try { decoded = JSON.parse(text.slice(start, cursor)); }
        catch { fail('invalid_json'); }
        if (!unicodeOkay(decoded)) fail('invalid_json');
        return decoded;
      }
      if (unit === '\\') {
        if (cursor >= text.length) fail('invalid_json');
        if (text[cursor] === 'u') {
          if (!/^[a-fA-F0-9]{4}$/.test(text.slice(cursor + 1, cursor + 5))) {
            fail('invalid_json');
          }
          cursor += 5;
        } else {
          if (!/["\\/bfnrt]/.test(text[cursor])) fail('invalid_json');
          cursor += 1;
        }
      } else if (unit.charCodeAt(0) < 0x20) fail('invalid_json');
    }
    fail('invalid_json');
  }
  function value(depth) {
    if (depth > 8) fail('invalid_json');
    whitespace();
    if (text[cursor] === '"') return string();
    if (text[cursor] === '{') {
      cursor += 1;
      whitespace();
      const result = {};
      const keys = new Set();
      if (text[cursor] === '}') { cursor += 1; return result; }
      while (cursor < text.length) {
        whitespace();
        const key = string();
        if (keys.has(key)) fail('invalid_json');
        keys.add(key);
        whitespace();
        if (text[cursor++] !== ':') fail('invalid_json');
        Object.defineProperty(result, key, {
          value: value(depth + 1), enumerable: true, writable: true, configurable: true,
        });
        whitespace();
        const separator = text[cursor++];
        if (separator === '}') return result;
        if (separator !== ',') fail('invalid_json');
      }
      fail('invalid_json');
    }
    if (text[cursor] === '[') {
      cursor += 1;
      whitespace();
      const result = [];
      if (text[cursor] === ']') { cursor += 1; return result; }
      while (cursor < text.length) {
        result.push(value(depth + 1));
        whitespace();
        const separator = text[cursor++];
        if (separator === ']') return result;
        if (separator !== ',') fail('invalid_json');
      }
      fail('invalid_json');
    }
    for (const [literal, decoded] of [['true', true], ['false', false], ['null', null]]) {
      if (text.startsWith(literal, cursor)) { cursor += literal.length; return decoded; }
    }
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(text.slice(cursor));
    if (!match) fail('invalid_json');
    cursor += match[0].length;
    // Reject decimal/exponent spellings instead of letting Number round a
    // mathematically non-integral literal into an apparently safe integer.
    if (!/^-?(?:0|[1-9][0-9]*)$/.test(match[0])) fail('invalid_json');
    const number = Number(match[0]);
    if (!Number.isSafeInteger(number) || Object.is(number, -0)) fail('invalid_json');
    return number;
  }
  const result = value(0);
  whitespace();
  if (cursor !== text.length) fail('invalid_json');
  return result;
}

function ascii(text) {
  return typeof text === 'string' && /^[\x01-\x7f]*$/.test(text);
}

function ownRecord(value, code) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)
      || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) fail(code);
  const keys = Reflect.ownKeys(value);
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (typeof key !== 'string' || !descriptor || !('value' in descriptor)
        || !descriptor.enumerable) fail(code);
  }
  return keys;
}

/** PostgreSQL jsonb::text for the deliberately small accepted value domain. */
export function pgJsonbText(value) {
  const active = new Set();
  function encode(current, depth) {
    if (depth > 8) fail('invalid_canonical_value');
    if (current === null) return 'null';
    if (typeof current === 'boolean') return current ? 'true' : 'false';
    if (typeof current === 'number') {
      if (!Number.isSafeInteger(current) || Object.is(current, -0)) fail('invalid_canonical_value');
      return String(current);
    }
    if (typeof current === 'string') {
      if (!ascii(current)) fail('invalid_canonical_value');
      return JSON.stringify(current);
    }
    if (typeof current !== 'object' || active.has(current)) fail('invalid_canonical_value');
    active.add(current);
    let encoded;
    if (Array.isArray(current)) {
      if (Reflect.ownKeys(current).length !== current.length + 1) fail('invalid_canonical_value');
      const values = [];
      for (let i = 0; i < current.length; i += 1) {
        const descriptor = Object.getOwnPropertyDescriptor(current, String(i));
        if (!descriptor || !('value' in descriptor)) fail('invalid_canonical_value');
        values.push(encode(descriptor.value, depth + 1));
      }
      encoded = `[${values.join(', ')}]`;
    } else {
      const keys = ownRecord(current, 'invalid_canonical_value');
      if (!keys.every(ascii)) fail('invalid_canonical_value');
      keys.sort((a, b) => a.length - b.length || Buffer.compare(Buffer.from(a), Buffer.from(b)));
      encoded = `{${keys.map((key) => `${JSON.stringify(key)}: ${encode(current[key], depth + 1)}`).join(', ')}}`;
    }
    active.delete(current);
    return encoded;
  }
  return encode(value, 0);
}

export function sha256(text) {
  if (typeof text !== 'string' || !unicodeOkay(text)) fail('invalid_canonical_value');
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[a-f0-9]{64}$/;
const RELEASE = /^[a-f0-9]{40}$/;
const PRINCIPAL = /^[A-Za-z0-9@._:-]{3,120}$/;
const NIL_UUID = '00000000-0000-0000-0000-000000000000';
const UUID_KEYS = [
  'workspace_id', 'content_item_id', 'content_version_id', 'publication_id',
  'job_id', 'resolution_id', 'operator_approval_id',
];
const AUDIT_KEYS = [
  'schema_version', 'scan_source', 'public_channel', 'first_message_id',
  'last_message_id', 'message_count', 'checked_at', 'caption_match_count',
  'png_match_count', 'snapshot_sha256',
];
const REQUEST_KEYS = [
  'schema_version', 'project_ref', 'environment', 'client_id', 'release_sha',
  ...UUID_KEYS, 'inspected_by', 'approved_by', 'expires_at', 'public_audit',
];
const AUTH_KEYS = [
  'schema_version', 'authorization_id', 'request_sha256', 'authorized_by',
  'authorized_at', 'expires_at', 'scope', 'signing_key_id', 'max_rpc_calls',
  'resend_authorized', 'automatic_publication',
];
const SUBJECT_HASH_KEYS = [
  'delivery_request_sha256', 'publication_request_sha256', 'publication_response_sha256',
  'job_input_sha256', 'job_output_sha256', 'content_item_row_sha256',
  'content_version_row_sha256', 'publication_row_sha256', 'job_row_sha256',
  'publication_approval_row_sha256', 'asset_row_sha256', 'caption_sha256',
  'asset_sha256', 'public_audit_sha256',
];
const FORBIDDEN_ACTIONS = [
  'provider_call', 'claim', 'requeue', 'resend', 'mark_published',
  'create_publication', 'create_job',
];
const SUBJECT_KEYS = [
  'schema_version', 'action', ...UUID_KEYS, 'client_id', 'publication_approval_id',
  'asset_id', 'delivery_attempt_id', 'delivery_started_at', ...SUBJECT_HASH_KEYS,
  'publication_status', 'job_status', 'delivery_outcome', 'disposition',
  'public_observation', 'public_audit', 'approved_by', 'expires_at',
  'approved_release_sha', 'resend_authorized', 'provider_calls', 'database_claims',
  'publication_state_changed', 'job_state_changed', 'forbidden_actions',
];
const RESPONSE_KEYS = [
  'eligible', 'resolved', 'reused', 'resolution_id', 'publication_id', 'job_id',
  'content_item_id', 'content_version_id', 'delivery_outcome', 'disposition',
  'public_observation', 'approval_subject', 'approval_subject_sha256', 'approved',
  'approved_at', 'resend_authorized',
];

function exactKeys(value, keys, code) {
  const actual = ownRecord(value, code);
  if (actual.length !== keys.length || !actual.every((key) => keys.includes(key))) fail(code);
}

function matches(value, pattern, code) {
  if (typeof value !== 'string' || !pattern.test(value)) fail(code);
}

function uuid(value, code) {
  matches(value, UUID, code);
  if (value === NIL_UUID) fail(code);
}

function nowMicros(now, code) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) fail(code);
  return BigInt(now.getTime()) * 1000n;
}

function dateMicros(text, code, database = false) {
  const expression = database
    ? /^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,6}))?(?:Z|\+00:00)$/
    : /^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})Z$/;
  const matched = typeof text === 'string' ? expression.exec(text) : null;
  if (!matched || Number(text.slice(0, 4)) < 1) fail(code);
  const milliseconds = Date.parse(`${matched[1]}Z`);
  if (!Number.isFinite(milliseconds)
      || new Date(milliseconds).toISOString().slice(0, 19) !== matched[1]) fail(code);
  return BigInt(milliseconds) * 1000n + BigInt((matched[2] ?? '').padEnd(6, '0'));
}

function frozenClone(value, code) {
  // Canonical encoding first rejects accessors, aliases outside JSON and cycles.
  let clone;
  try { clone = parseStrictJson(pgJsonbText(value)); }
  catch { fail(code); }
  const freeze = (entry) => {
    if (entry !== null && typeof entry === 'object') {
      for (const child of Object.values(entry)) freeze(child);
      Object.freeze(entry);
    }
    return entry;
  };
  return freeze(clone);
}

function validateAudit(audit, current, code) {
  exactKeys(audit, AUDIT_KEYS, code);
  if (audit.schema_version !== 'telegram-public-channel-audit@1'
      || audit.scan_source !== 'public_telegram_web_history'
      || audit.public_channel !== 'squid_kor_update'
      || audit.caption_match_count !== 0 || audit.png_match_count !== 0
      || !Number.isSafeInteger(audit.message_count)
      || audit.message_count < 1 || audit.message_count > 1000) fail(code);
  matches(audit.snapshot_sha256, HASH, code);
  for (const name of ['first_message_id', 'last_message_id']) {
    matches(audit[name], /^[1-9][0-9]{0,18}$/, code);
    if (BigInt(audit[name]) > 9223372036854775807n) fail(code);
  }
  const first = BigInt(audit.first_message_id);
  const last = BigInt(audit.last_message_id);
  if (first > last || BigInt(audit.message_count) > last - first + 1n) fail(code);
  const checked = dateMicros(audit.checked_at, code);
  if (checked > current || checked < current - 1800000000n) fail(code);
  return checked;
}

export function validateRequest(request, now = new Date()) {
  const code = 'invalid_request';
  exactKeys(request, REQUEST_KEYS, code);
  const clean = frozenClone(request, code);
  const current = nowMicros(now, code);
  if (clean.schema_version !== 'telegram-resolution-inspect-request@1'
      || clean.environment !== 'production' || clean.client_id !== 'squid') fail(code);
  matches(clean.project_ref, /^[a-z]{20}$/, code);
  matches(clean.release_sha, RELEASE, code);
  for (const key of UUID_KEYS) uuid(clean[key], code);
  if (new Set(UUID_KEYS.map((key) => clean[key])).size !== UUID_KEYS.length) fail(code);
  matches(clean.inspected_by, PRINCIPAL, code);
  matches(clean.approved_by, PRINCIPAL, code);
  const expires = dateMicros(clean.expires_at, code);
  if (expires <= current || expires > current + 7200000000n) fail(code);
  validateAudit(clean.public_audit, current, code);
  const public_audit_sha256 = sha256(pgJsonbText(clean.public_audit));
  const rpc_payload = {};
  for (const key of [...UUID_KEYS, 'inspected_by', 'approved_by', 'expires_at', 'release_sha', 'public_audit']) {
    rpc_payload[`target_${key}`] = clean[key];
  }
  return Object.freeze({
    request: clean,
    request_sha256: sha256(pgJsonbText(clean)),
    public_audit_sha256,
    rpc_payload: Object.freeze(rpc_payload),
  });
}

function checkedRequest(validated, now, code) {
  try {
    const fresh = validateRequest(validated.request, now);
    if (validated.request_sha256 !== fresh.request_sha256
        || validated.public_audit_sha256 !== fresh.public_audit_sha256
        || pgJsonbText(validated.rpc_payload) !== pgJsonbText(fresh.rpc_payload)) fail(code);
    return fresh;
  } catch { fail(code); }
}

export function validateAuthorization(auth, validated, now = new Date()) {
  const code = 'invalid_authorization';
  const fresh = checkedRequest(validated, now, code);
  exactKeys(auth, AUTH_KEYS, code);
  const clean = frozenClone(auth, code);
  uuid(clean.authorization_id, code);
  uuid(clean.signing_key_id, code);
  matches(clean.authorized_by, PRINCIPAL, code);
  const current = nowMicros(now, code);
  const authorized = dateMicros(clean.authorized_at, code);
  if (clean.schema_version !== 'telegram-resolution-inspect-authorization@1'
      || clean.request_sha256 !== fresh.request_sha256
      || clean.expires_at !== fresh.request.expires_at
      || clean.scope !== 'issue_inspect_jwt_and_call_once'
      || clean.max_rpc_calls !== 1 || clean.resend_authorized !== false
      || clean.automatic_publication !== false
      || authorized > current || authorized < current - 1800000000n) fail(code);
  return clean;
}

export function issueInspectJwt(validated, authorization, jwk, now = new Date()) {
  const fresh = checkedRequest(validated, now, 'invalid_authorization');
  const auth = validateAuthorization(authorization, fresh, now);
  const code = 'invalid_signing_key';
  try {
    const keys = ownRecord(jwk, code);
    const required = ['kty', 'crv', 'alg', 'kid', 'x', 'y', 'd'];
    if (!required.every((key) => keys.includes(key))
        || !keys.every((key) => [...required, 'use'].includes(key))
        || jwk.kty !== 'EC' || jwk.crv !== 'P-256' || jwk.alg !== 'ES256'
        || jwk.kid !== auth.signing_key_id
        || (Object.hasOwn(jwk, 'use') && jwk.use !== 'sig')) fail(code);
    for (const name of ['x', 'y', 'd']) {
      matches(jwk[name], /^[A-Za-z0-9_-]{43}$/, code);
      const bytes = Buffer.from(jwk[name], 'base64url');
      if (bytes.length !== 32 || bytes.toString('base64url') !== jwk[name]) fail(code);
    }
    const key = createPrivateKey({ key: { ...jwk }, format: 'jwk' });
    if (key.asymmetricKeyType !== 'ec'
        || key.asymmetricKeyDetails?.namedCurve !== 'prime256v1') fail(code);
    const request = fresh.request;
    const iat = Math.floor(now.getTime() / 1000);
    const claims = {
      iss: `https://${request.project_ref}.supabase.co/auth/v1`,
      aud: 'authenticated', iat, nbf: iat,
      exp: Number(dateMicros(request.expires_at, code) / 1000000n),
      role: 'coineasy_telegram_resolution', workspace_id: request.workspace_id,
      sub: request.inspected_by, capability: 'telegram_delivery_unknown_inspect',
      environment: 'production', release_sha: request.release_sha,
      automatic_publication: false, resend_authorized: false, max_external_actions: 0,
      jti: request.resolution_id, content_item_id: request.content_item_id,
      content_version_id: request.content_version_id, publication_id: request.publication_id,
      job_id: request.job_id, resolution_id: request.resolution_id,
      operator_approval_id: request.operator_approval_id, approved_by: request.approved_by,
      expires_at: request.expires_at, public_audit_sha256: fresh.public_audit_sha256,
    };
    const header = { alg: 'ES256', typ: 'JWT', kid: auth.signing_key_id };
    const encode = (value) => Buffer.from(JSON.stringify(value), 'utf8').toString('base64url');
    const input = `${encode(header)}.${encode(claims)}`;
    const signature = sign('sha256', Buffer.from(input), { key, dsaEncoding: 'ieee-p1363' });
    if (signature.length !== 64 || !verify('sha256', Buffer.from(input), {
      key: createPublicKey(key), dsaEncoding: 'ieee-p1363',
    }, signature)) fail(code);
    return `${input}.${signature.toString('base64url')}`;
  } catch { fail(code); }
}

export function validateInspectResponse(raw, validated, now = new Date()) {
  const code = 'invalid_inspect_response';
  try {
    const fresh = checkedRequest(validated, now, code);
    const response = typeof raw === 'string' ? parseStrictJson(raw) : raw;
    exactKeys(response, RESPONSE_KEYS, code);
    const clean = frozenClone(response, code);
    const subject = clean.approval_subject;
    exactKeys(subject, SUBJECT_KEYS, code);
    if (Buffer.byteLength(pgJsonbText(subject), 'utf8') > 16384) fail(code);
    if (clean.eligible !== true || clean.resolved !== false || clean.reused !== false
        || clean.approved !== false || clean.approved_at !== null
        || clean.resend_authorized !== false) fail(code);
    for (const key of ['resolution_id', 'publication_id', 'job_id', 'content_item_id', 'content_version_id']) {
      if (clean[key] !== fresh.request[key]) fail(code);
    }
    const constants = {
      schema_version: 'exact-telegram-delivery-resolution@1',
      action: 'resolve_delivery_unknown_without_resend', client_id: 'squid',
      publication_status: 'delivery_unknown', job_status: 'failed', delivery_outcome: 'unknown',
      disposition: 'operator_closed_without_resend', public_observation: 'not_observed_at_checked_at',
      resend_authorized: false, provider_calls: 0, database_claims: 0,
      publication_state_changed: false, job_state_changed: false,
    };
    for (const [key, value] of Object.entries(constants)) {
      if (subject[key] !== value) fail(code);
    }
    for (const key of ['delivery_outcome', 'disposition', 'public_observation']) {
      if (clean[key] !== constants[key]) fail(code);
    }
    for (const key of [...UUID_KEYS, 'approved_by']) {
      if (subject[key] !== fresh.request[key]) fail(code);
    }
    const subjectUuids = [...UUID_KEYS, 'publication_approval_id', 'asset_id', 'delivery_attempt_id'];
    for (const key of subjectUuids) uuid(subject[key], code);
    if (new Set(subjectUuids.map((key) => subject[key])).size !== subjectUuids.length) fail(code);
    for (const key of SUBJECT_HASH_KEYS) matches(subject[key], HASH, code);
    matches(clean.approval_subject_sha256, HASH, code);
    if (subject.approved_release_sha !== fresh.request.release_sha
        || subject.public_audit_sha256 !== fresh.public_audit_sha256
        || pgJsonbText(subject.public_audit) !== pgJsonbText(fresh.request.public_audit)
        || pgJsonbText(subject.forbidden_actions) !== pgJsonbText(FORBIDDEN_ACTIONS)
        || sha256(pgJsonbText(subject)) !== clean.approval_subject_sha256
        || dateMicros(subject.expires_at, code, true)
          !== dateMicros(fresh.request.expires_at, code)) fail(code);
    const checked = validateAudit(subject.public_audit, nowMicros(now, code), code);
    if (dateMicros(subject.delivery_started_at, code, true) > checked - 600000000n) fail(code);
    return clean;
  } catch { fail(code); }
}
