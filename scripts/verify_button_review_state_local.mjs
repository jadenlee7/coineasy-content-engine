/** Disposable local PostgreSQL only. No URL, existing database or runtime auth. */
import { mkdtempSync, readdirSync, existsSync } from 'node:fs';
import { spawnSync, spawn } from 'node:child_process';

if (process.argv.length !== 3 || process.argv[2] !== '--local-only'
    || !existsSync('supabase/tests/bootstrap_local_postgres.sql')) throw Error('local-only repo root required');
const bin = '/opt/homebrew/opt/postgresql@16/bin/';
const dir = mkdtempSync('/private/tmp/coineasy-button-review-');
const env = { ...process.env };
for (const k of Object.keys(env)) if (k.startsWith('PG') || k === 'DATABASE_URL') delete env[k];
const pg = ['-h', dir, '-p', '65439', '-U', 'postgres'];
let started = false;
function run(cmd, args) {
  const r = spawnSync(bin + cmd, args, { encoding: 'utf8', env, timeout: 60000 });
  if (r.status !== 0) { console.error(r.stderr.slice(-2000)); throw Error('local SQL verification failed'); }
  return r.stdout.trim();
}
function sql(file, db = 'postgres') { return run('psql', ['-X', '-q', '-v', 'ON_ERROR_STOP=1', ...pg, '-d', db, '-f', file]); }
function query(q, db = 'synthetic_buttons') { return run('psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', ...pg, '-d', db, '-c', q]); }
function concurrent(q) {
  return new Promise((resolve, reject) => {
    const p = spawn(bin + 'psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', ...pg, '-d', 'synthetic_buttons', '-c', q], { env });
    let output = ''; p.stdout.on('data', b => { output += b; }); p.stderr.resume();
    const timer = setTimeout(() => { p.kill('SIGTERM'); reject(Error('local transaction timeout')); }, 30000);
    p.on('error', () => { clearTimeout(timer); reject(Error('local transaction spawn failed')); });
    p.on('close', code => { clearTimeout(timer); if (code === 0) resolve(output.trim()); else reject(Error('local concurrent transaction failed')); });
  });
}
const proposal = 'supabase/proposals/content_ops_button_review_state.sql';
const editProposal = 'supabase/proposals/content_ops_button_edit_reply.sql';
const registrationProposal = 'supabase/proposals/content_ops_button_prompt_registration.sql';
const durableProposal = 'supabase/proposals/content_ops_button_durable_attempt.sql';
const cardSendLedgerProposal = 'supabase/proposals/content_ops_button_card_send_ledger.sql';
const markupProposal = 'supabase/proposals/content_ops_button_markup_attempt.sql';
const authorityProposal = 'supabase/proposals/content_ops_button_markup_authority.sql';
const confirmationProposal = 'supabase/proposals/content_ops_button_markup_confirmation.sql';
const confirmationDeliveryProposal = 'supabase/proposals/content_ops_button_confirmation_delivery.sql';
const confirmationSourceProposal = 'supabase/proposals/content_ops_button_confirmation_source.sql';
const confirmationSendProposal = 'supabase/proposals/content_ops_button_confirmation_send_permission.sql';
const confirmationSendEventProposal = 'supabase/proposals/content_ops_button_confirmation_send_event.sql';
const confirmationDispatchProposal = 'supabase/proposals/content_ops_button_confirmation_dispatch.sql';
const bannerProposal = 'supabase/proposals/content_ops_banner_revision.sql';
const driverPython = env.BUTTON_EDIT_TEST_PYTHON;
function driverTest(phase) {
  if (!driverPython) return;
  const result = spawnSync(driverPython, ['scripts/verify_button_edit_driver_local.py',
    '--local-socket', dir, '--phase', phase], { encoding: 'utf8', env, timeout: 60000 });
  if (result.status !== 0) {
    for (const line of String(result.stdout ?? '').split('\n')) {
      try { const p = JSON.parse(line); if (p.driverPhase) console.error(JSON.stringify({
        driverPhase: p.driverPhase, phaseStarted: p.phaseStarted === true, phasePassed: p.phasePassed === true })); }
      catch { /* Never reflect arbitrary driver text. */ }
    }
    console.error(String(result.stderr ?? '').slice(-2000));
    if (result.error) console.error(JSON.stringify({ localDriverSpawnError: result.error.code ?? 'unknown' }));
    throw Error('local Python driver verification failed');
  }
  console.log(result.stdout.trim());
}
const acl = `do $$ declare r text; f text; t text; begin
  foreach r in array array['anon','authenticated','service_role'] loop
    foreach f in array array['private.content_ops_button_version_fingerprint(uuid,uuid,uuid)',
      'private.content_ops_button_check_state(uuid,uuid)',
      'private.record_content_ops_button_action(uuid,uuid,text,text,text)',
      'private.register_content_ops_button_edit_prompt(uuid,text)',
      'private.guard_content_ops_button_durable_record()',
      'private.record_content_ops_button_card(uuid,uuid,text,bigint,jsonb,jsonb,timestamptz,timestamptz)',
      'private.guard_content_ops_button_card_send_attempt()',
      'private.bind_content_ops_button_card_outbox(uuid,uuid,uuid,text)',
      'private.content_ops_button_card_outbox_owned(uuid)',
      'private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text)',
      'private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz)',
      'private.register_content_ops_button_card_from_sends(uuid,uuid,text,bigint,jsonb,jsonb,text,jsonb,timestamptz,timestamptz)',
      'private.read_content_ops_button_card_terminal(uuid,uuid,uuid)',
      'private.reserve_content_ops_button_prompt_attempt(uuid,uuid,uuid,text,text)',
      'private.assert_content_ops_button_prompt_card_active(uuid,uuid,text)',
      'private.revoke_content_ops_button_card(uuid,uuid,text,uuid,text,text)',
      'private.guard_content_ops_button_markup_attempt()',
      'private.guard_content_ops_button_control_evidence()',
      'private.guard_content_ops_button_markup_approval()',
      'private.guard_content_ops_button_markup_confirmation()',
      'private.guard_content_ops_button_confirmation_delivery()',
      'private.lock_content_ops_button_markup_card(uuid,uuid,text,text)',
      'private.reserve_content_ops_button_markup_attempt(uuid,uuid,uuid,text,text,text,text,text,timestamptz,timestamptz)',
      'private.record_content_ops_button_markup_response(uuid,uuid,uuid,text,text,text,text,timestamptz)',
      'private.save_content_ops_button_edit_reply(uuid,text,text,text,text,text,text)'] loop
      if has_function_privilege(r,f,'EXECUTE') then raise exception 'runtime function ACL leaked';end if;
    end loop;
    foreach t in array array['private.content_ops_button_reviews','private.content_ops_button_reviewers',
      'private.content_ops_button_checks','private.content_ops_button_actions',
      'private.content_ops_button_identities','private.content_ops_button_edit_prompts',
      'private.content_ops_button_prompt_receipts','private.content_ops_button_cards',
      'private.content_ops_button_prompt_attempts','private.content_ops_button_markup_attempts',
      'private.content_ops_button_card_send_attempts',
      'private.content_ops_button_card_outbox_owners',
      'private.content_ops_button_control_evidence','private.content_ops_button_markup_approvals',
      'private.content_ops_button_markup_confirmations','private.content_ops_button_markup_confirmation_events',
      'private.content_ops_button_confirmation_deliveries','private.content_ops_button_confirmation_sources',
      'private.content_ops_button_confirmation_send_permissions',
      'private.content_ops_button_confirmation_send_events',
      'private.content_ops_button_confirmation_dispatches',
      'private.content_ops_banner_requests','private.content_ops_banner_results',
      'private.content_ops_banner_briefs','private.content_ops_banner_feedback'] loop
      if has_table_privilege(r,t,'SELECT,INSERT,UPDATE,DELETE') then raise exception 'runtime table ACL leaked';end if;
    end loop;
  end loop;
end $$;`;
try {
  run('initdb', ['-D', dir, '-U', 'postgres', '--auth=trust', '--no-locale', '-E', 'UTF8']);
  run('pg_ctl', ['-D', dir, '-l', dir + '/server.log', '-o', `-h '' -k ${dir} -p 65439`, '-w', 'start']); started = true;
  sql('supabase/tests/bootstrap_local_postgres.sql');
  query("create function auth.role() returns text language sql stable as $$select current_setting('request.jwt.claim.role',true)$$;", 'postgres');
  const migrations = readdirSync('supabase/migrations').filter(p => p.endsWith('.sql')).sort();
  for (const p of migrations) sql('supabase/migrations/' + p);
  sql(proposal); sql(editProposal); sql(registrationProposal); sql(durableProposal); sql(cardSendLedgerProposal); sql(markupProposal); sql(authorityProposal); sql(confirmationProposal); sql(confirmationDeliveryProposal); sql(confirmationSourceProposal); sql(confirmationSendProposal); sql(confirmationSendEventProposal); sql(confirmationDispatchProposal); sql(bannerProposal); query(acl, 'postgres');
  console.log(JSON.stringify({ fullLocalMigrationFiles: migrations.length, proposalApplied: true, runtimeAclDenied: true, hostedProof: false }));
  driverTest('initial');
  driverTest('callbacks');
  driverTest('banner');
  driverTest('cancellation');
  driverTest('guard');
  driverTest('authority');
  driverTest('confirmation');
  run('createdb', [...pg, 'synthetic_buttons']);
  sql('supabase/tests/content_ops_review_outbox.bootstrap.sql', 'synthetic_buttons');
  sql('supabase/migrations/20260906100000_content_ops_review_outbox.sql', 'synthetic_buttons');
  sql('supabase/migrations/20260916190000_content_ops_review_producer_binding.sql', 'synthetic_buttons');
  query(`create table auth.users(id uuid primary key);
    alter table public.content_versions add column locale text default 'ko-KR',
      add column content jsonb default '{}',add column qa jsonb default '{}',add column created_by uuid;
    alter table public.content_items add column scheduled_for timestamptz;`);
  sql(proposal, 'synthetic_buttons');
  sql(editProposal, 'synthetic_buttons');
  sql(registrationProposal, 'synthetic_buttons');
  sql(durableProposal, 'synthetic_buttons');
  sql(cardSendLedgerProposal, 'synthetic_buttons');
  sql(markupProposal, 'synthetic_buttons');
  sql(authorityProposal, 'synthetic_buttons');
  sql(confirmationProposal, 'synthetic_buttons');
  sql(confirmationDeliveryProposal, 'synthetic_buttons');
  sql(confirmationSourceProposal, 'synthetic_buttons');
  sql(confirmationSendProposal, 'synthetic_buttons');
  sql(confirmationSendEventProposal, 'synthetic_buttons');
  sql(confirmationDispatchProposal, 'synthetic_buttons');
  sql(bannerProposal, 'synthetic_buttons');
  query(acl);
  sql('supabase/tests/content_ops_button_review_state.sql', 'synthetic_buttons');
  sql('supabase/tests/content_ops_button_card_send_ledger.sql', 'synthetic_buttons');
  sql('supabase/tests/content_ops_button_edit_reply.sql', 'synthetic_buttons');

  // Persistent synthetic fixture solely for separate-connection race/restart tests.
  query(`create table public.test_button_context(r uuid,a uuid,fp text);
    do $$ declare w uuid:=private.test_content_ops_seed(); a uuid:=gen_random_uuid(); i uuid; v uuid; r uuid:=gen_random_uuid(); fp text;
    begin insert into auth.users values(a);
      select id,current_version_id into i,v from public.content_items where workspace_id=w and client_id='squid';
      insert into private.content_ops_button_reviewers values(w,'squid',a,true);
      fp:=private.content_ops_button_version_fingerprint(w,i,v);
      insert into private.content_ops_button_reviews(id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint)
        values(r,w,'squid',i,v,fp);
      insert into public.test_button_context values(r,a,fp);
    end $$;`);
  const call = action => `select private.record_content_ops_button_action(r,a,fp,'${action}',repeat('d',64)) from public.test_button_context;`;
  const results = await Promise.all(Array.from({ length: 8 }, () => concurrent(call('source_checked'))));
  const receipts = results.map(JSON.parse);
  if (receipts.filter(r => r.reused === false).length !== 1 || receipts.filter(r => r.reused === true).length !== 7) throw Error('duplicate race failed');
  if (query('select count(*) from private.content_ops_button_actions;') !== '1') throw Error('duplicate ledger rows');
  run('pg_ctl', ['-D', dir, '-m', 'fast', '-w', 'stop']); started = false;
  run('pg_ctl', ['-D', dir, '-l', dir + '/server.log', '-o', `-h '' -k ${dir} -p 65439`, '-w', 'start']); started = true;
  driverTest('restart');
  if (JSON.parse(query(call('source_checked'))).reused !== true) throw Error('restart lost idempotency');
  query(`select private.record_content_ops_button_action(r,a,fp,'hold',repeat('e',64)) from public.test_button_context;`);
  if (JSON.parse(query(call('source_checked'))).status !== 'superseded') throw Error('replay restored cleared checks');
  if (query('select (select count(*) from public.approvals)+(select count(*) from public.publications);') !== '0') throw Error('unexpected public mutation');
  console.log(JSON.stringify({ syntheticBehaviorPassed: true, concurrentClicks: 8, newActions: 1,
    reusedActions: 7, restartIdempotencyPassed: true, holdReplaySuperseded: true, approvals: 0, publications: 0 }));
  query(`create table public.test_button_edit_context as select private.test_button_edit_seed() ctx;`);
  const editCall = `select private.test_button_edit_call((ctx->>'prompt')::uuid) from public.test_button_edit_context;`;
  const edits = (await Promise.all(Array.from({ length: 8 }, () => concurrent(editCall)))).map(JSON.parse);
  if (edits.filter(r => !r.reused).length !== 1 || new Set(edits.map(r => r.content_version_id)).size !== 1) throw Error('edit race duplicated revision');
  run('pg_ctl', ['-D', dir, '-m', 'fast', '-w', 'stop']); started = false;
  run('pg_ctl', ['-D', dir, '-l', dir + '/server.log', '-o', `-h '' -k ${dir} -p 65439`, '-w', 'start']); started = true;
  if (JSON.parse(query(editCall)).reused !== true) throw Error('edit restart lost receipt');
  if (query('select (select count(*) from public.approvals)+(select count(*) from public.publications);') !== '0') throw Error('edit wrote public approval');
  console.log(JSON.stringify({ editCasesPassed: true, concurrentEditReplies: 8,
    newRevisions: 1, editRestartIdempotencyPassed: true, approvalOrPublicationRows: 0 }));
} finally {
  if (started) run('pg_ctl', ['-D', dir, '-m', 'fast', '-w', 'stop']);
  console.log(JSON.stringify({ localServerStopped: true, socketRemoved: !existsSync(dir + '/.s.PGSQL.65439') }));
}
