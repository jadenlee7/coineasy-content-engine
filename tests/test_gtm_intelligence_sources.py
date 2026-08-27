from __future__ import annotations

import ast
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.gtm_intelligence import (
    AuthorizedSanitizedRailwayOpsRecord,
    GtmDomain,
    GtmStatus,
    SanitizedRailwayRuntimeReceipt,
    SanitizedXQaOwnerProjection,
    SquidGtmSourceBundle,
    SquidOpsSourceState,
    SquidTelegramSourceState,
    SquidXQaSourceState,
    TelegramOwnerProjection,
    build_squid_gtm_projection,
    project_squid_railway_ops,
    project_squid_x_qa,
    project_squid_x_qa_records,
    project_telegram_triage,
)
from core.gtm_intelligence.models import (
    _qa_receipt_sha256,
    _qa_receipt_subject_sha256,
    _telegram_faq_binding_sha256,
)
from scripts.run_gtm_intelligence import main as gtm_cli_main


GENERATED_AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
OBSERVED_AT = GENERATED_AT - timedelta(minutes=10)
QUESTION_HMAC = "b" * 64
CONTENT_SHA256 = "c" * 64
BANNER_SHA256 = "d" * 64
FAQ_SOURCE_SHA256 = "e" * 64
OFFICIAL_X_URL = "https://x.com/SquidRouter/status/1959999999999999999"
TELEGRAM_DRAFT = "공식 안내를 확인한 운영자가 다음 절차를 안내합니다."
FAQ_BINDING_SHA256 = _telegram_faq_binding_sha256(
    question_ref=f"question:{QUESTION_HMAC}",
    faq_match="exact",
    faq_source_sha256=FAQ_SOURCE_SHA256,
    draft_reply_ko=TELEGRAM_DRAFT,
)


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _ops_receipt(**overrides: object) -> SanitizedRailwayRuntimeReceipt:
    values: dict[str, object] = {
        "schema_version": "coineasy-sanitized-railway-runtime@1",
        "client_id": "squid",
        "service_name": "squid-runtime",
        "observed_at": OBSERVED_AT,
        "deployment_status": "running",
        "deployed_sha": "a" * 40,
        "expected_sha": "a" * 40,
        "runtime_status": "healthy",
        "schedule_status": "not_scheduled",
        "failure_count": 0,
        "failure_codes": (),
        "change_detected": False,
    }
    values.update(overrides)
    return SanitizedRailwayRuntimeReceipt.model_validate(values)


def _ops_record(**receipt_overrides: object) -> AuthorizedSanitizedRailwayOpsRecord:
    receipt = _ops_receipt(**receipt_overrides)
    return AuthorizedSanitizedRailwayOpsRecord(
        schema_version="coineasy-authorized-railway-ops@1",
        source_system="railway",
        owner_projection="sanitized_runtime_owner",
        authorization_scope="squid:ops:read_only",
        sanitized=True,
        read_only=True,
        raw_logs_included=False,
        environment_values_included=False,
        provider_payload_included=False,
        mutation_capability=False,
        receipt=receipt,
        source_receipt_sha256=receipt.canonical_sha256,
    )


def _telegram_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
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
        "observed_at": OBSERVED_AT,
        "question_observed_at": OBSERVED_AT,
        "digest_scheme": "hmac-sha256-v1",
        "question_ref": f"question:{QUESTION_HMAC}",
        "question_hmac_sha256": QUESTION_HMAC,
        "topic": "product",
        "question_summary_ko": "제품 사용 절차를 묻는 비식별 질문입니다.",
        "answer_state": "unanswered",
        "faq_match": "exact",
        "faq_source_sha256": FAQ_SOURCE_SHA256,
        "faq_binding_sha256": FAQ_BINDING_SHA256,
        "draft_reply_ko": TELEGRAM_DRAFT,
        "safety_class": "none",
    }
    values.update(overrides)
    return values


def _telegram_record(**overrides: object) -> TelegramOwnerProjection:
    return TelegramOwnerProjection.model_validate(_telegram_payload(**overrides))


