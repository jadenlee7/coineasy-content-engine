/** Disposable socket-only PostgreSQL16; no existing DSN, credentials or provider. */
import { existsSync, mkdtempSync, readdirSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
if (process.argv.slice(2).join(' ') !== '--local-only') throw Error('explicit --local-only required');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bin = '/opt/homebrew/opt/postgresql@16/bin/';
if (!existsSync(bin + 'initdb')) throw Error('local PostgreSQL16 required');
const directory = mkdtempSync('/private/tmp/coineasy-daily-private-');
const port = '65439';
const env = { PATH: bin + ':' + process.env.PATH, LANG: 'C', TZ: 'UTC', PGHOST: directory,
  PGPORT: port, PGUSER: 'postgres', PGDATABASE: 'coineasy_daily_private_test',
  PGPASSFILE: '/dev/null', PGCONNECT_TIMEOUT: '3' };
let started = false;
function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, env, encoding: 'utf8', timeout: 120000, maxBuffer: 2 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw Error(`local daily scope proof failed: ${String(result.stderr ?? '').slice(-2500)}`);
  return result.stdout.trim();
}
const policySource=readFileSync(resolve(root,'core/automation/higgsfield_banner.py'),'utf8');
const policyMatch=policySource.match(/^POLICY = ("[^"\r\n]+")$/m);
if (!policyMatch) throw Error('renderer policy marker missing');
const policy=JSON.parse(policyMatch[1]);
const pg = ['-X', '--no-password', '-q', '-v', 'ON_ERROR_STOP=1', '-v', 'quality_banner_policy='+policy];
const file = path => run(bin + 'psql', [...pg, '-f', path]);
try {
  run(bin + 'initdb', ['-D', directory, '-U', 'postgres', '--auth=trust', '--no-locale', '-E', 'UTF8']);
  run(bin + 'pg_ctl', ['-D', directory, '-l', directory + '/server.log', '-o', `-h '' -k ${directory} -p ${port}`, '-w', 'start']); started = true;
  run(bin + 'createdb', ['--no-password', env.PGDATABASE]);
  file('supabase/tests/bootstrap_local_postgres.sql');
  run(bin + 'psql', [...pg, '-c', "create function auth.role() returns text language sql stable as $$select current_setting('request.jwt.claim.role',true)$$;"]);
  const migrations = readdirSync(resolve(root, 'supabase/migrations')).filter(x => x.endsWith('.sql')).sort();
  for (const name of migrations) file('supabase/migrations/' + name);
  for (const name of ['quality_banner_generation_context', 'content_ops_button_review_state',
    'content_ops_button_edit_reply', 'content_ops_button_prompt_registration',
    'content_ops_button_durable_attempt', 'content_ops_button_card_send_ledger',
    'content_ops_button_card_owner_gateway', 'content_ops_daily_private_scope']) {
    file('supabase/proposals/'+name+'.sql');
  }
  file('supabase/proposals/content_ops_button_card_readonly_preflight.sql');
  file('supabase/tests/content_ops_daily_private_scope.sql');
  const inventory = JSON.parse(run(bin+'psql',[...pg,'-At','-f',
    'supabase/proposals/content_ops_daily_private_readonly_inventory.sql']));
  if (inventory.functions.some(f => !f.installed || f.anon_execute || f.authenticated_execute)
    || inventory.tables.some(t => !t.installed || !t.rls || t.anon_direct || t.authenticated_direct || t.service_role_direct)) {
    throw Error('initial daily catalog/ACL mismatch');
  }
  const expected=JSON.parse(readFileSync(resolve(root,'ops/content-ops/daily-private-expected-catalog.json'),'utf8'));
  for (const group of ['functions','tables']) {
    const common=expected[group==='functions'?'function_common':'table_common'];
    const key=group==='functions'?'signature':'name';
    for (const row of expected[group]) {
      const actual=inventory[group].find(x=>x[key]===row[key]);
      if (!actual || Object.entries({...common,...row}).some(([k,v])=>JSON.stringify(actual[k])!==JSON.stringify(v))) {
        throw Error('expected local catalog drift: '+row[key]);
      }
    }
  }
  console.log(JSON.stringify({ dailyPrivateScopeSqlPassed: true, postgresMajor: 16, clients: 3,
    excludesSquidBeforeClaim: true, naturalJobBinding: true, restartDoesNotResend: true,
    productionCalls: 0, providerCalls: 0, hostedCompatibilityProof: false,
    proposalDependencies: 8, initialCardLedgerAcl: true, invalidQualityRejections: 4,
    exactExpectedCatalogMatch: true, rendererPolicy:policy, obsoletePolicyRejected:true }));
} finally {
  if (started) run(bin + 'pg_ctl', ['-D', directory, '-m', 'fast', '-w', 'stop']);
  console.log(JSON.stringify({ disposableServerStopped: started, socketRemoved: !existsSync(directory + '/.s.PGSQL.' + port) }));
}
