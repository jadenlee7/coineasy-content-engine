-- Disposable synthetic PostgreSQL only. All writes roll back; no provider I/O.
begin;
create function pg_temp.check_card_send(ok boolean, label text)
returns void language plpgsql as $$
begin
    if ok is not true then raise exception 'card send ledger test: %', label; end if;
end $$;

do $$
declare
    w uuid := private.test_content_ops_seed();
    i uuid; v uuid; rid uuid := gen_random_uuid(); cid uuid := gen_random_uuid();
    fp text; n integer; result jsonb; claim jsonb; parts jsonb := '[]'::jsonb;
    token uuid := gen_random_uuid(); outbox uuid;
    bindings jsonb; delivered timestamptz; expiry timestamptz;
    payloads text[] := array[repeat('1',64),repeat('2',64),repeat('3',64),repeat('4',64)];
    messages text[] := array[repeat('5',64),repeat('6',64),repeat('7',64),repeat('8',64)];
    responses text[] := array[repeat('9',64),repeat('a',64),repeat('b',64),repeat('c',64)];
begin
    select id,current_version_id into i,v from public.content_items
        where workspace_id=w and client_id='yellow';
    perform pg_temp.check_card_send(
        private.content_ops_review_candidate(w,i,v) is not null,
        'candidate must be current and fresh');
    fp := private.content_ops_button_version_fingerprint(w,i,v);
    insert into private.content_ops_button_reviews
        (id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint)
        values(rid,w,'yellow',i,v,fp);

    begin
        perform private.reserve_content_ops_button_card_send(rid,cid,0::smallint,payloads[1]);
        raise exception 'expected unbound outbox rejection';
    exception when check_violation then null; end;
    perform set_config('request.jwt.claim.role','service_role',true);
    perform pg_temp.check_card_send(public.content_ops_reconcile_daily(w) = 4,
        'existing outbox reconciles one row per client');
    claim := public.content_ops_claim_review(w,token,v);
    outbox := (claim->>'outbox_id')::uuid;
    perform pg_temp.check_card_send(outbox is not null
        and claim->>'content_version_id'=v::text,'exact-version outbox claimed');
    result := public.content_ops_begin_review_send(w,outbox,token,repeat('d',64),v);
    perform pg_temp.check_card_send(result->'accepted'='true'::jsonb,
        'existing outbox begins once');
    result := public.content_ops_begin_review_send(w,outbox,token,repeat('d',64),v);
    perform pg_temp.check_card_send(result->'accepted'='false'::jsonb,
        'old link-card path cannot begin same outbox again');
    begin
        perform private.bind_content_ops_button_card_outbox(
            rid,outbox,gen_random_uuid(),repeat('d',64));
        raise exception 'expected wrong-owner rejection';
    exception when check_violation then null; end;
    begin
        perform private.bind_content_ops_button_card_outbox(
            rid,outbox,token,repeat('e',64));
        raise exception 'expected packet hash mismatch rejection';
    exception when check_violation then null; end;
    result := private.bind_content_ops_button_card_outbox(
        rid,outbox,token,repeat('d',64));
    perform pg_temp.check_card_send(result->>'status'='bound'
        and (select count(*)=1 from private.content_ops_button_card_outbox_owners
            where review_id=rid and outbox_id=outbox),
        'new button-card path bound to same exclusive outbox');
    begin
        perform private.bind_content_ops_button_card_outbox(
            rid,outbox,token,repeat('d',64));
        raise exception 'expected duplicate binding rejection';
    exception when unique_violation then null; end;

    result := private.reserve_content_ops_button_card_send(rid,cid,0::smallint,payloads[1]);
    perform pg_temp.check_card_send(result = jsonb_build_object('status','reserved',
        'new_attempt',true,'execution_authorized',false),'first reservation');
    result := private.reserve_content_ops_button_card_send(rid,cid,0::smallint,payloads[1]);
    perform pg_temp.check_card_send(result->'new_attempt'='false'::jsonb,
        'duplicate reservation is not a send permit');
    begin
        perform private.reserve_content_ops_button_card_send(rid,cid,1::smallint,payloads[2]);
        raise exception 'expected incomplete predecessor rejection';
    exception when check_violation then null; end;
    begin
        perform private.reserve_content_ops_button_card_send(rid,gen_random_uuid(),0::smallint,payloads[1]);
        raise exception 'expected alternate card rejection';
    exception when check_violation then null; end;

    for n in 0..3 loop
        if n > 0 then
            result := private.reserve_content_ops_button_card_send(
                rid,cid,n::smallint,payloads[n+1]);
            perform pg_temp.check_card_send(result->'new_attempt'='true'::jsonb,
                'next reservation');
        end if;
        result := private.confirm_content_ops_button_card_send(
            rid,cid,n::smallint,payloads[n+1],messages[n+1],responses[n+1],clock_timestamp());
        perform pg_temp.check_card_send(result->'new_confirmation'='true'::jsonb,
            'first confirmation');
        result := private.confirm_content_ops_button_card_send(
            rid,cid,n::smallint,payloads[n+1],messages[n+1],responses[n+1],clock_timestamp());
        perform pg_temp.check_card_send(result->'new_confirmation'='false'::jsonb,
            'repeated confirmation cannot advance');
        if n < 3 then
            parts := parts || jsonb_build_array(jsonb_build_object(
                'kind',(array['image','telegram','x'])[n+1],
                'outcome','sent','message_binding',messages[n+1],
                'payload_sha256',payloads[n+1]));
        end if;
    end loop;
    perform pg_temp.check_card_send((select count(*)=4
        from private.content_ops_button_card_send_attempts where review_id=rid),
        'one row per part');

    bindings := jsonb_build_object('bot',repeat('d',64),'room',repeat('e',64),
        'message',messages[4],'packet_receipt',repeat('f',64),
        'card_receipt',repeat('0',64),'parent_binding',repeat('1',64),
        'thread_id',null);
    delivered := clock_timestamp();
    expiry := least((select expires_at from private.content_ops_button_reviews
        where id=rid), delivered+interval '30 minutes');
    begin
        perform private.register_content_ops_button_card_from_sends(
            rid,cid,fp,0,bindings,parts,payloads[4],
            to_jsonb(array[responses[1],responses[2],responses[3],repeat('f',64)]),
            delivered,expiry);
        raise exception 'expected response hash mismatch rejection';
    exception when check_violation then null; end;
    perform pg_temp.check_card_send((select count(*)=0
        from private.content_ops_button_cards where review_id=rid),
        'mismatch creates no card');
    result := private.register_content_ops_button_card_from_sends(
        rid,cid,fp,0,bindings,parts,payloads[4],to_jsonb(responses),delivered,expiry);
    perform pg_temp.check_card_send(result->>'status'='card_recorded'
        and result->'reused'='false'::jsonb and result->>'card_id'=cid::text,
        'exact four-part card recorded once');
    result := private.register_content_ops_button_card_from_sends(
        rid,cid,fp,0,bindings,parts,payloads[4],to_jsonb(responses),delivered,expiry);
    perform pg_temp.check_card_send(result->'reused'='true'::jsonb,
        'registration replay is readback only');
    begin
        perform private.reserve_content_ops_button_card_send(rid,cid,0::smallint,payloads[1]);
        raise exception 'expected post-card reservation denial';
    exception when check_violation then null; end;
    perform pg_temp.check_card_send((select count(*)=0 from public.approvals
        where workspace_id=w and content_item_id=i)
        and (select count(*)=0 from public.publications
        where workspace_id=w and content_item_id=i),
        'no approval or publication');
    result := public.content_ops_finish_review_send(w,outbox,token,'sent',123,v);
    perform pg_temp.check_card_send(result->'accepted'='true'::jsonb
        and private.content_ops_button_card_outbox_owned(rid) is not true,
        'terminal outbox cannot authorize any further button-card send');
end $$;
rollback;
