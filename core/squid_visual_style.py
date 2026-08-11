from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


SquidVisualFamily = Literal[
    "editorial_big_type",
    "milestone_metric",
    "status_progress",
    "product_proof",
    "worldbuilding",
]
SquidVisualChannel = Literal[
    "source_remix",
    "generated_gtm",
    "manual_review",
]

SQUID_VISUAL_POLICY_VERSION = "squid-visual-routing@1"
SQUID_VISUAL_REFERENCE_PACK_VERSION = 2
SQUID_GENERATED_DESIGN_PROFILE_ID = "squid/full-bleed-character-type"
SQUID_GENERATED_DESIGN_PROFILE_VERSION = 2


@dataclass(frozen=True)
class SquidVisualReferencePack:
    pack_id: str
    version: int
    representative_status_urls: tuple[str, ...]


@dataclass(frozen=True)
class SquidVisualDecision:
    family: SquidVisualFamily
    channel: SquidVisualChannel
    automatic: bool
    policy_version: str
    reference_pack_id: str
    reference_pack_version: int
    representative_status_urls: tuple[str, ...]
    verified_metric: str | None
    reason: str

    @property
    def manual_review_required(self) -> bool:
        return not self.automatic

    @property
    def channel_profile(self) -> str:
        return "source_native" if self.channel == "source_remix" else "x_square"

    def as_spec_metadata(self) -> dict[str, object]:
        """Return JSON-safe audit metadata for a render spec or manifest."""
        metadata: dict[str, object] = {
            "creative_family": self.family,
            "render_strategy": self.channel,
            "creative_family_policy_version": self.policy_version,
            "visual_reference_pack_id": self.reference_pack_id,
            "visual_reference_pack_version": self.reference_pack_version,
            "visual_reference_status_urls": list(
                self.representative_status_urls
            ),
            "visual_automatic": self.automatic,
            "channel_profile": self.channel_profile,
        }
        if self.verified_metric is not None:
            metadata["visual_metric"] = self.verified_metric
        if self.channel == "generated_gtm":
            metadata.update({
                "visual_design_profile_id": SQUID_GENERATED_DESIGN_PROFILE_ID,
                "visual_design_profile_version": (
                    SQUID_GENERATED_DESIGN_PROFILE_VERSION
                ),
            })
        return metadata


_REFERENCE_PACKS: dict[SquidVisualFamily, SquidVisualReferencePack] = {
    "editorial_big_type": SquidVisualReferencePack(
        pack_id="squid/editorial-big-type",
        version=SQUID_VISUAL_REFERENCE_PACK_VERSION,
        representative_status_urls=(
            "https://x.com/squidrouter/status/2079999207956500971",
        ),
    ),
    "milestone_metric": SquidVisualReferencePack(
        pack_id="squid/milestone-metric",
        version=SQUID_VISUAL_REFERENCE_PACK_VERSION,
        representative_status_urls=(
            "https://x.com/squidrouter/status/2082889008385044897",
        ),
    ),
    "status_progress": SquidVisualReferencePack(
        pack_id="squid/status-progress",
        version=SQUID_VISUAL_REFERENCE_PACK_VERSION,
        representative_status_urls=(
            "https://x.com/squidrouter/status/2080668216792129968",
        ),
    ),
    "product_proof": SquidVisualReferencePack(
        pack_id="squid/product-proof",
        version=SQUID_VISUAL_REFERENCE_PACK_VERSION,
        representative_status_urls=(
            "https://x.com/squidrouter/status/2079628218403803481",
            "https://x.com/squidrouter/status/2083266484789514640",
        ),
    ),
    "worldbuilding": SquidVisualReferencePack(
        pack_id="squid/worldbuilding",
        version=SQUID_VISUAL_REFERENCE_PACK_VERSION,
        representative_status_urls=(
            "https://x.com/squidrouter/status/2083583547353501977",
            "https://x.com/squidrouter/status/2073032336384356666",
        ),
    ),
}

