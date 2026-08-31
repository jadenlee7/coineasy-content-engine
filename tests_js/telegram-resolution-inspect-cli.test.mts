import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawn, spawnSync} from 'node:child_process';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {generateKeyPairSync} from 'node:crypto';
import {main, parseArguments} from '../scripts/inspect-telegram-delivery-unknown.mjs';
import {
  InspectError, pgJsonbText, sha256, validateRequest,
} from '../scripts/lib/telegram-resolution-inspect.mjs';
import {
  readIntentFile, readSecretDescriptor, validatePublishableKey,
  validateSecretDescriptors, reserveAttempt, postInspectOnce, executeInspectOnce,
} from '../scripts/lib/telegram-resolution-inspect-io.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const NOW = new Date('2026-01-01T00:00:20Z');
const REQUEST = readIntentFile(path.join(ROOT, 'examples/telegram-resolution-inspect-request.json'));
const AUTH = readIntentFile(path.join(ROOT, 'examples/telegram-resolution-inspect-authorization.json'));
const VALIDATED = validateRequest(REQUEST, NOW);
const {privateKey} = generateKeyPairSync('ec', {namedCurve: 'prime256v1'});
const JWK = {...privateKey.export({format: 'jwk'}), alg: 'ES256', kid: AUTH.signing_key_id};
const API_KEY = 'sb_publishable_' + 'synthetic_only_not_a_real_key';
const SECRET_SENTINEL = 'DO_NOT_LEAK_SYNTHETIC_SECRET';

function fixtureResponse() {
  const request = VALIDATED.request;
  const subject: any = {
    schema_version: 'exact-telegram-delivery-resolution@1',
    action: 'resolve_delivery_unknown_without_resend', client_id: 'squid',
    workspace_id: request.workspace_id, content_item_id: request.content_item_id,
    content_version_id: request.content_version_id, publication_id: request.publication_id,
    job_id: request.job_id, resolution_id: request.resolution_id,
    operator_approval_id: request.operator_approval_id,
    publication_approval_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    asset_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    delivery_attempt_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    delivery_started_at: '2025-12-31T23:30:00.123456+00:00',
    publication_status: 'delivery_unknown', job_status: 'failed', delivery_outcome: 'unknown',
    disposition: 'operator_closed_without_resend', public_observation: 'not_observed_at_checked_at',
    public_audit: request.public_audit, public_audit_sha256: VALIDATED.public_audit_sha256,
    approved_by: request.approved_by, expires_at: request.expires_at.replace('Z', '+00:00'),
    approved_release_sha: request.release_sha, resend_authorized: false,
    provider_calls: 0, database_claims: 0, publication_state_changed: false, job_state_changed: false,
    forbidden_actions: ['provider_call', 'claim', 'requeue', 'resend', 'mark_published', 'create_publication', 'create_job'],
  };
  for (const key of ['delivery_request_sha256', 'publication_request_sha256',
    'publication_response_sha256', 'job_input_sha256', 'job_output_sha256',
    'content_item_row_sha256', 'content_version_row_sha256', 'publication_row_sha256',
    'job_row_sha256', 'publication_approval_row_sha256', 'asset_row_sha256',
    'caption_sha256', 'asset_sha256']) subject[key] = 'd'.repeat(64);
  return {
    eligible: true, resolved: false, reused: false, resolution_id: request.resolution_id,
    publication_id: request.publication_id, job_id: request.job_id,
    content_item_id: request.content_item_id, content_version_id: request.content_version_id,
    delivery_outcome: subject.delivery_outcome, disposition: subject.disposition,
    public_observation: subject.public_observation, approval_subject: subject,
    approval_subject_sha256: sha256(pgJsonbText(subject)),
    approved: false, approved_at: null, resend_authorized: false,
  };
}

function temp(t: any) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'resolution-inspect-unit-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  return dir;
}
function options(dir: string) {
  return {request: REQUEST, authorization: AUTH, signingKeyFd: 3, publishableKeyFd: 4,
    attemptLedgerDir: path.join(dir, 'attempts')};
}
function mockDeps(overrides: any = {}) {
  return {now: () => NOW, readSecret: async (fd: number) => fd === 3 ? JSON.stringify(JWK) : API_KEY,
    post: async () => fixtureResponse(), ...overrides};
}
const errorCode = (expected: string) => (error: any) => {
  assert.equal(error.code, expected);
  assert.ok(!String(error.stack).includes(SECRET_SENTINEL));
  return true;
};

