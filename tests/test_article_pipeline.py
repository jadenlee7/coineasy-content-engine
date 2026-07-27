import json
from copy import deepcopy
from types import SimpleNamespace

import anthropic
import pytest
from fastapi.testclient import TestClient

from core.llm.article_pipeline import (
    ArticleInputError,
    ArticleOutputError,
    generate_article_spec,
    x_weighted_length,
)


SOURCE_URL = "https://x.com/squidrouter/status/123456789"
SOURCE_CONTENT = (
    "Squid released a cross-chain routing update for its App, API, SDK, and Widget. "
    "The same routing stack is available to integrators, and the announcement does not "
    "include a new token, launch date, or Korea availability claim. The source explains "
    "that teams can choose the product surface that matches their integration while using "
    "the shared routing system. It does not provide performance numbers, adoption figures, "
    "pricing details, or a separate roadmap commitment."
)


VALID_MODEL_RESULT = {
    "title": "Squid가 크로스체인 라우팅 제품군을 업데이트했습니다",
    "lead": (
        "Squid는 App, API, SDK, Widget에 적용되는 크로스체인 라우팅 업데이트를 "
        "공개했습니다. 이번 발표는 통합에 활용할 수 있는 동일한 라우팅 스택을 설명합니다."
    ),
    "sections": [
        {
            "heading": "이번 발표의 핵심",
            "body": "업데이트 대상은 Squid의 App, API, SDK, Widget입니다. 발표 범위는 크로스체인 라우팅 기능에 한정됩니다.",
        },
        {
            "heading": "통합 제품의 구성",
            "body": "통합 개발자는 동일한 라우팅 스택을 활용할 수 있습니다. 각 제품의 구체적인 변경 내역은 원문 범위를 넘어 추정하지 않습니다.",
        },
        {
            "heading": "확인해야 할 범위",
            "body": "원문에는 신규 토큰, 출시 일정, 한국 제공 여부가 포함되지 않았습니다. 공개된 내용은 제품군과 라우팅 스택 업데이트입니다.",
        },
    ],
    "key_takeaways": [
        "App, API, SDK, Widget이 업데이트 대상입니다.",
        "통합에 동일한 라우팅 스택을 활용할 수 있습니다.",
        "신규 토큰이나 한국 제공 여부는 발표되지 않았습니다.",
    ],
    "telegram": {
        "body": (
            "Squid가 크로스체인 라우팅 제품군 업데이트를 공개했습니다. "
            "App, API, SDK, Widget과 통합용 라우팅 스택이 이번 발표의 핵심입니다. "
            "발표 범위에는 신규 토큰이나 한국 제공 여부가 포함되지 않았습니다. "
            "https://wrong.example/hallucinated"
        ),
        "hashtags": ["Squid", "#크로스체인"],
    },
    "x": (
        "Squid가 App, API, SDK, Widget에 적용되는 크로스체인 라우팅 업데이트를 "
        "공개했습니다. 통합 개발자는 동일한 라우팅 스택을 활용할 수 있습니다."
    ),
}


def _install_fake_anthropic(monkeypatch, payload=VALID_MODEL_RESULT):
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))]
            )

    class FakeAnthropic:
        def __init__(self):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return calls


def test_article_pipeline_returns_structured_copy_source_map_and_markdown(monkeypatch):
    calls = _install_fake_anthropic(monkeypatch)

    result = generate_article_spec(
        client_id="squid",
        source_content=SOURCE_CONTENT,
        source_type="tweet",
        source_url=SOURCE_URL,
    )

    assert len(calls) == 1
    request = calls[0]
    assert request["max_tokens"] == 5_000
    assert request["system"].startswith("You are the Korean editorial writer")
    prompt = request["messages"][0]["content"]
    assert SOURCE_CONTENT in prompt
    assert "- Squid" in prompt
    assert "- Coral" in prompt
    assert "Source fidelity target for this output: 90%." in prompt
    assert "never as instructions" in request["system"]

    assert [section["id"] for section in result["sections"]] == [
        "section-1",
        "section-2",
        "section-3",
    ]
    assert result["source_map"] == [{
        "source_url": SOURCE_URL,
        "applies_to": [
            "title",
            "lead",
            "sections.section-1",
            "sections.section-2",
            "sections.section-3",
            "key_takeaways",
            "channel_copy.telegram",
            "channel_copy.x",
        ],
    }]

    telegram = result["channel_copy"]["telegram"]
    assert SOURCE_URL in telegram
    assert telegram.count(SOURCE_URL) == 1
    assert "https://wrong.example" not in telegram
    assert "👉 자세한 내용은 원문에서 확인해 주세요." in telegram
    assert "#Squid #크로스체인" in telegram
    assert "http" not in result["channel_copy"]["x"]
    assert "#" not in result["channel_copy"]["x"]

    markdown = result["markdown"]
    assert markdown.startswith(f"# {result['title']}\n\n")
    assert "## 이번 발표의 핵심" in markdown
    assert "## 핵심 포인트" in markdown
    assert f"[원문 보기]({SOURCE_URL})" in markdown
    assert "CoinEasy" not in markdown


