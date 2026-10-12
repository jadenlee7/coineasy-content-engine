/** Fresh disposable socket-only PG16 proof. No existing DSN, credentials or provider. */
import { existsSync, mkdtempSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.argv.slice(2).join(' ') !== '--local-only') throw Error('explicit --local-only required');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bin = '/opt/homebrew/opt/postgresql@16/bin/';
if (!existsSync(bin + 'initdb')) throw Error('local PostgreSQL 16 required');
const directory = mkdtempSync('/private/tmp/coineasy-release-pair-');
const port = '65438';
const base = 'coineasy_release_pair_base_test';
const env = { PATH: bin + ':' + process.env.PATH, LANG: 'C', TZ: 'UTC',
  PGHOST: directory, PGPORT: port, PGUSER: 'postgres', PGDATABASE: base,
  PGPASSFILE: '/dev/null', PGCONNECT_TIMEOUT: '3' };
const pg = ['-X', '--no-password', '-q', '-v', 'ON_ERROR_STOP=1'];
let started = false;
function run(command, args, extra = {}) {
  const result = spawnSync(command, args, { cwd: root, env, encoding: 'utf8',
    timeout: 120_000, maxBuffer: 2 * 1024 * 1024, ...extra });
  if (result.error || result.status !== 0) throw Error(
    `local release-pair proof failed (${command}): ${String(result.stderr ?? '').slice(-2500)}`);
  return result.stdout.trim();
}
function file(path) { run(bin + 'psql', [...pg, '-f', path]); }
function query(sql) { return run(bin + 'psql', [...pg, '-At', '-c', sql]); }
try {
  run(bin + 'initdb', ['-D', directory, '-U', 'postgres', '--auth=trust', '--no-locale', '-E', 'UTF8']);
  run(bin + 'pg_ctl', ['-D', directory, '-l', directory + '/server.log', '-o',
    `-h '' -k ${directory} -p ${port}`, '-w', 'start']); started = true;
  run(bin + 'createdb', ['--no-password', base]);
  file('supabase/tests/bootstrap_local_postgres.sql');
  query("create function auth.role() returns text language sql stable as $$select current_setting('request.jwt.claim.role',true)$$;");
  const migrations = readdirSync(resolve(root, 'supabase/migrations')).filter(x => x.endsWith('.sql')).sort();
  for (const migration of migrations) file('supabase/migrations/' + migration);
  file('supabase/tests/content_ops_final_approval_attribution_fixture.sql');
  for (const name of ['content_ops_final_approval_attribution_preapply_readonly',
    'content_ops_final_approval_attribution', 'content_ops_button_review_state',
    'content_ops_button_edit_reply', 'content_ops_button_prompt_registration',
    'content_ops_button_durable_attempt', 'content_ops_final_card_preflight',
    'content_ops_publication_routes', 'content_ops_release_pair_registry',
    'content_ops_final_card_delivery_ledger']) file('supabase/proposals/' + name + '.sql');
  file('supabase/tests/content_ops_release_pair_registry.sql');
  file('supabase/tests/content_ops_final_card_preflight.sql');
  for (const name of ['content_ops_final_decision_ledger', 'content_ops_publication_handoff_intent'])
    file('supabase/proposals/' + name + '.sql');
  file('supabase/tests/content_ops_final_decision_ledger.sql');
  for (const name of ['content_ops_four_client_exact_telegram',
    'content_ops_typefully_final_approval', 'content_ops_final_publication_queue'])
    file('supabase/proposals/' + name + '.sql');
  file('supabase/tests/content_ops_final_publication_queue.sql');
  for (const [name, script] of [
    ['coineasy_release_pair_signup_test', 'verify_final_queue_signup_free_local.mjs'],
    ['coineasy_release_pair_race_test', 'verify_final_queue_signup_free_concurrency_local.mjs']]) {
    run(bin + 'createdb', ['--no-password', '-T', base, name]);
    const receipt = run(process.execPath, ['scripts/' + script, '--local-only'],
      { env: { ...env, PGDATABASE: name } });
    console.log(receipt);
  }
  console.log(JSON.stringify({ releasePairSqlPassed: true, postgresMajor: 16,
    migrationFiles: migrations.length, engineNotCallback: true, productionCalls: 0,
    providerCalls: 0, hostedCompatibilityProof: false }));
} finally {
  if (started) run(bin + 'pg_ctl', ['-D', directory, '-m', 'fast', '-w', 'stop']);
  console.log(JSON.stringify({ disposableServerStopped: started,
    socketRemoved: !existsSync(directory + '/.s.PGSQL.' + port) }));
}