test('argument grammar allows only local validate or complete explicit inspect-once', () => {
  assert.equal(parseArguments(['--request', 'packet.json'])['--request'], 'packet.json');
  assert.equal(parseArguments(['--help']).help, true);
  for (const args of [[], ['--resolve'], ['--approve'], ['--url', 'https://example.invalid'],
    ['--request', 'x', '--request', 'y'], ['--request', 'x', '--validate-only', '--inspect-once']]) {
    assert.throws(() => parseArguments(args), errorCode('arguments_invalid'));
  }
  assert.throws(() => parseArguments(['--request', 'x', '--signing-key-fd', '3']),
    errorCode('validate_only_rejects_credentials'));
  assert.throws(() => parseArguments(['--request', 'x', '--inspect-once']),
    errorCode('inspect_arguments_required'));
});

test('default validation cannot access a key, reserve an attempt, or call transport', async () => {
  let output = '';
  const forbidden = () => { assert.fail('default mode crossed the I/O boundary'); };
  const code = await main(['--request', 'fixture'], {
    now: () => NOW, readIntent: () => REQUEST, output: (text: string) => { output += text; },
    readSecret: forbidden, reserve: forbidden, post: forbidden,
  });
  assert.equal(code, 0);
  assert.deepEqual(JSON.parse(output), {ok: true, mode: 'validate_only',
    request_sha256: VALIDATED.request_sha256, public_audit_sha256: VALIDATED.public_audit_sha256,
    release_sha: REQUEST.release_sha, credential_issued: false, database_calls: 0,
    provider_calls: 0, execution_authorized: false});
  assert.equal(VALIDATED.request_sha256, AUTH.request_sha256);
});

test('real default entrypoint rejects expired documentation fixture without credentials', () => {
  const result = spawnSync(process.execPath, ['scripts/inspect-telegram-delivery-unknown.mjs',
    '--request', 'examples/telegram-resolution-inspect-request.json'], {
    cwd: ROOT, encoding: 'utf8', timeout: 5000, env: {PATH: process.env.PATH},
  });
  assert.equal(result.status, 2);
  assert.equal(result.stderr, '');
  assert.deepEqual(JSON.parse(result.stdout), {ok: false, error: 'invalid_request',
    credential_issued: false, database_calls: 0, provider_calls: 0, automatic_retry: false});
});

test('input file errors never disclose a path or raw file contents', async (t) => {
  const dir = temp(t);
  for (const text of ['{"x":1,"x":2}', '{' + SECRET_SENTINEL, 'x'.repeat(32769)]) {
    const file = path.join(dir, SECRET_SENTINEL);
    fs.writeFileSync(file, text);
    let output = '';
    assert.equal(await main(['--request', file], {output: (s: string) => { output += s; }}), 2);
    assert.ok(!output.includes(SECRET_SENTINEL));
  }
  const link = path.join(dir, 'link');
  fs.symlinkSync(path.join(dir, SECRET_SENTINEL), link);
  assert.throws(() => readIntentFile(link), errorCode('intent_file_invalid'));
  assert.throws(() => readIntentFile(dir), errorCode('intent_file_invalid'));
});

test('descriptors are distinct non-stdio integers; key never accepts service/legacy tokens', () => {
  validateSecretDescriptors(3, 4);
  for (const pair of [[3, 3], [0, 4], [3, 2], [3.5, 4], [3, Infinity]]) {
    assert.throws(() => validateSecretDescriptors(...pair), errorCode('secret_descriptors_invalid'));
  }
  assert.equal(validatePublishableKey(API_KEY + '\n'), API_KEY);
  for (const value of ['sb_secret_' + 's'.repeat(30), 'eyJ.legacy.jwt', '12345:bot-token', SECRET_SENTINEL]) {
    assert.throws(() => validatePublishableKey(value), errorCode('publishable_key_invalid'));
  }
});

test('descriptor reads enforce owner-only file mode, bounded bytes, UTF8 and nonempty values', async (t) => {
  const dir = temp(t);
  let i = 0;
  const read = async (bytes: string | Buffer, mode = 0o600) => {
    const file = path.join(dir, `key-${i++}`);
    fs.writeFileSync(file, bytes, {mode});
    const fd = fs.openSync(file, 'r');
    return await readSecretDescriptor(fd); // Reader owns and closes the FD.
  };
  assert.equal(await read('synthetic-inherited-value'), 'synthetic-inherited-value');
  await assert.rejects(read('x', 0o644), errorCode('secret_descriptor_permissions'));
  await assert.rejects(read(''), errorCode('secret_descriptor_empty'));
  await assert.rejects(read('s'.repeat(16385)), errorCode('secret_descriptor_too_large'));
  await assert.rejects(read(Buffer.from([0xc3, 0x28])), errorCode('secret_descriptor_encoding'));
  await assert.rejects(readSecretDescriptor(1), errorCode('secret_descriptor_invalid'));
});

