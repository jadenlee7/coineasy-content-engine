"""Bounded private daily cards; no scheduler, listener, generation or publisher.

The database remains the durable latest-source/version/dedupe owner. Every
claim creates an exact-version one-shot session for the existing private
courier. Uncertainty stops this invocation; no send/claim acknowledgement is
blindly retried. A new process cannot bypass the persisted owner ledger.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.content_ops.private_review_card_canary import PrivateCardCanary
from core.content_ops.private_review_card_gateway import DailyButtonGateway


_KST = ZoneInfo("Asia/Seoul")


class DailyPrivateCardWorker:
    def __init__(self, *, gateway, runner_factory, start_kst="09:00",
                 clock=None, uuid_factory=None):
        if (type(gateway) is not DailyButtonGateway or not callable(runner_factory)
            or type(start_kst) is not str
            or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", start_kst) is None):
            raise ValueError("daily_private_card_configuration_invalid")
        self._gateway, self._factory = gateway, runner_factory
        self._start = time.fromisoformat(start_kst)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uuid = uuid_factory or (lambda: str(uuid4()))
        self._attempted = False

    def _local_now(self):
        now = self._clock()
        if type(now) is not datetime or now.utcoffset() is None:
            raise ValueError("daily_private_card_clock_invalid")
        return now.astimezone(_KST)

    async def run(self, *, enabled=False):
        counts = {"queued": 0, "claimed": 0, "cards_recorded": 0, "confirmed_parts": 0}

        def result(status):
            return {"status": status, **counts, "public_send_attempted": False}

        if enabled is not True:
            return result("disabled")
        if self._attempted:
            return result("replay_denied")
        self._attempted = True
        discovery_attempted = False
        try:
            now = self._local_now()
            if now.time() < self._start:
                return result("waiting_for_window")
            day = now.date().isoformat()
            discovery_attempted = True
            counts["queued"] = await self._gateway.reconcile()
            for _ in range(4):
                current = self._local_now()
                if current.date().isoformat() != day or current.time() < self._start:
                    return result("day_rolled_over")
                claim = await self._gateway.claim(self._uuid())
                if claim is None:
                    return result("completed" if counts["cards_recorded"] else "no_candidate")
                counts["claimed"] += 1
                if claim.kst_date != day:
                    return result("day_rolled_over")
                bound = self._gateway.bind(claim)
                runner = self._factory(bound)
                if type(runner) is not PrivateCardCanary:
                    return result("blocked")
                outcome = await runner.run_bound(enabled=True)
                if (type(outcome) is not dict
                    or not set(outcome) <= {"status", "confirmed_parts", "public_send_attempted"}
                    or outcome.get("public_send_attempted") is not False
                    or outcome.get("status") not in {
                        "card_recorded", "blocked", "outbox_unknown", "delivery_unknown"}
                    or type(outcome.get("confirmed_parts", 0)) is not int
                    or not 0 <= outcome.get("confirmed_parts", 0) <= 4):
                    return result("delivery_unknown")
                counts["confirmed_parts"] += outcome.get("confirmed_parts", 0)
                if outcome["status"] != "card_recorded":
                    return result(outcome["status"])
                if outcome.get("confirmed_parts") != 4:
                    return result("delivery_unknown")
                counts["cards_recorded"] += 1
            return result("completed")
        except Exception:
            return result("outbox_unknown" if discovery_attempted else "blocked")
