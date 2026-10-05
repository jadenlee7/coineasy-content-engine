/** Disposable PostgreSQL proof only: no URLs, inherited credentials or live DB. */
import { existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { platform } from 'node:os';

const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== '--local-only' || !['--pg16', '--pg17'].includes(args[1])) {
  throw Error('explicit --local-only --pg16 or --pg17 required');
}
const version = args[1].slice(4);
const bin = platform() === 'darwin'
  ? `/opt/homebrew/opt/postgresql@${version}/bin/` : `/usr/lib/postgresql/${version}/bin/`;
const fixture = 'supabase/tests/official_x_same_day_refresh_security.sql';
if (!existsSync(bin + 'initdb') || !existsSync(fixture)
    || !existsSync('supabase/tests/bootstrap_local_postgres.sql')) {
  throw Error('local PostgreSQL or repository fixture unavailable');
}
// Do not pass through PG*, database/provider URLs, tokens, proxy or bot settings.
const env = { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C', LC_ALL: 'C' };
const dir = mkdtempSync(platform() === 'darwin' ? '/private/tmp/coineasy-refresh-' : '/tmp/coineasy-refresh-');
const port = '65439';
const pg = ['-h', dir, '-p', port, '-U', 'postgres', '-d', 'postgres'];
const auth = "set request.jwt.claim.role='service_role'; ";
let startAttempted = false;
let phase = 'initialization';

function run(command, argv) {
  const r = spawnSync(bin + command, argv, { env, encoding: 'utf8', timeout: 60_000 });
  if (r.status !== 0) throw Error(`local verification failed: ${phase} (${command})`);
  return r.stdout.trim();
}
function query(sql) {
  return run('psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', ...pg, '-c', sql]);
}
function file(path) {
  return run('psql', ['-X', '-q', '-v', 'ON_ERROR_STOP=1', ...pg, '-f', path]);
}
function start() {
  startAttempted = true;
  run('pg_ctl', ['-D', dir, '-l', dir + '/server.log', '-o', `-h '' -k ${dir} -p ${port}`, '-w', 'start']);
}
function stop() { run('pg_ctl', ['-D', dir, '-m', 'fast', '-w', 'stop']); }
function check(value, label) { if (!value) throw Error(`acceptance failed: ${label}`); }
function uuid(value) {
  check(typeof value === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value), 'synthetic UUID');
  return `'${value}'`;
}
function concurrent(sql) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin + 'psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', ...pg, '-c', sql], { env });
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.resume(); // SQL text/claim input must not enter a tool/user receipt.
    const timer = setTimeout(() => child.kill('SIGKILL'), 30_000);
    child.on('error', () => { clearTimeout(timer); reject(Error('local SQL child unavailable')); });
    child.on('close', code => {
      clearTimeout(timer);
      resolve(code === 0 ? { accepted: true, output: output.trim() } : { accepted: false });
    });
  });
}

// Hold a real row lock until a competing RPC's short operation window expires.
// Only synthetic fixture workspaces are passed here; output stays internal.
async function lockFeed(seed) {
  const child = spawn(bin + 'psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', ...pg], { env });
  child.stderr.resume();
  let output = '';
  let ready;
  let failed;
  const readyPromise = new Promise((resolve, reject) => { ready = resolve; failed = reject; });
  const timer = setTimeout(() => {
    child.kill('SIGKILL');
    failed(Error('synthetic lock timeout'));
  }, 10_000);
  const closed = new Promise(resolve => child.on('close', code => {
    clearTimeout(timer);
    if (!output.includes('LOCK_READY')) failed(Error('synthetic lock unavailable'));
    resolve(code);
  }));
  child.on('error', () => failed(Error('synthetic lock child unavailable')));
  child.stdout.on('data', chunk => {
    output += chunk;
    if (output.includes('LOCK_READY')) ready();
  });
  child.stdin.write(`begin; select 1 from public.source_feeds where id=(
    select source_feed_id from public.source_items where id=${uuid(seed.source_item_id)}) for update;
    select 'LOCK_READY';\n`);
  await readyPromise;
  return async () => {
    child.stdin.end('commit;\n');
    check(await closed === 0, 'synthetic lock released');
  };
}

