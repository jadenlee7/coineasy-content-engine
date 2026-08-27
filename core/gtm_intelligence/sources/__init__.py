"""Dependency-free adapters for already sanitized owner projections."""

from .ops import (
    AuthorizedSanitizedRailwayOpsRecord,
    MissingReasonCode,
    SanitizedRailwayRuntimeReceipt,
    project_squid_railway_ops,
)
from .telegram import TelegramOwnerProjection, project_telegram_triage
from .telegram_v2 import (
    EligibleTelegramV2Event,
    EligibleTelegramV2TriageItem,
    TelegramOwnerProjectionV2,
    TelegramV2ReaderError,
    TelegramV2ReaderIneligible,
    TelegramV2ReaderSnapshot,
    project_telegram_v2_delivery,
    read_eligible_telegram_v2_event,
    validate_v2_outbox_event,
)
from .x_qa import (
    SanitizedXQaOwnerProjection,
    X_QA_VERDICTS,
    X_SIGNAL_KINDS,
    project_squid_x_qa,
    project_squid_x_qa_records,
)

__all__ = [
    "AuthorizedSanitizedRailwayOpsRecord",
    "EligibleTelegramV2Event",
    "EligibleTelegramV2TriageItem",
    "MissingReasonCode",
    "SanitizedRailwayRuntimeReceipt",
    "SanitizedXQaOwnerProjection",
    "TelegramOwnerProjection",
    "TelegramOwnerProjectionV2",
    "TelegramV2ReaderError",
    "TelegramV2ReaderIneligible",
    "TelegramV2ReaderSnapshot",
    "X_QA_VERDICTS",
    "X_SIGNAL_KINDS",
    "project_squid_railway_ops",
    "project_squid_x_qa",
    "project_squid_x_qa_records",
    "project_telegram_triage",
    "project_telegram_v2_delivery",
    "read_eligible_telegram_v2_event",
    "validate_v2_outbox_event",
]
