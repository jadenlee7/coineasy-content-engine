"""Build source-locked brand voice instructions from per-client config."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.client_config import ClientConfig
from core.style_references import build_runtime_style_reference_prompt


_CHANNEL_FOR_PIPELINE = {
    "news_card": "banner",
    "education": "education",
}


def _bullet_block(items: list[str], fallback: str) -> str:
    if not items:
        return f"  - {fallback}"
    return "\n".join(f"  - {item}" for item in items)


def build_brand_voice_prompt(
    config: ClientConfig,
    pipeline: str,
    style_references: Sequence[Mapping[str, str]] = (),
) -> str:
    """Return LLM-ready official-post voice and source-fidelity rules."""
    voice = config.brand_voice
    channel = _CHANNEL_FOR_PIPELINE.get(pipeline, pipeline)
    fidelity = voice.source_fidelity.get(
        channel,
        voice.source_fidelity.get("default", 90),
    )
    guidance = str(voice.channel_guidance.get(channel, "")).strip()
    if not guidance:
        raise ValueError(
            f"client '{config.client_id}' must configure brand_voice.channel_guidance."
            f"{channel} before generating {pipeline} content"
        )

    examples = []
    for example in voice.reference_examples[:5]:
        text = str(example.get("text", "")).strip()
        if not text:
            continue
        example_type = str(example.get("type", "official post")).strip()
        source_url = str(example.get("url", "")).strip()
        source_note = f" ({source_url})" if source_url else ""
        examples.append(f"  - [{example_type}] {text}{source_note}")
    example_block = "\n".join(examples) or "  - No official-post examples configured."

    static_prompt = f"""## Official Brand Voice Lock

- Source fidelity target for this output: {fidelity}%.
- Treat the source as the factual boundary. Do not add benefits, availability,
  partnerships, dates, numbers, or Korea-specific claims that are not present.
- Korean localization may improve clarity and natural phrasing, but must not
  change the original post's claim, certainty, or promotional intensity.
- If a detail is uncertain, omit it instead of inferring it.
- Reference examples below guide cadence and structure only. Never borrow their
  facts for the current source.

Voice constants:
{_bullet_block(voice.identity, "Clear, factual, and recognizably official")}

Do not sound like:
{_bullet_block(voice.avoid, "Generic crypto hype or an unrelated media account")}

Observed official-post patterns:
{_bullet_block(voice.writing_patterns, "Lead with the source's strongest factual hook")}

Channel rule ({channel}):
  - {guidance}

Official-post reference examples:
{example_block}
"""
    runtime_prompt = build_runtime_style_reference_prompt(style_references)
    return static_prompt if not runtime_prompt else f"{static_prompt}\n{runtime_prompt}"
