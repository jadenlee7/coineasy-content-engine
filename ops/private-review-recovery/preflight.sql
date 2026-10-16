-- Read-only protected-scope preflight BEFORE applying the new proposal.
-- This deliberately emits no copy, source URL, destination, token or asset path.
-- Candidate freshness, current feed, exact source and policy hash are separate
-- live checks; do not treat this metadata snapshot as delivery authority.
begin read only;
set local timezone='UTC';
set local statement_timeout='15s';
select jsonb_build_object(
    'server_version_num',current_setting('server_version_num'),
    'read_only',current_setting('transaction_read_only'),
    'observed_at',clock_timestamp(),
    'expected_workspace', q.workspace_id = '5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid,
    'expected_client', q.client_id = 'babylon',
    'expected_version', q.content_version_id = '43e68255-9797-451d-909f-0dce460a6c18'::uuid,
    'original_claim_expired', q.status = 'claimed' and q.lease_expires_at < clock_timestamp(),
    'original_send_absent', q.packet_sha256 is null and q.send_started_at is null
        and q.message_id is null and q.finished_at is null,
    'original_outbox_pgtext_sha256', encode(sha256(convert_to(to_jsonb(q)::text,'UTF8')),'hex'),
    'review_count', (select count(*) from private.content_ops_button_reviews r
        where r.workspace_id=q.workspace_id and r.content_version_id=q.content_version_id),
    'owner_count', (select count(*) from private.content_ops_button_card_outbox_owners b
        where b.outbox_id=q.outbox_id),
    'candidate_function_present', to_regprocedure(
        'private.content_ops_review_candidate(uuid,uuid,uuid)') is not null,
    'reserve_function_present', to_regprocedure(
        'private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text)') is not null,
    'confirm_function_present', to_regprocedure(
        'private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz)') is not null,
    'owned_predecessor_sha256', (select encode(extensions.digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
        from pg_proc p where p.oid = 'private.content_ops_button_card_outbox_owned(uuid)'::regprocedure),
    'recovery_grant_table_absent', to_regclass('private.content_ops_review_recovery_grants') is null,
    'dependencies',(select jsonb_agg(jsonb_build_object('name',p.proname,
        'body_sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),
        'owner',pg_get_userbyid(p.proowner),'security_definer',p.prosecdef,
        'search_path',p.proconfig,'acl',p.proacl) order by p.proname)
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where n.nspname='private' and p.proname in (
        'content_ops_review_candidate','content_ops_review_matches',
        'content_ops_button_card_outbox_owned','content_ops_button_version_fingerprint',
        'reserve_content_ops_button_card_send','confirm_content_ops_button_card_send',
        'record_content_ops_button_card'))
) as protected_readback
from private.content_ops_review_outbox q
where q.outbox_id = '94f31aa1-34dd-4220-9c88-34716e760c47'::uuid;
rollback;
