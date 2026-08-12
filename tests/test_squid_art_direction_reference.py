from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    ROOT
    / "docs"
    / "reference-manifests"
    / "squid-telegram-korean-style-v1.json"
)
REFERENCE_SHA256 = (
    "92a761fe9da400920f44d55c56007361854b2baeb8bead5fbac552051d03bb0b"
)


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_selected_telegram_direction_is_explicitly_reference_only():
    manifest = _manifest()

    assert manifest["classification"] == "reference_only"
    assert manifest["selection_scope"] == "user_selected_art_direction"
    assert manifest["runtime_eligible"] is False
    assert manifest["publication_eligible"] is False
    assert manifest["figma_template_eligible"] is False
    assert manifest["approved_clean_plate_eligible"] is False

    artifact = manifest["artifact"]
    assert isinstance(artifact, dict)
    assert artifact == {
        "sha256": REFERENCE_SHA256,
        "bytes": 1374292,
        "mime_type": "image/png",
        "width": 1254,
        "height": 1254,
        "stored_in_runtime_repository": False,
    }


def test_selected_direction_records_generated_provenance_and_source_boundary():
    manifest = _manifest()
    provenance = manifest["provenance"]
    official_source = manifest["related_official_source"]

    assert isinstance(provenance, dict)
    assert provenance["official_squid_export"] is False
    assert provenance["generated_media"] is True
    assert provenance["generator_agent"] == "gpt-image"
    assert provenance["digital_source_type"] == "trainedAlgorithmicMedia"

    assert isinstance(official_source, dict)
    assert (
        official_source["x_status_url"]
        == "https://x.com/squidrouter/status/2083266484789514640"
    )
    assert official_source["binding_status"] == "not_runtime_source_bound"


def test_reference_raster_cannot_be_silently_promoted_to_a_runtime_asset():
    runtime_roots = (
        ROOT / "clients" / "squid" / "assets",
        ROOT / "web" / "console" / "assets" / "brands",
    )

    for runtime_root in runtime_roots:
        for path in runtime_root.rglob("*"):
            if path.is_file():
                assert hashlib.sha256(path.read_bytes()).hexdigest() != REFERENCE_SHA256


def test_selected_direction_preserves_dense_type_character_hierarchy():
    manifest = _manifest()
    design_profile = manifest["design_profile"]

    assert isinstance(design_profile, dict)
    assert design_profile["version"] == 2
    required_cues = design_profile["required_cues"]
    forbidden_cleanup = design_profile["forbidden_cleanup"]
    assert "deliberate type-to-character overlap" in required_cues
    assert "tight stacked leading and assertive edge crop" in required_cues
    assert "centered lower headline for short Korean hooks" in required_cues
    assert "adding generous whitespace around the headline" in forbidden_cleanup
    assert "separating all type from the character" in forbidden_cleanup
    assert (
        "adding decorative bubbles that compete with the selected poster silhouette"
        in forbidden_cleanup
    )
