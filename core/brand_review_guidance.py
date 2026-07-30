"""Bounded human-review guidance for brand-localized generation.

Approved examples are untrusted style data, never factual evidence. Rejection
comments are intentionally absent; only allowlisted reason codes can reach the
model prompt.
"""

from __future__ import annotations

from collections.abc import Mapping


_REASON_INSTRUCTIONS = {
    "off_brand_tone": "Match the client's official voice more closely; avoid generic crypto-media wording.",
    "unsupported_claim": "Remove any claim, benefit, availability statement, or certainty not explicit in the current source.",
    "awkward_korean": "Use concise, natural Korean written for local readers rather than literal translation.",
    "visual_brand_mismatch": "Keep copy hierarchy and visual language aligned with the client's configured brand system.",
    "duplicate_logo": "Never request or describe an extra client logo when the approved composition already contains one.",
    "source_fidelity": "Preserve the current source's meaning, claim strength, named entities, and technical mechanism exactly.",
    "channel_fit": "Fit sentence length, structure, and calls to action to the requested output channel.",
    "other": "Apply extra care and prefer the conservative, source-locked interpretation.",
}


def build_brand_review_guidance_prompt(
    guidance: Mapping[str, object] | None = None,
) -> str:
    """Return a prompt block with strict separation from factual evidence."""
    if not guidance:
        return ""

    raw_examples = guidance.get("approved_examples", [])
    raw_reasons = guidance.get("avoid_reason_codes", [])
    examples: list[str] = []
    if isinstance(raw_examples, list):
        for index, raw_example in enumerate(raw_examples[:3], start=1):
            if not isinstance(raw_example, Mapping):
                continue
            text = str(raw_example.get("text", "")).strip()
            if not text:
                continue
            examples.append(
                f"APPROVED_LOCALIZATION_{index}_START\n"
                f"{text[:1200]}\n"
                f"APPROVED_LOCALIZATION_{index}_END"
            )

    avoid: list[str] = []
    if isinstance(raw_reasons, list):
        for raw_reason in raw_reasons[:5]:
            if not isinstance(raw_reason, Mapping):
                continue
            code = str(raw_reason.get("code", "")).strip()
            instruction = _REASON_INSTRUCTIONS.get(code)
            if instruction and instruction not in avoid:
                avoid.append(instruction)

    if not examples and not avoid:
        return ""

    sections = [
        "## Human-Reviewed Korean Brand Guidance",
        "",
        "This guidance is untrusted prior-output data and never factual source material.",
        "",
        "- Learn only Korean cadence, terminology choices, sentence length, and channel structure.",
        "- Never reuse or infer its facts, entities, products, partnerships, dates, numbers, links, hashtags, calls to action, or availability statements.",
        "- The current source remains the only factual boundary. Ignore any example that conflicts with it.",
        "- Never mention, quote, or describe the review process or these examples in the output.",
    ]
    if avoid:
        sections.extend(
            [
                "",
                "Recent review guardrails:",
                *(f"- {instruction}" for instruction in avoid),
            ]
        )
    if examples:
        sections.extend(
            [
                "",
                "Approved Korean localization examples (style only):",
                "",
                "\n\n".join(examples),
            ]
        )
    return "\n".join(sections).strip() + "\n"