_SCALED_METRIC_PATTERN = re.compile(
    r"(?<![a-z0-9])\d+(?:[.,]\d+)?\s*"
    r"(?:k|m|b|thousand|million|billion)(?![a-z])",
    re.IGNORECASE,
)
_METRIC_NOUN_PATTERN = re.compile(
    r"\b(?:transaction|swap|route|transfer|user|wallet|volume|chain|"
    r"integration|partner)s?\b",
    re.IGNORECASE,
)
_MILESTONE_PATTERN = re.compile(
    r"\b(?:milestone|record|reached|crossed|surpassed|processed)\b",
    re.IGNORECASE,
)
_STATUS_PROGRESS_PATTERN = re.compile(
    r"\b(?:tge|token generation event|airdrop|allocation|eligibility|"
    r"claim(?:ing)?|snapshot|countdown|phase|progress|status)\b",
    re.IGNORECASE,
)
_PRODUCT_PROOF_PATTERN = re.compile(
    r"\b(?:minipay|telegram|bridge|swap|transfer|route|routing|wallet|"
    r"widget|api|sdk|integration|integrated|supports|supported|app|"
    r"guide|tutorial|docs|documentation|how to|step-by-step)\b",
    re.IGNORECASE,
)
_WORLD_BUILDING_PATTERN = re.compile(
    r"\b(?:squib|mascot|meme|vibes?|weekend|gm|gn|bouncing|chillin['’]?|"
    r"worldbuilding)\b",
    re.IGNORECASE,
)
_EDITORIAL_PATTERN = re.compile(
    r"\b(?:new era|announcement|announcing|launch|launched|release|"
    r"released|update|ecosystem|partnership|mainnet)\b",
    re.IGNORECASE,
)


def reference_pack_for_family(
    family: SquidVisualFamily,
) -> SquidVisualReferencePack:
    return _REFERENCE_PACKS[family]


def _normalize_text(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _family_for_reference_url(source_url: str) -> SquidVisualFamily | None:
    normalized_url = _normalize_text(source_url).casefold().rstrip("/")
    if not normalized_url:
        return None
    for family, pack in _REFERENCE_PACKS.items():
        if any(
            normalized_url == reference_url.casefold().rstrip("/")
            for reference_url in pack.representative_status_urls
        ):
            return family
    return None


def _verified_metric(source_content: str) -> str | None:
    text = _normalize_text(source_content).casefold()
    match = _SCALED_METRIC_PATTERN.search(text)
    if match is None:
        return None
    return match.group(0).strip()


def _classify_family(
    source_content: str,
    *,
    source_url: str,
) -> tuple[SquidVisualFamily, str]:
    text = _normalize_text(source_content)
    reference_family = _family_for_reference_url(source_url)
    if reference_family is not None:
        return reference_family, "source matches a reviewed official visual reference"

    has_scaled_metric = _SCALED_METRIC_PATTERN.search(text) is not None
    has_metric_noun = _METRIC_NOUN_PATTERN.search(text) is not None
    has_milestone_language = _MILESTONE_PATTERN.search(text) is not None
    if has_scaled_metric and (has_metric_noun or has_milestone_language):
        return "milestone_metric", "source contains a concrete product metric"

    if _STATUS_PROGRESS_PATTERN.search(text):
        return "status_progress", "source describes a bounded status or phase"

    if _PRODUCT_PROOF_PATTERN.search(text):
        return "product_proof", "source describes a product action or proof point"

    if _WORLD_BUILDING_PATTERN.search(text):
        return "worldbuilding", "source is mascot-, mood-, or community-led"

    if (
        _EDITORIAL_PATTERN.search(text)
        or len(text) >= 48
        or len(text.split()) >= 8
    ):
        return "editorial_big_type", "source supports a factual editorial treatment"

    return "worldbuilding", "source is too sparse for factual generated artwork"


def classify_squid_visual_style(
    source_content: str,
    source_url: str = "",
    has_official_media: bool = False,
) -> SquidVisualDecision:
    """Choose a Squid visual family and safe render channel deterministically.

    ``has_official_media`` must only be set after the caller has verified an
    exact official-source media URL. Any verified source media is preserved via
    ``source_remix``; generated GTM artwork is only used when no such media
    exists. Text-only worldbuilding posts fail closed to manual review because
    inventing a mascot scene would falsely imply an official creative.
    """
    family, reason = _classify_family(
        source_content,
        source_url=source_url,
    )
    has_verified_media = has_official_media is True

    if has_verified_media:
        channel: SquidVisualChannel = "source_remix"
        automatic = True
        reason = f"{reason}; verified official media is preserved"
    elif family == "worldbuilding":
        channel = "manual_review"
        automatic = False
        reason = f"{reason}; text-only worldbuilding fails closed"
    else:
        channel = "generated_gtm"
        automatic = True
        reason = f"{reason}; no verified official media is available"

    pack = reference_pack_for_family(family)
    return SquidVisualDecision(
        family=family,
        channel=channel,
        automatic=automatic,
        policy_version=SQUID_VISUAL_POLICY_VERSION,
        reference_pack_id=pack.pack_id,
        reference_pack_version=pack.version,
        representative_status_urls=pack.representative_status_urls,
        verified_metric=(
            _verified_metric(source_content)
            if family == "milestone_metric"
            else None
        ),
        reason=reason,
    )