def _x_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": "coineasy-sanitized-x-qa-owner-projection@1",
        "client_id": "squid",
        "source_system": "public_x_qa_owner_projection",
        "read_only_projection": True,
        "sanitized_public_data_only": True,
        "account_allowlisted": True,
        "owner_projection_claimed_current": True,
        "raw_post_text_included": False,
        "private_data_included": False,
        "mutation_capability": False,
        "publication_capability": False,
        "observed_at": OBSERVED_AT,
        "source_observed_at": OBSERVED_AT,
        "signal_kind": "content_qa",
        "source_url": OFFICIAL_X_URL,
        "source_account": "SquidRouter",
        "title_ko": "공식 출처 콘텐츠 QA 검토",
        "summary_ko": "공식 게시물과 현재 콘텐츠 checksum을 함께 검토합니다.",
        "claim_ko": "공식 출처와 현재 콘텐츠 버전을 함께 확인합니다.",
        "comparison_ko": None,
        "confidence": "high",
        "work_order_id": None,
        "content_item_id": _uuid(1),
        "content_version_id": _uuid(2),
        "content_sha256": CONTENT_SHA256,
        "content_observed_at": OBSERVED_AT,
        "banner_sha256": BANNER_SHA256,
        "banner_observed_at": OBSERVED_AT,
        "qa_receipt_sha256": None,
        "qa_receipt_subject_sha256": None,
        "qa_receipt_observed_at": None,
        "qa_verdict": "pending",
        "issue_codes": (),
    }
    values.update(overrides)
    return values


def _x_record(**overrides: object) -> SanitizedXQaOwnerProjection:
    return SanitizedXQaOwnerProjection.model_validate(_x_payload(**overrides))


def _available_bundle() -> SquidGtmSourceBundle:
    return SquidGtmSourceBundle(
        generated_at=GENERATED_AT,
        ops=SquidOpsSourceState(
            availability="available",
            observed_at=OBSERVED_AT,
            records=(_ops_record(),),
        ),
        telegram_triage=SquidTelegramSourceState(
            availability="available",
            observed_at=OBSERVED_AT,
            records=(_telegram_record(),),
        ),
        x_narrative_qa=SquidXQaSourceState(
            availability="available",
            observed_at=OBSERVED_AT,
            records=(_x_record(),),
        ),
    )


def _unavailable_bundle() -> SquidGtmSourceBundle:
    return SquidGtmSourceBundle(
        generated_at=GENERATED_AT,
        ops=SquidOpsSourceState(
            availability="unavailable",
            observed_at=OBSERVED_AT,
            unavailable_reason_code="source_access_denied",
        ),
        telegram_triage=SquidTelegramSourceState(
            availability="unavailable",
            observed_at=OBSERVED_AT,
            unavailable_reason_code="sanitized_source_missing",
        ),
        x_narrative_qa=SquidXQaSourceState(
            availability="unavailable",
            observed_at=OBSERVED_AT,
            unavailable_reason_code="sanitized_source_missing",
        ),
    )


def test_available_source_bundle_projects_complete_squid_page() -> None:
    bundle = _available_bundle()

    page = build_squid_gtm_projection(bundle)

    assert page.counts() == {
        "total": 3,
        "domains": {
            "ops": 1,
            "telegram_triage": 1,
            "x_narrative_qa": 1,
        },
        "statuses": {
            "info": 1,
            "needs_review": 2,
            "blocked": 0,
            "unobserved": 0,
        },
    }
    assert {item.client_id for item in page.items} == {"squid"}
    assert bundle.model_dump_json() == bundle.model_dump_json()
    assert page.snapshot_sha256 == build_squid_gtm_projection(bundle).snapshot_sha256


