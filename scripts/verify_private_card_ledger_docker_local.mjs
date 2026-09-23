/** Disposable local Docker PostgreSQL. No host port, existing DB or provider I/O. */
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

if (process.argv.length !== 3 || process.argv[2] !== '--local-only'
    || !existsSync('supabase/tests/content_ops_button_card_send_ledger.sql')) {
  throw Error('explicit --local-only from repository root required');
}

const name = `coineasy-card-ledger-${randomUUID().slice(0, 12)}`;
const env = { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C' };
let started = false;
let phase = 'start';

function docker(args, { allowFailure = false } = {}) {
  const result = spawnSync('docker', args, {
    env, encoding: 'utf8', timeout: 90_000,
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
    '-e', 'POSTGRES_INITDB_ARGS=--no-locale -E UTF8', 'postgres:16.13']);
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
  sql('supabase/migrations/20260906100000_content_ops_review_outbox.sql');
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
  sql('supabase/proposals/content_ops_button_card_send_ledger.sql');
  sql('supabase/proposals/content_ops_button_card_owner_gateway.sql');
  const secondPreapply = sql('supabase/proposals/content_ops_button_card_preapply_readonly.sql',
    { allowFailure: true });
  if (secondPreapply.status === 0
      || !String(secondPreapply.stderr).includes('button_card_preapply_state_conflict')) {
    throw Error('pre-apply check accepted a partially installed owner');
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
  console.log(JSON.stringify({ localPostgres: '16.13', syntheticLedgerPassed: true,
    rolledBack: true, forceRls: true, runtimeAclDenied: true,
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
