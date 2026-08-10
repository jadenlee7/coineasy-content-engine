-- Final least-privilege guard after the standalone text evidence predicate
-- replaces the review-item and Buzz review eligibility routines.

begin;

do $roles$
declare
    reviewer_routine constant text :=
        'public.get_agent_batch_review_item(uuid,uuid)';
    review_routines constant text[] := array[
        'public.list_origintrail_buzz_review_targets(uuid,integer,bigint,text)',
        'public.record_origintrail_buzz_review_decision(uuid,uuid,text,uuid,text,text,text,bigint,text,text,text,text,text,bigint)'
    ];
    routine text;
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_batch_reviewer'
    ) or not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_buzz_review_decider'
    ) then
        raise exception 'OriginTrail review evidence roles are missing';
    end if;
    if to_regprocedure(reviewer_routine) is null then
        raise exception 'OriginTrail reviewer routine is missing';
    end if;
    execute format(
        'grant execute on function %s to coineasy_batch_reviewer',
        reviewer_routine
    );
    foreach routine in array review_routines
    loop
        if to_regprocedure(routine) is null then
            raise exception 'OriginTrail Buzz review routine is missing: %', routine;
        end if;
        execute format(
            'grant execute on function %s to coineasy_buzz_review_decider',
            routine
        );
    end loop;
end;
$roles$;

revoke all on function private.origintrail_source_evidence_kind(uuid, uuid, uuid)
from public, anon, authenticated, service_role,
     coineasy_batch_reviewer, coineasy_buzz_review_decider;

revoke all on table public.source_items,
    private.origintrail_standalone_sources,
    private.official_x_poll_receipts,
    private.origintrail_x_article_evidence
from coineasy_batch_reviewer, coineasy_buzz_review_decider;

commit;
