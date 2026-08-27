"""Pure adapter for an owner-redacted Telegram triage projection.

The existing ``coineasydaily`` owner process is the sole Telegram update
consumer.  This module deliberately has no Telegram client, token, offset,
webhook, buffer, environment, network, database, filesystem, send, or mutation
surface.  It accepts only a record produced *after* owner-side redaction and
turns that record into the common read-only GTM operator item.

The keyed HMAC cannot be authenticated here because the owner key must never
cross this boundary.  The adapter validates only the fixed scheme marker,
digest shape, and exact cross-binding between the opaque question reference
and evidence digest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import (
    GtmDomain,
    GtmEvidence,
    GtmEvidenceKind,
    GtmLineage,
    GtmNextAction,
    GtmOperatorItem,
    GtmPriority,
    GtmStatus,
    TelegramTriageDetails,
    _safe_redacted_text,
    _telegram_faq_binding_sha256,
    _utc_seconds,
)


TelegramTopic = Literal[
    "onboarding",
    "product",
    "technical",
    "event",
    "rewards",
    "wallet_security",
    "investment",
    "privacy",
    "other",
]
TelegramAnswerState = Literal["unanswered", "answered", "resolved"]
TelegramFaqMatch = Literal["none", "partial", "exact"]
TelegramSafetyClass = Literal[
    "none",
    "investment",
    "legal",
    "privacy",
    "reward_account",
    "security",
    "unknown",
    "wallet_signing",
]


_SAFETY_ESCALATIONS: dict[str, tuple[str, ...]] = {
    "none": (),
    "investment": ("investment",),
    "legal": ("legal",),
    "privacy": ("privacy",),
    "reward_account": ("reward_account",),
    "security": ("security",),
    "unknown": ("unknown",),
    "wallet_signing": ("security", "wallet_signing"),
}
_SAFETY_TOPICS: dict[str, frozenset[str]] = {
    "investment": frozenset({"investment"}),
    "privacy": frozenset({"privacy"}),
    "reward_account": frozenset({"rewards"}),
    "security": frozenset({"technical", "wallet_security"}),
    "wallet_signing": frozenset({"wallet_security"}),
}
_TOPIC_KO = {
    "onboarding": "온보딩",
    "product": "제품",
    "technical": "기술",
    "event": "이벤트",
    "rewards": "리워드",
    "wallet_security": "지갑 보안",
    "investment": "투자",
    "privacy": "개인정보",
    "other": "기타",
}
_HANGUL_PATTERN = re.compile(r"[가-힣]")
_TELEGRAM_IDENTIFIER_PATTERN = re.compile(r"(?<!\d)-?\d{6,19}(?!\d)")


class TelegramOwnerProjection(BaseModel):
    """Strict input emitted downstream of the existing owner consumer.

    The literal boundary markers are assertions from the reviewed owner
    projection.  They do not prove source authenticity; a caller must obtain
    this value from the separately approved owner-side projection.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-telegram-owner-projection@1"]
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
    digest_scheme: Literal["hmac-sha256-v1"]
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
            "schema_version": "coineasy-telegram-owner-projection@1",
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
            "digest_scheme": "hmac-sha256-v1",
        }
        if isinstance(value, Mapping):
            for field_name, exact_value in expected.items():
                if field_name not in value:
                    continue
                candidate = value[field_name]
                if (
                    type(candidate) is not type(exact_value)
                    or candidate != exact_value
                ):
                    raise ValueError("gtm_telegram_source_boundary_invalid")
        return value

    @field_validator("observed_at", "question_observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "gtm_telegram_source_time_invalid")

    @field_validator("question_summary_ko")
    @classmethod
    def validate_question_summary(cls, value: str) -> str:
        normalized = _safe_redacted_text(
            value,
            "gtm_telegram_source_summary_invalid",
            5,
            500,
        )
        if (
            _HANGUL_PATTERN.search(normalized) is None
            or _TELEGRAM_IDENTIFIER_PATTERN.search(normalized)
        ):
            raise ValueError("gtm_telegram_source_summary_invalid")
        return normalized

    @field_validator("draft_reply_ko")
    @classmethod
    def validate_draft_reply(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = _safe_redacted_text(
            value,
            "gtm_telegram_source_draft_invalid",
            10,
            600,
        )
        if (
            _HANGUL_PATTERN.search(normalized) is None
            or _TELEGRAM_IDENTIFIER_PATTERN.search(normalized)
        ):
            raise ValueError("gtm_telegram_source_draft_invalid")
        return normalized

    @model_validator(mode="after")
    def validate_owner_projection(self) -> "TelegramOwnerProjection":
        digest = self.question_ref.removeprefix("question:")
        if digest != self.question_hmac_sha256:
            raise ValueError("gtm_telegram_source_hmac_binding_invalid")
        if self.question_observed_at > self.observed_at:
            raise ValueError("gtm_telegram_source_question_time_invalid")

        topic_allowlist = _SAFETY_TOPICS.get(self.safety_class)
        if topic_allowlist is not None and self.topic not in topic_allowlist:
            raise ValueError("gtm_telegram_source_safety_topic_invalid")

        has_faq_source = self.faq_source_sha256 is not None
        has_faq_binding = self.faq_binding_sha256 is not None
        has_draft = self.draft_reply_ko is not None
        if self.safety_class != "none":
            if (
                self.faq_match != "none"
                or has_faq_source
                or has_faq_binding
                or has_draft
            ):
                raise ValueError("gtm_telegram_source_escalated_reply_invalid")
        elif self.answer_state != "unanswered":
            if (
                self.faq_match != "none"
                or has_faq_source
                or has_faq_binding
                or has_draft
            ):
                raise ValueError("gtm_telegram_source_closed_reply_invalid")
        elif self.faq_match == "none":
            if has_faq_source or has_faq_binding or has_draft:
                raise ValueError("gtm_telegram_source_unbound_reply_invalid")
        elif not has_faq_source or not has_faq_binding or not has_draft:
            raise ValueError("gtm_telegram_source_faq_binding_missing")
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
                raise ValueError("gtm_telegram_source_faq_binding_invalid")
        return self


def _item_state(
    projection: TelegramOwnerProjection,
) -> tuple[GtmStatus, GtmPriority, GtmNextAction, str, str]:
    escalation_codes = _SAFETY_ESCALATIONS[projection.safety_class]
    topic_ko = _TOPIC_KO[projection.topic]
    if escalation_codes:
        return (
            GtmStatus.BLOCKED,
            GtmPriority.HIGH,
            GtmNextAction(code="investigate", human_required=True),
            "커뮤니티 질문 안전 검토",
            (
                f"비식별화된 {topic_ko} 질문에 "
                "운영자 안전 확인이 필요합니다."
            ),
        )
    if projection.answer_state == "unanswered" and projection.draft_reply_ko:
        return (
            GtmStatus.NEEDS_REVIEW,
            GtmPriority.HIGH,
            GtmNextAction(code="reply_draft", human_required=True),
            "FAQ 답변 초안 검토",
            (
                f"비식별화된 {topic_ko} 질문의 "
                "검증된 FAQ 초안을 확인합니다."
            ),
        )
    if projection.answer_state == "unanswered":
        return (
            GtmStatus.NEEDS_REVIEW,
            GtmPriority.NORMAL,
            GtmNextAction(code="review", human_required=True),
            "미해결 커뮤니티 질문",
            (
                f"비식별화된 {topic_ko} 질문의 "
                "공식 답변 근거를 확인합니다."
            ),
        )
    return (
        GtmStatus.INFO,
        GtmPriority.NORMAL,
        GtmNextAction(code="no_action", human_required=False),
        "커뮤니티 질문 처리 상태",
        f"비식별화된 {topic_ko} 질문의 처리 상태를 확인했습니다.",
    )


def project_telegram_triage(
    projection: TelegramOwnerProjection | Mapping[str, object],
) -> GtmOperatorItem:
    """Convert one pre-redacted Squid owner record into a read-only item."""

    source = (
        projection
        if isinstance(projection, TelegramOwnerProjection)
        else TelegramOwnerProjection.model_validate(projection)
    )
    status, priority, next_action, title_ko, summary_ko = _item_state(source)
    digest = source.question_hmac_sha256
    opaque_ref = f"telegram:squid:{digest}"
    escalation_codes = _SAFETY_ESCALATIONS[source.safety_class]
    if escalation_codes:
        next_action_ko = (
            "운영자가 원문 시스템에서 "
            "안전 문제를 확인합니다."
        )
    elif source.draft_reply_ko:
        next_action_ko = "운영자가 검증된 FAQ 초안을 확인합니다."
    elif source.answer_state == "unanswered":
        next_action_ko = (
            "운영자가 공식 출처를 확인하고 "
            "답변 여부를 결정합니다."
        )
    else:
        next_action_ko = "추가 조치가 필요하지 않습니다."
    details = TelegramTriageDetails(
        question_ref=source.question_ref,
        digest_scheme=source.digest_scheme,
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
    evidence = [
        GtmEvidence(
            kind=GtmEvidenceKind.QUESTION_DIGEST,
            sha256=digest,
            observed_at=source.question_observed_at,
        )
    ]
    if source.faq_binding_sha256 is not None:
        evidence.append(GtmEvidence(
            kind=GtmEvidenceKind.FAQ_RECEIPT,
            sha256=source.faq_binding_sha256,
            observed_at=source.observed_at,
        ))
    return GtmOperatorItem(
        ref=opaque_ref,
        domain=GtmDomain.TELEGRAM_TRIAGE,
        event_type="telegram.triage.v1",
        client_id="squid",
        observed_at=source.observed_at,
        status=status,
        priority=priority,
        title_ko=title_ko,
        summary_ko=summary_ko,
        evidence=tuple(evidence),
        lineage=GtmLineage(correlation_ref=opaque_ref),
        next_action=next_action,
        details=details,
    )


__all__ = ["TelegramOwnerProjection", "project_telegram_triage"]
