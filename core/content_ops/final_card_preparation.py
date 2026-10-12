"""Local/default-OFF ce1 intent hook and Engine-side private final-card worker.

No credentials, runtime mount, scheduler or second poller. A real host must
attest its OWN release (B for the hook, E for worker), provision narrow roles
and use a trusted private PNG reader/route verifier/sole-bot sender. Claiming
is one-shot; uncertainty is held. Only the separate human ce2 owner may queue
official Telegram and an unscheduled Typefully draft.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from core.content_ops.final_card_courier import FinalCardCourier, PreparedFinalCard
from core.content_ops.final_card_owner import PostgresFinalCardOwner
from core.content_ops.final_publication_confirmation import FinalConfirmationSnapshot, pin_verified_routes
from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.prompt_reservation import canonical_timestamp
from core.content_ops.publication_route_verification import VerifiedPublicationRoutes
from core.content_ops.release_pair import valid_release_pair
from core.content_ops.review_buttons import ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings

_SHA = re.compile(r"[a-f0-9]{64}\Z")
_CLAIM_KEYS = {"status", "execution_authorized", "intent_id", "workspace_id", "review_id",
    "parent_card_id", "review_epoch", "reviewer_id", "version_fingerprint", "bot_binding",
    "room_binding", "human_binding", "release_sha", "callback_release_sha", "release_pair_sha256",
    "telegram_route_binding", "typefully_route_binding", "candidate"}
_CANDIDATE_KEYS = {"client_id", "kst_date", "content_item_id", "content_version_id",
    "source_item_id", "generate_job_id", "banner_asset_id", "banner_sha256", "title",
    "telegram_copy", "x_copy", "source_url", "source_published_at"}


class FinalPreparationError(ValueError):
    """Metadata-only errors; never reflect SQL, copies or provider responses."""


def _check(value):
    if not value:
        raise FinalPreparationError("final_card_preparation_invalid")


class FinalPreparationHook:
    """Called only by the authenticated private-review owner, inside its tx."""

    def __init__(self, *, release_sha, callback_release_sha, release_pair_sha256,
                 runtime_callback_release_sha, enabled=False):
        _check(type(enabled) is bool and valid_release_pair(release_sha, callback_release_sha, release_pair_sha256)
               and callback_release_sha == runtime_callback_release_sha)
        self.enabled = enabled
        self.pair = (release_sha, callback_release_sha, release_pair_sha256)

    def in_transaction(self, cursor, *, review_id, parent_card_id, actor_id,
                       version_fingerprint, bot_binding, room_binding, human_binding):
        if not self.enabled:
            return {"status": "disabled", "execution_authorized": False}
        _check(all(canonical_uuid(value) for value in (review_id, parent_card_id, actor_id))
               and all(type(value) is str and _SHA.fullmatch(value) for value in
                       (version_fingerprint, bot_binding, room_binding, human_binding)))
        cursor.execute("select private.request_content_ops_final_card_preparation("
            "%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s)",
            (str(uuid4()), review_id, parent_card_id, actor_id, version_fingerprint,
             bot_binding, room_binding, human_binding, *self.pair))
        result = cursor.fetchone()[0]
        _check(type(result) is dict and set(result) == {"status", "intent_id", "reused", "execution_authorized"}
               and result["execution_authorized"] is False and type(result["reused"]) is bool
               and ((result["status"] == "not_ready" and result["intent_id"] is None and not result["reused"])
                    or (result["status"] == "preparation_recorded" and canonical_uuid(result["intent_id"]))))
        return result


class PostgresFinalPreparationOwner:
    """Claim commits once before a worker receives any candidate or does I/O."""

    def __init__(self, connection_factory, *, release_sha, callback_release_sha,
                 release_pair_sha256, enabled=False):
        _check(callable(connection_factory) and type(enabled) is bool
               and valid_release_pair(release_sha, callback_release_sha, release_pair_sha256))
        self.enabled = enabled
        self.pair = (release_sha, callback_release_sha, release_pair_sha256)
        self._factory = connection_factory

    def _claim(self, intent_id):
        try:
            with self._factory() as conn:
                _check(conn.autocommit is False)
                with conn.cursor() as cur:
                    cur.execute("select set_config('statement_timeout','5000',true), "
                                "set_config('lock_timeout','2000',true)")
                    _check(tuple(cur.fetchone()) == ("5000", "2000"))
                    cur.execute("select private.claim_content_ops_final_card_preparation(%s::uuid,%s,%s,%s)",
                                (intent_id, *self.pair))
                    result = cur.fetchone()[0]
                    _check(type(result) is dict and result.get("execution_authorized") is False)
                    if result.get("status") == "outcome_unknown":
                        _check(set(result) == {"status", "execution_authorized"})
                    else:
                        _check(set(result) == _CLAIM_KEYS and result["status"] == "preparation_claimed"
                               and result["intent_id"] == intent_id
                               and tuple(result[key] for key in
                                   ("release_sha", "callback_release_sha", "release_pair_sha256")) == self.pair)
            return result  # only after a successful commit acknowledgement
        except Exception:
            raise FinalPreparationError("final_card_preparation_outcome_unknown") from None

    async def claim(self, intent_id):
        if not self.enabled:
            return {"status": "disabled", "execution_authorized": False}
        _check(canonical_uuid(intent_id))
        return await asyncio.to_thread(self._claim, intent_id)


class FinalPreparationWorker:
    """One exact intent -> existing private courier. No provider retry or public owner."""

    def __init__(self, owner, courier, *, bindings, bot_id, chat_id, reviewers,
                 png_reader, route_reader, runtime_engine_release_sha, callback_release_sha,
                 release_pair_sha256, clock=None):
        _check(type(courier) is FinalCardCourier and type(bindings) is EditBindings
               and isinstance(courier._owner, PostgresPreparedFinalCardOwner)
               and courier._bindings is bindings and courier._release_sha == runtime_engine_release_sha
               and valid_release_pair(runtime_engine_release_sha, callback_release_sha, release_pair_sha256)
               and getattr(owner, "pair", None) == (runtime_engine_release_sha, callback_release_sha, release_pair_sha256)
               and callable(getattr(owner, "claim", None)) and callable(png_reader) and callable(route_reader)
               and type(bot_id) is int and 0 < bot_id < 2**52
               and type(chat_id) is int and -(2**52) < chat_id < 0
               and type(reviewers) is dict and 1 <= len(reviewers) <= 100
               and all(canonical_uuid(actor) and type(human) is int and 0 < human < 2**52 and human != bot_id
                       for actor, human in reviewers.items())
               and len(set(reviewers.values())) == len(reviewers))
        self._owner, self._courier = owner, courier
        self._bindings, self._bot, self._room = bindings, bot_id, chat_id
        self._reviewers, self._png, self._routes = dict(reviewers), png_reader, route_reader
        self._pair = (runtime_engine_release_sha, callback_release_sha, release_pair_sha256)
        self._clock = clock or (lambda: int(time.time()))

    async def run(self, intent_id, *, enabled=False):
        if enabled is not True:
            return {"status": "disabled", "public_send_attempted": False}
        try:
            _check(canonical_uuid(intent_id))
            data = await self._owner.claim(intent_id)
            if data == {"status": "outcome_unknown", "execution_authorized": False}:
                return {"status": "outcome_unknown", "public_send_attempted": False}
            _check(type(data) is dict and set(data) == _CLAIM_KEYS
                   and data["status"] == "preparation_claimed" and data["execution_authorized"] is False
                   and data["intent_id"] == intent_id
                   and len(json.dumps(data, ensure_ascii=True, allow_nan=False)) <= 50000
                   and tuple(data[key] for key in ("release_sha", "callback_release_sha", "release_pair_sha256")) == self._pair)
            actor = data["reviewer_id"]
            _check(actor in self._reviewers)
            human = self._reviewers[actor]
            _check(data["bot_binding"] == self._bindings.digest("bot", self._bot)
                   and data["room_binding"] == self._bindings.digest("room", self._bot, self._room)
                   and data["human_binding"] == self._bindings.digest("human", self._bot, human))
            c = data["candidate"]
            _check(type(c) is dict and set(c) == _CANDIDATE_KEYS
                   and all(canonical_uuid(value) for value in (data["workspace_id"],
                       c["content_item_id"], c["content_version_id"], c["banner_asset_id"],
                       c["source_item_id"], c["generate_job_id"])))
            review = ReviewSnapshot(data["workspace_id"], c["client_id"], c["content_item_id"],
                c["content_version_id"], c["source_url"], canonical_timestamp(c["source_published_at"]),
                c["telegram_copy"], c["x_copy"], c["banner_sha256"], "daily_ready")
            review.validate()
            # A trusted reader must freshly verify actual configured routes,
            # not return labels guessed from client config or Telegram text.
            routes = await self._routes(data["workspace_id"], c["client_id"], self._pair[0])
            _check(type(routes) is VerifiedPublicationRoutes
                   and routes.telegram_route_binding == data["telegram_route_binding"]
                   and routes.typefully_route_binding == data["typefully_route_binding"])
            now = self._clock()
            _check(type(now) is int and now > 0)
            snapshot = FinalConfirmationSnapshot(review, data["review_id"], intent_id, actor, actor, actor,
                data["review_epoch"], data["review_epoch"], data["review_epoch"], data["version_fingerprint"],
                0, 0, self._pair[0], data["telegram_route_binding"], data["typefully_route_binding"],
                routes.telegram_destination_label, routes.typefully_x_destination_label,
                self._pair[1], self._pair[2], decision_mode="telegram_and_typefully_draft",
                parent_card_id=data["parent_card_id"])
            snapshot = pin_verified_routes(snapshot, routes, now=datetime.fromtimestamp(now, timezone.utc))
            # No arbitrary URL from a callback. The canonical asset owner gets
            # scoped UUIDs and must return bounded actual PNG bytes.
            png = await self._png(data["workspace_id"], c["client_id"], c["content_version_id"],
                                  c["banner_asset_id"], c["banner_sha256"])
            _check(type(png) is bytes and 9 <= len(png) <= 10000000)
            return await self._courier.run(PreparedFinalCard(snapshot, intent_id, png,
                self._bot, self._room, human, None, now), enabled=True)
        except Exception:
            return {"status": "outcome_unknown", "public_send_attempted": False}


class PostgresPreparedFinalCardOwner(PostgresFinalCardOwner):
    """Same existing delivery ledger, with original-intent admission under lock.

    All bounded receipt parsing, per-part commits, uncertainty and terminal
    reconciliation are inherited unchanged. Reservation and new-part admission
    add the original intent, release-selection generation and active identity
    checks atomically in SQL. Receipt confirmation remains usable after revoke.
    """

    def _call(self, query, args, *, expected_keys):
        prefix = "select private.reserve_content_ops_final_card_delivery("
        if query.startswith(prefix):
            query = query.replace(prefix, "select private.reserve_prepared_content_ops_final_card_delivery(", 1)
        prefix = "select private.begin_content_ops_final_card_part("
        if query.startswith(prefix):
            query = query.replace(prefix, "select private.begin_prepared_content_ops_final_card_part(", 1)
        return super()._call(query, args, expected_keys=expected_keys)
