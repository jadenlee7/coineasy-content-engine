-- LOCAL PROPOSAL ONLY. Do not apply without hosted schema/ACL compatibility,
-- exact-release deployment and separate operator authorization. This is a
-- narrow PostgREST bridge for the existing Netlify review gateway; it does
-- not create a direct DB login, schedule, approval, publisher or provider call.
-- Requires content_ops_button_card_send_ledger.sql in the same candidate pack.
begin;

create function public.content_ops_button_card_owner_step(
    target_workspace_id uuid, target_content_version_id uuid,
    target_action text, target_args jsonb
) returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare
    allowed text[];
    review private.content_ops_button_reviews;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'button_card_owner_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null or target_content_version_id is null
       or target_action is null or target_args is null then
        raise exception 'button_card_owner_arguments_invalid' using errcode = '22023';
    end if;
    allowed := case target_action
        when 'prepare' then array['outbox_id','claim_token','review_id']
        when 'bind' then array['review_id','outbox_id','claim_token','packet_sha256']
        when 'reserve' then array['review_id','card_id','part_index','payload_sha256']
        when 'confirm' then array['review_id','card_id','part_index','payload_sha256',
            'message_id','message_binding','response_sha256','observed_at']
        when 'register' then array['review_id','card_id','expected_fingerprint',
            'epoch','bindings','parts','controls_payload_sha256',
            'response_sha256s','delivered','expires']
        when 'terminal' then array['review_id','card_id','outbox_id']
        else null end;
    if allowed is null or jsonb_typeof(target_args) is distinct from 'object'
       or not target_args ?& allowed or target_args - allowed <> '{}'::jsonb then
        raise exception 'button_card_owner_action_denied' using errcode = '22023';
    end if;
    if target_action <> 'prepare' then
        select * into review from private.content_ops_button_reviews
            where id = (target_args->>'review_id')::uuid;
        if not found or review.workspace_id is distinct from target_workspace_id
           or review.content_version_id is distinct from target_content_version_id then
            raise exception 'button_card_owner_scope_denied' using errcode = '23514';
        end if;
    end if;

    case target_action
        when 'prepare' then
            return private.prepare_content_ops_button_review_from_claim(
                target_workspace_id, (target_args->>'outbox_id')::uuid,
                (target_args->>'claim_token')::uuid, target_content_version_id,
                (target_args->>'review_id')::uuid);
        when 'bind' then
            return private.bind_content_ops_button_card_outbox(
                (target_args->>'review_id')::uuid,
                (target_args->>'outbox_id')::uuid,
                (target_args->>'claim_token')::uuid,
                target_args->>'packet_sha256');
        when 'reserve' then
            return private.reserve_content_ops_button_card_send(
                (target_args->>'review_id')::uuid,
                (target_args->>'card_id')::uuid,
                (target_args->>'part_index')::smallint,
                target_args->>'payload_sha256');
        when 'confirm' then
            return private.confirm_content_ops_button_card_send(
                (target_args->>'review_id')::uuid,
                (target_args->>'card_id')::uuid,
                (target_args->>'part_index')::smallint,
                target_args->>'payload_sha256',
                (target_args->>'message_id')::bigint,
                target_args->>'message_binding',
                target_args->>'response_sha256',
                (target_args->>'observed_at')::timestamptz);
        when 'register' then
            return private.register_content_ops_button_card_from_sends(
                (target_args->>'review_id')::uuid,
                (target_args->>'card_id')::uuid,
                target_args->>'expected_fingerprint',
                (target_args->>'epoch')::bigint,
                target_args->'bindings', target_args->'parts',
                target_args->>'controls_payload_sha256',
                target_args->'response_sha256s',
                (target_args->>'delivered')::timestamptz,
                (target_args->>'expires')::timestamptz);
        when 'terminal' then
            return private.read_content_ops_button_card_terminal(
                (target_args->>'review_id')::uuid,
                (target_args->>'card_id')::uuid,
                (target_args->>'outbox_id')::uuid);
    end case;
    raise exception 'button_card_owner_action_denied' using errcode = '22023';
end $$;

revoke all on function public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)
    from public, anon, authenticated, service_role;
grant execute on function public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)
    to service_role;
commit;
