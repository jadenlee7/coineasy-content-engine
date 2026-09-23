/** Disposable local Docker PostgreSQL. No host port, existing DB or provider I/O. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { buildAtomicProducerBindingSql, CORRECTED_BODY_SHA256 } from
  '../ops/private-review-producer-binding/atomic-apply-sql.mjs';

if (process.argv.length !== 3
    || !['--local-only', '--local-postgres17'].includes(process.argv[2])
    || !existsSync('supabase/tests/content_ops_button_card_send_ledger.sql')) {
  throw Error('explicit local-only mode from repository root required');
}
const postgresVersion = process.argv[2] === '--local-postgres17' ? '17.6' : '16.13';

const name = `coineasy-card-ledger-${randomUUID().slice(0, 12)}`;
const env = { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C' };
let started = false;
let phase = 'start';

function docker(args, { allowFailure = false, input } = {}) {
  const result = spawnSync('docker', args, {
    env, encoding: 'utf8', timeout: 90_000, input,
  });
  if (!allowFailure && result.status !== 0) {
    const detail = String(result.stderr ?? '').slice(-1600);
    throw Error(`disposable SQL verification failed at ${phase}: ${detail}`);
  }
  return result;
}

function sql(file, { allowFailure = false } = {}) {
  phase = file;
  return docker(['exec', '-u', 'postgres', name, 'psql', '-X', '-q',
    '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-U', 'postgres',
    '-d', 'postgres', '-f', `/repo/${file}`], { allowFailure });
}

function sqlScalar(file) {
  phase = file;
  return docker(['exec', '-u', 'postgres', name, 'psql', '-X', '-qAt',
    '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-U', 'postgres',
    '-d', 'postgres', '-f', `/repo/${file}`]);
}

function sqlIn(database, file, { allowFailure = false } = {}) {
  phase = file;
  return docker(['exec', '-u', 'postgres', name, 'psql', '-X', '-q',
    '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-U', 'postgres',
    '-d', database, '-f', `/repo/${file}`], { allowFailure });
}

function sqlTextIn(database, statement, { allowFailure = false } = {}) {
  phase = 'isolated atomic producer apply';
  return docker(['exec', '-i', '-u', 'postgres', name, 'psql', '-X', '-qAt',
    '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-U', 'postgres',
    '-d', database], { allowFailure, input: statement });
}

function query(statement) {
  phase = 'bounded verification query';
  return docker(['exec', '-u', 'postgres', name, 'psql', '-X', '-qAt',
    '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-U', 'postgres',
    '-d', 'postgres', '-c', statement]).stdout.trim();
}

try {
  const mount = `type=bind,source=${process.cwd()},target=/repo,readonly`;
  docker(['run', '--detach', '--rm', '--network', 'none', '--name', name,
    '--mount', mount, '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
    '-e', 'POSTGRES_INITDB_ARGS=--no-locale -E UTF8', `postgres:${postgresVersion}`]);
  started = true;
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    const logs = docker(['logs', name], { allowFailure: true });
    const check = docker(['exec', '-u', 'postgres', name, 'pg_isready',
      '-h', '/var/run/postgresql', '-U', 'postgres'], { allowFailure: true });
    // The image briefly accepts connections on its bootstrap server, then
    // shuts that server down. Wait for the final handoff before applying SQL.
    if (logs.status === 0 &&
        (logs.stdout + logs.stderr).includes('PostgreSQL init process complete')
        && check.status === 0) { ready = true; break; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!ready) throw Error('disposable PostgreSQL readiness unknown');

  sql('supabase/tests/content_ops_review_outbox.bootstrap.sql');
  query(`create schema supabase_migrations;
    create table supabase_migrations.schema_migrations(
      version text primary key,statements text[],name text,created_by text,
      idempotency_key text unique,rollback text[])`);
  sql('supabase/migrations/20260906100000_content_ops_review_outbox.sql');
  const legacyCandidateHash = query(`select encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
    from pg_proc p where p.oid=
      'private.content_ops_review_candidate(uuid,uuid,uuid)'::regprocedure`);
  if (legacyCandidateHash !== '5de6d755095e63f3f7e03eb9f53db5d7fdada0911e222237fd459f12eaa98ffb') {
    throw Error('local legacy candidate body does not match hosted readback');
  }
  const legacyContract = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql');
  if (!/"producer_binding_contract"\s*:\s*"legacy_no_history"/.test(legacyContract.stdout)) {
    throw Error('producer-binding pre-apply contract did not classify legacy body');
  }
  query('alter table supabase_migrations.schema_migrations add column unsafe_required text not null');
  const mandatoryHistoryColumn = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql',
    { allowFailure: true });
  if (mandatoryHistoryColumn.status === 0
      || !String(mandatoryHistoryColumn.stderr).includes('producer_binding_contract_history_column_mismatch')) {
    throw Error('producer-binding contract accepted an unfillable history column');
  }
  query('alter table supabase_migrations.schema_migrations drop column unsafe_required');
  query(`insert into supabase_migrations.schema_migrations(version,name,statements)
    values('20260916190000','content_ops_review_producer_binding',array[
      pg_read_file('/repo/supabase/migrations/20260916190000_content_ops_review_producer_binding.sql')])`);
  const legacyWithHistory = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql',
    { allowFailure: true });
  if (legacyWithHistory.status === 0
      || !String(legacyWithHistory.stderr).includes('producer_binding_contract_history_mismatch')) {
    throw Error('producer-binding contract accepted legacy body with applied history');
  }
  query("delete from supabase_migrations.schema_migrations where version='20260916190000'");
  query('alter table public.jobs alter column content_item_id set not null');
  const nonnullableJobLink = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql',
    { allowFailure: true });
  if (nonnullableJobLink.status === 0
      || !String(nonnullableJobLink.stderr).includes('producer_binding_contract_job_column_mismatch')) {
    throw Error('producer-binding contract accepted a nonnullable job link');
  }
  query('alter table public.jobs alter column content_item_id drop not null');
  const legacyCandidate = sql(
    'supabase/proposals/content_ops_button_card_preapply_readonly.sql',
    { allowFailure: true });
  if (legacyCandidate.status === 0
      || !String(legacyCandidate.stderr).includes('button_card_preapply_function_contract_mismatch')) {
    throw Error('card pre-apply accepted the hosted legacy candidate function');
  }
  const remaining = sqlScalar(
    'supabase/proposals/content_ops_button_card_remaining_preapply_readonly.sql');
  assert.deepEqual(JSON.parse(remaining.stdout), {
    changes: 0, overall_ready: false, producer_binding_checked: false,
    provider_calls: 0, read_only: true, remaining_preapply_contract: 'pass',
  });
  query('revoke execute on function public.content_ops_claim_review(uuid,uuid,uuid) from service_role');
  const missingBaseGrant = sql(
    'supabase/proposals/content_ops_button_card_remaining_preapply_readonly.sql',
    { allowFailure: true });
  if (missingBaseGrant.status === 0
      || !String(missingBaseGrant.stderr).includes('button_card_remaining_base_rpc_acl_mismatch')) {
    throw Error('remaining pre-apply accepted a missing base RPC grant');
  }
  query('grant execute on function public.content_ops_claim_review(uuid,uuid,uuid) to service_role');
  sql('supabase/migrations/20260916190000_content_ops_review_producer_binding.sql');
  const absentHistory = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql',
    { allowFailure: true });
  if (absentHistory.status === 0
      || !String(absentHistory.stderr).includes('producer_binding_contract_history_mismatch')) {
    throw Error('producer-binding contract accepted an unregistered correction');
  }
  query(`insert into supabase_migrations.schema_migrations(version,name,statements)
    values('20260916190000','content_ops_review_producer_binding',array['tampered'])`);
  const wrongHistory = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql',
    { allowFailure: true });
  if (wrongHistory.status === 0
      || !String(wrongHistory.stderr).includes('producer_binding_contract_history_mismatch')) {
    throw Error('producer-binding contract accepted mismatched history bytes');
  }
  query(`update supabase_migrations.schema_migrations set statements=array[
    pg_read_file('/repo/supabase/migrations/20260916190000_content_ops_review_producer_binding.sql')]
    where version='20260916190000'`);
  const correctedContract = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql');
  if (!/"producer_binding_contract"\s*:\s*"corrected_exact_history"/.test(correctedContract.stdout)) {
    throw Error('producer-binding post-apply contract did not classify corrected body');
  }
  query('create database synthetic_atomic_producer');
  sqlIn('synthetic_atomic_producer', 'supabase/tests/content_ops_review_outbox.bootstrap.sql');
  sqlTextIn('synthetic_atomic_producer', `create schema supabase_migrations;
    create table supabase_migrations.schema_migrations(
      version text primary key,statements text[],name text,created_by text,
      idempotency_key text unique,rollback text[]);`);
  sqlIn('synthetic_atomic_producer',
    'supabase/migrations/20260906100000_content_ops_review_outbox.sql');
  const atomicBefore = sqlIn('synthetic_atomic_producer',
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql');
  if (!/"producer_binding_contract"\s*:\s*"legacy_no_history"/.test(atomicBefore.stdout)) {
    throw Error('atomic fixture was not at exact legacy/no-history state');
  }
  const atomicSource = readFileSync(
    'supabase/migrations/20260916190000_content_ops_review_producer_binding.sql', 'utf8');
  const atomicSql = buildAtomicProducerBindingSql(atomicSource);
  sqlTextIn('synthetic_atomic_producer',
    'alter table supabase_migrations.schema_migrations add column unsafe_required text not null;');
  const unsafeHistoryApply = sqlTextIn('synthetic_atomic_producer', atomicSql,
    { allowFailure: true });
  if (unsafeHistoryApply.status === 0
      || !String(unsafeHistoryApply.stderr).includes('producer_binding_apply_precondition_mismatch')) {
    throw Error('atomic apply accepted an unfillable history column');
  }
  sqlTextIn('synthetic_atomic_producer',
    'alter table supabase_migrations.schema_migrations drop column unsafe_required;');
  const brokenHistorySql = atomicSql.replace(
    'insert into supabase_migrations.schema_migrations(version,name,statements)',
    'insert into supabase_migrations.absent_table(version,name,statements)');
  if (brokenHistorySql === atomicSql) throw Error('atomic history failure fixture missing');
  const brokenHistory = sqlTextIn('synthetic_atomic_producer', brokenHistorySql,
    { allowFailure: true });
  if (brokenHistory.status === 0) {
    throw Error('atomic fixture accepted a failed history registration');
  }
  const afterFailedHistory = sqlIn('synthetic_atomic_producer',
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql');
  if (!/"producer_binding_contract"\s*:\s*"legacy_no_history"/.test(afterFailedHistory.stdout)) {
    throw Error('failed history registration did not roll back function replacement');
  }
  const brokenPostSql = atomicSql.replace(CORRECTED_BODY_SHA256, '0'.repeat(64));
  if (brokenPostSql === atomicSql) throw Error('atomic postcondition failure fixture missing');
  const brokenPost = sqlTextIn('synthetic_atomic_producer', brokenPostSql,
    { allowFailure: true });
  if (brokenPost.status === 0
      || !String(brokenPost.stderr).includes('producer_binding_apply_postcondition_mismatch')) {
    throw Error('atomic fixture accepted a failed postcondition');
  }
  const afterFailedPost = sqlIn('synthetic_atomic_producer',
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql');
  if (!/"producer_binding_contract"\s*:\s*"legacy_no_history"/.test(afterFailedPost.stdout)) {
    throw Error('failed postcondition did not roll back function and history');
  }
  const atomicApplied = sqlTextIn('synthetic_atomic_producer', atomicSql);
  if (!/"producer_binding_apply"\s*:\s*"committed"/.test(atomicApplied.stdout)) {
    throw Error('atomic fixture lacked a committed one-migration receipt');
  }
  const atomicAfter = sqlIn('synthetic_atomic_producer',
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql');
  if (!/"producer_binding_contract"\s*:\s*"corrected_exact_history"/.test(atomicAfter.stdout)) {
    throw Error('atomic fixture lacked exact corrected function and history');
  }
  const atomicReplay = sqlTextIn('synthetic_atomic_producer', atomicSql,
    { allowFailure: true });
  if (atomicReplay.status === 0
      || !String(atomicReplay.stderr).includes('producer_binding_apply_precondition_mismatch')) {
    throw Error('atomic producer apply accepted a second execution');
  }
  query(`create or replace function private.content_ops_review_candidate(
    target_workspace_id uuid, target_content_item_id uuid,
    target_content_version_id uuid) returns jsonb language plpgsql volatile security definer
    set search_path='' as $$begin return null; end$$`);
  const driftedContract = sql(
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql',
    { allowFailure: true });
  if (driftedContract.status === 0
      || !String(driftedContract.stderr).includes('producer_binding_contract_function_mismatch')) {
    throw Error('producer-binding contract accepted an unknown function body');
  }
  const driftedCandidate = sql(
    'supabase/proposals/content_ops_button_card_preapply_readonly.sql',
    { allowFailure: true });
  if (driftedCandidate.status === 0
      || !String(driftedCandidate.stderr).includes('button_card_preapply_function_contract_mismatch')) {
    throw Error('card pre-apply accepted a drifted candidate function body');
  }
  sql('supabase/migrations/20260916190000_content_ops_review_producer_binding.sql');
  sql('supabase/proposals/content_ops_button_card_preapply_readonly.sql');
  query(`create table auth.users(id uuid primary key);
    alter table public.content_versions add column locale text default 'ko-KR',
      add column content jsonb default '{}', add column qa jsonb default '{}',
      add column created_by uuid;
    alter table public.content_items add column scheduled_for timestamptz;`);
  sql('supabase/proposals/content_ops_button_review_state.sql');
  sql('supabase/proposals/content_ops_button_edit_reply.sql');
  sql('supabase/proposals/content_ops_button_prompt_registration.sql');
  sql('supabase/proposals/content_ops_button_durable_attempt.sql');
  sql('supabase/proposals/content_ops_banner_revision.sql');
  query('create role coineasy_private_review login; grant usage on schema private to coineasy_private_review;');
  sql('supabase/proposals/content_ops_button_card_send_ledger.sql');
  sql('supabase/proposals/content_ops_button_card_owner_gateway.sql');
  const secondPreapply = sql('supabase/proposals/content_ops_button_card_preapply_readonly.sql',
    { allowFailure: true });
  if (secondPreapply.status === 0
      || !String(secondPreapply.stderr).includes('button_card_preapply_state_conflict')) {
    throw Error('pre-apply check accepted a partially installed owner');
  }
  const remainingInstalled = sql(
    'supabase/proposals/content_ops_button_card_remaining_preapply_readonly.sql',
    { allowFailure: true });
  if (remainingInstalled.status === 0
      || !String(remainingInstalled.stderr).includes('button_card_remaining_partial_installation')) {
    throw Error('remaining pre-apply accepted a partially installed owner');
  }
  sql('supabase/proposals/content_ops_button_card_readonly_preflight.sql');
  query(`do $$ declare r text; f text; begin
    foreach r in array array['anon','authenticated','service_role'] loop
      if has_table_privilege(r,'private.content_ops_button_card_send_attempts',
          'SELECT,INSERT,UPDATE,DELETE') then
        raise exception 'runtime ledger ACL leaked';
      end if;
      if has_table_privilege(r,'private.content_ops_button_card_outbox_owners',
          'SELECT,INSERT,UPDATE,DELETE') then
        raise exception 'runtime outbox-owner ACL leaked';
      end if;
      foreach f in array array[
        'private.prepare_content_ops_button_review_from_claim(uuid,uuid,uuid,uuid,uuid)',
        'private.bind_content_ops_button_card_outbox(uuid,uuid,uuid,text)',
        'private.content_ops_button_card_outbox_owned(uuid)',
        'private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text)',
        'private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz)',
        'private.register_content_ops_button_card_from_sends(uuid,uuid,text,bigint,jsonb,jsonb,text,jsonb,timestamptz,timestamptz)',
        'private.read_content_ops_button_card_terminal(uuid,uuid,uuid)'
      ] loop
        if has_function_privilege(r,f,'EXECUTE') then
          raise exception 'runtime card-send function ACL leaked';
        end if;
      end loop;
    end loop;
    if has_function_privilege('anon',
        'public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)','EXECUTE')
       or has_function_privilege('authenticated',
        'public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)','EXECUTE')
       or not has_function_privilege('service_role',
        'public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)','EXECUTE') then
      raise exception 'image locator ACL mismatch';
    end if;
    if has_function_privilege('anon',
        'public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)','EXECUTE')
       or has_function_privilege('authenticated',
        'public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)','EXECUTE')
       or not has_function_privilege('service_role',
        'public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)','EXECUTE') then
      raise exception 'owner gateway ACL mismatch';
    end if;
  end $$;`);
  sql('supabase/tests/content_ops_button_card_send_ledger.sql');
  const readback = query(`select jsonb_build_object(
    'ledger_rows',(select count(*) from private.content_ops_button_card_send_attempts),
    'owner_rows',(select count(*) from private.content_ops_button_card_outbox_owners),
    'card_rows',(select count(*) from private.content_ops_button_cards),
    'approvals',(select count(*) from public.approvals),
    'publications',(select count(*) from public.publications),
    'force_rls',(select relforcerowsecurity from pg_class where oid=
      'private.content_ops_button_card_send_attempts'::regclass))`);
  const parsed = JSON.parse(readback);
  if (parsed.ledger_rows !== 0 || parsed.owner_rows !== 0 || parsed.card_rows !== 0
      || parsed.approvals !== 0 || parsed.publications !== 0
      || parsed.force_rls !== true) {
    throw Error('disposable SQL rollback or access guard failed');
  }
  sql('supabase/tests/content_ops_button_principal_rebind_fixture.sql');
  query('alter table private.content_ops_button_identities drop constraint synthetic_review_principal_identity');
  const missingPrincipalScope = sql(
    'supabase/proposals/content_ops_button_prompt_preapply_readonly.sql',
    { allowFailure: true });
  if (missingPrincipalScope.status === 0
      || !String(missingPrincipalScope.stderr).includes('prompt_preapply_principal_scope_missing')) {
    throw Error('prompt pre-apply accepted an incomplete principal lineage');
  }
  query(`alter table private.content_ops_button_identities
    add constraint synthetic_review_principal_identity
    foreign key(workspace_id,bot_binding,human_binding,actor_id)
    references private.content_ops_review_principals(
      workspace_id,bot_binding,human_binding,id)`);
  query('revoke select on private.content_ops_button_prompt_attempts from coineasy_private_review');
  const missingReadAcl = sql(
    'supabase/proposals/content_ops_button_prompt_preapply_readonly.sql',
    { allowFailure: true });
  if (missingReadAcl.status === 0
      || !String(missingReadAcl.stderr).includes('prompt_preapply_runtime_read_acl')) {
    throw Error('prompt pre-apply accepted a missing runtime read grant');
  }
  query('grant select on private.content_ops_button_prompt_attempts to coineasy_private_review');
  sql('supabase/proposals/content_ops_button_prompt_preapply_readonly.sql');
  sql('supabase/proposals/content_ops_button_prompt_runtime_capability.sql');
  const promptPostapply = sqlScalar(
    'supabase/proposals/content_ops_button_prompt_postapply_readonly.sql');
  assert.deepEqual(JSON.parse(promptPostapply.stdout), {
    catalog_only: true, changes: 0, hosted_runtime_verified: false,
    prompt_runtime_postapply: 'pass', provider_calls: 0, read_only: true,
  });
  query(`do $$ begin
    if not has_function_privilege('coineasy_private_review',
        'private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text)', 'EXECUTE')
       or not has_function_privilege('coineasy_private_review',
        'private.register_content_ops_button_prompt_response_for_runtime(uuid,text,text,text,text,timestamptz)', 'EXECUTE')
       or has_function_privilege('coineasy_private_review',
        'private.reserve_content_ops_button_prompt_attempt(uuid,uuid,uuid,text,text)', 'EXECUTE')
       or has_function_privilege('coineasy_private_review',
        'private.register_content_ops_button_edit_prompt(uuid,text)', 'EXECUTE')
       or has_table_privilege('coineasy_private_review',
        'private.content_ops_button_prompt_attempts', 'INSERT')
       or has_table_privilege('coineasy_private_review',
        'private.content_ops_button_prompt_receipts', 'INSERT')
       or has_table_privilege('coineasy_private_review',
        'private.content_ops_button_edit_prompts', 'INSERT') then
      raise exception 'prompt runtime capability ACL mismatch';
    end if;
    if exists (
      select 1 from pg_proc p
      where p.oid in (
        'private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text)'::regprocedure,
        'private.register_content_ops_button_prompt_response_for_runtime(uuid,text,text,text,text,timestamptz)'::regprocedure)
        and (not p.prosecdef or not coalesce(
          p.proconfig @> array['search_path=""']::text[], false))
    ) then
      raise exception 'prompt runtime capability owner boundary mismatch';
    end if;
    if has_function_privilege('anon',
        'private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text)', 'EXECUTE')
       or has_function_privilege('authenticated',
        'private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text)', 'EXECUTE')
       or has_function_privilege('service_role',
        'private.register_content_ops_button_prompt_response_for_runtime(uuid,text,text,text,text,timestamptz)', 'EXECUTE') then
      raise exception 'prompt runtime capability broad grant';
    end if;
    begin
      perform private.reserve_content_ops_button_prompt_for_runtime(null,null,null,null,null);
      raise exception 'prompt runtime accepted wrong session owner';
    exception when insufficient_privilege then null; end;
  end $$;`);
  const promptSecondPreapply = sql(
    'supabase/proposals/content_ops_button_prompt_preapply_readonly.sql',
    { allowFailure: true });
  if (promptSecondPreapply.status === 0
      || !String(promptSecondPreapply.stderr).includes('prompt_preapply_partial_install')) {
    throw Error('prompt pre-apply check accepted a partially installed capability');
  }
  sql('supabase/tests/content_ops_button_prompt_runtime_capability.sql');
  query(`revoke execute on function
    private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text)
    from coineasy_private_review`);
  const missingPromptGrant = sql(
    'supabase/proposals/content_ops_button_prompt_postapply_readonly.sql',
    { allowFailure: true });
  if (missingPromptGrant.status === 0
      || !String(missingPromptGrant.stderr).includes('prompt_postapply_wrapper_acl_mismatch')) {
    throw Error('prompt post-apply accepted a missing runtime grant');
  }
  query(`grant execute on function
    private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text)
    to coineasy_private_review`);
  query(`create or replace function private.reserve_content_ops_button_prompt_for_runtime(
    target_card_id uuid,target_attempt_id uuid,target_actor_id uuid,
    verified_human_binding text,target_action_key text)
    returns jsonb language plpgsql volatile
    security definer set search_path='' as $$ begin return '{}'::jsonb; end $$`);
  const changedPromptBody = sql(
    'supabase/proposals/content_ops_button_prompt_postapply_readonly.sql',
    { allowFailure: true });
  if (changedPromptBody.status === 0
      || !String(changedPromptBody.stderr).includes('prompt_postapply_function_contract_mismatch')) {
    throw Error('prompt post-apply accepted a changed wrapper body');
  }
  console.log(JSON.stringify({ localPostgres: postgresVersion, syntheticLedgerPassed: true,
    rolledBack: true, forceRls: true, runtimeAclDenied: true,
    promptCapabilityAclVerified: true, promptRuntimeExecuted: true,
    promptPreapplyCatalogVerified: true,
    promptPostapplyCatalogVerified: true,
    producerBindingContractVerified: true,
    atomicProducerMigrationVerified: true,
    remainingPreapplyVerified: true,
    syntheticSignupFreePrincipal: true,
    providerCalls: 0, productionCalls: 0 }));
} finally {
  if (started) docker(['stop', '--time', '1', name], { allowFailure: true });
  let gone = !started;
  for (let attempt = 0; started && attempt < 30; attempt++) {
    gone = docker(['inspect', name], { allowFailure: true }).status !== 0;
    if (gone) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  console.log(JSON.stringify({ disposableContainerRemoved: gone }));
  if (!gone) throw Error('disposable SQL container cleanup unverified');
}
