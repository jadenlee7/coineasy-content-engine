-- Local-only schema/ACL admission regression. No fixture or provider writes.
begin;
do $$
declare bad text[]; client text;
begin
    foreach client in array array['yellow','origintrail','squid','babylon'] loop
        if not private.exact_telegram_allowed_client_subset(array[client]) then
            raise exception 'canonical_single_client_rejected';
        end if;
    end loop;
    if not private.exact_telegram_allowed_client_subset(array['yellow','origintrail','squid','babylon'])
       or private.exact_telegram_allowed_client_subset(null)
       or private.exact_telegram_allowed_client_subset(array[]::text[])
       or private.exact_telegram_allowed_client_subset(array['squid','yellow'])
       or private.exact_telegram_allowed_client_subset(array['yellow','yellow'])
       or private.exact_telegram_allowed_client_subset(array['yellow',null])
       or private.exact_telegram_allowed_client_subset(array['all']) then
        raise exception 'canonical_subset_boundary_broken';
    end if;
    begin
        perform public.claim_exact_telegram_publication_job_v2(
            gen_random_uuid(),'synthetic-only-worker',180,array['all']);
        raise exception 'noncanonical_subset_reached_selector';
    exception when invalid_parameter_value then null;end;
end $$;
do $$
declare role_name text; signature text;
begin
    foreach signature in array array[
        'private.exact_telegram_allowed_client_subset(text[])',
        'private.require_content_ops_exact_telegram_handoff(uuid,uuid,uuid)',
        'private.claim_exact_telegram_job_v2_before_double_fact_check(uuid,text,integer,text[])',
        'public.claim_exact_telegram_publication_job_v2(uuid,text,integer,text[])'
    ] loop
        foreach role_name in array array['anon','authenticated','service_role'] loop
            if has_function_privilege(role_name,signature,'EXECUTE') then
                raise exception 'new_telegram_proposal_rpc_role_grant_leak';
            end if;
        end loop;
        if exists(select 1 from pg_proc p, lateral aclexplode(coalesce(p.proacl,
            acldefault('f',p.proowner))) acl where p.oid=signature::regprocedure
            and acl.grantee=0 and acl.privilege_type='EXECUTE') then
            raise exception 'new_telegram_proposal_rpc_public_grant_leak';
        end if;
    end loop;
end $$;
rollback;
