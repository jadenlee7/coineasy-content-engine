from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from core.grok_qa.models import (
    GrokQaModelResult,
    GrokQaVerdict,
    GrokQaWorkClaim,
    provider_x_citation_matches,
    verdict_payload_sha256,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"


def claim(**overrides) -> GrokQaWorkClaim:
    values = {
        "content_item_id": "11111111-1111-4111-8111-111111111111",
        "content_version_id": "22222222-2222-4222-8222-222222222222",
        "client_id": "squid",
        "content_kind": "daily_news",
        "title": "Squid 한국 공지",
        "source_url": "https://x.com/squidrouter/status/2083266484789514640",
        "source_published_at": datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        "review_text": "한국 GTM 초안과 자동 QA 결과를 검토해 주세요.",
        "image_png": PNG,
        "image_sha256": hashlib.sha256(PNG).hexdigest(),
        "attempt": 1,
        "max_attempts": 3,
    }
    values.update(overrides)
    return GrokQaWorkClaim(**values)


def pass_verdict(**overrides) -> GrokQaVerdict:
    values = {
        "decision": "PASS",
        "summary": "공식 원문과 브랜드 배너가 모두 일치합니다.",
        "fact_check": {
            "status": "PASS",
            "checks": ["공식 원문을 직접 확인함"],
            "source_urls": [
                "https://x.com/squidrouter/status/2083266484789514640"
            ],
        },
        "brand_check": {
            "status": "PASS",
            "checks": ["Squid 브랜드 표현과 일치함"],
        },
        "issues": [],
        "next_action": "ready_for_human_approval",
    }
    values.update(overrides)
    return GrokQaVerdict.model_validate(values)


def test_claim_requires_exact_official_handle_but_accepts_handle_case():
    assert claim().client_id == "squid"
    assert claim(source_url=(
        "https://x.com/SquidRouter/status/2083266484789514640"
    )).client_id == "squid"
    with pytest.raises(ValidationError, match="grok_qa_source_url_invalid"):
        claim(source_url="https://x.com/squidkorea/status/2083266484789514640")
    with pytest.raises(ValidationError, match="grok_qa_source_url_invalid"):
        claim(source_url=(
            "https://x.com/squidrouter/status/2083266484789514640?token=secret"
        ))


@pytest.mark.parametrize(("client_id", "handle"), [
    ("yellow", "Yellow"),
    ("origintrail", "origin_trail"),
    ("squid", "SquidRouter"),
    ("babylon", "babylonlabs_io"),
])
def test_claim_pins_each_client_to_its_exact_official_handle(
    client_id: str,
    handle: str,
):
    item = claim(
        client_id=client_id,
        source_url=f"https://x.com/{handle}/status/2083266484789514640",
    )
    assert item.client_id == client_id
    with pytest.raises(ValidationError, match="grok_qa_source_url_invalid"):
        claim(
            client_id=client_id,
            source_url=(
                "https://x.com/unofficial/status/2083266484789514640"
            ),
        )


def test_claim_rejects_non_png_hash_mismatch_and_private_prompt_fields():
    with pytest.raises(ValidationError, match="grok_qa_image_invalid"):
        claim(image_png=b"not-png")
    with pytest.raises(ValidationError, match="grok_qa_image_invalid"):
        claim(image_sha256="a" * 64)
    with pytest.raises(ValidationError, match="grok_qa_review_text_private_data"):
        claim(review_text="private SUPABASE_SERVICE_ROLE_KEY should not pass")
    with pytest.raises(ValidationError, match="grok_qa_review_text_invalid"):
        claim(review_text=" " * 30)


def test_verdict_enforces_pass_and_exact_source_boundary():
    verdict = pass_verdict()
    verdict.validate_source_boundary(claim().source_url)
    with pytest.raises(ValidationError, match="grok_qa_pass_evidence_incomplete"):
        pass_verdict(fact_check={
            "status": "PASS",
            "checks": ["공식 원문을 직접 확인함"],
            "source_urls": [],
        })
    with pytest.raises(ValueError, match="grok_qa_source_mismatch"):
        verdict.validate_source_boundary(
            "https://x.com/squidrouter/status/2083266484789514641"
        )
    blocked_without_verdict_source = GrokQaVerdict.model_validate({
        "decision": "BLOCK",
        "summary": (
            "공식 원문과 초안 사이의 사실 관계를 검증할 수 없습니다."
        ),
        "fact_check": {
            "status": "BLOCK",
            "checks": ["공식 원문 검증에 실패함"],
            "source_urls": [],
        },
        "brand_check": {
            "status": "PASS",
            "checks": ["Squid 브랜드 표현과 일치함"],
        },
        "issues": [],
        "next_action": "verify_source",
    })
    with pytest.raises(ValueError, match="grok_qa_source_mismatch"):
        blocked_without_verdict_source.validate_source_boundary(claim().source_url)
    with pytest.raises(ValidationError, match="grok_qa_summary_invalid"):
        pass_verdict(summary=" " * 20)


def test_claim_input_hash_binds_text_source_timestamp_and_png():
    original = claim()
    assert claim(review_text=original.review_text + " 추가").input_sha256 != (
        original.input_sha256
    )
    assert claim(source_published_at=datetime(
        2026, 8, 12, 13, tzinfo=timezone.utc
    )).input_sha256 != original.input_sha256


def test_provider_citation_accepts_only_the_same_official_post_id():
    source = claim().source_url
    assert provider_x_citation_matches(
        "squid", source, "https://x.com/i/status/2083266484789514640"
    )
    assert provider_x_citation_matches("squid", source, source)
    assert not provider_x_citation_matches(
        "squid", source, "https://x.com/other/status/2083266484789514640"
    )
    assert not provider_x_citation_matches(
        "squid", source, "https://x.com/i/status/2083266484789514641"
    )


def test_staged_result_rejects_a_non_source_citation():
    original = claim()
    verdict = pass_verdict()
    staged = GrokQaModelResult(
        provider_response_id="resp_abc123",
        model="grok-4.5",
        cost_in_usd_ticks=100,
        input_sha256=original.input_sha256,
        x_search_performed=True,
        x_search_citations=[
            "https://x.com/i/status/2083266484789514641"
        ],
        x_search_calls=1,
        verdict=verdict,
    )
    with pytest.raises(ValidationError, match="grok_qa_staged_citation_mismatch"):
        claim(
            provider_call_required=False,
            staged_result=staged,
            staged_verdict_sha256=verdict_payload_sha256(verdict),
            staged_prompt_version="official-x-grok-qa@1",
        )
