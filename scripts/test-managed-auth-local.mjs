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
export const PRODUCTION_OBSERVED_REPLAY_IMAGES = Object.freeze({
  postgres: 'supabase/postgres:17.6.1.127@sha256:be60aee15997daca475b710b734bc6bfe52cd544dcd7e9fd2ff58210b6747d83',
  auth: 'supabase/gotrue:v2.196.0@sha256:c0c25187a6b835e65a6f6e6c6b39d090e832d40e6de5186f2c038e0411944232',
  rest: 'postgrest/postgrest:v14.5@sha256:b574528fe109c8343c1247155734d03df8c34b462f342dca0ccc20244fc36ef9',
  helper: 'node:24.11.1-slim',
});
export const LOCAL_IMAGE_PROFILES = Object.freeze({
  baseline: LOCAL_IMAGES,
  'production-observed-version-replay-2026-09-01': PRODUCTION_OBSERVED_REPLAY_IMAGES,
});
const LOCAL_VERSION_EXPECTATIONS = Object.freeze({
  baseline: Object.freeze({ databaseAdmin: 'postgres', postgresVersionNum: '160013', authVersion: 'v2.189.0', restVersion: 'PostgREST 14.12' }),
  'production-observed-version-replay-2026-09-01': Object.freeze({ databaseAdmin: 'supabase_admin', postgresVersionNum: '170006', authVersion: 'v2.196.0', restVersion: 'PostgREST 14.5' }),
});
export const LOCAL_IMAGE_PROFILE_ENV = 'COINEASY_MANAGED_AUTH_IMAGE_PROFILE';
const PRODUCTION_OBSERVED_PROFILE = 'production-observed-version-replay-2026-09-01';
const MANAGED_BUILD_MIGRATION = '20260831180000_managed_auth_telegram_inspect.sql';
const MANAGED_BOUNDARY_MIGRATION = '20260901120000_managed_inspector_role_boundary.sql';

export function localImagesForProfile(profile = 'baseline') {
  if (typeof profile !== 'string' || !Object.hasOwn(LOCAL_IMAGE_PROFILES, profile))
    throw new Error('local_image_profile_invalid');
  return LOCAL_IMAGE_PROFILES[profile];
}
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
        const failure = new Error(`local_docker_${args[0]}_failed_${exceeded ? 'timeout' : code}`);
        const postgresError = stderr.split('\n').find((line) => /^ERROR:  /u.test(line));
        const postgresLine = stderr.split('\n').find((line) => /^LINE [0-9]+:/u.test(line));
        if (postgresError) {
          const detail = `${postgresError.slice(8, 208)}${postgresLine ? ` ${postgresLine.slice(0, 208)}` : ''}`;
          failure.localPostgresError = detail.replace(/[^A-Za-z0-9 _.,:()=<>/-]/gu, '?');
        }
        reject(failure);
      }
      else resolve({ stdout, stderr, code });
    });
    child.stdin.on('error', () => {});
    child.stdin.end(input);
  });
}

