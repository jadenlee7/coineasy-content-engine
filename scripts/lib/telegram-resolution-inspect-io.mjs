import fs from 'node:fs';
import path from 'node:path';
import https from 'node:https';
import tty from 'node:tty';
import net from 'node:net';
import {
  InspectError, parseStrictJson, validateRequest, validateAuthorization,
  issueInspectJwt, validateInspectResponse,
} from './telegram-resolution-inspect.mjs';

const RPC_PATH = '/rest/v1/rpc/inspect_exact_telegram_delivery_unknown_resolution';
const fail = (code) => { throw new InspectError(code); };
const decode = (bytes) => {
  try { return new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
  catch { return fail('input_encoding_invalid'); }
};

// Intent files are ordinary local inputs, never credentials or remote URLs.
export function readIntentFile(filename) {
  let fd;
  try {
    if (typeof filename !== 'string' || !filename || !fs.lstatSync(filename).isFile()) fail('intent_file_invalid');
    fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const st = fs.fstatSync(fd);
    if (!st.isFile() || st.size < 2 || st.size > 32768) fail('intent_file_invalid');
    const bytes = Buffer.alloc(32769);
    let total = 0;
    while (total < bytes.length) {
      const n = fs.readSync(fd, bytes, total, bytes.length - total, null);
      if (!n) break;
      total += n;
    }
    if (total > 32768) fail('intent_file_invalid');
    return parseStrictJson(decode(bytes.subarray(0, total)));
  } catch (e) {
    if (e instanceof InspectError) throw e;
    return fail('intent_file_invalid');
  } finally { if (fd !== undefined) fs.closeSync(fd); }
}

export function validateSecretDescriptors(signingKeyFd, publishableKeyFd) {
  if (![signingKeyFd, publishableKeyFd].every(fd => Number.isSafeInteger(fd) && fd >= 3)
      || signingKeyFd === publishableKeyFd) fail('secret_descriptors_invalid');
}

// Inherited descriptors avoid argv, environment lookup, and token output files.
// A bounded deadline also covers a pipe whose producer never finishes writing.
export function readSecretDescriptor(fd, {timeoutMs = 5000} = {}) {
  return new Promise((resolve, reject) => {
    let stream, timer, settled = false, ownsFd = false;
    const chunks = [];
    let length = 0;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      for (const chunk of chunks) chunk.fill(0);
      const complete = () => {
        if (error) reject(new InspectError(error)); else resolve(value);
      };
      if (stream && !stream.closed) {
        stream.once('close', complete);
        stream.destroy();
      } else {
        if (!stream && ownsFd) { try { fs.closeSync(fd); } catch { /* Owned FD only. */ } }
        complete();
      }
    };
    try {
      if (!Number.isSafeInteger(fd) || fd < 3 || tty.isatty(fd)) fail('secret_descriptor_invalid');
      const st = fs.fstatSync(fd);
      if (!(st.isFile() || st.isFIFO() || st.isSocket())) fail('secret_descriptor_invalid');
      ownsFd = true;
      if (st.isFile() && ((st.mode & 0o077) !== 0 || st.uid !== process.getuid())) fail('secret_descriptor_permissions');
      if (st.isFile()) {
        const bytes = Buffer.alloc(16385);
        try {
          let size = 0;
          while (size < bytes.length) {
            const count = fs.readSync(fd, bytes, size, bytes.length - size, null);
            if (!count) break;
            size += count;
          }
          if (!size) finish('secret_descriptor_empty');
          else if (size > 16384) finish('secret_descriptor_too_large');
          else finish(null, decode(bytes.subarray(0, size)));
        } catch (e) {
          finish(e instanceof InspectError ? 'secret_descriptor_encoding' : 'secret_descriptor_read_failed');
        } finally { bytes.fill(0); }
        return;
      }
      // fs.ReadStream on a held-open pipe can leave a blocking libuv read even
      // after destroy(). A socket handle uses cancellable nonblocking I/O for
      // inherited FIFO/socket descriptors, so timeout also bounds process exit.
      stream = new net.Socket({fd, readable: true, writable: false});
      timer = setTimeout(() => finish('secret_descriptor_timeout'), timeoutMs);
      stream.on('data', chunk => {
        if (settled) { chunk.fill(0); return; }
        length += chunk.length;
        if (length > 16384) { chunk.fill(0); finish('secret_descriptor_too_large'); return; }
        chunks.push(chunk);
      });
      stream.on('end', () => {
        const bytes = Buffer.concat(chunks);
        try {
          if (!bytes.length) finish('secret_descriptor_empty');
          else finish(null, decode(bytes));
        } catch { finish('secret_descriptor_encoding'); }
        finally { bytes.fill(0); }
      });
      stream.on('error', () => finish('secret_descriptor_read_failed'));
      stream.on('close', () => { if (!settled) finish('secret_descriptor_read_failed'); });
    } catch (e) {
      finish(e instanceof InspectError ? e.code : 'secret_descriptor_read_failed');
    }
  });
}