test('held-open inherited socket times out and exits naturally without a blocking libuv read', async () => {
  const source = `import {readSecretDescriptor} from './scripts/lib/telegram-resolution-inspect-io.mjs';
    try { await readSecretDescriptor(3, {timeoutMs: 40}); process.exitCode = 1; }
    catch (error) { process.stdout.write(error.code); process.exitCode = error.code === 'secret_descriptor_timeout' ? 0 : 2; }`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', source], {
    cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe', 'pipe'], env: {PATH: process.env.PATH},
  });
  let output = '', errors = '';
  child.stdout!.on('data', data => { output += data; });
  child.stderr!.on('data', data => { errors += data; });
  // Parent deliberately keeps descriptor3's producer open with no data.
  const exit = await new Promise<{code: number | null, signal: string | null}>((resolve, reject) => {
    const timeout = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('descriptor timeout did not bound child exit')); }, 3000);
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('exit', (code, signal) => { clearTimeout(timeout); resolve({code, signal}); });
  });
  child.stdio[3]?.destroy();
  assert.equal(exit.code, 0);
  assert.equal(exit.signal, null);
  assert.equal(output, 'secret_descriptor_timeout');
  assert.equal(errors, '');
});

test('exclusive attempt reservation protects permissions and retains a consumed marker', (t) => {
  const dir = temp(t);
  const ledger = path.join(dir, 'attempts');
  const marker = reserveAttempt(ledger, AUTH, VALIDATED, NOW);
  assert.equal(fs.statSync(ledger).mode & 0o777, 0o700);
  const filename = path.join(ledger, `${AUTH.authorization_id}.json`);
  assert.equal(fs.statSync(filename).mode & 0o777, 0o600);
  assert.throws(() => reserveAttempt(ledger, AUTH, VALIDATED, NOW), errorCode('authorization_already_attempted'));
  marker.update('failed_closed', true, true, 'inspect_transport_unknown');
  marker.close();
  assert.equal(JSON.parse(fs.readFileSync(filename, 'utf8')).rpc_attempted, true);
  assert.throws(() => reserveAttempt(ledger, AUTH, VALIDATED, NOW), errorCode('authorization_already_attempted'));
  const unsafe = path.join(dir, 'unsafe');
  fs.mkdirSync(unsafe, {mode: 0o755});
  assert.throws(() => reserveAttempt(unsafe, AUTH, VALIDATED, NOW), errorCode('attempt_directory_permissions'));
  const link = path.join(dir, 'link');
  fs.symlinkSync(ledger, link);
  assert.throws(() => reserveAttempt(link, AUTH, VALIDATED, NOW), errorCode('attempt_directory_permissions'));
  assert.throws(() => reserveAttempt(ledger, {...AUTH, authorization_id: '../escape'}, VALIDATED, NOW),
    errorCode('attempt_directory_invalid'));
});

test('one complete mock inspection binds JWT/payload; output and ledger never contain keys/token', async (t) => {
  const opts = options(temp(t));
  let calls = 0, token = '';
  const result = await executeInspectOnce(opts, mockDeps({post: async (ref: string, payload: any, jwt: string, key: string) => {
    calls++;
    token = jwt;
    assert.equal(ref, REQUEST.project_ref);
    assert.deepEqual(payload, VALIDATED.rpc_payload);
    assert.equal(key, API_KEY);
    const claims = JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString());
    assert.equal(claims.role, 'coineasy_telegram_resolution');
    assert.equal(claims.capability, 'telegram_delivery_unknown_inspect');
    assert.equal(claims.publication_id, REQUEST.publication_id);
    assert.equal(claims.public_audit_sha256, VALIDATED.public_audit_sha256);
    assert.equal(claims.max_external_actions, 0);
    return fixtureResponse();
  }}));
  assert.equal(calls, 1);
  assert.equal(result.database_calls, 1);
  assert.equal(result.approval_performed, false);
  assert.equal(result.resolution_performed, false);
  const evidence = JSON.stringify(result) + fs.readFileSync(path.join(opts.attemptLedgerDir, `${AUTH.authorization_id}.json`), 'utf8');
  for (const secret of [token, JWK.d!, API_KEY]) assert.ok(!evidence.includes(secret));
  let secretReads = 0;
  await assert.rejects(executeInspectOnce(opts, mockDeps({readSecret: async () => { secretReads++; }})),
    errorCode('authorization_already_attempted'));
  assert.equal(secretReads, 0);
});

