"""Default-disabled append-only receipts for strict Telegram v2 intake.

The receipt proves only that the local content-engine contract validated one
eligible sanitized event and derived one exact triage item.  It does not ACK a
source event, send Telegram, publish, approve, call a provider, connect to a
database, or prove durable provider persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictInt,
    ValidationError,
    model_validator,
)

from .sources.telegram_v2 import (
    TelegramV2OutcomeCounts,
    V2_OUTBOX_STREAM_KEY,
    V2_READER_POLICY,
    _canonical_json,
    _sha256_text,
    _strict_json_object,
    project_telegram_v2_delivery,
    read_eligible_telegram_v2_event,
)


V2_INTAKE_RECEIPT_IDENTITY_SCHEMA = (
    "coineasy-telegram-v2-intake-delivery-identity@1"
)
V2_INTAKE_RECEIPT_SUBJECT_SCHEMA = (
    "coineasy-telegram-v2-intake-delivery-receipt-subject@1"
)
V2_INTAKE_RECEIPT_SCHEMA = (
    "coineasy-telegram-v2-intake-delivery-receipt@1"
)
V2_INTAKE_CONSUMER_NAMESPACE = (
    "coineasy-content-engine.gtm-intelligence.squid"
)
V2_INTAKE_RECEIPT_MAX_BYTES = 64 * 1024
_RECEIPT_BUILD_GRANT = object()


@dataclass(frozen=True)
class _ReceiptBuildGrant:
    marker: object
    owner_id: int
    payload_sha256: str


class TelegramV2ReceiptError(RuntimeError):
    """Stable receipt repository error."""


class TelegramV2ReceiptDisabled(TelegramV2ReceiptError):
    """Append was attempted while the repository was default-disabled."""


class TelegramV2ReceiptConflict(TelegramV2ReceiptError):
    """One delivery identity was rebound to different immutable bytes."""


class TelegramV2ReceiptIndeterminate(TelegramV2ReceiptError):
    """Append/readback outcome could not be proven exactly."""


class TelegramV2IntakeReceiptSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "coineasy-telegram-v2-intake-delivery-receipt-subject@1"
    ] = V2_INTAKE_RECEIPT_SUBJECT_SCHEMA
    consumer_namespace: Literal[
        "coineasy-content-engine.gtm-intelligence.squid"
    ] = V2_INTAKE_CONSUMER_NAMESPACE
    reader_policy: Literal["coineasy-telegram-v2-strict-reader@1"] = (
        V2_READER_POLICY
    )
    mode: Literal["local_validation_only"] = "local_validation_only"
    source_stream_key: Literal[
        "coineasydaily:{coineasy-gtm-owner}:squid:telegram:projection:v2"
    ] = V2_OUTBOX_STREAM_KEY
    stream_id: str = Field(pattern=r"^\d+-\d+$")
    event_ref: str = Field(pattern=r"^outbox-v2:[a-f0-9]{64}$")
    event_idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    question_ref: str = Field(pattern=r"^question:[a-f0-9]{64}$")
    projection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_commit_ref: str = Field(pattern=r"^commit:[a-f0-9]{64}$")
    source_stage_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_gate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_projection_ordinal: StrictInt = Field(ge=0, lt=100)
    source_batch_ref: str = Field(pattern=r"^batch:[a-f0-9]{64}$")
    promotion_ref: str = Field(pattern=r"^promotion:[a-f0-9]{64}$")
    promotion_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stream_row_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_event_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_index_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    intake_marker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    intake_gate_envelope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transport_updates_observed: StrictInt = Field(ge=1, le=100)
    outcome_counts: TelegramV2OutcomeCounts
    sanitized_projection_count: StrictInt = Field(ge=1, le=100)
    ordered_members_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    eligibility_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    item_ref: str = Field(pattern=r"^telegram:squid:[a-f0-9]{64}$")
    item_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_counts(self) -> "TelegramV2IntakeReceiptSubject":
        values = (
            self.outcome_counts.emitted,
            self.outcome_counts.tombstoned,
            self.outcome_counts.not_applicable,
        )
        if sum(values) != self.transport_updates_observed:
            raise ValueError("gtm_telegram_v2_receipt_outcome_count_invalid")
        if self.outcome_counts.emitted != self.sanitized_projection_count:
            raise ValueError("gtm_telegram_v2_receipt_projection_count_invalid")
        return self


def _receipt_delivery_identity(
    subject: TelegramV2IntakeReceiptSubject,
) -> dict[str, object]:
    """Bind one exact reader-derived event delivery, not question dedupe."""

    return {
        "schema_version": V2_INTAKE_RECEIPT_IDENTITY_SCHEMA,
        "consumer_namespace": subject.consumer_namespace,
        "reader_policy": subject.reader_policy,
        "source_stream_key": subject.source_stream_key,
        "stream_id": subject.stream_id,
        "event_ref": subject.event_ref,
        "event_idempotency_key": subject.event_idempotency_key,
        "event_sha256": subject.event_sha256,
        "question_ref": subject.question_ref,
        "projection_sha256": subject.projection_sha256,
        "source_commit_ref": subject.source_commit_ref,
        "promotion_ref": subject.promotion_ref,
        "promotion_manifest_sha256": subject.promotion_manifest_sha256,
        "item_sha256": subject.item_sha256,
    }


class TelegramV2IntakeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    _build_grant: object = PrivateAttr(default=None)

    schema_version: Literal[
        "coineasy-telegram-v2-intake-delivery-receipt@1"
    ] = V2_INTAKE_RECEIPT_SCHEMA
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_ref: str = Field(pattern=r"^intake-receipt:[a-f0-9]{64}$")
    subject: TelegramV2IntakeReceiptSubject
    intake_validated: Literal[True] = True
    exact_readback_required: Literal[True] = True
    source_acknowledged: Literal[False] = False
    public_delivery_observed: Literal[False] = False
    automatic_publication: Literal[False] = False
    approval_granted: Literal[False] = False
    provider_calls: Literal[False] = False
    database_calls: Literal[False] = False
    network_calls: Literal[False] = False
    telegram_calls: Literal[False] = False
    production_wiring_observed: Literal[False] = False
    durability_scope: Literal["process_memory_only"] = "process_memory_only"
    provider_persistence_observed: Literal[False] = False
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_receipt(self) -> "TelegramV2IntakeReceipt":
        identity = _receipt_delivery_identity(self.subject)
        expected_key = _sha256_text(_canonical_json(identity))
        expected_ref = (
            "intake-receipt:"
            + _sha256_text(_canonical_json(self.subject.model_dump(mode="json")))
        )
        if self.idempotency_key != expected_key or self.receipt_ref != expected_ref:
            raise ValueError("gtm_telegram_v2_receipt_identity_invalid")
        body = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if self.receipt_sha256 != _sha256_text(_canonical_json(body)):
            raise ValueError("gtm_telegram_v2_receipt_sha256_invalid")
        return self


def build_telegram_v2_intake_receipt(
    snapshot: dict[str, object],
) -> TelegramV2IntakeReceipt:
    """Revalidate six-object evidence and prepare exact receipt bytes."""

    eligible = read_eligible_telegram_v2_event(snapshot)
    item = project_telegram_v2_delivery(eligible)
    event = eligible.event
    eligibility_sha = _sha256_text(
        _canonical_json(eligible.model_dump(mode="json"))
    )
    subject = TelegramV2IntakeReceiptSubject(
        stream_id=eligible.stream_id,
        event_ref=event.event_ref,
        event_idempotency_key=event.idempotency_key,
        event_sha256=eligible.event_sha256,
        question_ref=event.projection.question_ref,
        projection_sha256=event.projection_sha256,
        source_commit_ref=event.source_commit_ref,
        source_stage_sha256=event.source_stage_sha256,
        source_gate_sha256=event.source_gate_sha256,
        source_projection_ordinal=event.source_projection_ordinal,
        source_batch_ref=eligible.source_batch_ref,
        promotion_ref=eligible.promotion_ref,
        promotion_manifest_sha256=eligible.promotion_manifest_sha256,
        stream_row_sha256=eligible.stream_row_sha256,
        current_event_index_sha256=eligible.current_event_index_sha256,
        source_index_binding_sha256=eligible.source_index_binding_sha256,
        intake_marker_sha256=eligible.intake_marker_sha256,
        intake_gate_envelope_sha256=eligible.intake_gate_envelope_sha256,
        transport_updates_observed=eligible.transport_updates_observed,
        outcome_counts=eligible.outcome_counts,
        sanitized_projection_count=eligible.sanitized_projection_count,
        ordered_members_sha256=eligible.ordered_members_sha256,
        eligibility_sha256=eligibility_sha,
        item_ref=item.ref,
        item_sha256=item.item_sha256,
    )
    identity = _receipt_delivery_identity(subject)
    idempotency_key = _sha256_text(_canonical_json(identity))
    receipt_ref = (
        "intake-receipt:"
        + _sha256_text(_canonical_json(subject.model_dump(mode="json")))
    )
    body = {
        "schema_version": V2_INTAKE_RECEIPT_SCHEMA,
        "idempotency_key": idempotency_key,
        "receipt_ref": receipt_ref,
        "subject": subject.model_dump(mode="json"),
        "intake_validated": True,
        "exact_readback_required": True,
        "source_acknowledged": False,
        "public_delivery_observed": False,
        "automatic_publication": False,
        "approval_granted": False,
        "provider_calls": False,
        "database_calls": False,
        "network_calls": False,
        "telegram_calls": False,
        "production_wiring_observed": False,
        "durability_scope": "process_memory_only",
        "provider_persistence_observed": False,
    }
    receipt = TelegramV2IntakeReceipt.model_validate({
        **body,
        "receipt_sha256": _sha256_text(_canonical_json(body)),
    })
    receipt._build_grant = _ReceiptBuildGrant(
        marker=_RECEIPT_BUILD_GRANT,
        owner_id=id(receipt),
        payload_sha256=_sha256_text(
            _canonical_json(receipt.model_dump(mode="json"))
        ),
    )
    return receipt


def _require_built_receipt(value: object) -> TelegramV2IntakeReceipt:
    if not isinstance(value, TelegramV2IntakeReceipt):
        raise TypeError("gtm_telegram_v2_intake_receipt_required")
    grant = value._build_grant
    payload_sha256 = _sha256_text(
        _canonical_json(value.model_dump(mode="json"))
    )
    if (
        not isinstance(grant, _ReceiptBuildGrant)
        or grant.marker is not _RECEIPT_BUILD_GRANT
        or grant.owner_id != id(value)
        or grant.payload_sha256 != payload_sha256
    ):
        raise TypeError("gtm_telegram_v2_intake_receipt_required")
    return value


class InMemoryTelegramV2ReceiptStore:
    """Test/local append-only store with exact replay and hard conflict."""

    def __init__(self) -> None:
        self._receipts: dict[str, str] = {}
        self.put_calls = 0
        self.get_calls = 0
        self._lock = Lock()

    def put_if_absent(self, idempotency_key: str, receipt_json: str) -> bool:
        with self._lock:
            self.put_calls += 1
            existing = self._receipts.get(idempotency_key)
            if existing is None:
                self._receipts[idempotency_key] = receipt_json
                return True
            if existing != receipt_json:
                raise TelegramV2ReceiptConflict(
                    "gtm_telegram_v2_receipt_identity_conflict"
                )
            return False

    def get(self, idempotency_key: str) -> str | None:
        with self._lock:
            self.get_calls += 1
            return self._receipts.get(idempotency_key)


class TelegramV2IntakeReceiptRepository:
    """Default-disabled append + exact-readback coordinator."""

    def __init__(
        self,
        store: InMemoryTelegramV2ReceiptStore | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        if type(enabled) is not bool:
            raise TypeError("gtm_telegram_v2_receipt_enabled_bool_required")
        if store is not None and type(store) is not InMemoryTelegramV2ReceiptStore:
            raise TypeError("gtm_telegram_v2_in_memory_store_required")
        self.store = (
            store if store is not None else InMemoryTelegramV2ReceiptStore()
        )
        self.enabled = enabled

    def put(
        self,
        receipt: TelegramV2IntakeReceipt,
    ) -> bool:
        """Append and exact-readback; return only whether this call created it."""
        if self.enabled is not True:
            raise TelegramV2ReceiptDisabled(
                "gtm_telegram_v2_receipt_repository_disabled"
            )
        receipt = _require_built_receipt(receipt)
        receipt_json = _canonical_json(receipt.model_dump(mode="json"))
        if len(receipt_json.encode("utf-8")) > V2_INTAKE_RECEIPT_MAX_BYTES:
            raise TelegramV2ReceiptConflict(
                "gtm_telegram_v2_receipt_too_large"
            )
        try:
            created = self.store.put_if_absent(
                receipt.idempotency_key,
                receipt_json,
            )
        except TelegramV2ReceiptConflict:
            raise
        except Exception as exc:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_append_indeterminate"
            ) from exc
        if type(created) is not bool:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_append_result_invalid"
            )
        try:
            readback = self.store.get(receipt.idempotency_key)
        except Exception as exc:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_readback_indeterminate"
            ) from exc
        if readback is None:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_readback_missing"
            )
        try:
            payload, canonical = _strict_json_object(
                readback,
                maximum=V2_INTAKE_RECEIPT_MAX_BYTES,
                code="gtm_telegram_v2_receipt_readback_invalid",
            )
            restored = TelegramV2IntakeReceipt.model_validate(payload)
            restored_json = _canonical_json(
                restored.model_dump(mode="json")
            )
        except (TelegramV2ReceiptError, ValidationError, ValueError) as exc:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_readback_invalid"
            ) from exc
        if (
            canonical != receipt_json
            or restored_json != canonical
            or restored.model_dump(mode="json")
            != receipt.model_dump(mode="json")
        ):
            raise TelegramV2ReceiptConflict(
                "gtm_telegram_v2_receipt_readback_conflict"
            )
        return created

    def get(self, idempotency_key: str) -> TelegramV2IntakeReceipt | None:
        if self.enabled is not True:
            raise TelegramV2ReceiptDisabled(
                "gtm_telegram_v2_receipt_repository_disabled"
            )
        try:
            value = self.store.get(idempotency_key)
        except Exception as exc:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_readback_indeterminate"
            ) from exc
        if value is None:
            return None
        try:
            payload, canonical = _strict_json_object(
                value,
                maximum=V2_INTAKE_RECEIPT_MAX_BYTES,
                code="gtm_telegram_v2_receipt_readback_invalid",
            )
            receipt = TelegramV2IntakeReceipt.model_validate(payload)
            restored_json = _canonical_json(
                receipt.model_dump(mode="json")
            )
        except (ValidationError, ValueError) as exc:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_readback_invalid"
            ) from exc
        if restored_json != canonical:
            raise TelegramV2ReceiptIndeterminate(
                "gtm_telegram_v2_receipt_readback_invalid"
            )
        if receipt.idempotency_key != idempotency_key:
            raise TelegramV2ReceiptConflict(
                "gtm_telegram_v2_receipt_readback_conflict"
            )
        return receipt


__all__ = [
    "InMemoryTelegramV2ReceiptStore",
    "TelegramV2IntakeReceipt",
    "TelegramV2IntakeReceiptRepository",
    "TelegramV2IntakeReceiptSubject",
    "TelegramV2ReceiptConflict",
    "TelegramV2ReceiptDisabled",
    "TelegramV2ReceiptError",
    "TelegramV2ReceiptIndeterminate",
    "build_telegram_v2_intake_receipt",
]
