"""Stable, non-sensitive diagnostics for Squid visual localization failures.

Provider text and exception messages must never cross the generation boundary.
Only the code-owned identifiers below may be stored in job ledgers or returned
to Studio automation.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


SQUID_LOCALIZATION_DIAGNOSTIC_VERSION = "squid-localization-failure@1"
SQUID_LOCALIZATION_FAILURE_UNSPECIFIED = (
    "squid_localization_failure_unspecified"
)

SQUID_LOCALIZATION_REASON_ACTIONS: Mapping[str, str] = MappingProxyType({
    "squid_copy_discovery_unavailable": "retry_generation",
    "squid_copy_discovery_invalid": "inspect_localization_contract",
    "squid_placement_audit_unavailable": "retry_generation",
    "squid_placement_audit_unsafe": "prepare_approved_clean_plate",
    "squid_placement_validation_rejected": "inspect_localization_contract",
    "squid_source_text_probe_failed": "inspect_localization_contract",
    "squid_localization_spec_invalid": "inspect_localization_contract",
    "squid_approved_clean_plate_unavailable": "repair_approved_clean_plate",
    "squid_source_cleanup_rejected": "prepare_approved_clean_plate",
    "squid_source_cleanup_unavailable": "retry_generation",
    "squid_translation_layout_rejected": "review_translation_layout",
    SQUID_LOCALIZATION_FAILURE_UNSPECIFIED: "inspect_localization_contract",
})

SQUID_LOCALIZATION_REASON_CODES = frozenset(
    SQUID_LOCALIZATION_REASON_ACTIONS
)

# Preserve bounded retries for failures that can change across provider call
# slots or transient local execution. Explicit safety rejections and reviewed
# asset failures require a person instead of repeating the same job.
SQUID_LOCALIZATION_RETRYABLE_REASONS = frozenset({
    "squid_copy_discovery_unavailable",
    "squid_copy_discovery_invalid",
    "squid_placement_audit_unavailable",
    "squid_placement_validation_rejected",
    "squid_source_text_probe_failed",
    "squid_localization_spec_invalid",
    "squid_source_cleanup_unavailable",
    SQUID_LOCALIZATION_FAILURE_UNSPECIFIED,
})

SQUID_LOCALIZATION_REASON_STATUSES: Mapping[str, frozenset[str]] = (
    MappingProxyType({
        "squid_copy_discovery_unavailable": frozenset({"cleanup_failed"}),
        "squid_copy_discovery_invalid": frozenset({"cleanup_failed"}),
        "squid_placement_audit_unavailable": frozenset({
            "cleanup_failed",
            "unsafe_placement",
        }),
        "squid_placement_audit_unsafe": frozenset({
            "cleanup_failed",
            "unsafe_placement",
        }),
        "squid_placement_validation_rejected": frozenset({
            "cleanup_failed",
            "unsafe_placement",
        }),
        "squid_source_text_probe_failed": frozenset({"cleanup_failed"}),
        "squid_localization_spec_invalid": frozenset({"unsafe_placement"}),
        "squid_approved_clean_plate_unavailable": frozenset({
            "cleanup_failed",
        }),
        "squid_source_cleanup_rejected": frozenset({"cleanup_failed"}),
        "squid_source_cleanup_unavailable": frozenset({"cleanup_failed"}),
        "squid_translation_layout_rejected": frozenset({"unsafe_placement"}),
        SQUID_LOCALIZATION_FAILURE_UNSPECIFIED: frozenset({
            "cleanup_failed",
            "unsafe_placement",
        }),
    })
)


def normalize_squid_localization_reason(value: object) -> str:
    """Return one allowlisted diagnostic without exposing arbitrary input."""
    if isinstance(value, str) and value in SQUID_LOCALIZATION_REASON_CODES:
        return value
    return SQUID_LOCALIZATION_FAILURE_UNSPECIFIED


def normalize_squid_localization_reason_for_status(
    value: object,
    status: object,
) -> str:
    """Return one reason only when it is valid for the failure status."""
    reason = normalize_squid_localization_reason(value)
    if (
        isinstance(status, str)
        and status in SQUID_LOCALIZATION_REASON_STATUSES[reason]
    ):
        return reason
    return SQUID_LOCALIZATION_FAILURE_UNSPECIFIED


def squid_localization_reason_action(value: object) -> str:
    """Return the code-owned operator action for one diagnostic."""
    reason = normalize_squid_localization_reason(value)
    return SQUID_LOCALIZATION_REASON_ACTIONS[reason]


def squid_localization_reason_retryable(value: object) -> bool:
    """Apply the bounded automation retry policy for one diagnostic."""
    reason = normalize_squid_localization_reason(value)
    return reason in SQUID_LOCALIZATION_RETRYABLE_REASONS


def mark_squid_visual_localization_failure(
    spec: dict,
    *,
    status: str,
    reason_code: object,
) -> dict:
    """Atomically preserve the source visual and attach one safe reason."""
    if status not in {"cleanup_failed", "unsafe_placement"}:
        raise ValueError("Squid visual localization failure status is invalid")
    spec["source_text_visible"] = False
    spec["translation_regions"] = []
    spec["visual_localization_status"] = status
    spec["visual_localization_reason_code"] = (
        normalize_squid_localization_reason_for_status(reason_code, status)
    )
    return spec