test('parallel callers using the same local authorization cannot both read keys or POST', async (t) => {
  const opts = options(temp(t));
  let posts = 0;
  const deps = mockDeps({post: async () => { posts++; return fixtureResponse(); }});
  const results = await Promise.allSettled([executeInspectOnce(opts, deps), executeInspectOnce(opts, deps)]);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
  assert.equal(posts, 1);
});

test('transport uncertainty consumes authorization without retry and preserves truthful counters', async (t) => {
  const opts = options(temp(t));
  let calls = 0;
  const deps = mockDeps({post: async () => { calls++; throw new Error(SECRET_SENTINEL); }});
  await assert.rejects(executeInspectOnce(opts, deps), (error: any) => {
    assert.equal(error.code, 'inspect_operator_io_failed');
    assert.equal(error.credentialIssued, true);
    assert.equal(error.rpcAttempted, true);
    assert.ok(!String(error.stack).includes(SECRET_SENTINEL));
    return true;
  });
  await assert.rejects(executeInspectOnce(opts, deps), errorCode('authorization_already_attempted'));
  assert.equal(calls, 1);
});

test('malformed response refuses success and does not print untrusted response data', async (t) => {
  const dir = temp(t);
  const args = ['--request', 'r', '--inspect-once', '--authorization', 'a',
    '--signing-key-fd', '3', '--publishable-key-fd', '4', '--attempt-ledger-dir', path.join(dir, 'attempts')];
  let output = '';
  const code = await main(args, mockDeps({readIntent: (name: string) => name === 'r' ? REQUEST : AUTH,
    output: (s: string) => { output += s; },
    post: async () => ({...fixtureResponse(), unexpected: SECRET_SENTINEL}),
  }));
  assert.equal(code, 2);
  assert.deepEqual(JSON.parse(output), {ok: false, error: 'invalid_inspect_response',
    credential_issued: true, database_calls: 1, provider_calls: 0, automatic_retry: false});
  assert.ok(!output.includes(SECRET_SENTINEL));
});

test('bad auth fails before any marker or credential; expiry during key read stops before sign/POST', async (t) => {
  const opts = options(temp(t));
  let reads = 0, posts = 0;
  await assert.rejects(executeInspectOnce({...opts, authorization: {...AUTH, request_sha256: '0'.repeat(64)}},
    mockDeps({readSecret: async () => { reads++; }})), errorCode('invalid_authorization'));
  assert.equal(fs.existsSync(opts.attemptLedgerDir), false);
  assert.equal(reads, 0);
  let clock = NOW;
  await assert.rejects(executeInspectOnce(opts, mockDeps({now: () => clock, readSecret: async (fd: number) => {
    if (fd === 3) return JSON.stringify(JWK);
    clock = new Date('2026-01-01T01:01:00Z');
    return API_KEY;
  }, post: async () => { posts++; }})), (e: any) => {
    assert.equal(e.credentialIssued, false);
    assert.equal(e.rpcAttempted, false);
    return true;
  });
  assert.equal(posts, 0);
});

test('insecure TLS or runtime diagnostics are refused before reading keys or consuming a marker', async (t) => {
  const opts = options(temp(t));
  let reads = 0;
  for (const [key, value, code] of [
    ['NODE_TLS_REJECT_UNAUTHORIZED', '0', 'insecure_tls_environment'],
    ['NODE_DEBUG', 'http,https', 'unsafe_runtime_diagnostics'],
    ['NODE_DEBUG_NATIVE', 'http', 'unsafe_runtime_diagnostics'],
    ['NODE_OPTIONS', '--inspect', 'unsafe_runtime_diagnostics'],
    ['SSLKEYLOGFILE', '/synthetic-not-created', 'unsafe_runtime_diagnostics'],
  ]) {
    const original = process.env[key];
    process.env[key] = value;
    try {
      await assert.rejects(executeInspectOnce(opts, mockDeps({readSecret: async () => { reads++; }})), errorCode(code));
    } finally {
      if (original === undefined) delete process.env[key]; else process.env[key] = original;
    }
  }
  assert.equal(reads, 0);
  assert.equal(fs.existsSync(opts.attemptLedgerDir), false);
});

