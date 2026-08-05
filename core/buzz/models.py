from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuzzShadowEvent:
    event_id: str
    event_type: str
    job_id: str
    review_ref: str
    client_id: str
    agent_id: str
    workflow_kind: str
    result_code: str
    model_tier: str
    actual_cost_microusd: int
    finished_at: str
    source_url: str
    studio_review_path: str
    headline_ko: str
    summary_ko: str


@dataclass(frozen=True)
class BuzzAttachment:
    filename: str
    media_type: str
    content_sha256: str
    content: bytes


@dataclass(frozen=True)
class BuzzDeliveryClaim:
    event_id: str
    job_id: str
    channel_id: str
    message_sha256: str
    request_sha256: str
    status: str
    claim_granted: bool
    reused: bool


@dataclass(frozen=True)
class BuzzRelayReceipt:
    event_id: str


@dataclass(frozen=True)
class BuzzDeliveryRunResult:
    ok: bool
    claimed: bool
    status: str
    event_id: str | None = None
    error: str | None = None
    # Outcome of the best-effort lease reconciliation that opens every run.
    # Kept out of `ok`: a reconcile fault must not mask the delivery outcome.
    reconcile: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": self.ok,
            "claimed": self.claimed,
            "status": self.status,
        }
        if self.event_id is not None:
            result["event_id"] = self.event_id
        if self.error is not None:
            result["error"] = self.error
        if self.reconcile is not None:
            result["reconcile"] = dict(self.reconcile)
        return result
