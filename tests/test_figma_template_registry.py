from core.figma_template_registry import (
    approved_figma_template,
    load_figma_template_registry,
)


def test_registry_uses_only_explicit_keep_frames():
    registry = load_figma_template_registry()

    assert registry["file_key"] == "hsRSASQjEMxl5NMLH9y5Wm"
    assert set(registry["templates"]) == {"squid", "yellow"}
    assert set(registry["pending_clients"]) == {"origintrail", "babylon"}
    for template in registry["templates"].values():
        assert template["frame_name"].startswith("[KEEP] Banner_")
        assert template["status"] == "approved"
        assert template["content_engine_style"] == "classic"


def test_template_reference_requires_matching_client_kind_and_renderer():
    squid = approved_figma_template(
        "squid",
        content_kind="daily_news",
        template_style="classic",
    )
    assert squid == {
        "registry_schema_version": "1.0",
        "file_key": "hsRSASQjEMxl5NMLH9y5Wm",
        "file_name": "CoinEasy Management",
        "page_name": "Daily content",
        "node_id": "1479:1954",
        "frame_name": "[KEEP] Banner_Squid_Sample",
        "status": "approved",
        "version": "2026-07-30.1",
    }
    assert approved_figma_template(
        "squid",
        content_kind="article",
        template_style="classic",
    ) is None
    assert approved_figma_template(
        "squid",
        content_kind="daily_news",
        template_style="remix",
    ) is None
    assert approved_figma_template(
        "babylon",
        content_kind="daily_news",
        template_style="classic",
    ) is None