def test_v1_source_bundle_and_saved_page_golden_bytes_stay_stable() -> None:
    bundle_bytes = _available_bundle().model_dump_json().encode("utf-8")
    page_bytes = build_squid_gtm_projection(
        _available_bundle()
    ).model_dump_json().encode("utf-8")

    assert len(bundle_bytes) == 4_436
    assert hashlib.sha256(bundle_bytes).hexdigest() == (
        "a1d64a4c419c653eb29aaf3a7f5bcf8e6d79bbf20f403e7907f7655b4615bdfd"
    )
    assert len(page_bytes) == 5_994
    assert hashlib.sha256(page_bytes).hexdigest() == (
        "c88f0217e86e13516b10daf9443f8717e741a323f1f669d82e4072d4457d02e2"
    )


def test_ops_source_state_projects_multiple_unique_railway_services() -> None:
    baseline = _available_bundle()
    bundle = SquidGtmSourceBundle(
        generated_at=GENERATED_AT,
        ops=SquidOpsSourceState(
            availability="available",
            observed_at=OBSERVED_AT,
            records=(
                _ops_record(),
                _ops_record(service_name="coineasy-grok-qa"),
            ),
        ),
        telegram_triage=baseline.telegram_triage,
        x_narrative_qa=baseline.x_narrative_qa,
    )

    page = build_squid_gtm_projection(bundle)

    assert page.counts()["domains"]["ops"] == 2
    assert {item.details.service_name for item in page.items if item.domain == GtmDomain.OPS} == {
        "coineasy-grok-qa",
        "squid-runtime",
    }
    with pytest.raises(ValidationError, match="ops_duplicate"):
        SquidOpsSourceState(
            availability="available",
            observed_at=OBSERVED_AT,
            records=(_ops_record(), _ops_record()),
        )


def test_unavailable_sources_are_explicitly_unobserved_not_zero() -> None:
    page = build_squid_gtm_projection(_unavailable_bundle())

    assert len(page.items) == 3
    assert {item.domain for item in page.items} == set(GtmDomain)
    assert {item.status for item in page.items} == {GtmStatus.UNOBSERVED}
    assert all(item.details.observed_count is None for item in page.items)
    assert page.counts()["statuses"]["unobserved"] == 3


def test_source_state_requires_explicit_availability_semantics() -> None:
    with pytest.raises(ValidationError, match="available_state_invalid"):
        SquidTelegramSourceState(
            availability="available",
            observed_at=OBSERVED_AT,
            records=(),
        )
    with pytest.raises(ValidationError, match="unavailable_state_invalid"):
        SquidXQaSourceState(
            availability="unavailable",
            observed_at=OBSERVED_AT,
            records=(_x_record(),),
            unavailable_reason_code="sanitized_source_missing",
        )
    with pytest.raises(ValidationError, match="observation_after_generation"):
        SquidGtmSourceBundle(
            **{
                **_unavailable_bundle().model_dump(),
                "generated_at": OBSERVED_AT - timedelta(seconds=1),
            }
        )


@pytest.mark.parametrize(
    ("field", "numeric_value"),
    (
        ("read_only_projection", 1),
        ("external_calls", 0),
        ("database_calls", 0),
        ("provider_calls", 0),
        ("publication_calls", 0),
        ("automatic_publication", 0),
    ),
)
def test_source_bundle_cli_rejects_numeric_authority_literals(
    field: str,
    numeric_value: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    model_payload = _available_bundle().model_dump(mode="python")
    model_payload[field] = numeric_value

    with pytest.raises(
        ValidationError,
        match="gtm_source_bundle_authority_type_invalid",
    ):
        SquidGtmSourceBundle.model_validate(model_payload)

    payload = _available_bundle().model_dump(mode="json")
    payload[field] = numeric_value
    input_path = tmp_path / f"numeric-authority-{field}.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_gtm_intelligence.py",
            "--input",
            str(input_path),
            "--source-bundle",
            "--snapshot-json",
        ],
    )

    assert gtm_cli_main() == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["ok"] is False
    assert failure["error"] == "gtm_intelligence_invalid"
    assert failure["read_only_projection"] is True
    assert failure["external_calls"] is False


