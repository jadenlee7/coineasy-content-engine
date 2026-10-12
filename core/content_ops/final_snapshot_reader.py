"""Unmounted/default-OFF, bounded read-only final-confirmation snapshot reader.

Supply a fresh read-only restricted connection factory, NOT the mutation factory.
This grants no privileges and cannot register, approve, queue, send or retry.
Decision SQL must revalidate under its own locks before any durable mutation.
"""
from __future__ import annotations

import re

from core.content_ops.final_publication_confirmation import FinalConfirmationSnapshot
from core.content_ops.prompt_reservation import canonical_timestamp
from core.content_ops.release_pair import release_pair_sha256 as compute_release_pair_sha256
from core.content_ops.review_buttons import ReviewSnapshot
from core.publications.handoff import CLIENT_TARGETS

_HASH = re.compile(r"[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")
_KEYS = {"read_only", "workspace_id", "client_id", "content_item_id", "content_version_id",
    "review_id", "card_id", "parent_card_id", "reviewer_id", "review_epoch",
    "source_check_actor_id", "claims_check_actor_id", "source_check_epoch", "claims_check_epoch",
    "version_fingerprint", "source_url", "source_published_at", "telegram_copy", "x_copy",
    "banner_sha256", "approval_count", "publication_count", "release_sha",
    "callback_release_sha", "release_pair_sha256",
    "telegram_route_binding", "typefully_route_binding", "decision_mode", "snapshot_sha256"}

