/** Opt-in disposable PostgreSQL integration. Never accepts a URL or existing DB. */
import { existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';

if (process.argv.length !== 3 || process.argv[2] !== '--local-only'
    || !existsSync('supabase/tests/bootstrap_local_postgres.sql')) {
  throw Error('explicit --local-only from repository root required');
}
const bin = '/opt/homebrew/opt/postgresql@16/bin/';
const migration = 'supabase/migrations/20260906100000_content_ops_review_outbox.sql';
if (!existsSync(bin + 'initdb') || !existsSync(migration)) throw Error('local PostgreSQL or migration missing');
// No inherited PG*, database URL, service credentials, or provider credentials.
const env = { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C', LC_ALL: 'C' };
const dir = mkdtempSync('/private/tmp/coineasy-daily-review-');
const pg = ['-h', dir, '-p', '65438', '-U', 'postgres'];
const db = 'synthetic_daily_review';
let startAttempted = false;
let phase = 'initialization';
function run(command, args) {
  const result = spawnSync(bin + command, args, { env, encoding: 'utf8', timeout: 60000 });
  if (result.status !== 0) throw Error(`local verification failed in ${phase} (${command})`);
  return result.stdout.trim();
}
function sql(file, database = 'postgres') {
  return run('psql', ['-X', '-q', '-v', 'ON_ERROR_STOP=1', ...pg, '-d', database, '-f', file]);
}
function query(statement, database = db) {
  return run('psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', ...pg, '-d', database, '-c', statement]);
}
function concurrent(statement) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin + 'psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', ...pg,
      '-d', db, '-c', statement], { env });
    let stdout = '';
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.resume(); // Never reflect arbitrary SQL errors, copy, or provider data.
    const timer = setTimeout(() => child.kill('SIGKILL'), 30000);
    child.on('error', () => { clearTimeout(timer); reject(Error('local SQL child failed')); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code === 0) resolve(stdout.trim()); else reject(Error('local SQL transaction failed'));
    });
  });
}
const auth = "set request.jwt.claim.role='service_role'; ";
function check(condition, label) { if (!condition) throw Error(`acceptance failed: ${label}`); }
function start() {
  startAttempted = true;
  run('pg_ctl', ['-D', dir, '-l', dir + '/server.log', '-o', `-h '' -k ${dir} -p 65438`, '-w', 'start']);
}
function stop() { run('pg_ctl', ['-D', dir, '-m', 'fast', '-w', 'stop']); }
try {
  run('initdb', ['-D', dir, '-U', 'postgres', '--auth=trust', '--no-locale', '-E', 'UTF8']);
  start();
  phase = 'fresh full migrations';
  sql('supabase/tests/bootstrap_local_postgres.sql');
  query("create function auth.role() returns text language sql stable as $$select current_setting('request.jwt.claim.role',true)$$", 'postgres');
  const migrations = readdirSync('supabase/migrations').filter(file => file.endsWith('.sql')).sort();
  for (const file of migrations) { phase = `migration ${file}`; sql('supabase/migrations/' + file); }
  check(query("select to_regclass('private.content_ops_review_outbox') is not null", 'postgres') === 't', 'fresh outbox exists');
  // Verify effective ACL on real migrated schema, not the fixture alone.
  phase = 'fresh schema ACL';
  query(`do $$ declare role_name text; signature text; begin
    foreach role_name in array array['anon','authenticated','service_role'] loop
      if has_table_privilege(role_name,'private.content_ops_review_outbox','SELECT,INSERT,UPDATE,DELETE')
        then raise exception 'direct ledger ACL leak'; end if;
      if has_function_privilege(role_name,'private.content_ops_review_candidate(uuid,uuid,uuid)','EXECUTE')
        then raise exception 'candidate helper ACL leak'; end if;
      foreach signature in array array['public.content_ops_reconcile_daily(uuid,uuid)',
        'public.content_ops_claim_review(uuid,uuid,uuid)',
        'public.content_ops_begin_review_send(uuid,uuid,uuid,text,uuid)',
        'public.content_ops_finish_review_send(uuid,uuid,uuid,text,bigint,uuid)'] loop
        if has_function_privilege(role_name,signature,'EXECUTE') <> (role_name='service_role')
          then raise exception 'RPC ACL mismatch'; end if;
      end loop;
    end loop;
  end $$`, 'postgres');
  sql('supabase/tests/content_ops_review_outbox.sql');
  console.log(JSON.stringify({ freshLocalMigrationFiles: migrations.length, freshSchemaAclPassed: true, hostedProof: false }));

  phase = 'synthetic fixture';
  run('createdb', [...pg, db]);
  sql('supabase/tests/content_ops_review_outbox.bootstrap.sql', db);
  sql(migration, db);
  sql('supabase/migrations/20260916190000_content_ops_review_producer_binding.sql', db);
  phase = 'production-shaped producer binding';
  sql('supabase/tests/content_ops_review_producer_binding.sql', db);
  console.log(JSON.stringify({ productionShapedProducerBindingPassed: true, hostedProof: false }));
  phase = 'synthetic rollback smoke';
  sql('supabase/tests/content_ops_review_outbox.sql', db);
  check(query('select count(*) from public.workspaces') === '0', 'smoke fixtures rolled back');
  const workspace = query('select private.test_content_ops_seed()');
  const version = query(`select current_version_id from public.content_items where workspace_id='${workspace}' and client_id='yellow'`);
  check(/^[a-f0-9-]{36}$/.test(workspace) && /^[a-f0-9-]{36}$/.test(version), 'synthetic identifiers');
  phase = 'exact scope reconcile';
  check(query(auth + `select public.content_ops_reconcile_daily('${workspace}','${version}')`) === '1', 'one exact version enqueued');
  check(query(auth + `select public.content_ops_reconcile_daily('${workspace}','${version}')`) === '0', 'reconcile deduplicated');
  check(query('select count(*) from private.content_ops_review_outbox') === '1', 'scope fenced');
  const claim = token => auth + `select coalesce(public.content_ops_claim_review('${workspace}','${token}','${version}'),'null'::jsonb)`;
  phase = 'concurrent claims';
  const claims = (await Promise.all(Array.from({ length: 8 }, () => concurrent(claim(randomUUID())))) ).map(JSON.parse);
  check(claims.filter(Boolean).length === 1, 'one claim winner');
  const winner = claims.find(Boolean);
  const begin = auth + `select public.content_ops_begin_review_send('${workspace}','${winner.outbox_id}',
    '${winner.claim_token}',repeat('b',64),'${version}')`;
  phase = 'concurrent begin';
  const begins = (await Promise.all(Array.from({ length: 8 }, () => concurrent(begin)))).map(JSON.parse);
  check(begins.filter(result => result.accepted).length === 1, 'one send-start winner');
  check(JSON.parse(query(claim(winner.claim_token))) === null, 'token cannot re-fetch');
  phase = 'unknown no retry';
  const finish = auth + `select public.content_ops_finish_review_send('${workspace}','${winner.outbox_id}',
    '${winner.claim_token}','delivery_unknown',null,'${version}')`;
  check(JSON.parse(query(finish)).status === 'delivery_unknown', 'unknown recorded');
  stop(); start();
  check(query(auth + `select public.content_ops_reconcile_daily('${workspace}','${version}')`) === '0', 'restart cannot re-enqueue');
  check(JSON.parse(query(claim(randomUUID()))) === null, 'new token cannot retry unknown');
  check(JSON.parse(query(begin)).accepted === false, 'begin cannot retry unknown');
  check(query('select count(*) from private.content_ops_review_outbox') === '1', 'no duplicate rows after restart');
  phase = 'stale source excluded';
  query(`update public.source_items set published_at=statement_timestamp()-interval '25 hours' where client_id='squid'`);
  check(query(auth + `select public.content_ops_reconcile_daily('${workspace}')`) === '2', 'only two other fresh clients enqueued');
  check(query('select (select count(*) from public.approvals)+(select count(*) from public.publications)') === '0', 'no approval/publication mutations');
  console.log(JSON.stringify({ syntheticSqlBehaviorPassed: true, rollbackSmokePassed: true, concurrentClaims: 8, claimWinners: 1,
    concurrentBegins: 8, sendStartWinners: 1, restartUnknownNeverRetry: true, staleSourceExcluded: true,
    exactVersionScopePassed: true, approvalOrPublicationRows: 0, providerCalls: 0, productionCalls: 0 }));
} finally {
  // Includes an uncertain startup: never claim cleanup until the PID/socket is gone.
  if (startAttempted && existsSync(dir + '/postmaster.pid')) stop();
  const stopped = !existsSync(dir + '/postmaster.pid') && !existsSync(dir + '/.s.PGSQL.65438');
  if (stopped) rmSync(dir, { recursive: true });
  console.log(JSON.stringify({ localServerStopped: stopped, disposableDirectoryRemoved: !existsSync(dir) }));
}