export function validatePublishableKey(value) {
  if (typeof value !== 'string') fail('publishable_key_invalid');
  const key = value.trim();
  if (!/^sb_publishable_[A-Za-z0-9_-]{20,200}$/.test(key)) fail('publishable_key_invalid');
  return key;
}

// Local dedupe is not a substitute for server-side authorization/consumption.
// Never remove or silently reset an attempt marker, including failed attempts.
export function reserveAttempt(directory, auth, validated, now = new Date()) {
  let fd;
  try {
    if (typeof directory !== 'string' || !path.isAbsolute(directory)) fail('attempt_directory_invalid');
    if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(auth?.authorization_id ?? '')
        || !/^[0-9a-f]{64}$/.test(validated?.request_sha256 ?? '')) fail('attempt_directory_invalid');
    try { fs.mkdirSync(directory, {mode: 0o700}); }
    catch (e) { if (e.code !== 'EEXIST') throw e; }
    const st = fs.lstatSync(directory);
    if (!st.isDirectory() || st.isSymbolicLink() || st.uid !== process.getuid() || (st.mode & 0o077) !== 0) fail('attempt_directory_permissions');
    const filename = path.join(fs.realpathSync(directory), `${auth.authorization_id}.json`);
    fd = fs.openSync(filename, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
    const base = {
      schema_version: 'telegram-resolution-inspect-attempt@1',
      authorization_id: auth.authorization_id,
      request_sha256: validated.request_sha256,
      created_at: now.toISOString(),
      expires_at: auth.expires_at,
    };
    const update = (state, credentialIssued, rpcAttempted, errorCode = null) => {
      const bytes = Buffer.from(JSON.stringify({...base, state, credential_issued: credentialIssued, rpc_attempted: rpcAttempted, error_code: errorCode}) + '\n');
      fs.ftruncateSync(fd, 0);
      let offset = 0;
      while (offset < bytes.length) {
        const written = fs.writeSync(fd, bytes, offset, bytes.length - offset, offset);
        if (written <= 0) fail('attempt_ledger_failed');
        offset += written;
      }
      fs.fsyncSync(fd);
    };
    update('authorization_reserved', false, false);
    // Make the directory entry durable before any credential/network action.
    const dirFd = fs.openSync(fs.realpathSync(directory), fs.constants.O_RDONLY);
    try { fs.fsyncSync(dirFd); } finally { fs.closeSync(dirFd); }
    return {update, close() { if (fd !== undefined) { fs.closeSync(fd); fd = undefined; } }};
  } catch (e) {
    if (fd !== undefined) fs.closeSync(fd);
    if (e instanceof InspectError) throw e;
    return fail(e.code === 'EEXIST' ? 'authorization_already_attempted' : 'attempt_ledger_failed');
  }
}