def test_ops_adapter_binds_receipt_and_fails_closed() -> None:
    record = _ops_record()
    item = project_squid_railway_ops(record, observed_at=OBSERVED_AT)
    assert item.details.source_receipt_sha256 == record.source_receipt_sha256
    assert item.status == GtmStatus.INFO

    with pytest.raises(ValidationError, match="sha256_mismatch"):
        AuthorizedSanitizedRailwayOpsRecord(
            schema_version="coineasy-authorized-railway-ops@1",
            source_system="railway",
            owner_projection="sanitized_runtime_owner",
            authorization_scope="squid:ops:read_only",
            sanitized=True,
            read_only=True,
            raw_logs_included=False,
            environment_values_included=False,
            provider_payload_included=False,
            mutation_capability=False,
            receipt=record.receipt,
            source_receipt_sha256="f" * 64,
        )
    with pytest.raises(ValueError, match="observation_binding_invalid"):
        project_squid_railway_ops(
            record,
            observed_at=OBSERVED_AT + timedelta(seconds=1),
        )


def test_telegram_adapter_requires_owner_redaction_and_opaque_hmac() -> None:
    item = project_telegram_triage(_telegram_payload())
    assert item.ref == f"telegram:squid:{QUESTION_HMAC}"
    assert item.details.raw_user_identifiers_included is False
    assert item.details.raw_chat_id_included is False

    with pytest.raises(ValidationError):
        TelegramOwnerProjection.model_validate(
            _telegram_payload(raw_user_id=12345)
        )
    with pytest.raises(ValidationError, match="hmac_binding_invalid"):
        TelegramOwnerProjection.model_validate(
            _telegram_payload(question_hmac_sha256="f" * 64)
        )
    with pytest.raises(ValidationError):
        TelegramOwnerProjection.model_validate(
            _telegram_payload(
                question_summary_ko="문의 이메일은 operator@example.com 입니다."
            )
        )


def test_telegram_requires_explicit_safety_and_rejects_identifier_runs() -> None:
    missing_safety = _telegram_payload()
    missing_safety.pop("safety_class")
    with pytest.raises(ValidationError):
        TelegramOwnerProjection.model_validate(missing_safety)
    with pytest.raises(ValidationError, match="source_summary_invalid"):
        TelegramOwnerProjection.model_validate(_telegram_payload(
            question_summary_ko="질문자 123456789 문의를 확인합니다.",
        ))


def test_telegram_faq_binding_covers_question_faq_and_exact_draft() -> None:
    with pytest.raises(ValidationError, match="faq_binding_invalid"):
        TelegramOwnerProjection.model_validate(_telegram_payload(
            draft_reply_ko=(
                "공식 안내를 확인한 운영자가 변경된 절차를 안내합니다."
            ),
        ))
    item = project_telegram_triage(_telegram_record())
    assert item.details.faq_source_sha256 == FAQ_SOURCE_SHA256
    assert item.details.faq_binding_sha256 == FAQ_BINDING_SHA256
    assert any(
        evidence.kind.value == "faq_receipt"
        and evidence.sha256 == FAQ_BINDING_SHA256
        for evidence in item.evidence
    )


def test_telegram_question_time_cannot_be_reaged_by_owner_projection() -> None:
    old_question = _telegram_record(
        question_observed_at=GENERATED_AT - timedelta(hours=25),
    )
    baseline = _available_bundle()
    bundle = SquidGtmSourceBundle(
        generated_at=GENERATED_AT,
        ops=baseline.ops,
        telegram_triage=SquidTelegramSourceState(
            availability="available",
            observed_at=OBSERVED_AT,
            records=(old_question,),
        ),
        x_narrative_qa=baseline.x_narrative_qa,
    )
    with pytest.raises(ValidationError, match="stale_evidence"):
        build_squid_gtm_projection(bundle)