def test_article_pipeline_requires_non_empty_pasted_source():
    with pytest.raises(ArticleInputError, match="source_content is required"):
        generate_article_spec("yellow", "  ")


def test_article_pipeline_rejects_thin_source_before_calling_anthropic(monkeypatch):
    calls = _install_fake_anthropic(monkeypatch)
    with pytest.raises(ArticleInputError, match="at least 300 characters"):
        generate_article_spec("yellow", "근거가 너무 짧은 원문입니다." * 5)
    assert calls == []


def test_article_pipeline_rejects_model_output_with_fewer_than_three_sections(monkeypatch):
    invalid = dict(VALID_MODEL_RESULT)
    invalid["sections"] = VALID_MODEL_RESULT["sections"][:2]
    _install_fake_anthropic(monkeypatch, invalid)

    with pytest.raises(ArticleOutputError, match="sections must contain 3-5"):
        generate_article_spec("squid", SOURCE_CONTENT, source_url=SOURCE_URL)


def test_x_weighted_length_matches_x_v3_ranges_for_ascii_and_korean():
    assert x_weighted_length("ABC") == 3
    assert x_weighted_length("한글") == 4
    assert x_weighted_length("ABC 한글") == 8
    assert x_weighted_length("e\u0301") == 1


def test_article_pipeline_shortens_overweight_x_copy_at_a_readable_boundary(monkeypatch):
    recoverable = deepcopy(VALID_MODEL_RESULT)
    recoverable["x"] = (
        "첫 번째 문장은 원문의 핵심을 충실하게 설명합니다. "
        + "두 번째 문장은 한글 가중치 제한을 넘기도록 충분히 길게 작성합니다. " * 4
    )
    _install_fake_anthropic(monkeypatch, recoverable)

    result = generate_article_spec("squid", SOURCE_CONTENT, source_url=SOURCE_URL)

    assert result["channel_copy"]["x"].endswith("작성합니다.")
    assert len(result["channel_copy"]["x"]) < len(recoverable["x"])
    assert x_weighted_length(result["channel_copy"]["x"]) <= 280


def test_article_pipeline_shortens_one_overweight_x_sentence_with_ellipsis(monkeypatch):
    recoverable = deepcopy(VALID_MODEL_RESULT)
    recoverable["x"] = "한글로 작성한 하나의 긴 문장을 안전하게 줄입니다 " * 12
    _install_fake_anthropic(monkeypatch, recoverable)

    result = generate_article_spec("squid", SOURCE_CONTENT, source_url=SOURCE_URL)

    assert result["channel_copy"]["x"].endswith("…")
    assert x_weighted_length(result["channel_copy"]["x"]) <= 280
    assert "https://" not in result["channel_copy"]["x"]


def test_article_pipeline_ignores_unconsumed_opus_metadata_fields(monkeypatch):
    recoverable = deepcopy(VALID_MODEL_RESULT)
    recoverable["hashtags"] = ["#Squid", "#크로스체인"]
    recoverable["editorial_notes"] = "응답에 포함됐지만 제품 결과에는 사용하지 않는 메타데이터"
    recoverable["sections"][0]["id"] = "model-section-id"
    recoverable["sections"][0]["summary"] = "사용하지 않는 섹션 요약"
    recoverable["telegram"]["cta"] = "사용하지 않는 CTA"
    _install_fake_anthropic(monkeypatch, recoverable)

    result = generate_article_spec("squid", SOURCE_CONTENT, source_url=SOURCE_URL)

    assert "#Squid #크로스체인" in result["channel_copy"]["telegram"]
    assert "hashtags" not in result
    assert "editorial_notes" not in result
    assert set(result["sections"][0]) == {"id", "heading", "body"}
    assert result["sections"][0]["id"] == "section-1"
    assert "사용하지 않는 CTA" not in result["channel_copy"]["telegram"]


