import json
from pathlib import Path

import pytest

from core.squid_localization_diagnostics import (
    SQUID_LOCALIZATION_DIAGNOSTIC_VERSION,
    SQUID_LOCALIZATION_FAILURE_UNSPECIFIED,
    SQUID_LOCALIZATION_REASON_ACTIONS,
    SQUID_LOCALIZATION_REASON_CODES,
    SQUID_LOCALIZATION_REASON_STATUSES,
    mark_squid_visual_localization_failure,
    normalize_squid_localization_reason,
    normalize_squid_localization_reason_for_status,
    squid_localization_reason_action,
    squid_localization_reason_retryable,
)


CONTRACT_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "squid_localization_failure_contract.json"
)


def test_squid_localization_diagnostics_are_allowlisted_and_actionable():
    assert normalize_squid_localization_reason(
        "squid_placement_audit_unsafe"
    ) == "squid_placement_audit_unsafe"
    assert squid_localization_reason_action(
        "squid_placement_audit_unsafe"
    ) == "prepare_approved_clean_plate"
    assert squid_localization_reason_retryable(
        "squid_placement_audit_unsafe"
    ) is False
    assert squid_localization_reason_retryable(
        "squid_placement_audit_unavailable"
    ) is True


@pytest.mark.parametrize(
    "value",
    [None, "", "provider said secret-token", "x" * 500],
)
def test_unknown_squid_localization_diagnostics_collapse_safely(value):
    assert normalize_squid_localization_reason(value) == (
        SQUID_LOCALIZATION_FAILURE_UNSPECIFIED
    )


def test_squid_localization_contract_fixture_matches_python_policy():
    contract = json.loads(CONTRACT_PATH.read_text())

    assert contract["diagnostic_version"] == (
        SQUID_LOCALIZATION_DIAGNOSTIC_VERSION
    )
    assert set(contract["reasons"]) == SQUID_LOCALIZATION_REASON_CODES
    for reason_code, expected in contract["reasons"].items():
        assert SQUID_LOCALIZATION_REASON_ACTIONS[reason_code] == (
            expected["action_code"]
        )
        assert squid_localization_reason_retryable(reason_code) is (
            expected["retryable"]
        )
        assert SQUID_LOCALIZATION_REASON_STATUSES[reason_code] == frozenset(
            expected["allowed_statuses"]
        )


def test_reason_must_match_the_failure_status():
    assert normalize_squid_localization_reason_for_status(
        "squid_translation_layout_rejected",
        "cleanup_failed",
    ) == SQUID_LOCALIZATION_FAILURE_UNSPECIFIED
    assert normalize_squid_localization_reason_for_status(
        "squid_translation_layout_rejected",
        "unsafe_placement",
    ) == "squid_translation_layout_rejected"


def test_mark_squid_localization_failure_is_atomic():
    spec = {
        "source_text_visible": True,
        "translation_regions": [{"text": "한국어"}],
    }

    marked = mark_squid_visual_localization_failure(
        spec,
        status="unsafe_placement",
        reason_code="squid_translation_layout_rejected",
    )

    assert marked is spec
    assert marked["source_text_visible"] is False
    assert marked["translation_regions"] == []
    assert marked["visual_localization_status"] == "unsafe_placement"
    assert marked["visual_localization_reason_code"] == (
        "squid_translation_layout_rejected"
    )


def test_mark_squid_localization_failure_rejects_nonfailure_status():
    with pytest.raises(
        ValueError,
        match="Squid visual localization failure status is invalid",
    ):
        mark_squid_visual_localization_failure(
            {},
            status="translated",
            reason_code="squid_placement_audit_unsafe",
        )


def test_mark_squid_localization_failure_collapses_a_status_mismatch():
    marked = mark_squid_visual_localization_failure(
        {},
        status="cleanup_failed",
        reason_code="squid_translation_layout_rejected",
    )

    assert marked["visual_localization_reason_code"] == (
        SQUID_LOCALIZATION_FAILURE_UNSPECIFIED
    )