// node:https has no redirect/proxy discovery. No general URL or RPC option exists.
export function postInspectOnce(projectRef, rpcPayload, token, publishableKey, {request = https.request, timeoutMs = 10000} = {}) {
  return new Promise((resolve, reject) => {
    if (!/^[a-z]{20}$/.test(projectRef)) { reject(new InspectError('project_ref_invalid')); return; }
    const body = Buffer.from(JSON.stringify(rpcPayload));
    let req, timer, settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) { req?.destroy(); reject(new InspectError(error)); }
      else resolve(result);
    };
    try {
      timer = setTimeout(() => finish('inspect_transport_unknown'), timeoutMs);
      req = request({
        hostname: `${projectRef}.supabase.co`, port: 443, path: RPC_PATH,
        method: 'POST', agent: false, rejectUnauthorized: true,
        servername: `${projectRef}.supabase.co`,
        headers: {'Content-Type': 'application/json', Accept: 'application/json',
          'Content-Length': body.length, Authorization: `Bearer ${token}`, apikey: publishableKey},
      }, response => {
        if (response.statusCode !== 200) { response.destroy(); finish('inspect_http_status_rejected'); return; }
        if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(String(response.headers['content-type'] ?? ''))) {
          response.destroy(); finish('inspect_response_type_invalid'); return;
        }
        const chunks = [];
        let length = 0;
        response.on('data', chunk => {
          length += chunk.length;
          if (length > 65536) { response.destroy(); finish('inspect_response_too_large'); return; }
          chunks.push(chunk);
        });
        response.on('end', () => {
          try { finish(null, parseStrictJson(decode(Buffer.concat(chunks)), 65536)); }
          catch { finish('inspect_response_invalid'); }
        });
        response.on('error', () => finish('inspect_transport_unknown'));
        response.on('aborted', () => finish('inspect_transport_unknown'));
      });
      req.on('error', () => finish('inspect_transport_unknown'));
      req.end(body);
    } catch { finish('inspect_transport_unknown'); }
  });
}

export function validationSummary(validated) {
  return {ok: true, mode: 'validate_only', request_sha256: validated.request_sha256,
    public_audit_sha256: validated.public_audit_sha256, release_sha: validated.request.release_sha,
    credential_issued: false, database_calls: 0, provider_calls: 0, execution_authorized: false};
}

export async function executeInspectOnce(options, deps = {}) {
  const now = deps.now ?? (() => new Date());
  const readSecret = deps.readSecret ?? readSecretDescriptor;
  const reserve = deps.reserve ?? reserveAttempt;
  const post = deps.post ?? postInspectOnce;
  let marker, credentialIssued = false, rpcAttempted = false;
  try {
    let validated = validateRequest(options.request, now());
    const auth = validateAuthorization(options.authorization, validated, now());
    validateSecretDescriptors(options.signingKeyFd, options.publishableKeyFd);
    if (process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') fail('insecure_tls_environment');
    // Node's HTTP debug output can print Authorization headers outside our
    // logger. Refuse diagnostic/key-log startup modes before reading any key.
    if (['NODE_DEBUG', 'NODE_DEBUG_NATIVE', 'NODE_OPTIONS', 'SSLKEYLOGFILE']
      .some(name => Boolean(process.env[name]?.trim()))
        || process.execArgv.some(arg => /^(?:--(?:inspect(?:-brk|-wait)?|debug|trace-tls|tls-keylog|require|import|(?:experimental-)?loader)(?:=|$)|-r$)/.test(arg))) {
      fail('unsafe_runtime_diagnostics');
    }
    marker = reserve(options.attemptLedgerDir, auth, validated, now());
    const keyText = await readSecret(options.signingKeyFd);
    const jwk = parseStrictJson(keyText, 16384);
    const apiKey = validatePublishableKey(await readSecret(options.publishableKeyFd));
    validated = validateRequest(options.request, now());
    validateAuthorization(auth, validated, now());
    const token = issueInspectJwt(validated, auth, jwk, now());
    credentialIssued = true;
    marker.update('credential_issued', true, false);
    validateRequest(options.request, now());
    validateAuthorization(auth, validated, now());
    // Fsync the ambiguous-attempt marker before the only outbound request.
    marker.update('request_started', true, true);
    rpcAttempted = true;
    const raw = await post(validated.request.project_ref, validated.rpc_payload, token, apiKey);
    const verified = validateInspectResponse(raw, validated, now());
    marker.update('inspection_verified', true, true);
    try { marker.close(); }
    catch { fail('attempt_ledger_close_failed'); }
    marker = undefined;
    return {ok: true, mode: 'inspect_once', request_sha256: validated.request_sha256,
      credential_issued: true, database_calls: 1, provider_calls: 0,
      approval_performed: false, resolution_performed: false, inspection: verified};
  } catch (cause) {
    const code = cause instanceof InspectError ? cause.code : 'inspect_operator_io_failed';
    try { marker?.update('failed_closed', credentialIssued, rpcAttempted, code); } catch { /* Never retry to repair local evidence. */ }
    const error = new InspectError(code);
    error.credentialIssued = credentialIssued;
    error.rpcAttempted = rpcAttempted;
    throw error;
  } finally {
    // A cleanup failure must never hide that a credential/request already ran.
    try { marker?.close(); } catch { /* The marker remains consumed. */ }
  }
}
