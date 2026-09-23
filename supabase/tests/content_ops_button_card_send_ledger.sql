-- Disposable synthetic PostgreSQL only. All writes roll back; no provider I/O.
begin;
create function pg_temp.check_card_send(ok boolean, label text)
returns void language plpgsql as $$
begin
    if ok is not true then raise exception 'card send ledger test: %', label; end if;
end $$;
create function pg_temp.block_card_outbox_finish()
returns trigger language plpgsql as $$
begin
    if new.status = 'sent' then
        raise exception 'synthetic outbox finish rejection' using errcode = '23514';
    end if;
    return new;
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
    perform set_config('request.jwt.claim.role','service_role',true);
    perform pg_temp.check_card_send(public.content_ops_reconcile_daily(w) = 4,
        'existing outbox reconciles one row per client');
    claim := public.content_ops_claim_review(w,token,v);
    outbox := (claim->>'outbox_id')::uuid;
    perform pg_temp.check_card_send(outbox is not null
        and claim->>'content_version_id'=v::text,'exact-version outbox claimed');
    begin
        perform private.prepare_content_ops_button_review_from_claim(
            w,outbox,gen_random_uuid(),v,rid);
        raise exception 'expected wrong claim token rejection';
    exception when check_violation then null; end;
    begin
        perform private.prepare_content_ops_button_review_from_claim(
            w,outbox,token,gen_random_uuid(),rid);
        raise exception 'expected wrong version rejection';
    exception when check_violation then null; end;
    result := private.prepare_content_ops_button_review_from_claim(w,outbox,token,v,rid);
    fp := result->>'version_fingerprint';
    perform pg_temp.check_card_send(result->>'status'='review_prepared'
        and result->>'review_id'=rid::text
        and fp=private.content_ops_button_version_fingerprint(w,i,v)
        and (select count(*)=1 from private.content_ops_button_reviews where id=rid),
        'exact claimed outbox creates one button review');
    begin
        perform private.prepare_content_ops_button_review_from_claim(w,outbox,token,v,rid);
        raise exception 'expected duplicate review rejection';
    exception when unique_violation then null; end;
    begin
        perform private.reserve_content_ops_button_card_send(rid,cid,0::smallint,payloads[1]);
        raise exception 'expected unbound outbox rejection';
    exception when check_violation then null; end;
    result := public.content_ops_begin_review_send(w,outbox,token,repeat('d',64),v);
    perform pg_temp.check_card_send(result->'accepted'='true'::jsonb,
        'existing outbox begins once');
    result := public.content_ops_begin_review_send(w,outbox,token,repeat('d',64),v);
    perform pg_temp.check_card_send(result->'accepted'='false'::jsonb,
        'old link-card path cannot begin same outbox again');
    begin
        perform private.prepare_content_ops_button_review_from_claim(
            w,outbox,token,v,gen_random_uuid());
        raise exception 'expected post-begin review creation rejection';
    exception when check_violation then null; end;
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
            rid,cid,n::smallint,payloads[n+1],(101+n)::bigint,
            messages[n+1],responses[n+1],clock_timestamp());
        perform pg_temp.check_card_send(result->'new_confirmation'='true'::jsonb,
            'first confirmation');
        result := private.confirm_content_ops_button_card_send(
            rid,cid,n::smallint,payloads[n+1],(101+n)::bigint,
            messages[n+1],responses[n+1],clock_timestamp());
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
    begin
        perform private.confirm_content_ops_button_card_send(
            rid,cid,3::smallint,payloads[4],999::bigint,
            messages[4],responses[4],clock_timestamp());
        raise exception 'expected message ID confirmation conflict';
    exception when unique_violation then null; end;

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
    execute 'create trigger synthetic_block_card_outbox_finish before update
        on private.content_ops_review_outbox for each row
        execute function pg_temp.block_card_outbox_finish()';
    begin
        perform private.register_content_ops_button_card_from_sends(
            rid,cid,fp,0,bindings,parts,payloads[4],to_jsonb(responses),delivered,expiry);
        raise exception 'expected atomic finish rejection';
    exception when check_violation then null; end;
    execute 'drop trigger synthetic_block_card_outbox_finish
        on private.content_ops_review_outbox';
    perform pg_temp.check_card_send((select count(*)=0
        from private.content_ops_button_cards where review_id=rid)
        and (select status='sending' and message_id is null
            from private.content_ops_review_outbox where outbox_id=outbox),
        'failed finish rolls back card registration');
    result := private.register_content_ops_button_card_from_sends(
        rid,cid,fp,0,bindings,parts,payloads[4],to_jsonb(responses),delivered,expiry);
    perform pg_temp.check_card_send(result->>'status'='card_recorded'
        and result->'reused'='false'::jsonb and result->>'card_id'=cid::text
        and (select status='sent' and message_id=104
            from private.content_ops_review_outbox where outbox_id=outbox),
        'exact card and original outbox commit atomically');
    result := private.read_content_ops_button_card_terminal(rid,cid,outbox);
    perform pg_temp.check_card_send(result->>'status'='sent'
        and result->>'card_id'=cid::text and result->>'outbox_id'=outbox::text
        and result->'execution_authorized'='false'::jsonb,
        'exact terminal readback has no send authority');
    result := private.read_content_ops_button_card_terminal(rid,gen_random_uuid(),outbox);
    perform pg_temp.check_card_send(result->>'status'='not_confirmed',
        'wrong card has no terminal receipt');
    begin
        perform private.register_content_ops_button_card_from_sends(
            rid,cid,fp,0,bindings,parts,payloads[4],to_jsonb(responses),delivered,expiry);
        raise exception 'expected registration replay rejection';
    exception when check_violation then null; end;
    begin
        perform private.reserve_content_ops_button_card_send(rid,cid,0::smallint,payloads[1]);
        raise exception 'expected post-card reservation denial';
    exception when check_violation then null; end;
    perform pg_temp.check_card_send((select count(*)=0 from public.approvals
        where workspace_id=w and content_item_id=i)
        and (select count(*)=0 from public.publications
        where workspace_id=w and content_item_id=i),
        'no approval or publication');
    result := public.content_ops_finish_review_send(w,outbox,token,'sent',999,v);
    perform pg_temp.check_card_send(result->'accepted'='false'::jsonb
        and private.content_ops_button_card_outbox_owned(rid) is not true,
        'legacy finish cannot replace terminal controls message');
end $$;
rollback;
