"""Strict, provider-disconnected reader for marker-gated Telegram v2 events.

This module accepts one *atomic read snapshot* supplied by an owner-approved
read adapter.  It creates no Redis client, consumer group, cursor, ACK, network
connection, file, environment, provider, Telegram, or publication surface.

A v2 projection is not trusted on its own.  Eligibility requires exact binding
across the stream row, current event index, source index, promotion marker,
immutable intake marker, and raw-free sanitized gate.  Only the opaque
``EligibleTelegramV2Event`` returned by ``read_eligible_telegram_v2_event`` may
be projected into the separate v2 triage surface.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from ..models import (
    GtmDomain,
    GtmEvidence,
    GtmEvidenceKind,
    GtmLineage,
    GtmNextAction,
    GtmPolicy,
    GtmPriority,
    GtmStatus,
    TelegramTriageDetails,
    _safe_redacted_text,
    _telegram_faq_binding_sha256,
    _utc_seconds,
)
from .telegram import (
    TelegramAnswerState,
    TelegramFaqMatch,
    TelegramSafetyClass,
    TelegramTopic,
    _HANGUL_PATTERN,
    _SAFETY_ESCALATIONS,
    _SAFETY_TOPICS,
    _TELEGRAM_IDENTIFIER_PATTERN,
    _TOPIC_KO,
    _item_state,
)


V2_PROJECTION_SCHEMA = "coineasy-telegram-owner-projection@2"
V2_DIGEST_SCHEME = "hmac-sha256-v2"
V2_OUTBOX_EVENT_SCHEMA = "coineasy-telegram-owner-outbox-event@2"
V2_OUTBOX_EVENT_IDENTITY_SCHEMA = (
    "coineasy-telegram-owner-outbox-event-identity@2"
)
V2_PROMOTION_SUBJECT_SCHEMA = (
    "coineasy-telegram-owner-sanitized-promotion-subject@1"
)
V2_OUTBOX_NAME = "squid.telegram.owner_projection.v2"
V2_READER_EVIDENCE_SCHEMA = "coineasy-telegram-v2-reader-evidence@1"
V2_READER_POLICY = "coineasy-telegram-v2-strict-reader@1"
V2_STREAM_ROW_SUBJECT_SCHEMA = "coineasy-telegram-v2-stream-row-subject@1"

INTAKE_COMMIT_SUBJECT_SCHEMA = (
    "coineasy-telegram-owner-intake-commit-subject@1"
)
INTAKE_SANITIZED_GATE_SUBJECT_SCHEMA = (
    "coineasy-telegram-owner-sanitized-gate-subject@1"
)
INTAKE_SANITIZED_GATE_ENVELOPE_SCHEMA = (
    "coineasy-telegram-owner-sanitized-gate-envelope@1"
)
INTAKE_DISPATCH_SCORE = "0"
INTAKE_DISPATCH_OFFSET_WIDTH = 19
INTAKE_LEGACY_SHADOW_DISPATCH_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:telegram:intake-dispatch:"
    "legacy-shadow:v1"
)
INTAKE_V2_PROMOTION_DISPATCH_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:telegram:intake-dispatch:"
    "v2-promotion:v1"
)

V2_OUTBOX_STREAM_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:squid:telegram:projection:v2"
)
V2_OUTBOX_INDEX_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:squid:telegram:projection-idem:v2"
)
V2_PROMOTION_SOURCE_INDEX_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:squid:telegram:"
    "projection-promotion-source-index:v1"
)
V2_PROMOTION_LEDGER_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:squid:telegram:"
    "projection-promotion-ledger:v1"
)
INTAKE_LEDGER_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:telegram:intake-commit-ledger:v1"
)
INTAKE_SANITIZED_GATES_KEY = (
    "coineasydaily:{coineasy-gtm-owner}:telegram:intake-sanitized-gates:v1"
)

V2_EVENT_MAX_BYTES = 12 * 1024
V2_INTAKE_MARKER_MAX_BYTES = 128 * 1024
V2_SANITIZED_GATE_MAX_BYTES = 512 * 1024
V2_PROMOTION_MANIFEST_MAX_BYTES = 128 * 1024

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_COMMIT_REF_RE = re.compile(r"^commit:[a-f0-9]{64}$")
_PROMOTION_REF_RE = re.compile(r"^promotion:[a-f0-9]{64}$")
_EVENT_REF_RE = re.compile(r"^outbox-v2:[a-f0-9]{64}$")
_QUESTION_REF_RE = re.compile(r"^question:[a-f0-9]{64}$")
_CONTEXT_REF_RE = re.compile(r"^context:[a-f0-9]{64}$")
_SOURCE_EPOCH_RE = re.compile(r"^epoch:[a-f0-9]{64}$")
_SOURCE_BATCH_RE = re.compile(r"^batch:[a-f0-9]{64}$")
_UPDATE_REF_RE = re.compile(r"^update:[a-f0-9]{64}$")
_HEAD_REF_RE = re.compile(r"^(?:bootstrap|commit):[a-f0-9]{64}$")
_STREAM_ID_RE = re.compile(r"^\d+-\d+$")
_UTC_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_TOMBSTONE_REASONS = frozenset({
    "authorization_rejected",
    "not_a_question",
    "operator_quarantined",
    "privacy_rejected",
    "unsupported_question",
})

_V2_EVENT_FIELDS = frozenset({
    "schema_version",
    "outbox_name",
    "client_id",
    "event_ref",
    "source_commit_ref",
    "source_stage_sha256",
    "source_gate_sha256",
    "source_projection_ordinal",
    "idempotency_key",
    "projection_sha256",
    "raw_update_included",
    "telegram_identifiers_included",
    "owner_private_stage_included",
    "automatic_publication",
    "projection",
})


class TelegramV2ReaderError(ValueError):
    """Stable fail-closed reader contract error."""


class TelegramV2ReaderIneligible(TelegramV2ReaderError):
    """The supplied six-object snapshot does not prove current eligibility."""


def _fail(code: str) -> None:
    raise TelegramV2ReaderIneligible(code)


_ELIGIBILITY_GRANT = object()


@dataclass(frozen=True)
class _EligibilityGrant:
    marker: object
    owner_id: int
    payload_sha256: str


def _canonical_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        encoded = None
    if encoded is None:
        _fail("gtm_telegram_v2_canonical_json_invalid")
    return encoded


def _sha256_text(value: str) -> str:
    try:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        digest = None
    if digest is None:
        _fail("gtm_telegram_v2_utf8_invalid")
    return digest


def _strict_json_object(
    value: object,
    *,
    maximum: int,
    code: str,
) -> tuple[dict[str, object], str]:
    if type(value) is not str:
        _fail(code)
    text = value
    try:
        if len(text.encode("utf-8")) > maximum:
            _fail(code)
    except UnicodeError:
        _fail(code)

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if type(key) is not str or key in result:
                raise ValueError("duplicate_or_non_string_key")
            result[key] = item
        return result

    invalid = object()
    try:
        parsed = json.loads(text, object_pairs_hook=no_duplicates)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        parsed = invalid
    if parsed is invalid:
        _fail(code)
    if type(parsed) is not dict or _canonical_json(parsed) != text:
        _fail(code)
    return parsed, text


class TelegramOwnerProjectionV2(BaseModel):
    """Exact v2 owner projection; never sufficient for delivery by itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-telegram-owner-projection@2"]
    source_system: Literal["coineasydaily.single-consumer"]
    projection_stage: Literal["post-owner-redaction"]
    client_id: Literal["squid"]
    read_only_projection: Literal[True]
    new_telegram_consumer: Literal[False]
    raw_update_included: Literal[False]
    telegram_identifiers_included: Literal[False]
    private_links_included: Literal[False]
    hmac_key_included: Literal[False]
    summary_mode: Literal["owner-redacted-nonverbatim"]

    observed_at: datetime
    question_observed_at: datetime
    digest_scheme: Literal["hmac-sha256-v2"]
    question_ref: str = Field(pattern=r"^question:[a-f0-9]{64}$")
    question_hmac_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    topic: TelegramTopic
    question_summary_ko: str = Field(min_length=5, max_length=500)
    answer_state: TelegramAnswerState
    faq_match: TelegramFaqMatch
    faq_source_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    faq_binding_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    draft_reply_ko: Optional[str] = Field(default=None, min_length=10, max_length=600)
    safety_class: TelegramSafetyClass

    @model_validator(mode="before")
    @classmethod
    def validate_boundary_literals(cls, value: object) -> object:
        expected = {
            "schema_version": V2_PROJECTION_SCHEMA,
            "source_system": "coineasydaily.single-consumer",
            "projection_stage": "post-owner-redaction",
            "client_id": "squid",
            "read_only_projection": True,
            "new_telegram_consumer": False,
            "raw_update_included": False,
            "telegram_identifiers_included": False,
            "private_links_included": False,
            "hmac_key_included": False,
            "summary_mode": "owner-redacted-nonverbatim",
            "digest_scheme": V2_DIGEST_SCHEME,
        }
        if not isinstance(value, Mapping):
            return value
        for field_name, exact_value in expected.items():
            if field_name not in value:
                continue
            candidate = value[field_name]
            if type(candidate) is not type(exact_value) or candidate != exact_value:
                raise ValueError("gtm_telegram_v2_projection_boundary_invalid")
        return value

    @field_validator("observed_at", "question_observed_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "gtm_telegram_v2_projection_time_invalid")

    @field_validator("question_summary_ko")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = _safe_redacted_text(
            value,
            "gtm_telegram_v2_projection_summary_invalid",
            5,
            500,
        )
        if (
            _HANGUL_PATTERN.search(normalized) is None
            or _TELEGRAM_IDENTIFIER_PATTERN.search(normalized)
        ):
            raise ValueError("gtm_telegram_v2_projection_summary_invalid")
        return normalized

    @field_validator("draft_reply_ko")
    @classmethod
    def validate_draft(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = _safe_redacted_text(
            value,
            "gtm_telegram_v2_projection_draft_invalid",
            10,
            600,
        )
        if (
            _HANGUL_PATTERN.search(normalized) is None
            or _TELEGRAM_IDENTIFIER_PATTERN.search(normalized)
        ):
            raise ValueError("gtm_telegram_v2_projection_draft_invalid")
        return normalized

    @model_validator(mode="after")
    def validate_projection(self) -> "TelegramOwnerProjectionV2":
        digest = self.question_ref.removeprefix("question:")
        if digest != self.question_hmac_sha256:
            raise ValueError("gtm_telegram_v2_projection_hmac_binding_invalid")
        if self.question_observed_at > self.observed_at:
            raise ValueError("gtm_telegram_v2_projection_question_time_invalid")

        topic_allowlist = _SAFETY_TOPICS.get(self.safety_class)
        if topic_allowlist is not None and self.topic not in topic_allowlist:
            raise ValueError("gtm_telegram_v2_projection_safety_topic_invalid")

        has_faq_source = self.faq_source_sha256 is not None
        has_faq_binding = self.faq_binding_sha256 is not None
        has_draft = self.draft_reply_ko is not None
        if self.safety_class != "none":
            if self.faq_match != "none" or has_faq_source or has_faq_binding or has_draft:
                raise ValueError("gtm_telegram_v2_projection_escalated_reply_invalid")
        elif self.answer_state != "unanswered":
            if self.faq_match != "none" or has_faq_source or has_faq_binding or has_draft:
                raise ValueError("gtm_telegram_v2_projection_closed_reply_invalid")
        elif self.faq_match == "none":
            if has_faq_source or has_faq_binding or has_draft:
                raise ValueError("gtm_telegram_v2_projection_unbound_reply_invalid")
        elif not has_faq_source or not has_faq_binding or not has_draft:
            raise ValueError("gtm_telegram_v2_projection_faq_binding_missing")
        else:
            assert self.faq_source_sha256 is not None
            assert self.faq_binding_sha256 is not None
            assert self.draft_reply_ko is not None
            expected = _telegram_faq_binding_sha256(
                question_ref=self.question_ref,
                faq_match=self.faq_match,
                faq_source_sha256=self.faq_source_sha256,
                draft_reply_ko=self.draft_reply_ko,
            )
            if self.faq_binding_sha256 != expected:
                raise ValueError("gtm_telegram_v2_projection_faq_binding_invalid")
        return self


def _strict_projection(value: object) -> TelegramOwnerProjectionV2:
    if type(value) is not dict:
        _fail("gtm_telegram_v2_projection_invalid")
    try:
        projection = TelegramOwnerProjectionV2.model_validate(value)
    except ValidationError:
        projection = None
    if projection is None:
        _fail("gtm_telegram_v2_projection_invalid")
    if projection.model_dump(mode="json") != value:
        _fail("gtm_telegram_v2_projection_not_canonical")
    return projection


class TelegramV2OutboxEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-telegram-owner-outbox-event@2"]
    outbox_name: Literal["squid.telegram.owner_projection.v2"]
    client_id: Literal["squid"]
    event_ref: str = Field(pattern=r"^outbox-v2:[a-f0-9]{64}$")
    source_commit_ref: str = Field(pattern=r"^commit:[a-f0-9]{64}$")
    source_stage_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_gate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_projection_ordinal: StrictInt = Field(ge=0, lt=100)
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    projection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_update_included: Literal[False]
    telegram_identifiers_included: Literal[False]
    owner_private_stage_included: Literal[False]
    automatic_publication: Literal[False]
    projection: TelegramOwnerProjectionV2

    @model_validator(mode="before")
    @classmethod
    def validate_exact_event(cls, value: object) -> object:
        if type(value) is not dict or frozenset(value) != _V2_EVENT_FIELDS:
            raise ValueError("gtm_telegram_v2_event_fields_invalid")
        exact = {
            "schema_version": V2_OUTBOX_EVENT_SCHEMA,
            "outbox_name": V2_OUTBOX_NAME,
            "client_id": "squid",
            "raw_update_included": False,
            "telegram_identifiers_included": False,
            "owner_private_stage_included": False,
            "automatic_publication": False,
        }
        for field_name, expected in exact.items():
            candidate = value.get(field_name)
            if type(candidate) is not type(expected) or candidate != expected:
                raise ValueError("gtm_telegram_v2_event_boundary_invalid")
        if type(value.get("source_projection_ordinal")) is not int:
            raise ValueError("gtm_telegram_v2_event_ordinal_invalid")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> "TelegramV2OutboxEvent":
        projection_sha = _sha256_text(
            _canonical_json(self.projection.model_dump(mode="json"))
        )
        digest = _event_identity_digest(
            source_commit_ref=self.source_commit_ref,
            ordinal=self.source_projection_ordinal,
            projection_sha256=projection_sha,
        )
        if (
            self.projection_sha256 != projection_sha
            or self.idempotency_key != digest
            or self.event_ref != f"outbox-v2:{digest}"
        ):
            raise ValueError("gtm_telegram_v2_event_identity_invalid")
        return self


class TelegramV2StreamRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-telegram-owner-outbox-event@2"]
    idempotency_key: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    event_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    projection_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    event_json: StrictStr


class TelegramV2ReaderSnapshot(BaseModel):
    """One atomically captured read-only snapshot of all eligibility objects."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-telegram-v2-reader-evidence@1"]
    reader_policy: Literal["coineasy-telegram-v2-strict-reader@1"]
    mode: Literal["atomic_read_snapshot"]
    source_stream_key: Literal[
        "coineasydaily:{coineasy-gtm-owner}:squid:telegram:projection:v2"
    ]
    stream_id: StrictStr = Field(pattern=r"^\d+-\d+$")
    stream_row: TelegramV2StreamRow
    current_event_index_value: StrictStr
    source_promotion_index_value: StrictStr = Field(
        pattern=r"^promotion:[a-f0-9]{64}$"
    )
    promotion_marker_json: StrictStr
    intake_marker_json: StrictStr
    sanitized_gate_json: StrictStr
    atomic_snapshot: Literal[True]
    read_only_projection: Literal[True]
    new_telegram_consumer: Literal[False]
    raw_update_included: Literal[False]
    telegram_identifiers_included: Literal[False]
    owner_private_stage_included: Literal[False]
    external_calls: Literal[False]
    database_calls: Literal[False]
    provider_calls: Literal[False]
    publication_calls: Literal[False]
    automatic_publication: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def validate_literals(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        expected = {
            "schema_version": V2_READER_EVIDENCE_SCHEMA,
            "reader_policy": V2_READER_POLICY,
            "mode": "atomic_read_snapshot",
            "source_stream_key": V2_OUTBOX_STREAM_KEY,
            "atomic_snapshot": True,
            "read_only_projection": True,
            "new_telegram_consumer": False,
            "raw_update_included": False,
            "telegram_identifiers_included": False,
            "owner_private_stage_included": False,
            "external_calls": False,
            "database_calls": False,
            "provider_calls": False,
            "publication_calls": False,
            "automatic_publication": False,
        }
        required_fields = frozenset(expected) | {
            "stream_id",
            "stream_row",
            "current_event_index_value",
            "source_promotion_index_value",
            "promotion_marker_json",
            "intake_marker_json",
            "sanitized_gate_json",
        }
        if frozenset(value) != required_fields:
            raise ValueError("gtm_telegram_v2_snapshot_fields_invalid")
        for field_name, exact_value in expected.items():
            candidate = value[field_name]
            if type(candidate) is not type(exact_value) or candidate != exact_value:
                raise ValueError("gtm_telegram_v2_snapshot_boundary_invalid")
        return value


class TelegramV2OutcomeCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    emitted: StrictInt = Field(ge=0, le=100)
    tombstoned: StrictInt = Field(ge=0, le=100)
    not_applicable: StrictInt = Field(ge=0, le=100)


class EligibleTelegramV2Event(BaseModel):
    """Opaque capability proving one currently eligible, sanitized v2 row."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    _reader_grant: object = PrivateAttr(default=None)

    schema_version: Literal["coineasy-telegram-v2-eligible-event@1"] = (
        "coineasy-telegram-v2-eligible-event@1"
    )
    reader_policy: Literal["coineasy-telegram-v2-strict-reader@1"] = (
        V2_READER_POLICY
    )
    mode: Literal["local_validation_only"] = "local_validation_only"
    source_stream_key: Literal[
        "coineasydaily:{coineasy-gtm-owner}:squid:telegram:projection:v2"
    ] = V2_OUTBOX_STREAM_KEY
    stream_id: str = Field(pattern=r"^\d+-\d+$")
    event: TelegramV2OutboxEvent
    event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stream_row_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_event_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_index_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    promotion_ref: str = Field(pattern=r"^promotion:[a-f0-9]{64}$")
    promotion_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    intake_marker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    intake_gate_envelope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_batch_ref: str = Field(pattern=r"^batch:[a-f0-9]{64}$")
    transport_updates_observed: StrictInt = Field(ge=1, le=100)
    outcome_counts: TelegramV2OutcomeCounts
    sanitized_projection_count: StrictInt = Field(ge=1, le=100)
    ordered_members_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    atomic_snapshot_validated: Literal[True] = True
    marker_index_eligible: Literal[True] = True
    raw_update_included: Literal[False] = False
    telegram_identifiers_included: Literal[False] = False
    owner_private_stage_included: Literal[False] = False
    source_acknowledged: Literal[False] = False
    automatic_publication: Literal[False] = False
    production_wiring_observed: Literal[False] = False


def _require_eligible_v2_event(
    value: object,
) -> EligibleTelegramV2Event:
    if not isinstance(value, EligibleTelegramV2Event):
        raise TypeError("gtm_telegram_v2_eligible_event_required")
    grant = value._reader_grant
    payload_sha256 = _sha256_text(
        _canonical_json(value.model_dump(mode="json"))
    )
    if (
        not isinstance(grant, _EligibilityGrant)
        or grant.marker is not _ELIGIBILITY_GRANT
        or grant.owner_id != id(value)
        or grant.payload_sha256 != payload_sha256
    ):
        raise TypeError("gtm_telegram_v2_eligible_event_required")
    return value


class TelegramV2TriageDetails(TelegramTriageDetails):
    """v2 detail kept outside the general saved-page discriminator."""

    schema_version: Literal["coineasy-telegram-triage-detail@2"] = (
        "coineasy-telegram-triage-detail@2"
    )
    digest_scheme: Literal["hmac-sha256-v2"] = V2_DIGEST_SCHEME


class EligibleTelegramV2TriageItem(BaseModel):
    """Display model; construction alone grants no receipt or page authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-telegram-v2-triage-item@1"] = (
        "coineasy-telegram-v2-triage-item@1"
    )
    ref: str = Field(pattern=r"^telegram:squid:[a-f0-9]{64}$")
    domain: Literal[GtmDomain.TELEGRAM_TRIAGE] = GtmDomain.TELEGRAM_TRIAGE
    event_type: Literal["telegram.triage.v2"] = "telegram.triage.v2"
    client_id: Literal["squid"] = "squid"
    observed_at: datetime
    status: GtmStatus
    priority: GtmPriority
    title_ko: str = Field(min_length=3, max_length=160)
    summary_ko: str = Field(min_length=5, max_length=600)
    evidence: tuple[GtmEvidence, ...] = Field(min_length=1, max_length=8)
    lineage: GtmLineage
    policy: GtmPolicy = Field(default_factory=GtmPolicy)
    next_action: GtmNextAction
    details: TelegramV2TriageDetails
    source_stream_id: str = Field(pattern=r"^\d+-\d+$")
    source_event_ref: str = Field(pattern=r"^outbox-v2:[a-f0-9]{64}$")
    source_event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_projection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_promotion_ref: str = Field(pattern=r"^promotion:[a-f0-9]{64}$")
    eligibility_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    marker_index_eligible: Literal[True] = True
    read_only_projection: Literal[True] = True
    source_acknowledged: Literal[False] = False
    automatic_publication: Literal[False] = False

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "gtm_telegram_v2_item_time_invalid")

    @model_validator(mode="after")
    def validate_bindings(self) -> "EligibleTelegramV2TriageItem":
        digest = self.details.question_ref.removeprefix("question:")
        expected_ref = f"telegram:squid:{digest}"
        if self.ref != expected_ref or self.lineage.correlation_ref != expected_ref:
            raise ValueError("gtm_telegram_v2_item_ref_invalid")
        question_evidence = [
            item
            for item in self.evidence
            if item.kind == GtmEvidenceKind.QUESTION_DIGEST
        ]
        if len(question_evidence) != 1 or question_evidence[0].sha256 != digest:
            raise ValueError("gtm_telegram_v2_item_question_evidence_invalid")
        faq_evidence = [
            item
            for item in self.evidence
            if item.kind == GtmEvidenceKind.FAQ_RECEIPT
        ]
        if self.details.faq_binding_sha256 is None and faq_evidence:
            raise ValueError("gtm_telegram_v2_item_faq_evidence_invalid")
        if self.details.faq_binding_sha256 is not None and (
            len(faq_evidence) != 1
            or faq_evidence[0].sha256 != self.details.faq_binding_sha256
        ):
            raise ValueError("gtm_telegram_v2_item_faq_evidence_invalid")
        return self

    def canonical_item(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def item_sha256(self) -> str:
        return _sha256_text(_canonical_json(self.canonical_item()))


def _event_identity_digest(
    *,
    source_commit_ref: str,
    ordinal: int,
    projection_sha256: str,
) -> str:
    subject = {
        "schema_version": V2_OUTBOX_EVENT_IDENTITY_SCHEMA,
        "source_commit_ref": source_commit_ref,
        "source_projection_ordinal": ordinal,
        "projection_sha256": projection_sha256,
    }
    return _sha256_text(_canonical_json(subject))


def _build_event(
    projection: TelegramOwnerProjectionV2,
    *,
    source_commit_ref: str,
    source_stage_sha256: str,
    source_gate_sha256: str,
    source_projection_ordinal: int,
) -> TelegramV2OutboxEvent:
    projection_payload = projection.model_dump(mode="json")
    projection_sha = _sha256_text(_canonical_json(projection_payload))
    digest = _event_identity_digest(
        source_commit_ref=source_commit_ref,
        ordinal=source_projection_ordinal,
        projection_sha256=projection_sha,
    )
    return TelegramV2OutboxEvent.model_validate({
        "schema_version": V2_OUTBOX_EVENT_SCHEMA,
        "outbox_name": V2_OUTBOX_NAME,
        "client_id": "squid",
        "event_ref": f"outbox-v2:{digest}",
        "source_commit_ref": source_commit_ref,
        "source_stage_sha256": source_stage_sha256,
        "source_gate_sha256": source_gate_sha256,
        "source_projection_ordinal": source_projection_ordinal,
        "idempotency_key": digest,
        "projection_sha256": projection_sha,
        "raw_update_included": False,
        "telegram_identifiers_included": False,
        "owner_private_stage_included": False,
        "automatic_publication": False,
        "projection": projection_payload,
    })


def validate_v2_outbox_event(value: object) -> TelegramV2OutboxEvent:
    """Validate event identity only; this does not grant delivery eligibility."""

    try:
        event = TelegramV2OutboxEvent.model_validate(value)
    except ValidationError:
        event = None
    if event is None:
        _fail("gtm_telegram_v2_event_invalid")
    if len(_canonical_json(event).encode("utf-8")) > V2_EVENT_MAX_BYTES:
        _fail("gtm_telegram_v2_event_too_large")
    return event


@dataclass(frozen=True)
class _ValidatedSource:
    commit_ref: str
    marker_text: str
    gate_text: str
    gate_sha256: str
    stage_sha256: str
    source_batch_ref: str
    transport_updates_observed: int
    outcome_counts: dict[str, int]
    emitted: tuple[tuple[int, TelegramOwnerProjectionV2], ...]


def _validate_source(
    *,
    commit_ref: str,
    marker_value: object,
    gate_value: object,
) -> _ValidatedSource:
    marker, marker_text = _strict_json_object(
        marker_value,
        maximum=V2_INTAKE_MARKER_MAX_BYTES,
        code="gtm_telegram_v2_intake_marker_invalid",
    )
    if (
        commit_ref != f"commit:{_sha256_text(marker_text)}"
        or marker.get("schema_version") != INTAKE_COMMIT_SUBJECT_SCHEMA
    ):
        _fail("gtm_telegram_v2_intake_marker_binding_invalid")
    marker_fields = {
        "schema_version",
        "identity_context_ref",
        "source_epoch_ref",
        "source_batch_ref",
        "expected_cursor_sha256",
        "predecessor_head_ref",
        "next_offset_sha256",
        "stage_sha256",
        "transport_updates_observed",
        "members",
        "gate_sha256",
        "dispatch_score",
        "dispatch_offset_width",
        "legacy_shadow_dispatch_key",
        "v2_promotion_dispatch_key",
        "owner_private_stage",
        "legacy_effects_materialized",
        "sanitized_outbox_materialized",
    }
    if set(marker) != marker_fields:
        _fail("gtm_telegram_v2_intake_marker_fields_invalid")

    gate, gate_text = _strict_json_object(
        gate_value,
        maximum=V2_SANITIZED_GATE_MAX_BYTES,
        code="gtm_telegram_v2_sanitized_gate_invalid",
    )
    if set(gate) != {
        "schema_version",
        "commit_ref",
        "gate_sha256",
        "gate_subject",
    }:
        _fail("gtm_telegram_v2_sanitized_gate_fields_invalid")
    gate_subject = gate.get("gate_subject")
    gate_sha = gate.get("gate_sha256")
    if (
        gate.get("schema_version") != INTAKE_SANITIZED_GATE_ENVELOPE_SCHEMA
        or gate.get("commit_ref") != commit_ref
        or type(gate_subject) is not dict
        or type(gate_sha) is not str
        or _SHA256_RE.fullmatch(gate_sha) is None
        or gate_sha != _sha256_text(_canonical_json(gate_subject))
        or marker.get("gate_sha256") != gate_sha
    ):
        _fail("gtm_telegram_v2_sanitized_gate_binding_invalid")
    gate_subject_fields = {
        "schema_version",
        "identity_context_ref",
        "source_epoch_ref",
        "source_batch_ref",
        "previous_cursor_sha256",
        "predecessor_head_ref",
        "next_offset",
        "staged_at",
        "transport_updates_observed",
        "stage_sha256",
        "updates",
        "raw_update_included",
        "private_legacy_payload_included",
        "telegram_identifiers_included",
        "sanitized_projections_included",
    }
    if set(gate_subject) != gate_subject_fields:
        _fail("gtm_telegram_v2_sanitized_gate_subject_fields_invalid")

    stage_sha = gate_subject.get("stage_sha256")
    source_batch_ref = gate_subject.get("source_batch_ref")
    transport_count = gate_subject.get("transport_updates_observed")
    updates = gate_subject.get("updates")
    if (
        gate_subject.get("schema_version") != INTAKE_SANITIZED_GATE_SUBJECT_SCHEMA
        or type(gate_subject.get("identity_context_ref")) is not str
        or _CONTEXT_REF_RE.fullmatch(gate_subject["identity_context_ref"]) is None
        or type(gate_subject.get("source_epoch_ref")) is not str
        or _SOURCE_EPOCH_RE.fullmatch(gate_subject["source_epoch_ref"]) is None
        or type(source_batch_ref) is not str
        or _SOURCE_BATCH_RE.fullmatch(source_batch_ref) is None
        or type(gate_subject.get("previous_cursor_sha256")) is not str
        or _SHA256_RE.fullmatch(gate_subject["previous_cursor_sha256"]) is None
        or type(gate_subject.get("predecessor_head_ref")) is not str
        or _HEAD_REF_RE.fullmatch(gate_subject["predecessor_head_ref"]) is None
        or type(gate_subject.get("next_offset")) is not int
        or gate_subject["next_offset"] < 1
        or type(gate_subject.get("staged_at")) is not str
        or _UTC_Z_RE.fullmatch(gate_subject["staged_at"]) is None
        or type(transport_count) is not int
        or not 1 <= transport_count <= 100
        or type(stage_sha) is not str
        or _SHA256_RE.fullmatch(stage_sha) is None
        or type(updates) is not list
        or len(updates) != transport_count
        or gate_subject.get("raw_update_included") is not False
        or gate_subject.get("private_legacy_payload_included") is not False
        or gate_subject.get("telegram_identifiers_included") is not False
        or gate_subject.get("sanitized_projections_included") is not True
    ):
        _fail("gtm_telegram_v2_sanitized_gate_subject_invalid")
    if (
        type(marker.get("transport_updates_observed")) is not int
        or type(marker.get("dispatch_offset_width")) is not int
        or marker.get("stage_sha256") != stage_sha
        or marker.get("source_batch_ref") != source_batch_ref
        or marker.get("identity_context_ref") != gate_subject["identity_context_ref"]
        or marker.get("source_epoch_ref") != gate_subject["source_epoch_ref"]
        or marker.get("transport_updates_observed") != transport_count
        or marker.get("expected_cursor_sha256")
        != gate_subject["previous_cursor_sha256"]
        or marker.get("predecessor_head_ref")
        != gate_subject["predecessor_head_ref"]
        or marker.get("next_offset_sha256")
        != _sha256_text(str(gate_subject["next_offset"]))
        or marker.get("v2_promotion_dispatch_key")
        != INTAKE_V2_PROMOTION_DISPATCH_KEY
        or marker.get("dispatch_score") != INTAKE_DISPATCH_SCORE
        or marker.get("dispatch_offset_width") != INTAKE_DISPATCH_OFFSET_WIDTH
        or marker.get("legacy_shadow_dispatch_key")
        != INTAKE_LEGACY_SHADOW_DISPATCH_KEY
        or marker.get("owner_private_stage") is not True
        or marker.get("legacy_effects_materialized") is not False
        or marker.get("sanitized_outbox_materialized") is not False
    ):
        _fail("gtm_telegram_v2_intake_marker_gate_mismatch")

    marker_members = marker.get("members")
    if type(marker_members) is not list or len(marker_members) != transport_count:
        _fail("gtm_telegram_v2_intake_marker_members_invalid")
    outcome_counts = {"emitted": 0, "tombstoned": 0, "not_applicable": 0}
    emitted: list[tuple[int, TelegramOwnerProjectionV2]] = []
    question_refs: set[str] = set()
    projection_shas: set[str] = set()
    for ordinal, update in enumerate(updates):
        if type(update) is not dict or set(update) != {
            "ordinal",
            "update_ref",
            "member_binding_sha256",
            "projection_state",
            "projection_sha256",
            "projection_member",
            "tombstone_reason",
        }:
            _fail("gtm_telegram_v2_gate_update_invalid")
        state = update.get("projection_state")
        update_ref = update.get("update_ref")
        binding = update.get("member_binding_sha256")
        marker_member = marker_members[ordinal]
        if (
            type(marker_member) is not dict
            or set(marker_member) != {
                "ordinal",
                "update_ref",
                "member_binding_sha256",
                "legacy_payload_hmac_sha256",
                "projection_sha256",
                "projection_state",
            }
            or type(marker_member.get("ordinal")) is not int
            or marker_member.get("ordinal") != ordinal
            or marker_member.get("update_ref") != update_ref
            or marker_member.get("member_binding_sha256") != binding
            or marker_member.get("projection_state") != state
            or marker_member.get("projection_sha256") != update.get("projection_sha256")
            or type(marker_member.get("legacy_payload_hmac_sha256")) is not str
            or _SHA256_RE.fullmatch(marker_member["legacy_payload_hmac_sha256"])
            is None
        ):
            _fail("gtm_telegram_v2_marker_member_mismatch")
        if (
            type(update.get("ordinal")) is not int
            or update.get("ordinal") != ordinal
            or type(update_ref) is not str
            or _UPDATE_REF_RE.fullmatch(update_ref) is None
            or type(binding) is not str
            or _SHA256_RE.fullmatch(binding) is None
            or type(state) is not str
            or state not in outcome_counts
        ):
            _fail("gtm_telegram_v2_gate_update_invalid")

        projection_member = update.get("projection_member")
        projection_sha = update.get("projection_sha256")
        tombstone_reason = update.get("tombstone_reason")
        if state == "emitted":
            if (
                type(projection_member) is not dict
                or set(projection_member) != {
                    "question_ref",
                    "projection_sha256",
                    "projection",
                }
                or type(projection_sha) is not str
                or _SHA256_RE.fullmatch(projection_sha) is None
                or tombstone_reason is not None
            ):
                _fail("gtm_telegram_v2_gate_projection_invalid")
            projection = _strict_projection(projection_member.get("projection"))
            actual_sha = _sha256_text(
                _canonical_json(projection.model_dump(mode="json"))
            )
            if (
                projection_member.get("projection_sha256") != actual_sha
                or projection_sha != actual_sha
                or projection_member.get("question_ref") != projection.question_ref
                or projection.question_ref in question_refs
                or actual_sha in projection_shas
            ):
                _fail("gtm_telegram_v2_gate_projection_invalid")
            question_refs.add(projection.question_ref)
            projection_shas.add(actual_sha)
            emitted.append((ordinal, projection))
        elif state == "tombstoned":
            if (
                projection_member is not None
                or projection_sha is not None
                or type(tombstone_reason) is not str
                or tombstone_reason not in _TOMBSTONE_REASONS
            ):
                _fail("gtm_telegram_v2_gate_tombstone_invalid")
        elif (
            projection_member is not None
            or projection_sha is not None
            or tombstone_reason is not None
        ):
            _fail("gtm_telegram_v2_gate_not_applicable_invalid")
        outcome_counts[str(state)] += 1

    return _ValidatedSource(
        commit_ref=commit_ref,
        marker_text=marker_text,
        gate_text=gate_text,
        gate_sha256=gate_sha,
        stage_sha256=stage_sha,
        source_batch_ref=source_batch_ref,
        transport_updates_observed=transport_count,
        outcome_counts=outcome_counts,
        emitted=tuple(emitted),
    )


def _promotion_subject(
    source: _ValidatedSource,
    events: tuple[TelegramV2OutboxEvent, ...],
) -> dict[str, object]:
    ordered_members = []
    for event in events:
        event_payload = event.model_dump(mode="json")
        event_sha = _sha256_text(_canonical_json(event_payload))
        ordered_members.append({
            "source_projection_ordinal": event.source_projection_ordinal,
            "event_ref": event.event_ref,
            "idempotency_key": event.idempotency_key,
            "question_ref": event.projection.question_ref,
            "projection_sha256": event.projection_sha256,
            "event_sha256": event_sha,
        })
    return {
        "schema_version": V2_PROMOTION_SUBJECT_SCHEMA,
        "outbox_name": V2_OUTBOX_NAME,
        "source_commit_ref": source.commit_ref,
        "source_stage_sha256": source.stage_sha256,
        "source_gate_sha256": source.gate_sha256,
        "source_batch_ref": source.source_batch_ref,
        "transport_updates_observed": source.transport_updates_observed,
        "outcome_counts": dict(source.outcome_counts),
        "sanitized_projection_count": len(events),
        "ordered_members": ordered_members,
        "complete_source_stage_outcomes": True,
        "raw_update_included": False,
        "telegram_identifiers_included": False,
        "owner_private_stage_included": False,
        "legacy_effects_materialized": False,
        "consumer_delivery_observed": False,
        "automatic_publication": False,
        "production_wiring_observed": False,
    }


def read_eligible_telegram_v2_event(
    snapshot: dict[str, object],
) -> EligibleTelegramV2Event:
    """Validate one exact serialized six-object snapshot and grant eligibility."""

    if type(snapshot) is not dict:
        _fail("gtm_telegram_v2_snapshot_dict_required")
    try:
        evidence = TelegramV2ReaderSnapshot.model_validate(snapshot)
    except (TypeError, ValueError, ValidationError, RecursionError):
        evidence = None
    if evidence is None:
        _fail("gtm_telegram_v2_snapshot_invalid")

    event_payload, event_text = _strict_json_object(
        evidence.stream_row.event_json,
        maximum=V2_EVENT_MAX_BYTES,
        code="gtm_telegram_v2_event_json_invalid",
    )
    event = validate_v2_outbox_event(event_payload)
    if _canonical_json(event.model_dump(mode="json")) != event_text:
        _fail("gtm_telegram_v2_event_not_canonical")
    event_sha = _sha256_text(event_text)
    if (
        evidence.stream_row.schema_version != V2_OUTBOX_EVENT_SCHEMA
        or evidence.stream_row.idempotency_key != event.idempotency_key
        or evidence.stream_row.event_sha256 != event_sha
        or evidence.stream_row.projection_sha256 != event.projection_sha256
    ):
        _fail("gtm_telegram_v2_stream_row_binding_invalid")

    source = _validate_source(
        commit_ref=event.source_commit_ref,
        marker_value=evidence.intake_marker_json,
        gate_value=evidence.sanitized_gate_json,
    )
    if (
        source.stage_sha256 != event.source_stage_sha256
        or source.gate_sha256 != event.source_gate_sha256
    ):
        _fail("gtm_telegram_v2_event_source_binding_invalid")
    expected_events = tuple(
        _build_event(
            projection,
            source_commit_ref=source.commit_ref,
            source_stage_sha256=source.stage_sha256,
            source_gate_sha256=source.gate_sha256,
            source_projection_ordinal=ordinal,
        )
        for ordinal, projection in source.emitted
    )
    if sum(candidate == event for candidate in expected_events) != 1:
        _fail("gtm_telegram_v2_event_not_in_source")

    promotion_subject = _promotion_subject(source, expected_events)
    expected_manifest = _canonical_json(promotion_subject)
    promotion_marker, promotion_text = _strict_json_object(
        evidence.promotion_marker_json,
        maximum=V2_PROMOTION_MANIFEST_MAX_BYTES,
        code="gtm_telegram_v2_promotion_marker_invalid",
    )
    if promotion_marker != promotion_subject or promotion_text != expected_manifest:
        _fail("gtm_telegram_v2_promotion_marker_mismatch")
    promotion_ref = f"promotion:{_sha256_text(promotion_text)}"
    if (
        _PROMOTION_REF_RE.fullmatch(promotion_ref) is None
        or evidence.source_promotion_index_value != promotion_ref
    ):
        _fail("gtm_telegram_v2_source_index_invalid")

    expected_index = "|".join((
        "v2",
        event_sha,
        event.projection_sha256,
        event.source_commit_ref,
        str(event.source_projection_ordinal),
        promotion_ref,
        evidence.stream_id,
    ))
    if evidence.current_event_index_value != expected_index:
        _fail("gtm_telegram_v2_event_index_invalid")

    stream_row_subject = {
        "schema_version": V2_STREAM_ROW_SUBJECT_SCHEMA,
        "source_stream_key": evidence.source_stream_key,
        "stream_id": evidence.stream_id,
        "fields": evidence.stream_row.model_dump(mode="json"),
    }
    outcome = TelegramV2OutcomeCounts.model_validate(source.outcome_counts)
    eligible = EligibleTelegramV2Event(
        stream_id=evidence.stream_id,
        event=event,
        event_sha256=event_sha,
        stream_row_sha256=_sha256_text(_canonical_json(stream_row_subject)),
        current_event_index_sha256=_sha256_text(
            evidence.current_event_index_value
        ),
        source_index_binding_sha256=_sha256_text(
            evidence.source_promotion_index_value
        ),
        promotion_ref=promotion_ref,
        promotion_manifest_sha256=_sha256_text(promotion_text),
        intake_marker_sha256=_sha256_text(source.marker_text),
        intake_gate_envelope_sha256=_sha256_text(source.gate_text),
        source_batch_ref=source.source_batch_ref,
        transport_updates_observed=source.transport_updates_observed,
        outcome_counts=outcome,
        sanitized_projection_count=len(expected_events),
        ordered_members_sha256=_sha256_text(
            _canonical_json(promotion_subject["ordered_members"])
        ),
    )
    eligible._reader_grant = _EligibilityGrant(
        marker=_ELIGIBILITY_GRANT,
        owner_id=id(eligible),
        payload_sha256=_sha256_text(
            _canonical_json(eligible.model_dump(mode="json"))
        ),
    )
    return eligible


def project_telegram_v2_delivery(
    eligible: EligibleTelegramV2Event,
) -> EligibleTelegramV2TriageItem:
    """Project only an opaque eligible v2 event into a human-review item."""

    eligible = _require_eligible_v2_event(eligible)
    source = eligible.event.projection
    status, priority, next_action, title_ko, summary_ko = _item_state(source)
    digest = source.question_hmac_sha256
    opaque_ref = f"telegram:squid:{digest}"
    escalation_codes = _SAFETY_ESCALATIONS[source.safety_class]
    topic_ko = _TOPIC_KO[source.topic]
    if escalation_codes:
        next_action_ko = "운영자가 원문 시스템에서 안전 문제를 확인합니다."
    elif source.draft_reply_ko:
        next_action_ko = "운영자가 검증된 FAQ 초안을 확인합니다."
    elif source.answer_state == "unanswered":
        next_action_ko = "운영자가 공식 출처를 확인하고 답변 여부를 결정합니다."
    else:
        next_action_ko = "추가 조치가 필요하지 않습니다."
    if not title_ko or not summary_ko or not topic_ko:
        raise AssertionError("gtm_telegram_v2_projection_state_invalid")

    details = TelegramV2TriageDetails(
        question_ref=source.question_ref,
        topic=source.topic,
        question_summary_ko=source.question_summary_ko,
        answer_state=source.answer_state,
        faq_match=source.faq_match,
        faq_source_sha256=source.faq_source_sha256,
        faq_binding_sha256=source.faq_binding_sha256,
        draft_reply_ko=source.draft_reply_ko,
        next_action_ko=next_action_ko,
        escalation_codes=escalation_codes,
    )
    evidence_items = [
        GtmEvidence(
            kind=GtmEvidenceKind.QUESTION_DIGEST,
            sha256=digest,
            observed_at=source.question_observed_at,
        )
    ]
    if source.faq_binding_sha256 is not None:
        evidence_items.append(GtmEvidence(
            kind=GtmEvidenceKind.FAQ_RECEIPT,
            sha256=source.faq_binding_sha256,
            observed_at=source.observed_at,
        ))
    eligibility_sha = _sha256_text(
        _canonical_json(eligible.model_dump(mode="json"))
    )
    return EligibleTelegramV2TriageItem(
        ref=opaque_ref,
        domain=GtmDomain.TELEGRAM_TRIAGE,
        event_type="telegram.triage.v2",
        client_id="squid",
        observed_at=source.observed_at,
        status=status,
        priority=priority,
        title_ko=title_ko,
        summary_ko=summary_ko,
        evidence=tuple(evidence_items),
        lineage=GtmLineage(correlation_ref=opaque_ref),
        next_action=next_action,
        details=details,
        source_stream_id=eligible.stream_id,
        source_event_ref=eligible.event.event_ref,
        source_event_sha256=eligible.event_sha256,
        source_projection_sha256=eligible.event.projection_sha256,
        source_promotion_ref=eligible.promotion_ref,
        eligibility_sha256=eligibility_sha,
    )


__all__ = [
    "EligibleTelegramV2Event",
    "EligibleTelegramV2TriageItem",
    "TelegramOwnerProjectionV2",
    "TelegramV2OutboxEvent",
    "TelegramV2ReaderError",
    "TelegramV2ReaderIneligible",
    "TelegramV2ReaderSnapshot",
    "TelegramV2StreamRow",
    "TelegramV2TriageDetails",
    "project_telegram_v2_delivery",
    "read_eligible_telegram_v2_event",
    "validate_v2_outbox_event",
]
