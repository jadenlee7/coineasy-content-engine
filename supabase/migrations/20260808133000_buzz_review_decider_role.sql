-- Additive PostgREST role for the two-function Buzz review decision boundary.
-- Adoption is optional via SUPABASE_BUZZ_REVIEW_KEY; service_role remains as a
-- rollback fallback until the scoped credential is proven in production.

begin;

do $role$
declare
    review_routines constant text[] := array[
        'public.list_origintrail_buzz_review_targets(uuid,integer,bigint,text)',
        'public.record_origintrail_buzz_review_decision(uuid,uuid,text,uuid,text,text,text,bigint,text,text,text,text,text,bigint)'
    ];
    routine text;
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_buzz_review_decider'
    ) then
        create role coineasy_buzz_review_decider nologin noinherit;
    end if;
    alter role coineasy_buzz_review_decider nologin noinherit nobypassrls;
    grant usage on schema public to coineasy_buzz_review_decider;
    grant coineasy_buzz_review_decider to authenticator;

    foreach routine in array review_routines
    loop
        if to_regprocedure(routine) is null then
            raise exception 'Buzz review grant target does not exist: %', routine;
        end if;
        execute format(
            'grant execute on function %s to coineasy_buzz_review_decider',
            routine
        );
    end loop;
end;
$role$;

revoke all on table agent_runtime.buzz_review_decisions
from coineasy_buzz_review_decider;

commit;
