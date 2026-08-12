-- Final least-privilege grant for the decision scanner and durable Buzz
-- acknowledgement outbox.  The scoped role receives RPC execution only; it
-- never receives table, provider, Batch, publication, or relay credentials.

begin;

do $role$
declare
    review_routines constant text[] := array[
        'public.list_origintrail_buzz_review_targets(uuid,integer,bigint,text)',
        'public.record_origintrail_buzz_review_decision(uuid,uuid,text,uuid,text,text,text,bigint,text,text,text,text,text,bigint)',
        'public.record_origintrail_buzz_review_decision_with_ack(uuid,uuid,text,uuid,text,text,text,bigint,text,text,text,text,text,bigint)',
        'public.claim_origintrail_buzz_review_ack(uuid,uuid,text,integer)',
        'public.mark_origintrail_buzz_review_ack_attempt(uuid,uuid,text,text,text)',
        'public.complete_origintrail_buzz_review_ack(uuid,uuid,text,text,text,boolean)',
        'public.fail_origintrail_buzz_review_ack(uuid,uuid,text,text,boolean)',
        'public.reconcile_origintrail_buzz_review_ack_leases(uuid,integer)',
        'public.list_origintrail_buzz_review_ack_unknown(uuid,integer)'
    ];
    routine text;
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_buzz_review_decider'
    ) then
        raise exception 'Buzz review decider role is missing';
    end if;
    alter role coineasy_buzz_review_decider nologin noinherit nobypassrls;
    grant usage on schema public to coineasy_buzz_review_decider;
    grant coineasy_buzz_review_decider to authenticator;

    foreach routine in array review_routines
    loop
        if to_regprocedure(routine) is null then
            raise exception 'Buzz review acknowledgement grant target is missing: %',
                routine;
        end if;
        execute format(
            'grant execute on function %s to coineasy_buzz_review_decider',
            routine
        );
    end loop;
end;
$role$;

revoke all on table agent_runtime.buzz_review_decisions,
    agent_runtime.buzz_review_ack_receipts
from coineasy_buzz_review_decider;

revoke all on function private.origintrail_buzz_review_ack_message(text)
from coineasy_buzz_review_decider;
revoke all on function private.origintrail_buzz_review_ack_message_sha256(text)
from coineasy_buzz_review_decider;
revoke all on function private.origintrail_buzz_review_ack_object(
    uuid, uuid, boolean, boolean
) from coineasy_buzz_review_decider;

commit;