def test_telegram_safety_class_deterministically_blocks_reply() -> None:
    record = _telegram_record(
        topic="wallet_security",
        faq_match="none",
        faq_source_sha256=None,
        faq_binding_sha256=None,
        draft_reply_ko=None,
        safety_class="wallet_signing",
    )

    item = project_telegram_triage(record)

    assert item.status == GtmStatus.BLOCKED
    assert item.details.escalation_codes == ("security", "wallet_signing")
    assert item.next_action.code == "investigate"


def test_x_qa_adapter_binds_public_url_content_and_pending_state() -> None:
    record = _x_record()
    item = project_squid_x_qa(record)

    assert item.details.source_url == OFFICIAL_X_URL
    assert item.lineage.content_item_id == record.content_item_id
    assert item.lineage.content_version_id == record.content_version_id
    assert item.details.content_sha256 == CONTENT_SHA256
    assert item.status == GtmStatus.NEEDS_REVIEW
    assert project_squid_x_qa_records((record,)) == (item,)

    with pytest.raises(ValidationError, match="official_account_invalid"):
        SanitizedXQaOwnerProjection.model_validate(
            _x_payload(
                source_url="https://x.com/Competitor/status/1959999999999999999",
                source_account="Competitor",
            )
        )
    with pytest.raises(ValidationError, match="pending_receipt_invalid"):
        SanitizedXQaOwnerProjection.model_validate(
            _x_payload(
                qa_receipt_sha256="f" * 64,
                qa_receipt_observed_at=OBSERVED_AT,
            )
        )


def test_x_qa_rejects_unsafe_text_at_owner_ingress() -> None:
    with pytest.raises(ValidationError, match="owner_text_invalid"):
        SanitizedXQaOwnerProjection.model_validate(_x_payload(
            title_ko=f"노출 키 xai-{'a' * 24}",
        ))


def test_x_qa_completed_verdict_requires_receipt_and_issue_binding() -> None:
    subject = _qa_receipt_subject_sha256(
        source_url=OFFICIAL_X_URL,
        content_item_id=_uuid(1),
        content_version_id=_uuid(2),
        content_sha256=CONTENT_SHA256,
        banner_sha256=BANNER_SHA256,
        qa_verdict="warn",
        issue_codes=("missing_source",),
    )
    receipt = _qa_receipt_sha256(subject_sha256=subject)
    warn = _x_record(
        qa_verdict="warn",
        issue_codes=("missing_source",),
        qa_receipt_sha256=receipt,
        qa_receipt_subject_sha256=subject,
        qa_receipt_observed_at=OBSERVED_AT,
    )

    item = project_squid_x_qa(warn)

    assert item.status == GtmStatus.NEEDS_REVIEW
    assert item.details.qa_receipt_sha256 == receipt
    assert item.details.qa_receipt_subject_sha256 == subject
    with pytest.raises(ValidationError, match="receipt_missing"):
        SanitizedXQaOwnerProjection.model_validate(
            _x_payload(qa_verdict="pass")
        )

    with pytest.raises(ValidationError, match="receipt_subject_mismatch"):
        SanitizedXQaOwnerProjection.model_validate(_x_payload(
            qa_verdict="warn",
            issue_codes=("missing_source",),
            qa_receipt_sha256=receipt,
            qa_receipt_subject_sha256="0" * 64,
            qa_receipt_observed_at=OBSERVED_AT,
        ))

    changed_subject = _qa_receipt_subject_sha256(
        source_url=OFFICIAL_X_URL,
        content_item_id=_uuid(1),
        content_version_id=_uuid(2),
        content_sha256="9" * 64,
        banner_sha256=BANNER_SHA256,
        qa_verdict="warn",
        issue_codes=("missing_source",),
    )
    with pytest.raises(ValidationError, match="receipt_binding_mismatch"):
        SanitizedXQaOwnerProjection.model_validate(_x_payload(
            content_sha256="9" * 64,
            qa_verdict="warn",
            issue_codes=("missing_source",),
            qa_receipt_sha256=receipt,
            qa_receipt_subject_sha256=changed_subject,
            qa_receipt_observed_at=OBSERVED_AT,
        ))
    with pytest.raises(ValidationError, match="receipt_chronology_invalid"):
        SanitizedXQaOwnerProjection.model_validate(_x_payload(
            qa_verdict="warn",
            issue_codes=("missing_source",),
            qa_receipt_sha256=receipt,
            qa_receipt_subject_sha256=subject,
            qa_receipt_observed_at=OBSERVED_AT - timedelta(seconds=1),
        ))
    with pytest.raises(ValidationError, match="receipt_subject_mismatch"):
        SanitizedXQaOwnerProjection.model_validate(_x_payload(
            content_sha256="9" * 64,
            qa_verdict="warn",
            issue_codes=("missing_source",),
            qa_receipt_sha256=receipt,
            qa_receipt_subject_sha256=subject,
            qa_receipt_observed_at=OBSERVED_AT,
        ))


