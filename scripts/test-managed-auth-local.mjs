#!/usr/bin/env node
// LOCAL TEST HARNESS ONLY. Never loads a project .env or accepts a remote URL.
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { randomBytes, randomUUID, generateKeyPairSync, sign } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const LOCAL_IMAGES = Object.freeze({
  postgres: 'postgres:16.13',
  auth: 'supabase/gotrue:v2.189.0',
  rest: 'postgrest/postgrest:v14.12',
  helper: 'node:24.11.1-slim',
});
const DOCKER = existsSync('/Applications/Docker.app/Contents/Resources/bin/docker')
  ? '/Applications/Docker.app/Contents/Resources/bin/docker' : 'docker';
// Do not forward SUPABASE_*, service-role, provider, proxy, or other workspace env.
const baseEnv = () => ({ PATH: `${path.dirname(DOCKER)}:${process.env.PATH ?? '/usr/local/bin:/usr/bin:/bin'}`, HOME: process.env.HOME ?? '', LANG: 'C.UTF-8' });
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function dockerCommand(args, { input, env = {}, timeout = 60_000, acceptable = [0], host } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(DOCKER, host ? ['--host', host, ...args] : args, { cwd: ROOT, env: { ...baseEnv(), ...env }, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '', exceeded = false;
    const timer = setTimeout(() => { exceeded = true; child.kill('SIGKILL'); }, timeout);
    child.stdout.on('data', (data) => { stdout += data; if (stdout.length > 8_000_000) child.kill('SIGKILL'); });
    child.stderr.on('data', (data) => { stderr += data; if (stderr.length > 8_000_000) child.kill('SIGKILL'); });
    child.on('error', () => { clearTimeout(timer); reject(new Error('local_docker_unavailable')); });
    child.on('close', (code) => {
      clearTimeout(timer);
      // Never echo command arguments, Docker env, SQL, tokens, or provider bodies.
      if (exceeded || !acceptable.includes(code)) {
        reject(new Error(`local_docker_${args[0]}_failed_${exceeded ? 'timeout' : code}`));
      }
      else resolve({ stdout, stderr, code });
    });
    child.stdin.on('error', () => {});
    child.stdin.end(input);
  });
}

