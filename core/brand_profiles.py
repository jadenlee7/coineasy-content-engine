"""Server-owned brand contracts for generated client news cards.

The LLM may write card copy, but it must never select or rename the visual
brand system. These profiles bind every non-Squid news-card result to the
reviewed client tokens, official asset pack, and template family. Squid keeps
its stricter source/generated routing contract in ``squid_visual_style``.
"""
from __future__ import annotations

from dataclasses import dataclass


NEWS_BRAND_PROFILE_POLICY_VERSION = "client-news-brand-profiles@1"


@dataclass(frozen=True)
class NewsBrandProfile:
    client_id: str
    design_profile_id: str
    design_profile_version: int
    brand_tokens_version: str
    asset_pack_version: str

    def spec_metadata(self, template_style: str) -> dict[str, object]:
        if template_style not in {"classic", "editorial", "signal", "remix"}:
            raise ValueError(f"Unsupported news-card template style: {template_style}")
        return {
            "brand_profile_policy_version": NEWS_BRAND_PROFILE_POLICY_VERSION,
            "render_strategy": (
                "source_remix" if template_style == "remix" else "brand_native"
            ),
            "channel_profile": "x_square",
            "brand_tokens_version": self.brand_tokens_version,
            "template_version": f"{self.client_id}-news-{template_style}@1",
            "asset_pack_version": self.asset_pack_version,
            "visual_design_profile_id": self.design_profile_id,
            "visual_design_profile_version": self.design_profile_version,
        }


NEWS_BRAND_PROFILES = {
    "yellow": NewsBrandProfile(
        client_id="yellow",
        design_profile_id="yellow/institutional-market-infrastructure",
        design_profile_version=1,
        brand_tokens_version="yellow-brand-tokens@1",
        asset_pack_version="yellow-official-brand-assets@1",
    ),
    "origintrail": NewsBrandProfile(
        client_id="origintrail",
        design_profile_id="origintrail/verifiable-knowledge",
        design_profile_version=1,
        brand_tokens_version="origintrail-brand-tokens@1",
        asset_pack_version="origintrail-official-brand-assets@1",
    ),
    "babylon": NewsBrandProfile(
        client_id="babylon",
        design_profile_id="babylon/bitcoin-native-infrastructure",
        design_profile_version=1,
        brand_tokens_version="babylon-brand-tokens@1",
        asset_pack_version="babylon-official-brand-assets@1",
    ),
}


_SERVER_OWNED_PROFILE_FIELDS = frozenset({
    "brand_profile_policy_version",
    "render_strategy",
    "channel_profile",
    "brand_tokens_version",
    "template_version",
    "asset_pack_version",
    "visual_design_profile_id",
    "visual_design_profile_version",
})


def apply_news_brand_profile(
    spec: dict,
    client_id: str,
    template_style: str,
) -> None:
    """Replace any model-supplied branding metadata with the client contract."""
    profile = NEWS_BRAND_PROFILES.get(client_id)
    if profile is None:
        raise ValueError(f"No standard news brand profile for client: {client_id}")
    for field in _SERVER_OWNED_PROFILE_FIELDS:
        spec.pop(field, None)
    spec.update(profile.spec_metadata(template_style))
