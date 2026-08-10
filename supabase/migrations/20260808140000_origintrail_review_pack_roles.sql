-- Final least-privilege delta for the review-pack delivery claim. The V1 claim
-- remains available as the feature-flag rollback path; V1 cannot create a V2
-- review target because it leaves attachment_sha256 null.

begin;

do $roles$
begin
    if to_regprocedure(
        'public.claim_origintrail_buzz_delivery_v2(uuid,uuid,text,uuid,text,text,text,text,integer)'
    ) is null then
        raise exception 'review-pack delivery claim signature is missing';
    end if;
    grant execute on function public.claim_origintrail_buzz_delivery_v2(
        uuid, uuid, text, uuid, text, text, text, text, integer
    ) to coineasy_buzz_delivery;
end;
$roles$;

commit;
