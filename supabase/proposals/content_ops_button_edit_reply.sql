-- LOCAL ONLY; schema starts after content_ops_button_review_state.sql.
-- Runtime calls also require the registration and durable-attempt proposals.
-- Missing downstream guard fails closed. No runtime grants.
-- Identity/prompt registrations may ONLY be populated by the trusted owner
-- after actual identity and prompt-delivery verification; tests use fixtures.
begin;
create table private.content_ops_button_identities (
    workspace_id uuid not null references public.workspaces(id),
    bot_binding text not null check (bot_binding ~ '^[a-f0-9]{64}$'),
    human_binding text not null check (human_binding ~ '^[a-f0-9]{64}$'),
    actor_id uuid not null references auth.users(id),
    active boolean not null default false,
    primary key (workspace_id,bot_binding,human_binding),
    unique(workspace_id,bot_binding,actor_id)
);

create table private.content_ops_button_edit_prompts (
    id uuid primary key default gen_random_uuid(),
    review_id uuid not null references private.content_ops_button_reviews(id),
    actor_id uuid not null references auth.users(id),
    epoch bigint not null,
    edit_action_key text not null,
    bot_binding text not null check (bot_binding ~ '^[a-f0-9]{64}$'),
    room_binding text not null check (room_binding ~ '^[a-f0-9]{64}$'),
    message_binding text not null check (message_binding ~ '^[a-f0-9]{64}$'),
    -- Owner-recorded positive prompt receipt hash, not caller-supplied consent.
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    delivered_at timestamptz not null,
    expires_at timestamptz not null,
    consumed_key text check (consumed_key ~ '^[a-f0-9]{64}$'),
    reply_sha256 text check (reply_sha256 ~ '^[a-f0-9]{64}$'),
    new_version_id uuid references public.content_versions(id),
    check (expires_at > delivered_at and expires_at <= delivered_at + interval '30 minutes'),
    check ((consumed_key is null and reply_sha256 is null and new_version_id is null)
        or (consumed_key is not null and reply_sha256 is not null and new_version_id is not null)),
    unique(bot_binding,room_binding,message_binding),
    unique(review_id,epoch),
    foreign key(review_id,edit_action_key)
        references private.content_ops_button_actions(review_id,idempotency_key)
);
alter table private.content_ops_button_identities enable row level security;
alter table private.content_ops_button_edit_prompts enable row level security;
revoke all on private.content_ops_button_identities,private.content_ops_button_edit_prompts
    from public,anon,authenticated,service_role;

