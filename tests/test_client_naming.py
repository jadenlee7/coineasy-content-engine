from core.client_naming import enforce_client_display_name


def test_squid_uses_approved_display_name_across_nested_generated_content():
    value = {
        "headline": "Squid Router가 크로스체인 UX를 단순화합니다",
        "body_lines": ["SQUID ROUTER 통합", {"label": "squid router 업데이트"}],
        "source_url": "https://x.com/squidrouter/status/123",
        "handle": "@SquidRouter",
        "hashtag": "#SquidRouter",
    }

    normalized = enforce_client_display_name("squid", value)

    assert normalized["headline"] == "Squid가 크로스체인 UX를 단순화합니다"
    assert normalized["body_lines"] == ["Squid 통합", {"label": "Squid 업데이트"}]
    assert normalized["source_url"] == "https://x.com/squidrouter/status/123"
    assert normalized["handle"] == "@SquidRouter"
    assert normalized["hashtag"] == "#SquidRouter"


def test_other_clients_are_not_rewritten():
    value = {"headline": "Squid Router integration"}
    assert enforce_client_display_name("yellow", value) is value


def test_squid_uses_natural_korean_particles():
    value = [
        "Squid이 업데이트했습니다",
        "Squid은 연결합니다",
        "Squid을 사용합니다",
        "Squid과 통합합니다",
        "Squid으로 이동합니다",
    ]

    assert enforce_client_display_name("squid", value) == [
        "Squid가 업데이트했습니다",
        "Squid는 연결합니다",
        "Squid를 사용합니다",
        "Squid와 통합합니다",
        "Squid로 이동합니다",
    ]