async function waitForLock(applicationName) {
  check(/^refresh-local-delayed-(queue|claim)$/.test(applicationName), 'synthetic lock observer');
  for (let attempt = 0; attempt < 20; attempt++) {
    if (query(`select exists(select 1 from pg_catalog.pg_stat_activity
      where application_name='${applicationName}' and wait_event_type='Lock')`) === 't') return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw Error('acceptance failed: competing RPC did not enter real lock wait');
}

try {
  run('initdb', ['-D', dir, '-U', 'postgres', '--auth=trust', '--no-locale', '-E', 'UTF8']);
  start();
  phase = 'full schema bootstrap';
  file('supabase/tests/bootstrap_local_postgres.sql');
  query("create function auth.role() returns text language sql stable as $$select current_setting('request.jwt.claim.role',true)$$");
  const allMigrations = readdirSync('supabase/migrations').filter(p => p.endsWith('.sql')).sort();
  // The unrelated managed-inspector boundary intentionally fails closed on
  // plain PG17. Do not weaken it or invent hosted platform-admin role edges.
  // PG16 verifies the full chain; PG17 verifies only the real refresh dependency
  // chain and RPC/ACL behavior in an otherwise disposable synthetic database.
  const reviewDependencies = new Set([
    '20260906100000_content_ops_review_outbox.sql',
    '20260916190000_content_ops_review_producer_binding.sql',
    '20261003143000_content_ops_reconcile_producer_binding.sql',
    '20261003143100_content_ops_review_exact_copy.sql',
    '20261005030000_official_x_same_day_refresh.sql',
  ]);
  const migrations = version === '16' ? allMigrations : allMigrations.filter(path =>
    path < '20260901120000' || reviewDependencies.has(path));
  for (const path of migrations) { phase = `migration ${path}`; file('supabase/migrations/' + path); }
  phase = 'transactional refresh security fixture';
  file(fixture);
  check(query('select count(*) from private.official_x_same_day_refresh_requests') === '0', 'security fixture rollback');

  phase = 'synthetic concurrency seed';
  const seed = JSON.parse(query('select private.test_same_day_refresh_seed()'));
  for (const key of ['workspace_id', 'predecessor_job_id', 'predecessor_content_item_id',
    'predecessor_content_version_id', 'source_item_id']) uuid(seed[key]);
  check(['yellow', 'babylon', 'squid', 'origintrail'].includes(seed.client_id), 'synthetic client');
  check(/^[a-f0-9]{40}$/.test(seed.release_sha), 'synthetic release');
  check(/^\d{4}-\d{2}-\d{2}$/.test(seed.kst_date), 'synthetic day');
  const w = uuid(seed.workspace_id);
  const previous = uuid(seed.predecessor_content_item_id);
  const source = uuid(seed.source_item_id);
  const snapshot = () => query(`select md5(jsonb_build_object(
    'slot',(select to_jsonb(s) from private.official_x_daily_slots s where s.workspace_id=${w}),
    'job',(select to_jsonb(j) from public.jobs j where j.id=${uuid(seed.predecessor_job_id)}),
    'item',(select to_jsonb(i) from public.content_items i where i.id=${previous}),
    'version',(select to_jsonb(v) from public.content_versions v where v.id=${uuid(seed.predecessor_content_version_id)}),
    'links',(select jsonb_agg(to_jsonb(l) order by l.source_item_id) from public.content_source_links l where l.content_item_id=${previous}),
    'old_source_state',(select jsonb_agg(to_jsonb(s) order by s.source_item_id) from private.official_x_source_state s where s.workspace_id=${w} and s.source_item_id<>${source})
  )::text)`);
  const before = snapshot();
  const queue = (refresh, request) => auth + `select public.queue_official_x_same_day_refresh(
    ${w},'${seed.client_id}','${seed.kst_date}',${uuid(seed.predecessor_job_id)},${previous},
    ${uuid(seed.predecessor_content_version_id)},${source},${uuid(refresh)},${uuid(request)},
    '${seed.release_sha}',statement_timestamp()+interval '30 minutes')`;
  phase = 'concurrent refresh reservations';
  const queued = await Promise.all(Array.from({ length: 8 }, () => concurrent(queue(randomUUID(), randomUUID()))));
  check(queued.filter(r => r.accepted).length === 1, 'one client/day queue winner');
  const winner = JSON.parse(queued.find(r => r.accepted).output);
  uuid(winner.refresh_id); uuid(winner.job_id); uuid(winner.request_id);
  check(query(`select count(*) from private.official_x_same_day_refresh_requests where workspace_id=${w}`) === '1', 'one refresh ledger');
  check(query(`select count(*) from public.jobs where workspace_id=${w} and input->>'same_day_refresh_id' is not null`) === '1', 'one new refresh job');
  const claim = worker => auth + `select coalesce(public.claim_official_x_same_day_refresh(
    ${w},${uuid(winner.refresh_id)},${uuid(winner.job_id)},${uuid(winner.request_id)},${source},
    '${seed.release_sha}','${worker}',900),'null'::jsonb)`;
  phase = 'concurrent exact claims';
  const claims = await Promise.all(Array.from({ length: 8 }, (_, i) => concurrent(claim(`refresh-local-${i}`))));
  const claimed = claims.filter(r => r.accepted).map(r => JSON.parse(r.output)).filter(Boolean);
  check(claimed.length === 1, 'one exact claim winner');
  check(claimed[0].attempts === 1 && claimed[0].max_attempts === 1, 'one attempt budget');
  check(claimed[0].execution_plane === 'studio_sync', 'prebound synchronous plane');
  check(snapshot() === before, 'predecessor records unchanged');
  check(JSON.parse(query(claim(claimed[0].locked_by))) === null, 'same worker cannot reclaim');
  phase = 'restart and consumed lease';
  stop(); start();
  check(JSON.parse(query(claim('refresh-local-restarted'))) === null, 'restart cannot reclaim');
  query(`update public.jobs set lease_expires_at=statement_timestamp()-interval '1 second' where id=${uuid(winner.job_id)}`);
  check(JSON.parse(query(claim('refresh-local-expired'))) === null, 'expired lease cannot reclaim');
  check(JSON.parse(query(auth + `select coalesce(public.claim_review_draft_job(${w},'natural-local',900),'null'::jsonb)`)) === null,
    'natural FIFO cannot consume or retry refresh');
  check(query(`select attempts||':'||max_attempts from public.jobs where id=${uuid(winner.job_id)}`) === '1:1', 'no attempt reset');
  check(query('select (select count(*) from public.approvals)+(select count(*) from public.publications)') === '0', 'no approval/publication');

  phase = 'queue expiry during real feed lock';
  const delayedSeed = JSON.parse(query('select private.test_same_day_refresh_seed()'));
  const delayedWorkspace = uuid(delayedSeed.workspace_id);
  const releaseQueueLock = await lockFeed(delayedSeed);
  const delayedQueue = concurrent(auth + `set application_name='refresh-local-delayed-queue';
    select public.queue_official_x_same_day_refresh(
    ${delayedWorkspace},'${delayedSeed.client_id}','${delayedSeed.kst_date}',
    ${uuid(delayedSeed.predecessor_job_id)},${uuid(delayedSeed.predecessor_content_item_id)},
    ${uuid(delayedSeed.predecessor_content_version_id)},${uuid(delayedSeed.source_item_id)},
    ${uuid(randomUUID())},${uuid(randomUUID())},'${delayedSeed.release_sha}',
    statement_timestamp()+interval '2 seconds')`);
  await waitForLock('refresh-local-delayed-queue');
  await new Promise(resolve => setTimeout(resolve, 2600));
  await releaseQueueLock();
  check(!(await delayedQueue).accepted, 'expired queue rejected after lock wait');
  check(query(`select count(*) from private.official_x_same_day_refresh_requests where workspace_id=${delayedWorkspace}`) === '0',
    'expired queue has no durable receipt');
  check(query(`select count(*) from public.jobs where workspace_id=${delayedWorkspace} and input->>'same_day_refresh_id' is not null`) === '0',
    'expired queue has no new job');
  check(query(`select queued_job_id is null from private.official_x_source_state where workspace_id=${delayedWorkspace}
    and source_item_id=${uuid(delayedSeed.source_item_id)}`) === 't', 'expired queue source not reserved');

  phase = 'claim expiry during real feed lock';
  const claimSeed = JSON.parse(query('select private.test_same_day_refresh_seed()'));
  const claimWorkspace = uuid(claimSeed.workspace_id);
  const shortReceipt = JSON.parse(query(auth + `select public.queue_official_x_same_day_refresh(
    ${claimWorkspace},'${claimSeed.client_id}','${claimSeed.kst_date}',${uuid(claimSeed.predecessor_job_id)},
    ${uuid(claimSeed.predecessor_content_item_id)},${uuid(claimSeed.predecessor_content_version_id)},
    ${uuid(claimSeed.source_item_id)},${uuid(randomUUID())},${uuid(randomUUID())},
    '${claimSeed.release_sha}',statement_timestamp()+interval '2 seconds')`));
  const releaseClaimLock = await lockFeed(claimSeed);
  const delayedClaim = concurrent(auth + `set application_name='refresh-local-delayed-claim';
    select coalesce(public.claim_official_x_same_day_refresh(
    ${claimWorkspace},${uuid(shortReceipt.refresh_id)},${uuid(shortReceipt.job_id)},${uuid(shortReceipt.request_id)},
    ${uuid(claimSeed.source_item_id)},'${claimSeed.release_sha}','refresh-local-delayed',900),'null'::jsonb)`);
  await waitForLock('refresh-local-delayed-claim');
  await new Promise(resolve => setTimeout(resolve, 2600));
  await releaseClaimLock();
  const expiredClaim = await delayedClaim;
  check(expiredClaim.accepted && JSON.parse(expiredClaim.output) === null, 'expired claim rejected after lock wait');
  check(query(`select claimed_at is null from private.official_x_same_day_refresh_requests
    where refresh_id=${uuid(shortReceipt.refresh_id)}`) === 't', 'expired claim not consumed');
  check(query(`select status||':'||attempts from public.jobs where id=${uuid(shortReceipt.job_id)}`) === 'queued:0',
    'expired claim starts no attempt');
  console.log(JSON.stringify({ postgresMajor: Number(version), localMigrationFiles: migrations.length,
    schemaScope: version === '16' ? 'full_chain' : 'refresh_dependency_chain',
    rollbackSecurityPassed: true, concurrentQueues: 8, queueWinners: 1, concurrentClaims: 8, claimWinners: 1,
    predecessorUnchanged: true, consumedClaimNeverRetried: true, naturalFifoExcluded: true,
    lockWaitObserved: true, lockWaitQueueExpiryRejected: true, lockWaitClaimExpiryRejected: true,
    productionCalls: 0, providerCalls: 0, telegramCalls: 0, hostedProof: false }));
} finally {
  if (startAttempted && existsSync(dir + '/postmaster.pid')) stop();
  const stopped = !existsSync(dir + '/postmaster.pid') && !existsSync(dir + '/.s.PGSQL.' + port);
  if (stopped) rmSync(dir, { recursive: true });
  console.log(JSON.stringify({ localServerStopped: stopped, disposableDirectoryRemoved: !existsSync(dir) }));
}