test('ledger failure before POST prevents request; close failures never reset request counters', async (t) => {
  let posts = 0;
  await assert.rejects(executeInspectOnce(options(temp(t)), mockDeps({
    reserve: () => ({update: (state: string) => { if (state === 'request_started') throw new Error(SECRET_SENTINEL); }, close() {}}),
    post: async () => { posts++; },
  })), (e: any) => { assert.equal(e.credentialIssued, true); assert.equal(e.rpcAttempted, false); return true; });
  assert.equal(posts, 0);
  await assert.rejects(executeInspectOnce(options(temp(t)), mockDeps({
    reserve: () => ({update() {}, close() { throw new Error(SECRET_SENTINEL); }}),
  })), (e: any) => {
    assert.equal(e.code, 'attempt_ledger_close_failed');
    assert.equal(e.credentialIssued, true);
    assert.equal(e.rpcAttempted, true);
    return true;
  });
});

// In-memory transport stub only. This never opens a socket or contacts a service.
function transport({status = 200, type = 'application/json', body = '{"ok":true}',
  error = false, stall = false, partial = false}: any = {}) {
  let calls = 0, seen: any, sent: Buffer | undefined;
  const request = (opts: any, callback: any) => {
    calls++; seen = opts;
    const req: any = new EventEmitter();
    req.destroy = () => { req.destroyed = true; };
    req.end = (bytes: Buffer) => {
      sent = bytes;
      queueMicrotask(() => {
        if (error) { req.emit('error', new Error(SECRET_SENTINEL)); return; }
        if (stall) return;
        const response: any = new PassThrough();
        response.statusCode = status;
        response.headers = {'content-type': type, location: 'https://evil.invalid/' + SECRET_SENTINEL};
        callback(response);
        if (partial) { response.emit('aborted'); return; }
        response.end(body);
      });
    };
    return req;
  };
  return {request, state: () => ({calls, seen, sent})};
}

test('transport pins HTTPS host/port/RPC, checks response and POSTs exactly once', async () => {
  const fake = transport();
  assert.deepEqual(await postInspectOnce(REQUEST.project_ref, VALIDATED.rpc_payload, 'synthetic.jwt', API_KEY,
    {request: fake.request}), {ok: true});
  const {calls, seen, sent} = fake.state();
  assert.equal(calls, 1);
  assert.equal(seen.hostname, 'abcdefghijklmnopqrst.supabase.co');
  assert.equal(seen.servername, seen.hostname);
  assert.equal(seen.port, 443);
  assert.equal(seen.path, '/rest/v1/rpc/inspect_exact_telegram_delivery_unknown_resolution');
  assert.equal(seen.method, 'POST');
  assert.equal(seen.rejectUnauthorized, true);
  assert.equal(seen.agent, false);
  assert.deepEqual(JSON.parse(sent!.toString()), VALIDATED.rpc_payload);
});

test('HTTP errors and redirects never follow, retry, or echo raw responses', async () => {
  for (const status of [301, 302, 307, 308, 401, 403, 429, 500]) {
    const fake = transport({status, body: SECRET_SENTINEL});
    await assert.rejects(postInspectOnce(REQUEST.project_ref, {}, 'jwt', API_KEY, {request: fake.request}),
      errorCode('inspect_http_status_rejected'));
    assert.equal(fake.state().calls, 1);
  }
});

test('transport rejects wrong content, oversized/duplicate JSON, interruption and bounded timeout', async () => {
  for (const [config, code] of [
    [{type: 'text/html', body: SECRET_SENTINEL}, 'inspect_response_type_invalid'],
    [{body: 's'.repeat(65537)}, 'inspect_response_too_large'],
    [{body: '{"x":1,"x":2}'}, 'inspect_response_invalid'],
    [{body: Buffer.from([0xc3, 0x28])}, 'inspect_response_invalid'],
    [{partial: true}, 'inspect_transport_unknown'],
    [{error: true}, 'inspect_transport_unknown'],
    [{stall: true}, 'inspect_transport_unknown'],
  ] as const) {
    const fake = transport(config);
    await assert.rejects(postInspectOnce(REQUEST.project_ref, {}, 'jwt', API_KEY,
      {request: fake.request, timeoutMs: 20}), errorCode(code));
    assert.equal(fake.state().calls, 1);
  }
});