# One statement snapshot, exact binding predicates, bounded at two rows to reject
# ambiguity. No complete row dump/body/source media/storage paths or actor IDs
# from Telegram are loaded. The two copies are the version's review payload.
FINAL_SNAPSHOT_QUERY = """select jsonb_build_object(
    'read_only',current_setting('transaction_read_only')='on',
    'workspace_id',r.workspace_id,'client_id',r.client_id,
    'content_item_id',r.content_item_id,'content_version_id',r.content_version_id,
    'review_id',r.id,'card_id',c.id,'parent_card_id',c.parent_card_id,
    'reviewer_id',c.actor_id,'review_epoch',r.epoch,
    'source_check_actor_id',source_check.actor_id,'claims_check_actor_id',claims_check.actor_id,
    'source_check_epoch',source_check.epoch,'claims_check_epoch',claims_check.epoch,
    'version_fingerprint',r.version_fingerprint,'source_url',s.canonical_url,
    'source_published_at',s.published_at,'telegram_copy',v.channel_copy->>'telegram',
    'x_copy',v.channel_copy->>'x','banner_sha256',a.sha256,
    'approval_count',(select count(*) from public.approvals z
        where z.workspace_id=r.workspace_id and z.content_item_id=r.content_item_id),
    'publication_count',(select count(*) from public.publications z
        where z.workspace_id=r.workspace_id and z.content_item_id=r.content_item_id),
    'release_sha',d.release_sha,'callback_release_sha',d.callback_release_sha,
    'release_pair_sha256',d.release_pair_sha256,
    'telegram_route_binding',d.telegram_route_binding,
    'typefully_route_binding',d.typefully_route_binding,'decision_mode',d.decision_mode,
    'snapshot_sha256',c.snapshot_sha256)
from private.content_ops_final_cards c
join private.content_ops_final_card_deliveries d on d.id=c.id
    and d.review_id=c.review_id and d.parent_card_id=c.parent_card_id
    and d.actor_id=c.actor_id and d.epoch=c.epoch
    and d.version_fingerprint=c.version_fingerprint and d.snapshot_sha256=c.snapshot_sha256
join private.content_ops_button_reviews r on r.id=c.review_id
    and r.epoch=c.epoch and r.version_fingerprint=c.version_fingerprint and r.state='active'
join private.content_ops_current_release_pairs current_pair
    on current_pair.workspace_id=r.workspace_id and current_pair.client_id=r.client_id
join private.content_ops_release_pairs pair
    on pair.release_pair_sha256=current_pair.release_pair_sha256
    and pair.release_sha=d.release_sha and pair.callback_release_sha=d.callback_release_sha
    and pair.release_pair_sha256=d.release_pair_sha256
join private.content_ops_button_cards parent on parent.id=c.parent_card_id
    and parent.review_id=r.id and parent.epoch=r.epoch
    and parent.version_fingerprint=r.version_fingerprint and parent.active
    and parent.bindings->>'bot'=d.bot_binding and parent.bindings->>'room'=d.room_binding
join private.content_ops_review_principals principal on principal.id=c.actor_id
    and principal.workspace_id=r.workspace_id and principal.bot_binding=d.bot_binding
    and principal.human_binding=d.human_binding
join private.content_ops_button_identities identity on identity.workspace_id=r.workspace_id
    and identity.actor_id=principal.id and identity.bot_binding=d.bot_binding
    and identity.human_binding=d.human_binding and identity.active
join private.content_ops_button_reviewers reviewer on reviewer.workspace_id=r.workspace_id
    and reviewer.client_id=r.client_id and reviewer.actor_id=principal.id and reviewer.active
join private.content_ops_button_checks source_check on source_check.review_id=r.id
    and source_check.epoch=r.epoch and source_check.actor_id=principal.id
    and source_check.check_kind='source_checked'
join private.content_ops_button_checks claims_check on claims_check.review_id=r.id
    and claims_check.epoch=r.epoch and claims_check.actor_id=principal.id
    and claims_check.check_kind='claims_checked'
join public.content_items i on i.workspace_id=r.workspace_id and i.client_id=r.client_id
    and i.id=r.content_item_id and i.current_version_id=r.content_version_id
    and i.status='needs_review' and i.content_kind='daily_news'
join public.workspace_clients client on client.workspace_id=i.workspace_id
    and client.client_id=i.client_id and client.active
join public.content_versions v on v.workspace_id=i.workspace_id and v.content_item_id=i.id
    and v.id=i.current_version_id and v.generation_meta->'mock_mode'='false'::jsonb
join public.content_source_links l on l.workspace_id=i.workspace_id and l.client_id=i.client_id
    and l.content_item_id=i.id and l.position=0
join public.source_items s on s.workspace_id=l.workspace_id and s.client_id=l.client_id
    and s.id=l.source_item_id and s.source_type='tweet'
join public.source_feeds feed on feed.workspace_id=s.workspace_id and feed.client_id=s.client_id
    and feed.id=s.source_feed_id and feed.provider='x' and feed.active
    and feed.handle=s.author_handle and feed.poll_interval_minutes=15
join public.assets a on a.workspace_id=v.workspace_id and a.content_item_id=v.content_item_id
    and a.content_version_id=v.id and a.id::text=v.deliverables->>'primary_asset_id'
    and a.asset_kind='png' and a.mime_type='image/png' and a.storage_bucket='content-studio'
    and a.metadata->>'filename'='news-card.png'
    and a.storage_path=i.workspace_id::text||'/'||i.client_id||'/'||a.id::text||'/news-card.png'
    and a.sha256 ~ '^[a-f0-9]{64}$' and a.byte_size between 9 and 10000000
    and a.width>0 and a.height>0
join storage.objects stored on stored.bucket_id=a.storage_bucket and stored.name=a.storage_path
join private.content_ops_publication_routes tg on tg.workspace_id=r.workspace_id
    and tg.client_id=r.client_id and tg.channel='telegram' and tg.active
    and tg.release_sha=d.release_sha and tg.route_binding=d.telegram_route_binding
join private.content_ops_publication_routes x on x.workspace_id=r.workspace_id
    and x.client_id=r.client_id and x.channel='typefully_x' and x.active
    and x.release_sha=d.release_sha and x.route_binding=d.typefully_route_binding
where d.room_binding=%s and c.control_message_binding=%s
    and d.bot_binding=%s and d.human_binding=%s and d.release_sha=%s
    and d.callback_release_sha=%s and d.release_pair_sha256=%s
    and d.created_at>=current_pair.selected_at
    and c.id<>c.parent_card_id
    and c.registered_at<=clock_timestamp() and c.expires_at>clock_timestamp()
    and d.created_at<=clock_timestamp() and d.expires_at>clock_timestamp()
    and parent.delivered_at<=clock_timestamp() and parent.expires_at>clock_timestamp()
    and r.expires_at>clock_timestamp()
    and jsonb_typeof(parent.parts)='array' and jsonb_array_length(parent.parts)=3
    and parent.parts->0->>'kind'='image' and parent.parts->1->>'kind'='telegram'
    and parent.parts->2->>'kind'='x'
    and parent.parts->0->>'outcome'='sent' and parent.parts->1->>'outcome'='sent'
    and parent.parts->2->>'outcome'='sent'
    and tg.verified_at between clock_timestamp()-interval '15 minutes' and clock_timestamp()
    and x.verified_at between clock_timestamp()-interval '15 minutes' and clock_timestamp()
    and s.published_at>clock_timestamp()-interval '24 hours' and s.published_at<=v.created_at
    and s.published_at<=clock_timestamp() and v.created_at<=clock_timestamp()
    and feed.last_polled_at between clock_timestamp()-interval '15 minutes' and clock_timestamp()
    and s.author_handle=case i.client_id when 'yellow' then '@Yellow'
        when 'babylon' then '@babylonlabs_io' when 'squid' then '@SquidRouter'
        when 'origintrail' then '@origin_trail' end
    and s.id=(select latest.id from public.source_items latest
        where latest.workspace_id=s.workspace_id and latest.client_id=s.client_id
          and latest.source_feed_id=s.source_feed_id and latest.source_type='tweet'
        order by latest.published_at desc nulls last,latest.id desc limit 1)
    and (select count(*) from public.content_source_links z where z.workspace_id=i.workspace_id
        and z.content_item_id=i.id and z.position=0)=1
    and (select count(*) from public.assets z where z.workspace_id=v.workspace_id
        and z.content_item_id=v.content_item_id and z.content_version_id=v.id and z.asset_kind='png')=1
    and (select count(*) from private.content_ops_final_card_parts p
        where p.delivery_id=c.id and p.state='confirmed')=4
    and exists(select 1 from private.content_ops_final_card_parts p where p.delivery_id=c.id
        and p.part_index=3 and p.state='confirmed' and p.message_binding=c.control_message_binding)
    and r.version_fingerprint=private.content_ops_button_version_fingerprint(r.workspace_id,r.content_item_id,v.id)
    and exists(select 1 from private.official_x_daily_slots slot join public.jobs j on j.id=slot.job_id
        and j.workspace_id=slot.workspace_id and j.client_id=slot.client_id
        where slot.workspace_id=i.workspace_id and slot.client_id=i.client_id
          and slot.kst_date=(clock_timestamp() at time zone 'Asia/Seoul')::date
          and j.job_kind='generate' and j.status='succeeded'
          and j.input->>'workflow'='official_x_review_draft_v1'
          and j.input->>'content_kind'='daily_news' and j.input->'manual_only'='false'::jsonb
          and j.input->>'request_id'=i.id::text and j.input->'source_item_ids'=jsonb_build_array(s.id::text)
          and j.output->>'content_item_id'=i.id::text and j.output->>'content_version_id'=v.id::text
          and j.output->'source_item_ids'=jsonb_build_array(s.id::text)
          and (j.content_item_id is null or j.content_item_id=i.id)
          and (j.finished_at at time zone 'Asia/Seoul')::date=slot.kst_date)
limit 2"""


