/** Local-only, rollback-only signup-free proof using the real queue fixture. */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

if (process.argv.slice(2).join(' ') !== '--local-only') {
  throw Error('explicit --local-only required');
}
const { PGHOST: host, PGPORT: port, PGUSER: user, PGDATABASE: database } = process.env;
if (!/^\/(?:private\/)?tmp(?:\/[A-Za-z0-9_.-]+)*$/.test(host ?? '')
    || !/^[1-9][0-9]{0,4}$/.test(port ?? '') || Number(port) > 65535
    || !/^coineasy_[a-z0-9_]+_test$/.test(database ?? '')
    || !/^[A-Za-z_][A-Za-z0-9_-]{0,62}$/.test(user ?? '')
    || !existsSync(resolve(host, `.s.PGSQL.${port}`))) {
  throw Error('explicit disposable UNIX-socket test database required');
}
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const fixture = readFileSync(resolve(root,
  'supabase/tests/content_ops_final_queue_hosted_principals_fixture.sql'), 'utf8');
let queue = readFileSync(resolve(root,
  'supabase/tests/content_ops_final_publication_queue.sql'), 'utf8');
const authSeed = '        insert into auth.users(id) values(actor);';
if (queue.split(authSeed).length !== 2
    || !/^-- Disposable[^\n]*\nbegin;\n/.test(queue)
    || !/\nrollback;\s*$/.test(queue)
    || !queue.includes('for scenario_index in 1..23 loop')) {
  throw Error('queue fixture shape changed; review signup-free adaptation');
}
queue = queue.replace(/^-- Disposable[^\n]*\nbegin;\n/,
  () => `begin;\nselect set_config('coineasy.signup_free_fixture','local-only',true);\n${fixture}\n`);
queue = queue.replace(authSeed, `        if exists(select 1 from auth.users where id=actor) then
            raise exception 'signup_free_queue_actor_has_auth_user';
        end if;`);
queue = queue.replace(/\nrollback;\s*$/, () => `
do $proof$
begin
    if exists(select 1 from private.content_ops_review_principals p
        join auth.users u on u.id=p.id) then
        raise exception 'signup_free_queue_created_auth_user';
    end if;
    if (select count(*) from private.content_ops_final_queue_receipts)<>4
       or (select count(distinct a.client_id) from public.approvals a
           join private.content_ops_final_queue_receipts q on q.approval_id=a.id
           where a.reviewer_source='telegram_principal' and a.reviewer_id is null
             and a.review_principal_id is not null)<>4 then
        raise exception 'signup_free_queue_missing_four_client_receipts';
    end if;
    if exists(select 1 from pg_constraint c
        where c.contype='f' and c.confrelid='auth.users'::regclass
          and c.conrelid in ('private.content_ops_button_checks'::regclass,
            'private.content_ops_button_reviewers'::regclass,
            'private.content_ops_button_identities'::regclass)) then
        raise exception 'signup_free_queue_retains_auth_actor_fk';
    end if;
end $proof$;
rollback;
`);
// Deliberately omit inherited credentials, services, options and network hosts.
const env = { PATH: process.env.PATH, LANG: 'C', TZ: 'UTC', PGHOST: host,
  PGPORT: port, PGUSER: user, PGDATABASE: database, PGPASSFILE: '/dev/null',
  PGCONNECT_TIMEOUT: '3', PGAPPNAME: 'signup-free-final-queue-local-test',
  PGOPTIONS: '-c statement_timeout=30000 -c lock_timeout=5000' };
const result = spawnSync('psql', ['-X', '--no-password', '-q', '-v', 'ON_ERROR_STOP=1'],
  { env, input: queue, encoding: 'utf8', timeout: 60_000, maxBuffer: 2 * 1024 * 1024 });
if (result.error || result.status !== 0) {
  throw Error(`signup-free local fixture failed: ${String(result.stderr ?? '').slice(-1500)}`);
}
console.log(JSON.stringify({ signupFreeQueuePassed: true, scenarios: 23,
  clients: 4, privateActorFksRebound: 7, authUserForReviewerCreated: false,
  crossWorkspaceAndIdentityFkRejections: true, rolledBack: true,
  distinctEngineCallbackReleases: true, wrongOrSwappedPairRejected: true,
  pairSwitchInvalidatesCards: true, pairRollbackCannotReviveCards: true,
  expiredSourceAndPriorPairTerminalReplay: true, downstreamEngineReleasePinned: true,
  providerCalls: 0, productionCalls: 0,
  proofScope: 'local hosted-principal FK subset, not full hosted catalog or runtime identity' }));