test('static error allowlist cannot turn untrusted errors into output', () => {
  assert.equal(new InspectError(SECRET_SENTINEL).code, 'invalid_request');
});

test('held-open actual FIFO reaches its deadline and closes before its producer exits', () => {
  const reader = `import fs from 'node:fs';
    import {readSecretDescriptor} from './scripts/lib/telegram-resolution-inspect-io.mjs';
    const started = Date.now();
    const fifo = fs.fstatSync(3).isFIFO();
    let outcome = 'unexpected_success';
    try { await readSecretDescriptor(3, {timeoutMs: 40}); process.exitCode = 1; }
    catch (error) { outcome = error.code; process.exitCode = outcome === 'secret_descriptor_timeout' ? 0 : 2; }
    process.on('exit', () => process.stdout.write(JSON.stringify({fifo, outcome, elapsed_ms: Date.now() - started})));`;
  // The shell pipeline creates a real FIFO, unlike child_process's socketpair.
  // Its producer stays open for a full second. The reader must naturally exit
  // near its own deadline, not when a blocking filesystem read finally returns.
  const result = spawnSync('/bin/sh', ['-c', 'sleep 1 | "$NODE" --input-type=module -e "$READER" 3<&0'], {
    cwd: ROOT, encoding: 'utf8', timeout: 3000,
    env: {PATH: process.env.PATH, NODE: process.execPath, READER: reader},
  });
  assert.equal(result.status, 0);
  assert.equal(result.signal, null);
  assert.equal(result.stderr, '');
  const observed = JSON.parse(result.stdout);
  assert.equal(observed.fifo, true);
  assert.equal(observed.outcome, 'secret_descriptor_timeout');
  assert.ok(observed.elapsed_ms < 700, 'FIFO reader waited for the producer instead of its own deadline');
});

test('successful inherited socket read preserves chunks and closes the FD before resolving', async () => {
  const source = `import fs from 'node:fs';
    import {readSecretDescriptor} from './scripts/lib/telegram-resolution-inspect-io.mjs';
    const socket = fs.fstatSync(3).isSocket();
    const value = await readSecretDescriptor(3, {timeoutMs: 1000});
    let closed = false;
    try { fs.fstatSync(3); } catch (error) { closed = error.code === 'EBADF'; }
    process.stdout.write(JSON.stringify({socket, closed, exact: value === 'synthetic-alpha-omega\\n'}));`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', source], {
    cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe', 'pipe'], env: {PATH: process.env.PATH},
  });
  let output = '', errors = '';
  child.stdout!.on('data', data => { output += data; });
  child.stderr!.on('data', data => { errors += data; });
  child.stdio[3]!.write('synthetic-alpha');
  child.stdio[3]!.end('-omega\n');
  const exit = await new Promise<{code: number | null, signal: string | null}>((resolve, reject) => {
    const timeout = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('successful descriptor read did not close')); }, 3000);
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('close', (code, signal) => { clearTimeout(timeout); resolve({code, signal}); });
  });
  assert.equal(exit.code, 0);
  assert.equal(exit.signal, null);
  assert.equal(errors, '');
  assert.deepEqual(JSON.parse(output), {socket: true, closed: true, exact: true});
});

test('successful actual FIFO read closes the descriptor and returns only complete input', () => {
  const reader = `import fs from 'node:fs';
    import {readSecretDescriptor} from './scripts/lib/telegram-resolution-inspect-io.mjs';
    const fifo = fs.fstatSync(3).isFIFO();
    const value = await readSecretDescriptor(3, {timeoutMs: 1000});
    let closed = false;
    try { fs.fstatSync(3); } catch (error) { closed = error.code === 'EBADF'; }
    process.stdout.write(JSON.stringify({fifo, closed, exact: value === 'synthetic-pipe-input'}));`;
  const result = spawnSync('/bin/sh', ['-c', 'printf %s synthetic-pipe-input | "$NODE" --input-type=module -e "$READER" 3<&0'], {
    cwd: ROOT, encoding: 'utf8', timeout: 3000,
    env: {PATH: process.env.PATH, NODE: process.execPath, READER: reader},
  });
  assert.equal(result.status, 0);
  assert.equal(result.signal, null);
  assert.equal(result.stderr, '');
  assert.deepEqual(JSON.parse(result.stdout), {fifo: true, closed: true, exact: true});
});
