from __future__ import annotations

import copy
import hashlib
import json

import pytest

from core.automation.origintrail_evidence import (
    ORIGINTRAIL_MEDIA_EVIDENCE_POLICY_VERSION,
    parse_origintrail_fact_evidence,
)


SOURCE_URL = (
    "https://x.com/origin_trail/status/2085782218815775024"
)
SOURCE_SHA256 = (
    "aa1676bb2f98b8f35ee7de430c161c9a4ba39a8d4a9c728b8abd93dba3655d74"
)
RECORDED_MEDIA_URL = (
    "https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/"
    "img/vH2LVZnApTMbJhq2.jpg"
)
PREVIEW_MEDIA_URL = f"{RECORDED_MEDIA_URL}?name=orig"
OBSERVED_AT = "2026-08-08T11:05:11Z"


def valid_origintrail_evidence_envelope() -> dict[str, object]:
    references = [
        {
            "kind": "origintrail_implementation",
            "label_ko": "OriginTrail Prime Agent 어댑터 구현 상태",
            "url": (
                "https://github.com/OriginTrail/dkg/blob/"
                "075e87d881260a1aad2d86b53fa250d5d3f67d40/"
                "packages/adapter-prime-agent/README.md"
            ),
            "observed_at": OBSERVED_AT,
            "snapshot_sha256": (
                "d7a3ec333d26feae1a90f51d6770858541b6c9134799d79397d1601ede42a51b"
            ),
            "availability": "available",
            "finding_ko": "현재 Stage 1 전송·연결 계층으로 명시돼 있습니다.",
        },
        {
            "kind": "prime_intellect_announcement",
            "label_ko": "Prime Agent 공식 발표",
            "url": "https://www.primeintellect.ai/blog/prime-agent",
            "observed_at": OBSERVED_AT,
            "snapshot_sha256": None,
            "availability": "available",
            "finding_ko": "95.5% Best@1은 Prime Intellect의 자체 발표입니다.",
        },
        {
            "kind": "prime_agent_release",
            "label_ko": "Prime Agent v0.7.0 불변 커밋",
            "url": (
                "https://github.com/PrimeIntellect-ai/prime-agent/commit/"
                "be9e2fa0714e7cd1c6bd9bdb1b554d2cc6550387"
            ),
            "observed_at": OBSERVED_AT,
            "snapshot_sha256": None,
            "availability": "available",
            "finding_ko": "공개 릴리스의 고정 커밋을 확인했습니다.",
        },
        {
            "kind": "arc_community_leaderboard",
            "label_ko": "ARC-AGI-3 공개 커뮤니티 점수표",
            "url": "https://arcprize.org/api/leaderboards",
            "observed_at": OBSERVED_AT,
            "snapshot_sha256": (
                "2f37594d945680d310a35b3959c84f12c17c14c629ee7c68ae70ede8c5306623"
            ),
            "availability": "available",
            "finding_ko": "관측 점수는 95.23982017078089입니다.",
        },
        {
            "kind": "arc_methodology",
            "label_ko": "ARC-AGI-3 기술 보고서",
            "url": (
                "https://arcprize.org/media/ARC_AGI_3_Technical_Report.pdf"
            ),
            "observed_at": OBSERVED_AT,
            "snapshot_sha256": None,
            "availability": "available",
            "finding_ko": "커뮤니티 점수표는 기본적으로 자체 보고 결과입니다.",
        },
        {
            "kind": "scorecard_source",
            "label_ko": "점수표 연결 소스 커밋",
            "url": (
                "https://github.com/PrimeIntellect-ai/"
                "arc-agi-3-prime-agent-scorecard/commit/"
                "aaee22436235de6f784df7b89302e1258aae9ab9"
            ),
            "observed_at": OBSERVED_AT,
            "snapshot_sha256": None,
            "availability": "unavailable",
            "finding_ko": "관측 시점에 연결 커밋을 확인할 수 없었습니다.",
        },
    ]
    payload = {
        "schema_version": "1.0",
        "policy_version": ORIGINTRAIL_MEDIA_EVIDENCE_POLICY_VERSION,
        "review_status": "qualified",
        "human_review_required": True,
        "verified_at": OBSERVED_AT,
        "source_url": SOURCE_URL,
        "source_content_sha256": SOURCE_SHA256,
        "media": {
            "type": "video",
            "media_key": "13_2085781578374860800",
            "recorded_url": RECORDED_MEDIA_URL,
            "preview_url": PREVIEW_MEDIA_URL,
            "preview_url_sha256": hashlib.sha256(
                PREVIEW_MEDIA_URL.encode("utf-8")
            ).hexdigest(),
            "width": 1920,
            "height": 1920,
            "factual_evidence": False,
        },
        "review_notes_ko": [
            "첨부 미디어는 출처 고정용이며 사실 근거로 사용하지 않습니다.",
            "벤치마크 수치는 발표 주체와 검증 한계를 함께 표기합니다.",
        ],
        "official_references": references,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "payload": payload,
        "evidence_sha256": hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
    }


def test_exact_origintrail_fact_evidence_is_canonical_and_immutable_by_copy():
    envelope = valid_origintrail_evidence_envelope()

    evidence = parse_origintrail_fact_evidence(envelope)

    assert evidence.source_url == SOURCE_URL
    assert evidence.source_content_sha256 == SOURCE_SHA256
    assert evidence.recorded_media_url == RECORDED_MEDIA_URL
    assert evidence.preview_media_url == PREVIEW_MEDIA_URL
    first = evidence.batch_envelope()
    first["payload"]["review_status"] = "tampered"
    assert evidence.batch_envelope()["payload"]["review_status"] == "qualified"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unexpected": True}),
        lambda value: value["payload"].update({"unexpected": True}),
        lambda value: value["payload"]["media"].update(
            {"factual_evidence": True}
        ),
        lambda value: value["payload"]["official_references"][0].update(
            {"url": "https://example.com/not-official"}
        ),
        lambda value: value.update({"evidence_sha256": "0" * 64}),
    ],
)
def test_origintrail_fact_evidence_rejects_widening_and_tampering(mutate):
    envelope = copy.deepcopy(valid_origintrail_evidence_envelope())
    mutate(envelope)

    with pytest.raises(ValueError):
        parse_origintrail_fact_evidence(envelope)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "url",
            "https://github.com/\nOriginTrail/dkg/blob/"
            "075e87d881260a1aad2d86b53fa250d5d3f67d40/"
            "packages/adapter-prime-agent/README.md",
        ),
        (
            "url",
            "https://GITHUB.COM/OriginTrail/dkg/blob/"
            "075e87d881260a1aad2d86b53fa250d5d3f67d40/"
            "packages/adapter-prime-agent/README.md",
        ),
        ("media_key", "video_2085781578374860800"),
        (
            "recorded_url",
            "https://PBS.TWIMG.COM/amplify_video_thumb/"
            "2085781578374860800/img/vH2LVZnApTMbJhq2.jpg",
        ),
    ],
)
def test_origintrail_fact_evidence_rejects_rehashed_cross_runtime_ambiguity(
    field,
    value,
):
    envelope = copy.deepcopy(valid_origintrail_evidence_envelope())
    if field == "url":
        envelope["payload"]["official_references"][0][field] = value
    else:
        envelope["payload"]["media"][field] = value
    canonical = json.dumps(
        envelope["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    envelope["evidence_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError):
        parse_origintrail_fact_evidence(envelope)