export async function startLocalManagedAuth() {
  if (process.env.COINEASY_MANAGED_AUTH_LIVE !== '1') throw new Error('local_live_test_opt_in_required');
  const endpoint = (await dockerCommand(['context', 'inspect', '--format', '{{ .Endpoints.docker.Host }}'])).stdout.trim();
  if (!/^unix:\/\/\/[^\u0000-\u001f\u007f?#]+$/.test(endpoint)) throw new Error('local_unix_docker_engine_required');
  // Every subsequent command, including cleanup, uses this exact local socket.
  // A concurrent Docker context switch cannot redirect any test operation.
  const command = (args, options = {}) => dockerCommand(args, { ...options, host: endpoint });
  // Keep each Docker DNS label below 63 bytes, including -auth/-rest suffix.
  const name = `coineasy-managed-auth-test-${randomBytes(8).toString('hex')}`;
  const containers = [], network = `${name}-net`;
  let networkCreated = false, closed = false;
  const password = randomBytes(32).toString('hex');
  const { privateKey, publicKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' });
  const kid = randomUUID();
  const privateJwk = { ...privateKey.export({ format: 'jwk' }), kid, alg: 'ES256', use: 'sig', key_ops: ['sign'] };
  const publicJwk = { ...publicKey.export({ format: 'jwk' }), kid, alg: 'ES256', use: 'sig', key_ops: ['verify'] };
  const projectRef = 'abcdefghijklmnopqrst';
  // Synthetic issuer exercises the exact production-format gate without DNS/network.
  const issuer = `https://${projectRef}.supabase.co/auth/v1`;
  const db = `${name}-db`;
  const helper = `${name}-helper`;
  const fetchSource = `
    let raw=''; for await(const chunk of process.stdin) { raw+=chunk; if(raw.length>150000) process.exit(2); }
    try {
      const input=JSON.parse(raw),url=new URL(input.url);
      if(url.protocol!=='http:' || url.username || url.password ||
         ![${JSON.stringify(`${name}-auth:9999`)},${JSON.stringify(`${name}-rest:3000`)}].includes(url.host)) process.exit(2);
      const response=await fetch(url,{method:input.method,headers:input.headers,body:input.body,
        redirect:'error',signal:AbortSignal.timeout(input.timeout)});
      const reader=response.body?.getReader(); let chunks=[],size=0;
      const limit=url.pathname==='/factors'&&input.method==='POST'?2000000:100000;
      if(reader) while(true) { const item=await reader.read(); if(item.done) break; size+=item.value.length;
        if(size>limit) { await reader.cancel(); process.exit(22); } chunks.push(Buffer.from(item.value)); }
      process.stdout.write(JSON.stringify({status:response.status,headers:{'content-type':response.headers.get('content-type')||''},
        body:Buffer.concat(chunks).toString('utf8')}));
    } catch { process.exit(3); }
  `;
  const state = {
    name, projectRef, issuer, publicJwk, authUrl: '', restUrl: '',
    async close() {
      if (closed) return;
      closed = true;
      process.removeListener('SIGINT', onInterrupt);
      process.removeListener('SIGTERM', onInterrupt);
      for (const container of [...containers].reverse()) await command(['rm', '-f', '-v', container], { acceptable: [0, 1] }).catch(() => {});
      if (networkCreated) await command(['network', 'rm', network], { acceptable: [0, 1] }).catch(() => {});
      try {
        const filter = `label=coineasy.managed-auth-test=${name}`;
        const remainingContainers = (await command(['ps', '-aq', '--filter', filter])).stdout.trim();
        const remainingNetworks = (await command(['network', 'ls', '-q', '--filter', filter])).stdout.trim();
        if (remainingContainers || remainingNetworks) throw new Error('local_cleanup_incomplete');
      } catch { throw new Error('local_cleanup_unverified'); }
    },
    async sql(sql) {
      if (closed || !containers.includes(db)) throw new Error('local_database_not_owned');
      return (await command(['exec', '-i', db, 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres', '-At'], { input: sql, timeout: 60_000 })).stdout.trim();
    },
    async jsonSql(sql) { return JSON.parse(await this.sql(sql)); },
    async localRecoveryLink(email) {
      if (!/^local-[0-9a-f]{16}@example\.invalid$/.test(email)) throw new Error('local_synthetic_identity_required');
      const seconds = Math.floor(Date.now() / 1000);
      const header = Buffer.from(JSON.stringify({ alg: 'ES256', kid, typ: 'JWT' })).toString('base64url');
      const payload = Buffer.from(JSON.stringify({ iss: issuer, aud: 'authenticated', role: 'service_role', iat: seconds, exp: seconds + 60 })).toString('base64url');
      const token = `${header}.${payload}.${sign('sha256', Buffer.from(`${header}.${payload}`), { key: privateKey, dsaEncoding: 'ieee-p1363' }).toString('base64url')}`;
      // Test-only admin fixture, not an application credential or executable path.
      return this.request('auth', '/admin/generate_link', { method: 'POST', token, body: { type: 'recovery', email } });
    },
    async fetch(service, endpointPath, init = {}) {
      if (closed || !containers.includes(helper) || !['auth', 'rest'].includes(service)) throw new Error('local_helper_not_owned');
      if (!endpointPath.startsWith('/') || endpointPath.startsWith('//')) throw new Error('local_path_invalid');
      const base = service === 'auth' ? this.authUrl : this.restUrl;
      const url = new URL(endpointPath, base);
      if (url.origin !== base || url.username || url.password) throw new Error('local_origin_changed');
      const input = JSON.stringify({ url: url.href, method: init.method ?? 'GET', headers: init.headers ?? {}, body: init.body, timeout: init.timeout ?? 10_000 });
      const result = JSON.parse((await command(['exec', '-i', helper, 'node', '--input-type=module', '-e', fetchSource], { input, timeout: 15_000 })).stdout);
      return new Response([204, 205, 304].includes(result.status) ? null : result.body, { status: result.status, headers: result.headers });
    },
    async request(service, endpointPath, { method = 'GET', token, body, timeout = 10_000 } = {}) {
      const response = await this.fetch(service, endpointPath, {
        method, timeout,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const raw = await response.text();
      if (raw.length > (service === 'auth' && endpointPath === '/factors' && method === 'POST' ? 2_000_000 : 100_000)) throw new Error('local_response_too_large');
      let data = null;
      if (raw) { try { data = JSON.parse(raw); } catch { throw new Error('local_response_invalid'); } }
      return { status: response.status, data };
    },
  };
  const onInterrupt = () => { void state.close().finally(() => { process.exitCode = 130; }); };
  process.once('SIGINT', onInterrupt);
  process.once('SIGTERM', onInterrupt);
  const run = async (suffix, image, env, args = [], commandArgs = []) => {
    const container = `${name}-${suffix}`;
    // Record before the launch so failures after creation still clean up our name.
    containers.push(container);
    await command(['run', '-d', '--name', container, '--label', `coineasy.managed-auth-test=${name}`, '--network', network,
      '--log-driver', 'none', '--security-opt', 'no-new-privileges',
      ...Object.keys(env).flatMap((key) => ['--env', key]), ...args, image, ...commandArgs], { env, timeout: 60_000 });
    return container;
  };
  const ready = async (test, label) => {
    for (let until = Date.now() + 30_000; Date.now() < until;) {
      if (await test().catch(() => false)) return;
      await wait(250);
    }
    throw new Error(`local_${label}_not_ready`);
  };
  try {
    for (const image of Object.values(LOCAL_IMAGES)) {
      console.log(`local test image: ${image}`);
      if ((await command(['image', 'inspect', '--format', '{{.Id}}', image], { acceptable: [0, 1] })).code !== 0)
        await command(['pull', image], { timeout: 300_000 });
    }
    await command(['network', 'create', '--internal', '--label', `coineasy.managed-auth-test=${name}`, network]);
    networkCreated = true;
    await run('db', LOCAL_IMAGES.postgres, { POSTGRES_PASSWORD: password }, ['--tmpfs', '/var/lib/postgresql/data:rw,noexec,nosuid,size=512m']);
    await ready(async () => (await command(['exec', db, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], { acceptable: [0, 1, 2] })).code === 0, 'postgres');
    await state.sql(`create role supabase_auth_admin login password '${password}' superuser; create schema auth authorization supabase_auth_admin; alter role supabase_auth_admin set search_path = auth, public;`);
    await run('helper', LOCAL_IMAGES.helper, {}, [], ['node', '-e', 'setInterval(()=>{},3600000)']);
    const auth = await run('auth', LOCAL_IMAGES.auth, {
      GOTRUE_API_HOST: '0.0.0.0', GOTRUE_API_PORT: '9999', API_EXTERNAL_URL: issuer,
      GOTRUE_DB_DRIVER: 'postgres', GOTRUE_DB_DATABASE_URL: `postgres://supabase_auth_admin:${password}@${db}:5432/postgres?search_path=auth`,
      GOTRUE_DB_NAMESPACE: 'auth', GOTRUE_SITE_URL: 'http://127.0.0.1:1',
      GOTRUE_DISABLE_SIGNUP: 'false', GOTRUE_EXTERNAL_EMAIL_ENABLED: 'true', GOTRUE_MAILER_AUTOCONFIRM: 'true',
      GOTRUE_JWT_SECRET: randomBytes(40).toString('hex'), GOTRUE_JWT_KEYS: JSON.stringify([privateJwk]),
      GOTRUE_JWT_AUD: 'authenticated', GOTRUE_JWT_DEFAULT_GROUP_NAME: 'authenticated', GOTRUE_JWT_ISSUER: issuer, GOTRUE_JWT_EXP: '300',
      GOTRUE_MFA_TOTP_ENROLL_ENABLED: 'true', GOTRUE_MFA_TOTP_VERIFY_ENABLED: 'true',
      GOTRUE_RATE_LIMIT_EMAIL_SENT: '1000', GOTRUE_RATE_LIMIT_TOKEN_REFRESH: '1000', GOTRUE_RATE_LIMIT_TOKEN_VERIFICATIONS: '1000',
      GOTRUE_MFA_RATE_LIMIT_CHALLENGE_AND_VERIFY: '1000', GOTRUE_LOG_LEVEL: 'error',
    });
    state.authUrl = `http://${auth}:9999`;
    await ready(async () => (await state.request('auth', '/health', { timeout: 1000 })).status === 200, 'auth');
    await state.sql(readFileSync(path.join(ROOT, 'supabase/tests/managed_auth_live_bootstrap.sql'), 'utf8'));
    for (const migration of readdirSync(path.join(ROOT, 'supabase/migrations')).filter((entry) => entry.endsWith('.sql')).sort()) {
      try { await state.sql(readFileSync(path.join(ROOT, 'supabase/migrations', migration), 'utf8')); }
      catch { throw new Error(`local_migration_failed:${migration}`); }
    }
    // Configure only this newly created disposable role before REST starts.
    await state.sql(`alter role authenticator password '${password}';`);
    const rest = await run('rest', LOCAL_IMAGES.rest, {
      PGRST_DB_URI: `postgres://authenticator:${password}@${db}:5432/postgres`, PGRST_DB_SCHEMAS: 'public',
      PGRST_DB_ANON_ROLE: 'anon', PGRST_JWT_AUD: 'authenticated', PGRST_JWT_SECRET: JSON.stringify({ keys: [publicJwk] }),
      PGRST_DB_USE_LEGACY_GUCS: 'false', PGRST_LOG_LEVEL: 'crit',
    });
    state.restUrl = `http://${rest}:3000`;
    await ready(async () => (await state.request('rest', '/', { timeout: 1000 })).status === 200, 'postgrest');
    await state.sql(readFileSync(path.join(ROOT, 'supabase/tests/managed_auth_live_fixture.sql'), 'utf8'));
    return state;
  } catch (error) { await state.close(); throw error; }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  if (process.argv.length !== 2) throw new Error('local_test_harness_accepts_no_arguments');
  const child = spawn(process.execPath, ['--test', '--test-concurrency=1', 'tests_js/managed-auth-live.test.mts'], {
    cwd: ROOT, env: { ...baseEnv(), COINEASY_MANAGED_AUTH_LIVE: '1' }, stdio: 'inherit',
  });
  child.on('error', () => { console.error('local_test_runner_failed'); process.exitCode = 1; });
  child.on('exit', (code) => { process.exitCode = code ?? 1; });
}
