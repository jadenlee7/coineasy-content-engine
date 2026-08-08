-- Forward-only least-privilege refresh for the OriginTrail reviewed-media
-- evidence path. The baseline role migration may already be present in a
-- deployed database, so this migration applies only the exact new/replaced
-- routine grants after their final signatures exist.

begin;

do $grants$
declare
    producer_evidence constant regprocedure := to_regprocedure(
        'public.get_origintrail_reviewed_source_evidence(uuid,uuid,text)'
    );
    reviewer_detail constant regprocedure := to_regprocedure(
        'public.get_agent_batch_review_item(uuid,uuid)'
    );
begin
    if producer_evidence is null then
        raise exception
            'least-privilege grant target does not exist: get_origintrail_reviewed_source_evidence';
    end if;
    if reviewer_detail is null then
        raise exception
            'least-privilege grant target does not exist: get_agent_batch_review_item';
    end if;

    grant execute on function
        public.get_origintrail_reviewed_source_evidence(uuid, uuid, text)
    to coineasy_batch_producer;
    revoke execute on function
        public.get_origintrail_reviewed_source_evidence(uuid, uuid, text)
    from coineasy_batch_dispatcher,
         coineasy_batch_reviewer,
         coineasy_buzz_delivery;

    -- CREATE OR REPLACE retains same-signature ACLs, but repeat the reviewed
    -- role boundary explicitly so this migration remains safe if the database
    -- implementation changes or the function is recreated during recovery.
    grant execute on function public.get_agent_batch_review_item(uuid, uuid)
    to coineasy_batch_reviewer;
    revoke execute on function public.get_agent_batch_review_item(uuid, uuid)
    from coineasy_batch_dispatcher,
         coineasy_batch_producer,
         coineasy_buzz_delivery;
end;
$grants$;

commit;
