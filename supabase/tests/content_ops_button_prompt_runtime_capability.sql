-- Disposable Docker PostgreSQL only. Synthetic actor/room/bindings; no provider I/O.
-- This exercises the runtime SQL transaction after a test-only signup-free
-- principal FK rebind. It is NOT the exact hosted migration/catalog.
create table public.test_prompt_runtime_context(
    card_id uuid not null, attempt_id uuid not null, actor_id uuid not null,
    human_binding text not null, action_key text not null
);

do $$
declare
    w uuid:=private.test_content_ops_seed(); actor uuid:=gen_random_uuid();
    item uuid; version uuid; review uuid:=gen_random_uuid();
    card uuid:=gen_random_uuid(); attempt uuid:=gen_random_uuid();
    fingerprint text; delivered timestamptz; action_result jsonb;
begin
    select id,current_version_id into item,version from public.content_items
        where workspace_id=w and client_id='yellow';
    insert into private.content_ops_review_principals(
        id,workspace_id,bot_binding,human_binding)
    values(actor,w,repeat('b',64),repeat('c',64));
    fingerprint:=private.content_ops_button_version_fingerprint(w,item,version);
    insert into private.content_ops_button_reviewers values(w,'yellow',actor,true);
    insert into private.content_ops_button_identities values(
        w,repeat('b',64),repeat('c',64),actor,true);
    insert into private.content_ops_button_reviews(
        id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint)
    values(review,w,'yellow',item,version,fingerprint);
    delivered:=clock_timestamp();
    insert into private.content_ops_button_cards(
        id,review_id,epoch,version_fingerprint,bindings,parts,
        delivered_at,expires_at,active)
    values(card,review,0,fingerprint,
        jsonb_build_object('bot',repeat('b',64),'room',repeat('d',64),
            'message',repeat('1',64),'parent_binding',repeat('8',64),
            'packet_receipt',repeat('9',64),'card_receipt',repeat('a',64),
            'thread_id',null),
        jsonb_build_array(
            jsonb_build_object('kind','image','message_binding',repeat('2',64),
                'payload_sha256',repeat('5',64),'outcome','sent'),
            jsonb_build_object('kind','telegram','message_binding',repeat('3',64),
                'payload_sha256',repeat('6',64),'outcome','sent'),
            jsonb_build_object('kind','x','message_binding',repeat('4',64),
                'payload_sha256',repeat('7',64),'outcome','sent')),
        delivered,delivered+interval '10 minutes',true);
    action_result:=private.record_content_ops_button_action(
        review,actor,fingerprint,'edit_x',repeat('e',64));
    if action_result->>'status' is distinct from 'edit_requested' then
        raise exception 'synthetic edit action not committed';
    end if;
    insert into public.test_prompt_runtime_context values(
        card,attempt,actor,repeat('c',64),repeat('e',64));
end $$;
grant select on public.test_prompt_runtime_context to coineasy_private_review;

set session authorization coineasy_private_review;
do $$
declare
    target public.test_prompt_runtime_context;
    result jsonb; observed timestamptz;
begin
    select * into target from public.test_prompt_runtime_context;
    begin
        perform private.reserve_content_ops_button_prompt_for_runtime(
            target.card_id,target.attempt_id,gen_random_uuid(),
            target.human_binding,target.action_key);
        raise exception 'wrong actor was accepted';
    exception when insufficient_privilege then null; end;
    result:=private.reserve_content_ops_button_prompt_for_runtime(
        target.card_id,target.attempt_id,target.actor_id,
        target.human_binding,target.action_key);
    if result->>'status' is distinct from 'attempt_recorded'
        or result->>'reused' is distinct from 'false' then
        raise exception 'prompt capability reservation failed';
    end if;
    observed:=clock_timestamp();
    begin
        perform private.register_content_ops_button_prompt_response_for_runtime(
            target.attempt_id,target.human_binding,repeat('f',64),
            repeat('1',64),repeat('a',64),observed);
        raise exception 'recycled card message was accepted';
    exception when check_violation then null; end;
    begin
        perform private.register_content_ops_button_prompt_response_for_runtime(
            target.attempt_id,target.human_binding,repeat('f',64),
            repeat('2',64),repeat('a',64),observed);
        raise exception 'recycled part message was accepted';
    exception when check_violation then null; end;
    result:=private.register_content_ops_button_prompt_response_for_runtime(
        target.attempt_id,target.human_binding,repeat('f',64),
        repeat('0',64),repeat('a',64),observed);
    if result->>'status' is distinct from 'prompt_registered'
        or result->>'reused' is distinct from 'false' then
        raise exception 'prompt capability registration failed';
    end if;
    result:=private.register_content_ops_button_prompt_response_for_runtime(
        target.attempt_id,target.human_binding,repeat('f',64),
        repeat('0',64),repeat('a',64),observed);
    if result->>'status' is distinct from 'prompt_registered'
        or result->>'reused' is distinct from 'true' then
        raise exception 'prompt capability exact replay failed';
    end if;
    begin
        perform private.register_content_ops_button_prompt_response_for_runtime(
            target.attempt_id,target.human_binding,repeat('f',64),
            repeat('0',64),repeat('b',64),observed);
        raise exception 'conflicting receipt was accepted';
    exception when unique_violation then null; end;
    begin
        insert into private.content_ops_button_prompt_attempts(id)
            values(gen_random_uuid());
        raise exception 'direct INSERT was accepted';
    exception when insufficient_privilege then null; end;
    begin
        perform private.reserve_content_ops_button_prompt_attempt(
            target.card_id,gen_random_uuid(),target.actor_id,
            target.human_binding,target.action_key);
        raise exception 'underlying reservation function was executable';
    exception when insufficient_privilege then null; end;
end $$;
reset session authorization;

do $$ begin
    if (select count(*) from private.content_ops_button_prompt_attempts)<>1
       or (select count(*) from private.content_ops_button_prompt_receipts)<>1
       or (select count(*) from private.content_ops_button_edit_prompts)<>1
       or (select count(*) from public.approvals)<>0
       or (select count(*) from public.publications)<>0 then
        raise exception 'prompt capability durable result mismatch';
    end if;
end $$;
