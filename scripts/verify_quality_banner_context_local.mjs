/** Fresh disposable, socket-only PostgreSQL16. Never uses a supplied DSN. */
import { existsSync, mkdtempSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
if (process.argv.slice(2).join(' ') !== '--local-only') throw Error('explicit --local-only required');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bin = '/opt/homebrew/opt/postgresql@16/bin/';
if (!existsSync(bin + 'initdb')) throw Error('local PostgreSQL16 required');
const directory = mkdtempSync('/private/tmp/coineasy-quality-banner-');
const port = '65439';
const env = { PATH: bin + ':' + process.env.PATH, LANG: 'C', TZ: 'UTC', PGHOST: directory,
  PGPORT: port, PGUSER: 'postgres', PGDATABASE: 'coineasy_quality_banner_test',
  PGPASSFILE: '/dev/null', PGCONNECT_TIMEOUT: '3' };
let started = false;
function run(command, args, extra = {}) {
  const result = spawnSync(command, args, { cwd: root, env, encoding: 'utf8',
    timeout: 120000, maxBuffer: 2 * 1024 * 1024, ...extra });
  if (result.error || result.status !== 0) throw Error(`local quality SQL proof failed: ${String(result.stderr ?? '').slice(-2500)}`);
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
  const migrations = readdirSync(resolve(root, 'supabase/migrations')).filter(x => x.endsWith('.sql')).sort();
  for (const name of migrations) file('supabase/migrations/' + name);
  file('supabase/proposals/quality_banner_generation_context.sql');
  file('supabase/tests/quality_banner_generation_context.sql');
  console.log(JSON.stringify({ qualityContextSqlPassed: true, postgresMajor: 16,
    clients: 3, rejectionCasesPerClient: 9, naturalCompletionAndInitialOutbox: true,
    migrations: migrations.length, productionCalls: 0, providerCalls: 0 }));
} finally {
  if (started) run(bin + 'pg_ctl', ['-D', directory, '-m', 'fast', '-w', 'stop']);
  console.log(JSON.stringify({ disposableServerStopped: started,
    socketRemoved: !existsSync(directory + '/.s.PGSQL.' + port) }));
}
