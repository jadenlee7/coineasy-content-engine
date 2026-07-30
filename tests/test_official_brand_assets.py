from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.renderers.playwright_renderer import (
    OfficialBrandAssetError,
    _logo_to_data_url,
)


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_LOGOS = {
    "babylon-dark.png": (
        ROOT / "clients/babylon/assets/logo_dark.png",
        "f10bae44af090ffa0c594af301dc06489c0cbe3add44a766a724cddc36b4cdd6",
    ),
    "babylon-light.png": (
        ROOT / "clients/babylon/assets/logo_light.png",
        "f10bae44af090ffa0c594af301dc06489c0cbe3add44a766a724cddc36b4cdd6",
    ),
    "origintrail-dark.png": (
        ROOT / "clients/origintrail/assets/logo_dark.png",
        "3e4247599f38271f9f107531fbf780b387a2704199a049f63af7964ac53c1416",
    ),
    "origintrail-light.png": (
        ROOT / "clients/origintrail/assets/logo_light.png",
        "a9792d581545acae6345ebd0db8ae8f8044474b387ec9df00706229df0a44e82",
    ),
    "squid-dark.png": (
        ROOT / "clients/squid/assets/logo_dark.png",
        "39ab13fa6dd2846a96a4d861bb0fc8574f6695790f6de7af2dda17811ece501c",
    ),
    "squid-light.png": (
        ROOT / "clients/squid/assets/logo_light.png",
        "bdd7715d117aeedba0fd9cf50263cd532aab3ab20716ce7d32da01ae71628ca7",
    ),
    "yellow-dark.svg": (
        ROOT / "clients/yellow/assets/logo_dark.svg",
        "e82e0ebf3b902deea8c962d062bb579774cd2f34b6dcfd5ca808308b79767e90",
    ),
    "yellow-light.svg": (
        ROOT / "clients/yellow/assets/logo_light.svg",
        "a8f05658f3ce83493ee6db7c358d6576bd05762d1fc7f816c78fee32b17baee6",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reviewed_official_logo_exports_and_console_copies_are_exact() -> None:
    public_dir = ROOT / "web/console/assets/brands"
    for public_name, (canonical_path, expected_hash) in OFFICIAL_LOGOS.items():
        public_path = public_dir / public_name
        assert canonical_path.is_file()
        assert public_path.is_file()
        assert _sha256(canonical_path) == expected_hash
        assert _sha256(public_path) == expected_hash


def test_renderer_fails_closed_instead_of_hiding_a_missing_official_logo(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-official-logo.png"
    with pytest.raises(OfficialBrandAssetError, match="Official logo is unavailable"):
        _logo_to_data_url(missing)
