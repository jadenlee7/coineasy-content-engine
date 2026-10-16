-- Read-only metadata only; no copy, source URLs, asset paths, credentials or plan grant.
begin transaction isolation level repeatable read read only;
set local timezone='UTC';
set local statement_timeout='20s';
select jsonb_build_object(
 'schema','standalone-review-recovery-preflight@1',
 'observed_at',clock_timestamp(),'read_only',current_setting('transaction_read_only'),
 'server_version_num',current_setting('server_version_num'),
 'constraints',(select jsonb_agg(jsonb_build_object('name',conname,'definition',pg_get_constraintdef(oid),
   'validated',convalidated) order by conname) from pg_constraint
   where conrelid='private.content_ops_review_recovery_grants'::regclass),
 'functions',(select jsonb_agg(jsonb_build_object('signature',p.oid::regprocedure::text,
   'body_sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),'owner',pg_get_userbyid(p.proowner),
   'security_definer',p.prosecdef,'search_path',p.proconfig,'acl',p.proacl) order by p.oid::regprocedure::text)
   from pg_proc p join pg_namespace n on n.oid=p.pronamespace where
   (n.nspname='private' and p.proname in ('content_ops_recovery_json_canonical','content_ops_recovery_json_sha256',
    'guard_content_ops_recovery_immutable','guard_content_ops_recovery_grant','content_ops_recovery_candidate',
    'content_ops_button_card_outbox_owned','content_ops_recovery_image_locator','content_ops_recovery_terminal'))
   or (n.nspname='public' and p.proname='content_ops_recovery_step')),
 'outboxes',(select jsonb_agg(jsonb_build_object('outbox_id',q.outbox_id,'version_id',q.content_version_id,
   'canonical_row_sha256',private.content_ops_recovery_json_sha256(to_jsonb(q)),
   'status',q.status,'expired_claim',q.lease_expires_at<clock_timestamp(),
   'no_begin_or_receipt',q.packet_sha256 is null and q.send_started_at is null and q.message_id is null and q.finished_at is null,
   'reviews',(select count(*) from private.content_ops_button_reviews r where r.workspace_id=q.workspace_id and r.content_version_id=q.content_version_id),
   'owners',(select count(*) from private.content_ops_button_card_outbox_owners o where o.outbox_id=q.outbox_id),
   'cards',(select count(*) from private.content_ops_button_cards c join private.content_ops_button_reviews r on r.id=c.review_id where r.workspace_id=q.workspace_id and r.content_version_id=q.content_version_id),
   'parts',(select count(*) from private.content_ops_button_card_send_attempts a join private.content_ops_button_reviews r on r.id=a.review_id where r.workspace_id=q.workspace_id and r.content_version_id=q.content_version_id))
   order by q.outbox_id) from private.content_ops_review_outbox q where q.outbox_id in
   ('94f31aa1-34dd-4220-9c88-34716e760c47','e82406d9-dc09-4367-883c-95dc2f6d47b5')),
 'recovery_counts',jsonb_build_object('grants',(select count(*) from private.content_ops_review_recovery_grants),
   'claims',(select count(*) from private.content_ops_review_recovery_claims),
   'prepared',(select count(*) from private.content_ops_review_recovery_prepared),
   'begins',(select count(*) from private.content_ops_review_recovery_begins),
   'terminals',(select count(*) from private.content_ops_review_recovery_terminals)),
 'tables',(select jsonb_agg(jsonb_build_object('name',c.relname,'owner',pg_get_userbyid(c.relowner),
   'acl',c.relacl,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity) order by c.relname)
   from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='private'
   and c.relname in ('content_ops_review_recovery_grants','content_ops_review_recovery_claims',
    'content_ops_review_recovery_prepared','content_ops_review_recovery_begins','content_ops_review_recovery_terminals')),
 'standalone_anchor_sha256',(select private.content_ops_recovery_json_sha256(to_jsonb(a))
   from private.ybo_standalone_babylon_trial_anchor a where trial_id='7b27e277-0641-4aef-b739-761f86547287'::uuid)
) as protected_readback;
rollback;
