/** Two real callback sessions on an expendable signup-free local DB clone. */
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';

if (process.argv.slice(2).join(' ') !== '--local-only') throw Error('explicit --local-only required');
const { PGHOST: host, PGPORT: port, PGUSER: user, PGDATABASE: database } = process.env;
if (!/^\/(?:private\/)?tmp(?:\/[A-Za-z0-9_.-]+)*$/.test(host ?? '')
    || !/^[1-9][0-9]{0,4}$/.test(port ?? '') || Number(port) > 65535
    || !/^coineasy_[a-z0-9_]+_test$/.test(database ?? '')
    || !/^[A-Za-z_][A-Za-z0-9_-]{0,62}$/.test(user ?? '')
    || !existsSync(resolve(host, `.s.PGSQL.${port}`))) {
  throw Error('explicit disposable UNIX-socket test database required');
}
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const env = { PATH: process.env.PATH, LANG: 'C', TZ: 'UTC', PGHOST: host, PGPORT: port,
  PGUSER: user, PGDATABASE: database, PGPASSFILE: '/dev/null', PGCONNECT_TIMEOUT: '3',
  PGOPTIONS: '-c statement_timeout=15000 -c lock_timeout=10000' };
const args = ['-X', '--no-password', '-qAt', '-v', 'ON_ERROR_STOP=1'];
function query(sql) {
  const result = spawnSync('psql', args, { env, input: sql, encoding: 'utf8', timeout: 30000 });
  if (result.error || result.status !== 0) throw Error(String(result.stderr ?? '').slice(-1200));
  return result.stdout.trim();
}
const compatibility = readFileSync(resolve(root,
  'supabase/tests/content_ops_final_queue_hosted_principals_fixture.sql'), 'utf8');
let seed = readFileSync(resolve(root, 'supabase/tests/content_ops_final_publication_queue.sql'), 'utf8');
const marker = "        perform set_config('coineasy.fixture_fail_stage',";
const authSeed = '        insert into auth.users(id) values(actor);';
if (seed.split(marker).length !== 2 || seed.split(authSeed).length !== 2
    || !/^-- Disposable[^\n]*\nbegin;\n/.test(seed)
    || seed.split('for scenario_index in 1..23 loop').length !== 2) {
  throw Error('queue fixture shape changed; review concurrency adaptation');
}
seed = seed.slice(0, seed.indexOf(marker));
seed = seed.replace(/^-- Disposable[^\n]*\nbegin;\n/, () => `begin;
select set_config('coineasy.signup_free_fixture','local-only',true);
${compatibility}
create table private.signup_free_concurrency_seed(w uuid,i uuid,card uuid,actor uuid,v uuid,
  fingerprint text,snapshot_hash text,control_binding text,bot text,room text,human text,
  release_sha text,operation_key text,callback_release_sha text,release_pair_sha256 text);
`);
seed = seed.replace('for scenario_index in 1..23 loop', 'for scenario_index in 1..1 loop');
seed = seed.replace(authSeed, `        if exists(select 1 from auth.users where id=actor) then
            raise exception 'concurrency_auth_user_present';end if;`);
