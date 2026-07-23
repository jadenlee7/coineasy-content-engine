-- Cover the composite foreign keys used by the official-X worker ledgers.
-- These indexes keep parent updates/deletes and job lease cleanup bounded as
-- the review-draft history grows.

begin;

create index if not exists official_x_poll_receipts_feed_fk_idx
    on private.official_x_poll_receipts (
        workspace_id, client_id, source_feed_id
    );

create index if not exists official_x_source_state_source_fk_idx
    on private.official_x_source_state (
        workspace_id, client_id, source_item_id
    );

create index if not exists official_x_source_state_queued_job_fk_idx
    on private.official_x_source_state (queued_job_id);

create index if not exists official_x_daily_slots_client_fk_idx
    on private.official_x_daily_slots (workspace_id, client_id);

commit;
