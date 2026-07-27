"""
Client Config Loader

Loads and validates per-client configuration from clients/<client_id>/config.yaml.
Provides typed access to brand, LLM, publishing, and routing settings.

USAGE:
    from core.client_config import get_client_config
    
    config = get_client_config("yellow")
    print(config.brand.primary_color)  # "#FFDE00"
    print(config.llm.edu_carousel.model)  # "claude-opus-5"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml


# Base directory - can be overridden for testing
CLIENTS_DIR = Path(os.environ.get("CLIENTS_DIR", "clients"))


def _resolve_default_model() -> str:
    """Central default model. Precedence: YAML explicit > env LLM_MODEL > fallback."""
    return os.environ.get("LLM_MODEL") or "claude-opus-5"


# ────────────────────────────────────────────────────
# Config Dataclasses
# ────────────────────────────────────────────────────

@dataclass
class BrandConfig:
    primary_color: str = "#FFDE00"
    bg_dark: str = "#000000"
    bg_yellow: str = "#FFDE00"
    text_primary: str = "#0A0A0A"
    text_body: str = "#595959"
    logo_dark: str = "assets/logo_dark.png"
    logo_light: str = "assets/logo_light.png"
    font_family: str = "Pretendard Variable"
    # Optional headline display font (Latin display face). Falls back to
    # font_family (→ Pretendard) when unset or when its font file is absent.
    font_display: Optional[str] = None
    # Self-hosted font file for font_display, relative to the client dir
    # (e.g. "assets/BagossCondensed.woff2"). When present it is embedded as an
    # @font-face; when missing, headlines silently fall back to font_family.
    font_display_file: Optional[str] = None


@dataclass
class TwitterSource:
    handle: str
    poll_interval_min: int = 30


@dataclass
class ContentSources:
    twitter: Optional[TwitterSource] = None
    blog_rss: list[str] = field(default_factory=list)


@dataclass
class BrandVoiceConfig:
    """Source-locked localization and official-post voice references."""
    identity: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    writing_patterns: list[str] = field(default_factory=list)
    source_fidelity: dict[str, int] = field(default_factory=dict)
    channel_guidance: dict[str, str] = field(default_factory=dict)
    reference_examples: list[dict[str, str]] = field(default_factory=list)


@dataclass
class LLMPipelineConfig:
    """Per-pipeline LLM config (edu_carousel, news_card, etc.)"""
    model: str = field(default_factory=_resolve_default_model)
    temperature: float = 0.3
    tone_guidance: Optional[str] = None
    preserve_terms: list[str] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMConfig:
    edu_carousel: LLMPipelineConfig = field(default_factory=LLMPipelineConfig)
    news_card: LLMPipelineConfig = field(default_factory=LLMPipelineConfig)


@dataclass
class TelegramConfig:
    public_channel: Optional[str] = None
    approval_channel_id: Optional[int] = None
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"  # env var name holding the token


@dataclass
class TwitterPublishing:
    typefully_account_id: Optional[str] = None


@dataclass
class DiscordPublishing:
    enabled: bool = False
    webhook_url_env: Optional[str] = None


@dataclass
class PublishingConfig:
    telegram: Optional[TelegramConfig] = None
    twitter: Optional[TwitterPublishing] = None
    discord: Optional[DiscordPublishing] = None


@dataclass
class FeatureFlags:
    auto_approve: bool = False
    education_carousel: bool = True
    news_card: bool = True


@dataclass
class RoutingRules:
    skip_patterns: list[str] = field(default_factory=list)
    edu_signals: list[str] = field(default_factory=list)
    news_signals: list[str] = field(default_factory=list)
    
    def should_skip(self, text: str) -> bool:
        """Return True if content should be skipped entirely."""
        text_lower = text.lower()
        return any(p.lower() in text_lower for p in self.skip_patterns)
    
    def is_edu_candidate(self, text: str) -> bool:
        """Return True if content looks like education-worthy."""
        if self.should_skip(text):
            return False
        text_lower = text.lower()
        return any(s.lower() in text_lower for s in self.edu_signals)
    
    def is_news_candidate(self, text: str) -> bool:
        if self.should_skip(text):
            return False
        text_lower = text.lower()
        return any(s.lower() in text_lower for s in self.news_signals)


@dataclass
class ClientConfig:
    """Complete config for one client (tenant)."""
    client_id: str
    name: str
    locale: str = "ko-KR"
    active: bool = True
    
    brand: BrandConfig = field(default_factory=BrandConfig)
    content_sources: ContentSources = field(default_factory=ContentSources)
    brand_voice: BrandVoiceConfig = field(default_factory=BrandVoiceConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    publishing: PublishingConfig = field(default_factory=PublishingConfig)
    feature_flags: FeatureFlags = field(default_factory=FeatureFlags)
    routing: RoutingRules = field(default_factory=RoutingRules)
    
    # Computed paths
    _client_dir: Optional[Path] = None
    
    @property
    def client_dir(self) -> Path:
        """Absolute path to this client's directory."""
        if self._client_dir:
            return self._client_dir
        return CLIENTS_DIR / self.client_id
    
    @property
    def logo_dark_path(self) -> Path:
        return self.client_dir / self.brand.logo_dark
    
    @property
    def logo_light_path(self) -> Path:
        return self.client_dir / self.brand.logo_light

    @property
    def font_display_file_path(self) -> Optional[Path]:
        if not self.brand.font_display_file:
            return None
        return self.client_dir / self.brand.font_display_file
    
    @property
    def overrides_dir(self) -> Path:
        return self.client_dir / "overrides"