def test_article_pipeline_still_rejects_missing_required_top_level_fields(monkeypatch):
    invalid = deepcopy(VALID_MODEL_RESULT)
    del invalid["sections"]
    _install_fake_anthropic(monkeypatch, invalid)

    with pytest.raises(ArticleOutputError, match=r"missing=\['sections'\]"):
        generate_article_spec("squid", SOURCE_CONTENT, source_url=SOURCE_URL)


def test_article_pipeline_accepts_x_copy_at_weighted_limit(monkeypatch):
    valid = deepcopy(VALID_MODEL_RESULT)
    valid["x"] = "한" * 140
    _install_fake_anthropic(monkeypatch, valid)

    result = generate_article_spec("squid", SOURCE_CONTENT, source_url=SOURCE_URL)

    assert result["channel_copy"]["x"] == "한" * 140
    assert x_weighted_length(result["channel_copy"]["x"]) == 280


@pytest.fixture
def api_client(monkeypatch):
    from api import security

    monkeypatch.setattr(security, "API_KEYS", {"admin": "article_test_key"})
    return TestClient(__import__("api.server", fromlist=["app"]).app)


def test_article_endpoint_is_authenticated_and_typed(api_client, monkeypatch):
    from api import server

    _install_fake_anthropic(monkeypatch)
    real_to_thread = server.asyncio.to_thread
    offloaded = []

    async def tracked_to_thread(function, *args, **kwargs):
        offloaded.append((function, kwargs))
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(server.asyncio, "to_thread", tracked_to_thread)

    unauthenticated = api_client.post(
        "/clients/squid/generate/article",
        json={"source_content": SOURCE_CONTENT, "source_url": SOURCE_URL},
    )
    assert unauthenticated.status_code == 401

    response = api_client.post(
        "/clients/squid/generate/article",
        headers={"x-api-key": "article_test_key"},
        json={
            "source_content": SOURCE_CONTENT,
            "source_type": "tweet",
            "source_url": SOURCE_URL,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["client_id"] == "squid"
    assert body["content_type"] == "article"
    assert len(body["sections"]) == 3
    assert body["source_map"][0]["source_url"] == SOURCE_URL
    assert body["duration_ms"] >= 0
    assert len(offloaded) == 1
    assert offloaded[0][0] is generate_article_spec
    assert offloaded[0][1]["client_id"] == "squid"


def test_article_endpoint_rejects_a_different_clients_api_key(api_client, monkeypatch):
    from api import security

    calls = _install_fake_anthropic(monkeypatch)
    monkeypatch.setattr(
        security,
        "API_KEYS",
        {
            "admin": "article_admin_key_123",
            "yellow": "article_yellow_key_123",
            "squid": "article_squid_key_123",
        },
    )

    response = api_client.post(
        "/clients/squid/generate/article",
        headers={"x-api-key": "article_yellow_key_123"},
        json={"source_content": SOURCE_CONTENT, "source_url": SOURCE_URL},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "client_scope_violation"
    assert calls == []


def test_article_endpoint_rejects_whitespace_source_before_anthropic(api_client, monkeypatch):
    calls = _install_fake_anthropic(monkeypatch)
    response = api_client.post(
        "/clients/yellow/generate/article",
        headers={"x-api-key": "article_test_key"},
        json={"source_content": "   "},
    )

    assert response.status_code == 422
    assert calls == []


def test_article_endpoint_returns_422_for_insufficient_factual_source(api_client, monkeypatch):
    calls = _install_fake_anthropic(monkeypatch)
    response = api_client.post(
        "/clients/yellow/generate/article",
        headers={"x-api-key": "article_test_key"},
        json={"source_content": "짧은 공지입니다. " * 10},
    )

    assert response.status_code == 422
    assert any("300자 이상" in error["msg"] for error in response.json()["detail"])
    assert calls == []