export async function startLocalManagedAuth() {
  if (process.env.COINEASY_MANAGED_AUTH_LIVE !== '1') throw new Error('local_live_test_opt_in_required');
  // The selector chooses one of two closed, reviewed image profiles. Never
  // accept an image reference from the environment or a command-line argument.
  const imageProfile = process.env[LOCAL_IMAGE_PROFILE_ENV] ?? 'baseline';
  const localImages = localImagesForProfile(imageProfile);
  const versionExpectations = LOCAL_VERSION_EXPECTATIONS[imageProfile];
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
  const localAdminToken = () => {
    const seconds = Math.floor(Date.now() / 1000);
    const header = Buffer.from(JSON.stringify({ alg: 'ES256', kid, typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify({ iss: issuer, aud: 'authenticated', role: 'service_role', iat: seconds, exp: seconds + 60 })).toString('base64url');
    return `${header}.${payload}.${sign('sha256', Buffer.from(`${header}.${payload}`), { key: privateKey, dsaEncoding: 'ieee-p1363' }).toString('base64url')}`;
  };
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
    async sqlAs(databaseUser, sql) {
      if (closed || !containers.includes(db)) throw new Error('local_database_not_owned');
      if (![versionExpectations.databaseAdmin, 'postgres'].includes(databaseUser)) throw new Error('local_database_user_invalid');
      return (await command(['exec', '-i', db, 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', databaseUser, '-d', 'postgres', '-At'], { input: sql, timeout: 60_000 })).stdout.trim();
    },
    async sql(sql) { return this.sqlAs(versionExpectations.databaseAdmin, sql); },
    async jsonSql(sql) { return JSON.parse(await this.sql(sql)); },
    async localRecoveryLink(email) {
      if (!/^local-[0-9a-f]{16}@example\.invalid$/.test(email)) throw new Error('local_synthetic_identity_required');
      // Test-only admin fixture, not an application credential or executable path.
      return this.request('auth', '/admin/generate_link', { method: 'POST', token: localAdminToken(), body: { type: 'recovery', email } });
    },
    async localCreateInspector(email, userPassword) {
      if (!/^local-[0-9a-f]{16}@example\.invalid$/.test(email)
          || typeof userPassword !== 'string' || userPassword.length < 24 || userPassword.length > 128)
        throw new Error('local_synthetic_identity_required');
      // Atomic local Admin create proves the persisted role exists before any user token is issued.
      return this.request('auth', '/admin/users', { method: 'POST', token: localAdminToken(), body: {
        email, password: userPassword, email_confirm: true, role: 'coineasy_managed_inspector',
      } });
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
    const imageId = (await command(['inspect', '--format', '{{.Image}}', container])).stdout.trim();
    if (!/^sha256:[a-f0-9]{64}$/.test(imageId)) throw new Error(`local_${suffix}_image_id_invalid`);
    console.log(`local container image id (${suffix}): ${imageId}`);
    return container;
  };
  const ready = async (test, label) => {
    for (let until = Date.now() + 30_000; Date.now() < until;) {
      if (await test().catch(() => false)) return;
      await wait(250);
    }
    throw new Error(`local_${label}_not_ready`);
  };
  const validationQuery = (fileName) => {
    const sql = readFileSync(path.join(ROOT, 'ops/managed-inspector-activation', fileName), 'utf8').trim();
    if (!sql.endsWith(';')) throw new Error('local_validation_pack_statement_invalid');
    return sql.slice(0, -1);
  };
  const runValidateOnly = async (fileName) => {
    const query = validationQuery(fileName);
    const raw = await state.sql(`begin transaction read only;
      set local role supabase_read_only_user;
      select coalesce(pg_catalog.json_agg(result order by result.check_id), '[]'::json)::text
      from (${query}) result;
      rollback;`);
    let rows;
    try { rows = JSON.parse(raw); } catch { throw new Error('local_validation_pack_result_invalid'); }
    if (!Array.isArray(rows) || rows.length < 15
        || new Set(rows.map((row) => row.check_id)).size !== rows.length
        || rows.some((row) => row.generic_db_push_allowed !== false
          || row.full_history_not_reconciled !== true
          || row.exact_migration_bytes_proven !== false
          || row.custom_apply_receipt_required !== true))
      throw new Error('local_validation_pack_contract_invalid');
    return rows;
  };
  const assertValidateOnly = async (fileName, expectedFailures = []) => {
    const rows = await runValidateOnly(fileName);
    const expectedRowCount = fileName === 'preflight.sql' ? 25
      : fileName === 'postflight.sql' ? 39 : null;
    if (rows.length !== expectedRowCount)
      throw new Error(`local_${fileName.replace('.sql', '')}_row_count_unexpected`);
    const failures = rows.filter((row) => row.passed !== true).map((row) => row.check_id).sort();
    const expected = [...expectedFailures].sort();
    if (failures.length !== expected.length
        || failures.some((checkId, index) => checkId !== expected[index]))
      throw new Error(`local_${fileName.replace('.sql', '')}_validation_unexpected:${failures.join(',') || 'none'}`);
    console.log(`local ${fileName} validate-only rows=${rows.length} expected_failures=${expectedFailures.length}`);
    return rows;
  };
  try {
    console.log(`local test image profile: ${imageProfile}`);
    console.log('hosted_requests=false production_credentials=false activation_evidence=false');
    for (const image of Object.values(localImages)) {
      console.log(`local test image: ${image}`);
      if ((await command(['image', 'inspect', '--format', '{{.Id}}', image], { acceptable: [0, 1] })).code !== 0)
        await command(['pull', image], { timeout: 300_000 });
    }
    await command(['network', 'create', '--internal', '--label', `coineasy.managed-auth-test=${name}`, network]);
    networkCreated = true;
    await run('db', localImages.postgres, { POSTGRES_PASSWORD: password }, ['--tmpfs', '/var/lib/postgresql/data:rw,noexec,nosuid,size=512m']);
    await ready(async () => (await command(['exec', db, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], { acceptable: [0, 1, 2] })).code === 0, 'postgres');
    // The official postgres image starts empty; the exact Supabase image ships
    // its own baseline roles and Auth schema. Normalize only the disposable
    // local role password/ownership needed by this replay, preserving the
    // image's schema so GoTrue proves its real upgrade path.
    await state.sql(`do $local_bootstrap$
      begin
        if to_regrole('supabase_auth_admin') is null then
          create role supabase_auth_admin login password '${password}' superuser;
        else
          alter role supabase_auth_admin login password '${password}' superuser;
        end if;
      end
      $local_bootstrap$;
      create schema if not exists auth authorization supabase_auth_admin;
      alter schema auth owner to supabase_auth_admin;
      alter role supabase_auth_admin set search_path = auth, public;`);
    const postgresVersionNum = await state.sql('show server_version_num;');
    if (postgresVersionNum !== versionExpectations.postgresVersionNum) throw new Error('local_postgres_version_mismatch');
    console.log(`local runtime PostgreSQL server_version_num: ${postgresVersionNum}`);
    await run('helper', localImages.helper, {}, [], ['node', '-e', 'setInterval(()=>{},3600000)']);
    const auth = await run('auth', localImages.auth, {
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
    const authVersion = (await command(['exec', auth, 'auth', 'version'])).stdout.trim();
    if (authVersion !== versionExpectations.authVersion) throw new Error('local_auth_version_mismatch');
    console.log(`local runtime GoTrue version: ${authVersion}`);
    const authCatalog = await state.jsonSql(`select coalesce(json_agg(json_build_object(
      'column',c.table_name||'.'||c.column_name,
      'data_type',c.data_type,
      'udt_name',c.udt_name
    ) order by c.table_name,c.ordinal_position),'[]'::json) from information_schema.columns c
    where c.table_schema='auth' and (c.table_name,c.column_name) in (
      ('users','id'),('users','role'),('users','deleted_at'),('users','is_anonymous'),
      ('users','banned_until'),('users','encrypted_password'),('users','recovery_sent_at'),
      ('sessions','id'),('sessions','user_id'),('sessions','aal'),('sessions','not_after'),('sessions','factor_id'),
      ('mfa_factors','id'),('mfa_factors','user_id'),('mfa_factors','factor_type'),('mfa_factors','status'),('mfa_factors','created_at'),
      ('mfa_amr_claims','session_id'),('mfa_amr_claims','authentication_method'),('mfa_amr_claims','updated_at')
    );`);
    if (!Array.isArray(authCatalog) || authCatalog.length !== 20) throw new Error('local_auth_schema_incompatible');
    console.log(`local Auth required-column catalog: ${JSON.stringify(authCatalog)}`);
    if (imageProfile === 'baseline') {
      await state.sqlAs('postgres', readFileSync(path.join(ROOT, 'supabase/tests/managed_auth_live_bootstrap.sql'), 'utf8'));
    } else {
      // The digest-pinned Supabase image contains its platform bootstrap. Do
      // not recreate or weaken it: prove the local replay surface is complete
      // and fail closed on any drift.
      const platformCatalog = await state.jsonSql(`select json_build_object(
        'anon_role',to_regrole('anon') is not null,
        'authenticated_role',to_regrole('authenticated') is not null,
        'service_role',to_regrole('service_role') is not null,
        'authenticator_role',to_regrole('authenticator') is not null,
        'extensions_schema',to_regnamespace('extensions') is not null,
        'storage_schema',to_regnamespace('storage') is not null,
        'storage_buckets',to_regclass('storage.buckets') is not null,
        'storage_objects',to_regclass('storage.objects') is not null,
        'auth_uid',to_regprocedure('auth.uid()') is not null,
        'authenticator_anon',pg_has_role('authenticator','anon','MEMBER'),
        'authenticator_authenticated',pg_has_role('authenticator','authenticated','MEMBER'),
        'authenticator_service',pg_has_role('authenticator','service_role','MEMBER')
      );`);
      console.log(`local Supabase platform catalog: ${JSON.stringify(platformCatalog)}`);
      if (!platformCatalog
          || platformCatalog.storage_buckets !== platformCatalog.storage_objects
          || Object.entries(platformCatalog).some(([key, value]) => !['storage_buckets', 'storage_objects'].includes(key) && value !== true))
        throw new Error('local_observed_platform_bootstrap_incomplete');
      if (!platformCatalog.storage_buckets) {
        // The production-observed DB image intentionally ships the storage
        // schema but not a Storage service database. Add only the two synthetic
        // tables required by repository migrations to this disposable replay.
        await state.sql(`create table storage.buckets (
          id text primary key, name text not null, public boolean not null default false,
          file_size_limit bigint, allowed_mime_types text[]
        );
        create table storage.objects (
          id uuid primary key default gen_random_uuid(),
          bucket_id text not null references storage.buckets(id) on delete cascade,
          name text not null, created_at timestamptz not null default now(), unique(bucket_id,name)
        );
        alter table storage.objects enable row level security;`);
        console.log('local synthetic storage fixture created=true');
      }
      await state.sql(`grant usage on schema auth to anon,authenticated,service_role;
        grant execute on function auth.uid() to anon,authenticated,service_role;
        grant usage on schema storage to anon,authenticated,service_role;
        grant all on storage.buckets,storage.objects to anon,authenticated,service_role;`);
    }
    if (imageProfile === PRODUCTION_OBSERVED_PROFILE) {
      // The database image is a service database, not a CLI-linked project, so
      // it intentionally has no migration-history schema. Recreate only the
      // three-column local analogue needed to exercise the pack's history
      // readback; this is never evidence about hosted migration execution.
      await state.sqlAs('postgres', `create schema if not exists supabase_migrations authorization postgres;
        create table if not exists supabase_migrations.schema_migrations (
          version text primary key,
          statements text[],
          name text
        );`);
      const validationFixtureCatalog = await state.jsonSql(`select json_build_object(
        'read_only_role',to_regrole('supabase_read_only_user') is not null,
        'migration_schema',to_regnamespace('supabase_migrations') is not null,
        'migration_table',to_regclass('supabase_migrations.schema_migrations') is not null,
        'digest_function',to_regprocedure('extensions.digest(bytea,text)') is not null
      );`);
      console.log(`local validate-only prerequisite catalog: ${JSON.stringify(validationFixtureCatalog)}`);
      await state.sql(`do $read_only_fixture$
        begin
          if to_regrole('supabase_read_only_user') is null then
            create role supabase_read_only_user nologin noinherit nosuperuser nocreaterole
              nocreatedb noreplication nobypassrls;
          end if;
        end
        $read_only_fixture$;
        grant usage on schema supabase_migrations to supabase_read_only_user;
        grant select on table supabase_migrations.schema_migrations to supabase_read_only_user;
      `);
      console.log('local validate-only readback fixture configured=true');
    }
    for (const migration of readdirSync(path.join(ROOT, 'supabase/migrations')).filter((entry) => entry.endsWith('.sql')).sort()) {
      if (migration === MANAGED_BUILD_MIGRATION) {
        if (imageProfile === PRODUCTION_OBSERVED_PROFILE) {
          await assertValidateOnly('preflight.sql');

          await state.sqlAs('postgres', `grant select on table public.workspaces to public;`);
          await assertValidateOnly('preflight.sql', ['public_relation_privileges_zero']);
          await state.sqlAs('postgres', `revoke select on table public.workspaces from public;`);

          await state.sql(`grant authenticator to supabase_read_only_user
            with inherit false, set true, admin false;`);
          await assertValidateOnly('preflight.sql', [
            'authenticator_platform_admin_descendants_exact',
          ]);
          await state.sql(`revoke authenticator from supabase_read_only_user;`);

          await assertValidateOnly('preflight.sql');
        }
        await state.sqlAs('postgres', `
          create role coineasy_acl_hostile_fixture nologin noinherit nosuperuser
            nocreaterole nocreatedb noreplication nobypassrls;
          create role coineasy_acl_hostile_member_fixture nologin inherit nosuperuser
            nocreaterole nocreatedb noreplication nobypassrls;
          grant coineasy_acl_hostile_fixture to coineasy_acl_hostile_member_fixture;
          alter default privileges for role postgres in schema private
            grant all privileges on tables to coineasy_acl_hostile_fixture;
          alter default privileges for role postgres in schema private
            grant execute on functions to coineasy_acl_hostile_fixture;
          alter default privileges for role postgres in schema public
            grant execute on functions to coineasy_acl_hostile_fixture;`);
      }
      try { await state.sqlAs('postgres', readFileSync(path.join(ROOT, 'supabase/migrations', migration), 'utf8')); }
      catch (error) {
        const detail = typeof error?.localPostgresError === 'string' ? `:${error.localPostgresError}` : '';
        throw new Error(`local_migration_failed:${migration}${detail}`);
      }
      if (migration === MANAGED_BUILD_MIGRATION) {
        try {
          await state.sqlAs('postgres', readFileSync(path.join(ROOT, 'supabase/tests/managed_inspector_intermediate_acl_check.sql'), 'utf8'));
          console.log('local intermediate managed-inspector hostile default-ACL check: pass');
        } catch { throw new Error('local_intermediate_managed_inspector_acl_check_failed'); }
        await state.sqlAs('postgres', `
          alter default privileges for role postgres in schema private
            revoke all privileges on tables from coineasy_acl_hostile_fixture;
          alter default privileges for role postgres in schema private
            revoke execute on functions from coineasy_acl_hostile_fixture;
          alter default privileges for role postgres in schema public
            revoke execute on functions from coineasy_acl_hostile_fixture;
          revoke coineasy_acl_hostile_fixture from coineasy_acl_hostile_member_fixture;
          drop role coineasy_acl_hostile_member_fixture;
          drop role coineasy_acl_hostile_fixture;
          do $fixture_cleanup$
          begin
            if to_regrole('coineasy_acl_hostile_fixture') is not null
               or to_regrole('coineasy_acl_hostile_member_fixture') is not null then
              raise exception 'hostile ACL fixture cleanup failed';
            end if;
          end
          $fixture_cleanup$;`);
        if (imageProfile === PRODUCTION_OBSERVED_PROFILE)
          await state.sqlAs('postgres', `insert into supabase_migrations.schema_migrations(version,name)
            values ('20260831180000','managed_auth_telegram_inspect');`);
      }
      if (migration === MANAGED_BOUNDARY_MIGRATION && imageProfile === PRODUCTION_OBSERVED_PROFILE)
        await state.sqlAs('postgres', `insert into supabase_migrations.schema_migrations(version,name)
          values ('20260901120000','managed_inspector_role_boundary');`);
    }
    if (imageProfile === PRODUCTION_OBSERVED_PROFILE) {
      await assertValidateOnly('postflight.sql');

      await state.sqlAs('postgres', `grant usage on schema extensions
        to coineasy_managed_inspector;`);
      await assertValidateOnly('postflight.sql', [
        'column_privileges_zero',
        'relation_privileges_zero',
        'unexpected_schema_privileges_zero',
      ]);
      await state.sqlAs('postgres', `revoke usage on schema extensions
        from coineasy_managed_inspector;`);

      await state.sqlAs('postgres', `grant coineasy_managed_inspector to anon
        with inherit false, set true, admin false;`);
      await assertValidateOnly('postflight.sql', [
        'target_role_direct_members_exact',
        'target_role_membership_cardinality',
        'target_role_transitive_members_exact',
      ]);
      await state.sqlAs('postgres', `revoke coineasy_managed_inspector from anon;`);

      await state.sqlAs('postgres', `create role coineasy_postflight_hostile_fixture nologin noinherit;
        grant execute on function public.managed_telegram_inspect_context(uuid,text)
          to coineasy_postflight_hostile_fixture;`);
      await assertValidateOnly('postflight.sql', ['target_function_acl_exact_allowlist']);
      await state.sqlAs('postgres', `revoke execute on function public.managed_telegram_inspect_context(uuid,text)
          from coineasy_postflight_hostile_fixture;
        drop role coineasy_postflight_hostile_fixture;`);

      await state.sqlAs('postgres', `grant select (release_id)
        on table private.managed_telegram_inspect_releases to anon;`);
      await assertValidateOnly('postflight.sql', ['target_column_acl_inventory_zero']);
      await state.sqlAs('postgres', `revoke select (release_id)
        on table private.managed_telegram_inspect_releases from anon;`);

      await state.sqlAs('postgres', `drop trigger managed_inspect_immutable
          on private.managed_telegram_inspect_releases;
        create trigger managed_inspect_immutable before update or delete
          on private.managed_telegram_inspect_releases for each row when (false)
          execute function private.deny_managed_telegram_inspect_ledger_mutation();`);
      await assertValidateOnly('postflight.sql', ['target_table_triggers_exact']);
      await state.sqlAs('postgres', `drop trigger managed_inspect_immutable
          on private.managed_telegram_inspect_releases;
        create trigger managed_inspect_immutable before update or delete
          on private.managed_telegram_inspect_releases for each row
          execute function private.deny_managed_telegram_inspect_ledger_mutation();`);

      await state.sqlAs('postgres', `drop trigger managed_inspect_immutable
          on private.managed_telegram_inspect_releases;
        create trigger managed_inspect_immutable before update of enabled or delete
          on private.managed_telegram_inspect_releases for each row
          execute function private.deny_managed_telegram_inspect_ledger_mutation();`);
      await assertValidateOnly('postflight.sql', ['target_table_triggers_exact']);
      await state.sqlAs('postgres', `drop trigger managed_inspect_immutable
          on private.managed_telegram_inspect_releases;
        create trigger managed_inspect_immutable before update or delete
          on private.managed_telegram_inspect_releases for each row
          execute function private.deny_managed_telegram_inspect_ledger_mutation();`);

      await assertValidateOnly('postflight.sql');
    }
    // Configure only this newly created disposable role before REST starts.
    await state.sql(`alter role authenticator password '${password}';`);
    const rest = await run('rest', localImages.rest, {
      PGRST_DB_URI: `postgres://authenticator:${password}@${db}:5432/postgres`, PGRST_DB_SCHEMAS: 'public',
      PGRST_DB_ANON_ROLE: 'anon', PGRST_JWT_AUD: 'authenticated', PGRST_JWT_SECRET: JSON.stringify({ keys: [publicJwk] }),
      PGRST_DB_USE_LEGACY_GUCS: 'false', PGRST_LOG_LEVEL: 'crit',
    });
    state.restUrl = `http://${rest}:3000`;
    await ready(async () => (await state.request('rest', '/', { timeout: 1000 })).status === 200, 'postgrest');
    const restVersion = (await command(['exec', rest, 'postgrest', '--version'])).stdout.trim();
    if (restVersion !== versionExpectations.restVersion) throw new Error('local_postgrest_version_mismatch');
    console.log(`local runtime PostgREST version: ${restVersion}`);
    await state.sql(readFileSync(path.join(ROOT, 'supabase/tests/managed_auth_live_fixture.sql'), 'utf8'));
    return state;
  } catch (error) { await state.close(); throw error; }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  if (process.argv.length !== 2) throw new Error('local_test_harness_accepts_no_arguments');
  const imageProfile = process.env[LOCAL_IMAGE_PROFILE_ENV] ?? 'baseline';
  localImagesForProfile(imageProfile);
  const child = spawn(process.execPath, ['--test', '--test-concurrency=1', 'tests_js/managed-auth-live.test.mts'], {
    cwd: ROOT, env: { ...baseEnv(), COINEASY_MANAGED_AUTH_LIVE: '1', [LOCAL_IMAGE_PROFILE_ENV]: imageProfile }, stdio: 'inherit',
  });
  child.on('error', () => { console.error('local_test_runner_failed'); process.exitCode = 1; });
  child.on('exit', (code) => { process.exitCode = code ?? 1; });
}