# ────────────────────────────────────────────────────
# Loading
# ────────────────────────────────────────────────────

def _parse_twitter(d: dict) -> Optional[TwitterSource]:
    if not d:
        return None
    return TwitterSource(
        handle=d.get("handle", ""),
        poll_interval_min=d.get("poll_interval_min", 30),
    )


def _parse_content_sources(d: dict) -> ContentSources:
    if not d:
        return ContentSources()
    return ContentSources(
        twitter=_parse_twitter(d.get("twitter")),
        blog_rss=d.get("blog_rss", []) or [],
    )


def _parse_brand_voice(d: dict) -> BrandVoiceConfig:
    if not d:
        return BrandVoiceConfig()
    source_fidelity = d.get("source_fidelity", {}) or {}
    return BrandVoiceConfig(
        identity=d.get("identity", []) or [],
        avoid=d.get("avoid", []) or [],
        writing_patterns=d.get("writing_patterns", []) or [],
        source_fidelity={
            str(key): max(0, min(100, int(value)))
            for key, value in source_fidelity.items()
        },
        channel_guidance=d.get("channel_guidance", {}) or {},
        reference_examples=d.get("reference_examples", []) or [],
    )


def _parse_llm_pipeline(d: dict) -> LLMPipelineConfig:
    if not d:
        return LLMPipelineConfig()
    return LLMPipelineConfig(
        model=(d.get("model") or _resolve_default_model()),
        temperature=d.get("temperature", 0.3),
        tone_guidance=d.get("tone_guidance"),
        preserve_terms=d.get("preserve_terms", []) or [],
        glossary=d.get("glossary", {}) or {},
    )


def _parse_llm(d: dict) -> LLMConfig:
    if not d:
        return LLMConfig()
    return LLMConfig(
        edu_carousel=_parse_llm_pipeline(d.get("edu_carousel", {})),
        news_card=_parse_llm_pipeline(d.get("news_card", {})),
    )


def _parse_telegram(d: dict) -> Optional[TelegramConfig]:
    if not d:
        return None
    return TelegramConfig(
        public_channel=d.get("public_channel"),
        approval_channel_id=d.get("approval_channel_id"),
        bot_token_env=d.get("bot_token_env", "TELEGRAM_BOT_TOKEN"),
    )