def test_source_modules_have_no_external_or_mutating_import_surface() -> None:
    package_root = Path(__file__).parents[1] / "core" / "gtm_intelligence"
    paths = (
        package_root / "projection.py",
        package_root / "sources" / "ops.py",
        package_root / "sources" / "telegram.py",
        package_root / "sources" / "x_qa.py",
    )
    banned = {
        "aiohttp",
        "boto3",
        "httpx",
        "os",
        "pathlib",
        "psycopg",
        "redis",
        "requests",
        "socket",
        "subprocess",
        "supabase",
        "telegram",
        "urllib",
    }

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(banned), (path.name, imported & banned)


def test_source_bundle_cli_projects_saved_sanitized_records(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "source-bundle.json"
    input_path.write_text(_available_bundle().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_gtm_intelligence.py",
            "--input",
            str(input_path),
            "--source-bundle",
            "--snapshot-json",
        ],
    )

    assert gtm_cli_main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["total"] == 3
    assert payload["external_calls"] is False
    assert payload["database_calls"] is False
    assert payload["provider_calls"] is False
    assert payload["publication_calls"] is False
    assert len(payload["snapshot_sha256"]) == hashlib.sha256().digest_size * 2


def test_source_bundle_cli_fails_closed_on_unknown_or_wrong_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    payload = json.loads(_available_bundle().model_dump_json())
    payload["client_id"] = "yellow"
    payload["credential"] = "not accepted"
    input_path = tmp_path / "invalid-source-bundle.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_gtm_intelligence.py",
            "--input",
            str(input_path),
            "--source-bundle",
            "--snapshot-json",
        ],
    )

    assert gtm_cli_main() == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["ok"] is False
    assert failure["external_calls"] is False
    assert failure["publication_calls"] is False


@pytest.mark.parametrize("output_flag", ("--snapshot-json", "--dashboard"))
@pytest.mark.parametrize(
    "malformed_kind",
    ("duplicate", "NaN", "Infinity", "-Infinity"),
)
def test_source_bundle_cli_rejects_non_strict_json_before_validation(
    output_flag: str,
    malformed_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    raw = _available_bundle().model_dump_json()
    if malformed_kind == "duplicate":
        raw = raw.replace(
            '"client_id":"squid"',
            '"client_id":"squid","client_id":"squid"',
            1,
        )
    else:
        raw = raw.replace(
            '"client_id":"squid"',
            f'"client_id":{malformed_kind}',
            1,
        )

    def fail_if_model_validation_runs(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("source bundle model validation must not run")

    monkeypatch.setattr(
        SquidGtmSourceBundle,
        "model_validate_json",
        fail_if_model_validation_runs,
    )
    input_path = tmp_path / f"source-{malformed_kind}-{output_flag[2:]}.json"
    input_path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_gtm_intelligence.py",
            "--input",
            str(input_path),
            "--source-bundle",
            output_flag,
        ],
    )

    assert gtm_cli_main() == 2
    output = capsys.readouterr().out
    failure = json.loads(output)
    assert failure["ok"] is False
    assert failure["error"] == "gtm_intelligence_invalid"
    assert "coineasy-squid-gtm-source-bundle@1" not in output
    assert QUESTION_HMAC not in output
