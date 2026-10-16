/** Standalone Babylon disposable socket-only PostgreSQL 17; never uses a supplied DSN. */
import { existsSync, mkdtempSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.argv.slice(2).join(' ') !== '--local-only') throw Error('explicit --local-only required');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bin = '/opt/homebrew/opt/postgresql@17/bin/';
if (!existsSync(bin + 'initdb')) throw Error('local PostgreSQL 17 required');
const python = process.env.EXACT_CLAIM_LOCAL_PYTHON || 'python3';
const directory = mkdtempSync('/private/tmp/coineasy-ybo-standalone-proof-');
const port = '65441';
const env = { PATH: bin + ':' + process.env.PATH, LANG: 'C', TZ: 'UTC', PGHOST: directory,
  PGPORT: port, PGUSER: 'postgres', PGDATABASE: 'coineasy_ybo_standalone_test',
  PGPASSFILE: '/dev/null', PGCONNECT_TIMEOUT: '3', PYTHONPATH: [root, process.env.PYTHONPATH].filter(Boolean).join(':'),
  PYTHONDONTWRITEBYTECODE: '1', EXACT_CLAIM_PSQL: bin + 'psql',
  READINESS_TRIAL_PROOF_RECEIPT: directory + '/readiness-proof.json',
  EXACT_CLAIM_DISPOSABLE_DATABASE: 'coineasy_ybo_standalone_test' };
let started = false;
function run(command, args, extra = {}) {
  const result = spawnSync(command, args, { cwd: root, env, encoding: 'utf8',
    timeout: 180000, maxBuffer: 4 * 1024 * 1024, ...extra });
  if (result.error || result.status !== 0) {
    const details = [
      `status=${result.status} signal=${result.signal} error=${result.error?.message ?? 'none'}`,
      `stderr_tail: ${String(result.stderr ?? '').slice(-350).replaceAll('\n', ' | ')}`,
    ].join('\n');
    throw Error(`local YBO SQL proof failed:\n${details}`);
  }
  return result.stdout.trim();
}
const pg = ['-X', '--no-password', '-q', '-v', 'ON_ERROR_STOP=1'];
function file(path) { run(bin + 'psql', [...pg, '-f', path]); }
try {
  run(bin + 'initdb', ['-D', directory, '-U', 'postgres', '--auth=trust', '--no-locale', '-E', 'UTF8']);
  run(bin + 'pg_ctl', ['-D', directory, '-l', directory + '/server.log', '-o',
    `-h '' -k ${directory} -p ${port}`, '-w', 'start']); started = true;
  run(bin + 'createdb', ['--no-password', env.PGDATABASE]);
  file('supabase/tests/bootstrap_local_postgres.sql');
  run(bin + 'psql', [...pg, '-c', "create function auth.role() returns text language sql stable as $$select current_setting('request.jwt.claim.role',true)$$;"]);
  const migrations = readdirSync(resolve(root, 'supabase/migrations'))
    .filter(name => name.endsWith('.sql') && name < '20260901120000').sort();
  for (const name of migrations) file('supabase/migrations/' + name);
  const proof = run(python, ['ops/ybo-server-trial/exact_claim_local_unittest.py', '--local-only']);
  console.log(proof);
  console.log(run(python, ['ops/ybo-preserved-recovery/recovery_local_unittest.py', '--local-only']));
  console.log(run(python, ['ops/ybo-fresh-babylon/fresh_local_unittest.py', '--local-only']));
  console.log(run(python, ['ops/ybo-readiness-babylon/readiness_local_unittest.py', '--local-only']));
  console.log(run(python, ['ops/ybo-standalone-babylon/standalone_local_unittest.py', '--local-only']));
  console.log(JSON.stringify({ exactClaimAndLogoContextSqlPassed: true,
    postgresVersion: run(bin + 'psql', [...pg, '-Atc', 'show server_version_num']),
    migrations: migrations.length, productionCalls: 0, providerCalls: 0, telegramCalls: 0 }));
} finally {
  if (started) run(bin + 'pg_ctl', ['-D', directory, '-m', 'fast', '-w', 'stop']);
  console.log(JSON.stringify({ disposableServerStopped: started,
    socketRemoved: !existsSync(directory + '/.s.PGSQL.' + port) }));
}