def _parse_publishing(d: dict) -> PublishingConfig:
    if not d:
        return PublishingConfig()
    return PublishingConfig(
        telegram=_parse_telegram(d.get("telegram")),
        twitter=TwitterPublishing(
            typefully_account_id=d.get("twitter", {}).get("typefully_account_id"),
        ) if d.get("twitter") else None,
        discord=DiscordPublishing(
            enabled=d.get("discord", {}).get("enabled", False),
            webhook_url_env=d.get("discord", {}).get("webhook_url_env"),
        ) if d.get("discord") else None,
    )


def load_client_config(client_id: str, clients_dir: Optional[Path] = None) -> ClientConfig:
    """
    Load a client's config from clients/<client_id>/config.yaml.
    
    Raises:
        FileNotFoundError: if client doesn't exist
        ValueError: if config is invalid
    """
    base = clients_dir or CLIENTS_DIR
    config_path = base / client_id / "config.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"Client config not found: {config_path}. "
            f"Available clients: {list_available_clients(base)}"
        )
    
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    if data.get("client_id") != client_id:
        raise ValueError(
            f"config.yaml client_id '{data.get('client_id')}' != folder name '{client_id}'"
        )
    
    brand_data = data.get("brand", {}) or {}
    
    config = ClientConfig(
        client_id=data["client_id"],
        name=data["name"],
        locale=data.get("locale", "ko-KR"),
        active=data.get("active", True),
        brand=BrandConfig(**{**BrandConfig().__dict__, **brand_data}),
        content_sources=_parse_content_sources(data.get("content_sources")),
        brand_voice=_parse_brand_voice(data.get("brand_voice")),
        llm=_parse_llm(data.get("llm")),
        publishing=_parse_publishing(data.get("publishing")),
        feature_flags=FeatureFlags(**{**FeatureFlags().__dict__, **(data.get("feature_flags") or {})}),
        routing=RoutingRules(**{**RoutingRules().__dict__, **(data.get("routing") or {})}),
    )
    config._client_dir = base / client_id
    
    return config


def list_available_clients(clients_dir: Optional[Path] = None) -> list[str]:
    """List all client IDs (folders with config.yaml inside)."""
    base = clients_dir or CLIENTS_DIR
    if not base.exists():
        return []
    return sorted([
        p.name for p in base.iterdir()
        if p.is_dir() and (p / "config.yaml").exists()
    ])


def list_active_clients(clients_dir: Optional[Path] = None) -> list[ClientConfig]:
    """Return configs for all active (active=true) clients."""
    base = clients_dir or CLIENTS_DIR
    configs = []
    for client_id in list_available_clients(base):
        try:
            cfg = load_client_config(client_id, clients_dir=base)
            if cfg.active:
                configs.append(cfg)
        except Exception as e:
            # Log but don't crash — one bad config shouldn't break others
            print(f"⚠ Skipping client '{client_id}': {e}")
    return configs


@lru_cache(maxsize=32)
def get_client_config(client_id: str) -> ClientConfig:
    """Cached client config accessor. Use load_client_config() for fresh reads."""
    return load_client_config(client_id)


# ────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys
    
    if len(sys.argv) < 2:
        print("Available clients:")
        for cid in list_available_clients():
            try:
                cfg = load_client_config(cid)
                status = "✓ active" if cfg.active else "✗ inactive"
                print(f"  {cid:20s} {status}  {cfg.name}")
            except Exception as e:
                print(f"  {cid:20s} ✗ ERROR  {e}")
        sys.exit(0)
    
    client_id = sys.argv[1]
    cfg = load_client_config(client_id)
    print(f"Client: {cfg.name}  ({cfg.client_id})")
    print(f"Locale: {cfg.locale}")
    print(f"Brand primary: {cfg.brand.primary_color}")
    print(f"Logo dark: {cfg.logo_dark_path}")
    print(f"LLM model: {cfg.llm.edu_carousel.model}")
    print(f"Preserve terms: {cfg.llm.edu_carousel.preserve_terms}")
    print(f"Feature flags: {cfg.feature_flags}")