class FinalSnapshotReaderError(RuntimeError):
    """Fixed redacted errors only."""


class PostgresFinalSnapshotReader:
    def __init__(self, connection_factory, *, release_sha, callback_release_sha,
                 release_pair_sha256, enabled=False):
        # E is the Engine release and B the callback owner's release. Validate
        # their ordered identity binding even while disabled, before any I/O.
        if (not callable(connection_factory) or type(enabled) is not bool
            or type(release_sha) is not str or not _RELEASE.fullmatch(release_sha)
            or type(callback_release_sha) is not str or not _RELEASE.fullmatch(callback_release_sha)
            or type(release_pair_sha256) is not str or not _HASH.fullmatch(release_pair_sha256)
            or release_pair_sha256 != compute_release_pair_sha256(release_sha, callback_release_sha)):
            raise FinalSnapshotReaderError("final_snapshot_reader_configuration_invalid")
        self._factory, self._release, self._enabled = connection_factory, release_sha, enabled
        self._callback_release = callback_release_sha
        self._release_pair = release_pair_sha256

    def __call__(self, room_binding, message_binding, bot_binding, human_binding):
        if not self._enabled:
            raise FinalSnapshotReaderError("final_snapshot_reader_disabled")
        if any(type(v) is not str or not _HASH.fullmatch(v) for v in
               (room_binding, message_binding, bot_binding, human_binding)):
            raise FinalSnapshotReaderError("final_snapshot_reader_arguments_invalid")
        try:
            with self._factory() as connection:
                if connection.autocommit is not False or connection.read_only is not True:
                    raise ValueError("readonly transaction required")
                with connection.cursor() as cursor:
                    # SET LOCAL equivalents are legal in a read-only transaction;
                    # these do not change roles, persistent configuration or data.
                    cursor.execute("select set_config('statement_timeout','5000',true), "
                                   "set_config('lock_timeout','2000',true)")
                    if tuple(cursor.fetchone()) != ("5000", "2000"):
                        raise ValueError("bounded timeout required")
                    cursor.execute(FINAL_SNAPSHOT_QUERY,
                        (room_binding, message_binding, bot_binding, human_binding,
                         self._release, self._callback_release, self._release_pair))
                    rows = cursor.fetchall()
                    if len(rows) != 1 or type(rows[0]) not in (tuple, list) or len(rows[0]) != 1:
                        raise ValueError("ambiguous or missing card")
                    data = rows[0][0]
                    if type(data) is not dict or set(data) != _KEYS or data["read_only"] is not True:
                        raise ValueError("invalid projection")
                    client = data["client_id"]
                    if (client not in CLIENT_TARGETS or data["release_sha"] != self._release
                        or data["callback_release_sha"] != self._callback_release
                        or data["release_pair_sha256"] != self._release_pair):
                        raise ValueError("scope conflict")
                    review = ReviewSnapshot(data["workspace_id"], client, data["content_item_id"],
                        data["content_version_id"], data["source_url"],
                        canonical_timestamp(data["source_published_at"]), data["telegram_copy"],
                        data["x_copy"], data["banner_sha256"], "daily_ready")
                    snapshot = FinalConfirmationSnapshot(review=review,
                        **{key: data[key] for key in ("review_id", "card_id", "parent_card_id",
                            "reviewer_id", "source_check_actor_id", "claims_check_actor_id",
                            "review_epoch", "source_check_epoch", "claims_check_epoch",
                            "version_fingerprint", "approval_count", "publication_count",
                            "release_sha", "callback_release_sha", "release_pair_sha256",
                            "telegram_route_binding", "typefully_route_binding",
                            "decision_mode")},
                        telegram_destination_label="@"+CLIENT_TARGETS[client][0],
                        typefully_x_destination_label="@"+CLIENT_TARGETS[client][1])
                    snapshot.validate()
                    if snapshot.digest() != data["snapshot_sha256"]:
                        raise ValueError("stored digest mismatch")
            return snapshot
        except Exception:
            raise FinalSnapshotReaderError("final_snapshot_reader_unknown") from None
