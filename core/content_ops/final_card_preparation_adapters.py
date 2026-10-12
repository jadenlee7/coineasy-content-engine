"""Narrow final-card adapters: private send, canonical PNG GET, DB attestations.

No poller, public publisher, Typefully write or ambient credential discovery.
Route data is an existing trusted-owner DB attestation, not a new provider
readback. Its freshness and digest must match the independently pinned policy.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from core.content_ops.private_review_card_sender import TelegramPrivateCardSender, PrivateCardSenderError
from core.content_ops.private_review_card_receipt import ObservedSend, validate_part_response
from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.publication_route_verification import (
    ExpectedPublicationRoutes, VerifiedPublicationRoutes, validate_expected_publication_routes, _digest,
)

PROJECT_ORIGIN = "https://isuqcqwxpojgzevxfdwr.supabase.co"

# Discovery is a read-only projection, not send admission. Filter permanently
# superseded intents BEFORE LIMIT so one old epoch/pair cannot starve new work.
# The claim/reserve/begin owners still independently recheck under locks.
PENDING_SQL = """select p.id::text
    from private.content_ops_final_card_preparations p
    join private.content_ops_button_reviews r on r.id=p.review_id
    join private.content_ops_button_cards c on c.id=p.parent_card_id and c.review_id=r.id
    join public.content_items i on i.id=r.content_item_id and i.workspace_id=r.workspace_id and i.client_id=r.client_id
    join public.workspace_clients wc on wc.workspace_id=r.workspace_id and wc.client_id=r.client_id and wc.active
    join private.content_ops_current_release_pairs selected on selected.workspace_id=r.workspace_id
      and selected.client_id=r.client_id and selected.release_pair_sha256=p.release_pair_sha256
      and selected.selected_at<=p.created_at
    join private.content_ops_release_pairs registry on registry.release_pair_sha256=p.release_pair_sha256
      and registry.release_sha=p.release_sha and registry.callback_release_sha=p.callback_release_sha
    where r.workspace_id=%s::uuid and r.client_id=any(%s) and p.claimed_at is null
      and (p.release_sha,p.callback_release_sha,p.release_pair_sha256)=(%s,%s,%s)
      and p.epoch=r.epoch and r.state='active' and r.expires_at>clock_timestamp()
      and p.version_fingerprint=r.version_fingerprint and c.version_fingerprint=r.version_fingerprint
      and c.epoch=r.epoch and c.active and c.delivered_at<=clock_timestamp() and c.expires_at>clock_timestamp()
      and c.bindings->>'bot'=p.bot_binding and c.bindings->>'room'=p.room_binding
      and i.current_version_id=r.content_version_id and i.status='needs_review'
      and p.version_fingerprint=private.content_ops_button_version_fingerprint(r.workspace_id,r.content_item_id,r.content_version_id)
      and exists(select 1 from private.content_ops_review_principals actor
        join private.content_ops_button_reviewers reviewer on reviewer.actor_id=actor.id and reviewer.workspace_id=actor.workspace_id
        join private.content_ops_button_identities identity on identity.workspace_id=actor.workspace_id and identity.actor_id=actor.id
        where actor.id=p.actor_id and actor.workspace_id=r.workspace_id and actor.bot_binding=p.bot_binding
          and actor.human_binding=p.human_binding and reviewer.client_id=r.client_id and reviewer.active
          and identity.bot_binding=p.bot_binding and identity.human_binding=p.human_binding and identity.active)
      and (select count(*) from private.content_ops_button_checks checked where checked.review_id=r.id
        and checked.epoch=r.epoch and checked.actor_id=p.actor_id and checked.check_kind in ('source_checked','claims_checked'))=2
      and exists(select 1 from public.content_source_links link
        join public.source_items source on source.id=link.source_item_id and source.workspace_id=link.workspace_id and source.client_id=link.client_id
        join public.source_feeds feed on feed.id=source.source_feed_id and feed.workspace_id=source.workspace_id and feed.client_id=source.client_id
        where link.workspace_id=r.workspace_id and link.client_id=r.client_id and link.content_item_id=r.content_item_id
          and link.position=0 and source.source_type='tweet' and source.published_at>clock_timestamp()-interval '24 hours'
          and source.published_at<=clock_timestamp() and feed.active and feed.provider='x' and feed.poll_interval_minutes=15
          and feed.last_polled_at>clock_timestamp()-interval '15 minutes' and feed.last_polled_at<=clock_timestamp()
          and source.id=(select latest.id from public.source_items latest where latest.workspace_id=source.workspace_id
            and latest.client_id=source.client_id and latest.source_feed_id=feed.id and latest.source_type='tweet'
            order by latest.published_at desc nulls last,latest.id desc limit 1))
      and not exists(select 1 from private.content_ops_final_card_deliveries d where d.review_id=r.id and d.epoch=r.epoch)
      and not exists(select 1 from public.approvals a where a.workspace_id=r.workspace_id and a.content_item_id=r.content_item_id)
      and not exists(select 1 from public.publications pub where pub.workspace_id=r.workspace_id and pub.content_item_id=r.content_item_id)
    order by p.created_at,p.id limit 4"""


def _require(condition):
    if not condition:
        raise ValueError("final_preparation_adapter_invalid")


class TelegramPrivateFinalCardSender(TelegramPrivateCardSender):
    """Same existing bot/private-room preflight; strict ce2 packet only."""

    async def send_once(self, request, *, png):
        try:
            _require(self._verified and not self._terminal and self._next_index < 4
                and type(request) is dict)
            kind = ("image", "telegram", "x", "controls")[self._next_index]
            method = "sendPhoto" if kind == "image" else "sendMessage"
            text = request.get("text")
            _require(request.get("kind") == kind and request.get("method") == method
                and set(request) == ({"kind", "method", "text", "reply_markup"}
                    if kind == "controls" else {"kind", "method", "text"})
                and type(text) is str and 0 < len(text.encode("utf-16-le")) // 2 <= (1024 if kind == "image" else 4096)
                and (type(png) is bytes and 8 < len(png) <= 10000000 and png.startswith(b"\x89PNG\r\n\x1a\n")
                    if kind == "image" else png is None))
            if kind in {"image", "controls"}:
                _require("최종 게시 확인 (2차)" in text)
            if kind == "controls":
                markup = request["reply_markup"]
                _require(type(markup) is dict and set(markup) == {"inline_keyboard"}
                    and type(markup["inline_keyboard"]) is list and len(markup["inline_keyboard"]) == 1)
                buttons = markup["inline_keyboard"][0]
                _require(type(buttons) is list and len(buttons) == 2
                    and all(type(b) is dict and set(b) == {"text", "callback_data"}
                        and type(b["callback_data"]) is str and re.fullmatch(r"ce2:[A-Za-z0-9_-]{51}", b["callback_data"])
                        for b in buttons)
                    and [b["text"] for b in buttons] == ["✅ 이 버전 최종 게시 승인", "보류"]
                    and "X는 자동 게시하지 않으며 Typefully에서 최종 게시합니다." in text)
            digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            _require(digest not in self._attempted)
            self._attempted.add(digest)
            self._next_index += 1
            self._terminal = True  # uncertainty never reopens this sender
            if kind == "image":
                body = {"chat_id": self._chat_id, "caption": text, "protect_content": "true"}
            else:
                body = {"chat_id": self._chat_id, "text": text, "protect_content": True,
                        "link_preview_options": {"is_disabled": True}}
                if kind == "controls": body["reply_markup"] = request["reply_markup"]
            response = await self._post(method, body=body, image=png)
            observed = ObservedSend(method, response.status_code, response.content, self._clock())
            validate_part_response(observed, request, bot_id=self._bot_id, chat_id=self._chat_id, thread_id=None)
            self._terminal = False
            return observed
        except Exception:
            raise PrivateCardSenderError("final_card_telegram_unknown") from None


class PreparationReadStore:
    """Read-only restricted factory. No claim/mutation occurs during discovery."""

    def __init__(self, factory, *, workspace_id, engine, callback, pair, routes):
        _require(callable(factory) and canonical_uuid(workspace_id) and type(routes) is dict)
        self.factory, self.workspace, self.pair = factory, workspace_id, (engine, callback, pair)
        self.routes = dict(routes)
        for client, expected in self.routes.items():
            validate_expected_publication_routes(expected)
            _require(expected.workspace_id == workspace_id and expected.release_sha == engine
                and expected.client_id == client)

    def _read(self, query, args):
        try:
            with self.factory() as conn:
                _require(conn.autocommit is False and conn.read_only is True)
                with conn.cursor() as cur:
                    cur.execute(query, args)
                    return cur.fetchall()
        except Exception:
            raise ValueError("final_preparation_read_unknown") from None

    async def pending(self):
        rows = await asyncio.to_thread(self._read, PENDING_SQL, (self.workspace, list(self.routes), *self.pair))
        _require(type(rows) is list and len(rows) <= 4
            and all(type(row) in (tuple,list) and len(row)==1 and canonical_uuid(row[0]) for row in rows)
            and len({row[0] for row in rows}) == len(rows))
        return tuple(row[0] for row in rows)

    async def canonical_asset(self, workspace, client, version, asset, digest):
        _require(workspace == self.workspace and client in self.routes
            and canonical_uuid(version) and canonical_uuid(asset)
            and type(digest) is str and re.fullmatch(r"[a-f0-9]{64}",digest))
        rows = await asyncio.to_thread(self._read, """select a.byte_size,a.storage_path
            from public.assets a join public.content_versions v on v.id=a.content_version_id and v.workspace_id=a.workspace_id
            join public.content_items i on i.id=v.content_item_id and i.workspace_id=a.workspace_id
            join storage.objects o on o.bucket_id=a.storage_bucket and o.name=a.storage_path
            join storage.buckets b on b.id=o.bucket_id and b.public=false
            where i.workspace_id=%s::uuid and i.client_id=%s and i.current_version_id=v.id
              and i.status='needs_review' and a.content_version_id=%s::uuid and a.id=%s::uuid
              and a.content_item_id=i.id and v.deliverables->>'primary_asset_id'=a.id::text
              and a.sha256=%s and a.asset_kind='png' and a.mime_type='image/png'
              and a.storage_bucket='content-studio' and a.metadata->>'filename'='news-card.png'
              and a.storage_path=%s""", (workspace,client,version,asset,digest,
                f"{workspace}/{client}/{asset}/news-card.png"))
        _require(len(rows)==1 and type(rows[0]) in (list,tuple) and len(rows[0])==2
            and type(rows[0][0]) is int and 8 < rows[0][0] <= 10000000
            and rows[0][1] == f"{workspace}/{client}/{asset}/news-card.png")
        return rows[0]

    async def verified_routes(self, workspace, client, engine):
        _require(workspace == self.workspace and engine == self.pair[0] and client in self.routes)
        expected = self.routes[client]
        common = {"schema":"content-ops-publication-route@1","workspace_id":workspace,
                  "client_id":client,"release_sha":engine}
        tg = _digest({**common,"provider":"telegram","action":"official_channel_post",
            "channel_id":expected.telegram_channel_id,"bot_id":expected.telegram_bot_id,
            "username":expected.telegram_username.lower()})
        x = _digest({**common,"provider":"typefully_x","action":"x_draft_only",
            "social_set_id":expected.typefully_social_set_id,"username":expected.x_username.lower()})
        rows = await asyncio.to_thread(self._read, """select channel,route_binding,verified_at
            from private.content_ops_publication_routes where workspace_id=%s::uuid
              and client_id=%s and release_sha=%s and active
              and verified_at<=clock_timestamp() and verified_at>clock_timestamp()-interval '15 minutes'
            order by channel""", (workspace,client,engine))
        _require(len(rows)==2 and all(type(row) in (tuple,list) and len(row)==3 for row in rows))
        observed = {row[0]:row for row in rows}
        _require(set(observed)=={"telegram","typefully_x"}
            and observed["telegram"][1]==tg and observed["typefully_x"][1]==x)
        now = datetime.now(timezone.utc)
        dates = [row[2] for row in rows]
        _require(all(type(d) is datetime and d.utcoffset() is not None
            and timedelta(0)<=now-d<timedelta(minutes=15) for d in dates))
        return VerifiedPublicationRoutes(workspace,client,engine,tg,x,
            "@"+expected.telegram_username.lower(),"@"+expected.x_username.lower(),min(dates))


class CanonicalPrivatePngReader:
    def __init__(self, store, *, project_key, storage_read_token, transport=None):
        _require(type(store) is PreparationReadStore and all(type(v) is str and 32<=len(v)<=8192
            and v.isascii() and not any(c.isspace() for c in v) for v in (project_key,storage_read_token))
            and project_key != storage_read_token)
        self.store,self.key,self.token,self.transport=store,project_key,storage_read_token,transport

    async def __call__(self, workspace,client,version,asset,digest):
        try:
            size,path=await self.store.canonical_asset(workspace,client,version,asset,digest)
            url=PROJECT_ORIGIN+"/storage/v1/object/content-studio/"+path
            async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False,transport=self.transport) as http:
                async with http.stream("GET",url,headers={"Authorization":"Bearer "+self.token,
                        "apikey":self.key,"Accept-Encoding":"identity"}) as response:
                    _require(response.status_code==200 and not response.is_redirect
                        and response.headers.get("content-type","").split(";",1)[0]=="image/png"
                        and response.headers.get("content-encoding","identity")=="identity"
                        and "content-range" not in response.headers)
                    data=bytearray()
                    async for block in response.aiter_bytes():
                        data.extend(block); _require(len(data)<=size)
            _require(len(data)==size and data.startswith(b"\x89PNG\r\n\x1a\n")
                and hashlib.sha256(data).hexdigest()==digest)
            return bytes(data)
        except Exception:
            raise ValueError("final_preparation_png_unknown") from None