seed += `insert into private.signup_free_concurrency_seed values(w,i,card,actor,v,fingerprint,
snapshot_hash,control_binding,bot,room,human,release_sha,operation_key,callback_release_sha,release_pair_sha256);
end loop;end $$;commit;`;
query(seed); // Synthetic seed intentionally persists ONLY in the expendable clone.
const call = `select private.record_content_ops_final_publication(card,actor,v,fingerprint,
snapshot_hash,control_binding,bot,room,human,release_sha,'confirm_publication',operation_key,
callback_release_sha,release_pair_sha256)
from private.signup_free_concurrency_seed;`;
const lock = spawn('psql', args, { env, stdio: ['pipe', 'pipe', 'pipe'] });
let lockOutput = '';
let releaseReady;
const ready = new Promise(resolveReady => { releaseReady = resolveReady; });
lock.stdout.on('data', chunk => {
  lockOutput += chunk;
  if (lockOutput.includes('LOCK_READY')) releaseReady();
});
lock.stderr.resume();
let released = false;
function concurrent(name, sql = call) {
  return new Promise((resolveResult, reject) => {
    const child = spawn('psql', args, { env: { ...env, PGAPPNAME: name } });
    let output = ''; let error = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { error += chunk; });
    child.on('error', reject);
    child.on('close', code => {
      if (code !== 0) return reject(Error(error.slice(-1200)));
      try { resolveResult(JSON.parse(output.trim())); } catch (cause) { reject(cause); }
    });
    child.stdin.end(sql);
  });
}
try {
  lock.stdin.write(`begin;select 1 from public.content_items where id=(
select i from private.signup_free_concurrency_seed) for update;select 'LOCK_READY';\n`);
  let timer;
  await Promise.race([ready, new Promise((_, reject) => {
    timer = setTimeout(() => reject(Error('synthetic item lock unavailable')), 5000);
  })]).finally(() => clearTimeout(timer));
  const first = concurrent('signupfree-c1'); const second = concurrent('signupfree-c2');
  let waiting = false;
  for (let count = 0; count < 60; count++) {
    if (query(`select count(*) from pg_stat_activity where datname=current_database()
and application_name in ('signupfree-c1','signupfree-c2') and wait_event_type='Lock'`) === '2') {
      waiting = true; break;
    }
    await new Promise(resolveWait => setTimeout(resolveWait, 50));
  }
  lock.stdin.end('commit;\n'); released = true;
  const receipts = await Promise.all([first, second]);
  assert.equal(waiting, true, 'both real sessions must wait on the same item lock');
  assert.deepEqual(receipts.map(receipt => receipt.reused).sort(), [false, true]);
  for (const receipt of receipts) {
    assert.equal(receipt.status, 'channels_queued'); assert.equal(receipt.execution_authorized, false);
  }
  assert.deepEqual({ ...receipts[0], reused: false }, { ...receipts[1], reused: false });
  const counts = JSON.parse(query(`select jsonb_build_object(
'approvals',(select count(*) from public.approvals a join private.signup_free_concurrency_seed s on a.content_item_id=s.i),
'decisions',(select count(*) from private.content_ops_final_decisions d join private.signup_free_concurrency_seed s on d.card_id=s.card),
'handoffs',(select count(*) from private.content_ops_channel_handoffs h join private.signup_free_concurrency_seed s on h.content_item_id=s.i),
'publications',(select count(*) from public.publications p join private.signup_free_concurrency_seed s on p.content_item_id=s.i),
'publish_jobs',(select count(*) from public.jobs j join private.signup_free_concurrency_seed s on j.content_item_id=s.i where j.job_kind='publish'),
'slots',(select count(*) from private.typefully_daily_slots t join private.signup_free_concurrency_seed s on t.content_item_id=s.i),
'auth_users',(select count(*) from auth.users u join private.signup_free_concurrency_seed s on u.id=s.actor));`));
  assert.deepEqual(counts, { approvals: 1, decisions: 1, handoffs: 2, publications: 1,
    publish_jobs: 1, slots: 1, auth_users: 0 });
  // Verify the scoped selection itself serializes with an admission fence.
  // The synthetic current selection may change only after the helper's SHARE
  // lock commits; the already committed terminal receipt must still reconcile.
  const pairLock = spawn('psql', args, { env, stdio: ['pipe', 'pipe', 'pipe'] });
  let pairOutput = ''; let pairReadyResolve;
  const pairReady = new Promise(resolveReady => { pairReadyResolve = resolveReady; });
  pairLock.stdout.on('data', chunk => {
    pairOutput += chunk;
    if (pairOutput.includes('PAIR_LOCK_READY')) pairReadyResolve();
  });
  pairLock.stderr.resume();
  let pairReleased = false;
  try {
    pairLock.stdin.write(`begin;select private.require_content_ops_release_pair(
s.w,'yellow',s.release_sha,s.callback_release_sha,s.release_pair_sha256)
from private.signup_free_concurrency_seed s;select 'PAIR_LOCK_READY';\n`);
    let pairTimer;
    await Promise.race([pairReady, new Promise((_, reject) => {
      pairTimer = setTimeout(() => reject(Error('synthetic release-pair lock unavailable')), 5000);
    })]).finally(() => clearTimeout(pairTimer));
    const switchSql = `update private.content_ops_current_release_pairs c
set release_pair_sha256=(select p.release_pair_sha256 from private.content_ops_release_pairs p
where p.release_sha=s.release_sha and p.callback_release_sha<>s.callback_release_sha limit 1)
from private.signup_free_concurrency_seed s where c.workspace_id=s.w and c.client_id='yellow';
select jsonb_build_object('switched',true);`;
    const switched = concurrent('signupfree-pair-switch', switchSql);
    let pairWaiting = false;
    for (let count = 0; count < 60; count++) {
      if (query(`select count(*) from pg_stat_activity where datname=current_database()
and application_name='signupfree-pair-switch' and wait_event_type='Lock'`) === '1') {
        pairWaiting = true; break;
      }
      await new Promise(resolveWait => setTimeout(resolveWait, 50));
    }
    pairLock.stdin.end('commit;\n'); pairReleased = true;
    assert.deepEqual(await switched, { switched: true });
    assert.equal(pairWaiting, true, 'selection switch must wait for admission SHARE lock');
    assert.deepEqual(JSON.parse(query(call)), { ...receipts[0], reused: true });
    assert.equal(query('select count(*) from public.approvals;'), '1');
  } finally {
    if (!pairReleased) pairLock.stdin.end('rollback;\n');
  }
  console.log(JSON.stringify({ sameCardConcurrentCalls: 2, observedRealLockWaiters: 2,
    exactReceiptReplay: true, counts, providerCalls: 0, productionCalls: 0,
    scopedPairSwitchObservedLockWait: true, terminalReplayAfterPairSwitch: true,
    syntheticSeedRetainedInDisposableClone: true }));
} finally {
  if (!released) lock.stdin.end('rollback;\n');
}