create function private.save_content_ops_button_edit_reply(
    target_prompt_id uuid, verified_bot_binding text, verified_room_binding text,
    verified_message_binding text, verified_human_binding text,
    replacement_text text, operation_key text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    p private.content_ops_button_edit_prompts;
    r private.content_ops_button_reviews;
    act private.content_ops_button_actions;
    identity_actor uuid;
    item public.content_items;
    old_version public.content_versions;
    new_id uuid;
    new_number integer;
    reply_hash text;
    copy_key text;
    max_units integer;
    units integer;
    binding text;
    observed timestamptz;
begin
    if target_prompt_id is null or operation_key is null or operation_key !~ '^[a-f0-9]{64}$' then
        raise exception 'button_edit_arguments_invalid' using errcode='22023';end if;
    foreach binding in array array[verified_bot_binding,verified_room_binding,verified_message_binding,verified_human_binding] loop
        if binding is null or binding !~ '^[a-f0-9]{64}$' then
            raise exception 'button_edit_arguments_invalid' using errcode='22023';end if;
    end loop;
    if replacement_text is null or char_length(replacement_text)>3700
        or replacement_text !~ '[^[:space:]]'
        or replacement_text ~* '(t[.]me/(\+|joinchat/)|api[.]telegram[.]org/bot|[0-9]{5,16}:[A-Za-z0-9_-]{30,100}|-100[0-9]{6,})'
        or regexp_replace(replacement_text,E'[\n\t]','','g') ~ '[[:cntrl:]]' then
        raise exception 'button_edit_copy_invalid' using errcode='22023';end if;
    reply_hash:=encode(sha256(convert_to(replacement_text,'UTF8')),'hex');
    select * into p from private.content_ops_button_edit_prompts where id=target_prompt_id;
    if not found then raise exception 'button_edit_prompt_unknown' using errcode='P0002';end if;
    select * into r from private.content_ops_button_reviews where id=p.review_id;
    -- All mutators lock item -> review -> card/attempt/receipt -> prompt -> identity.
    select * into item from public.content_items where workspace_id=r.workspace_id and id=r.content_item_id for update;
    select * into r from private.content_ops_button_reviews where id=p.review_id for update;
    perform private.assert_content_ops_button_prompt_card_active(target_prompt_id,r.id,verified_human_binding);
    select * into p from private.content_ops_button_edit_prompts where id=target_prompt_id for update;
    if not found or p.bot_binding is distinct from verified_bot_binding
        or p.room_binding is distinct from verified_room_binding
        or p.message_binding is distinct from verified_message_binding then
        raise exception 'button_edit_message_forbidden' using errcode='42501';end if;
    select actor_id into identity_actor from private.content_ops_button_identities x
        where x.workspace_id=r.workspace_id and x.bot_binding=verified_bot_binding
        and x.human_binding=verified_human_binding and x.active for share;
    if not found or identity_actor is distinct from p.actor_id then
        raise exception 'button_edit_actor_forbidden' using errcode='42501';end if;
    perform 1 from private.content_ops_button_reviewers a where a.workspace_id=r.workspace_id
        and a.client_id=r.client_id and a.actor_id=identity_actor and a.active for share;
    if not found then raise exception 'button_edit_actor_forbidden' using errcode='42501';end if;
    observed:=clock_timestamp();
    if observed>=p.expires_at or observed>=r.expires_at or p.delivered_at>observed then
        raise exception 'button_edit_expired' using errcode='23514';end if;
    if not exists(select 1 from public.workspace_clients c where c.workspace_id=r.workspace_id
        and c.client_id=r.client_id and c.active)
        or exists(select 1 from public.approvals a where a.workspace_id=r.workspace_id and a.content_item_id=r.content_item_id)
        or exists(select 1 from public.publications a where a.workspace_id=r.workspace_id and a.content_item_id=r.content_item_id) then
        raise exception 'button_edit_existing_delivery_or_approval' using errcode='23514';end if;

    if p.consumed_key is not null then
        if p.consumed_key is distinct from operation_key or p.reply_sha256 is distinct from reply_hash then
            raise exception 'button_edit_idempotency_conflict' using errcode='23505';end if;
        if item.current_version_id is distinct from p.new_version_id then
            raise exception 'button_edit_revision_superseded' using errcode='23514';end if;
        return jsonb_build_object('status','revision_saved','content_version_id',p.new_version_id,
            'reused',true,'rereview_required',true,'execution_authorized',false);
    end if;
    if r.state is distinct from 'edit_requested' or r.epoch is distinct from p.epoch
        or item.status is distinct from 'needs_review' or item.current_version_id is distinct from r.content_version_id
        or r.version_fingerprint is distinct from private.content_ops_button_version_fingerprint(
            r.workspace_id,r.content_item_id,r.content_version_id) then
        raise exception 'button_edit_stale_or_not_requested' using errcode='23514';end if;
    select * into act from private.content_ops_button_actions where review_id=r.id and idempotency_key=p.edit_action_key;
    if not found or act.actor_id is distinct from identity_actor or act.epoch is distinct from p.epoch
        or act.version_fingerprint is distinct from r.version_fingerprint
        or act.action not in ('edit_telegram','edit_x') then
        raise exception 'button_edit_action_invalid' using errcode='23514';end if;
    copy_key:=case act.action when 'edit_telegram' then 'telegram' else 'x' end;
    max_units:=case act.action when 'edit_telegram' then 3700 else 1000 end;
    select sum(case when ascii(c)>65535 then 2 else 1 end) into units
        from regexp_split_to_table(replacement_text,'') c;
    if units>max_units then raise exception 'button_edit_copy_invalid' using errcode='22023';end if;
    select * into old_version from public.content_versions where workspace_id=r.workspace_id
        and content_item_id=r.content_item_id and id=r.content_version_id for share;
    if not found then raise exception 'button_edit_version_missing' using errcode='P0002';end if;
    if old_version.channel_copy->>copy_key is not distinct from replacement_text then
        raise exception 'button_edit_no_change' using errcode='22023';end if;
    select coalesce(max(v.version_number),0)+1 into new_number from public.content_versions v
        where v.workspace_id=r.workspace_id and v.content_item_id=r.content_item_id;
    new_id:=gen_random_uuid();
    insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version,
        locale,title,content,channel_copy,deliverables,qa,generation_meta,created_by)
    values(new_id,r.workspace_id,r.content_item_id,new_number,'button-edit@1',old_version.locale,old_version.title,
        old_version.content-'render'-'spec',
        jsonb_set(old_version.channel_copy,array[copy_key],to_jsonb(replacement_text)),
        '{}'::jsonb,
        jsonb_build_object('manual_review_required',true,'source_fidelity','needs_review','brand_alignment','needs_review'),
        jsonb_build_object('mock_mode',coalesce(old_version.generation_meta->'mock_mode','true'::jsonb),
            'fact_check',jsonb_build_object('status','needs_review'),
            'brand_qa',jsonb_build_object('status','needs_review'),
            'review_revision',jsonb_build_object('schema_version','button-edit@1',
                'previous_version_id',old_version.id,'prompt_id',p.id,'channel',copy_key,
                'canonical_banner_required',true)),identity_actor);
    -- No asset copy, approval, send, queue, generation or implicit QA pass.
    update public.content_items set current_version_id=new_id,status='draft',scheduled_for=null where id=item.id;
    update private.content_ops_button_reviews set state='held',epoch=epoch+1 where id=r.id;
    update private.content_ops_button_edit_prompts set consumed_key=operation_key,reply_sha256=reply_hash,
        new_version_id=new_id where id=p.id;
    return jsonb_build_object('status','revision_saved','content_version_id',new_id,
        'reused',false,'rereview_required',true,'execution_authorized',false);
end $$;
revoke all on function private.save_content_ops_button_edit_reply(uuid,text,text,text,text,text,text)
    from public,anon,authenticated,service_role;
commit;
