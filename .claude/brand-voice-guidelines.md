# Multi-Client Brand Voice Guidelines

## Generation Metadata

- Created: 2026-07-19
- Version: 1
- Sources: Direct user direction, existing client configuration, and 46 visible posts sampled from the official X accounts for Yellow, OriginTrail, Squid, and Babylon
- Overall confidence: Medium-High
- Operating mode: Strict source-locked Korean localization

## Executive Summary

Every output should feel like the client's official post localized by its Korean
team, not like a separate media brand summarizing the client. The official source
sets the factual boundary, certainty, structure, and promotional intensity.
Korean localization may clarify unfamiliar context but must not add claims.

Voice stays client-specific. Tone flexes by channel: X remains closest to the
original, Telegram may add one Korean relevance sentence and a clear CTA, banners
keep the original visual dominant, and education content may explain more deeply
without changing the mechanism.

## Global We Are / We Are Not

| We Are | We Are Not |
|---|---|
| Source-faithful and precise | A commentary outlet adding its own thesis |
| Recognizably native to each client | One generic CoinEasy voice for every client |
| Natural in Korean | Literal when literal wording sounds unnatural |
| Clear about uncertainty | Willing to infer missing launches, benefits, or availability |
| Visually restrained | Adding internal labels, duplicate logos, or unnecessary branding |

## Channel Matrix

| Context | Source fidelity | Formality | Energy | Technical depth | Rule |
|---|---:|---|---|---|---|
| X | 90-95% | Match source | Match source | Match source | Localize the original content only; preserve handles, cashtags, hashtags, cadence, and claim strength |
| Telegram | 80-85% | Medium | Medium | Medium | Add at most one Korean relevance line, then facts, CTA, original link, and focused hashtags |
| Banner | 90%+ | Low text volume | Match source | Low-Medium | Preserve the source visual and official logo; add one Korean headline and minimal supporting copy |
| Education | 75-82% | Medium | Medium | Medium-High | Explain the source mechanism progressively without adding a broader thesis |

## Yellow Voice

- **We are:** institutional, infrastructure-led, analytical, measured, precise.
- **We are not:** meme-first, breathless, vague, or generically excited.
- **Default structure:** structural problem → market transition → Yellow mechanism → restrained implication or CTA.
- **Language:** complete paragraphs, settlement and clearing terminology, few emojis.
- **Evidence:** [payment infrastructure](https://x.com/Yellow/status/2078328954574750124), [off-chain clearing](https://x.com/Yellow/status/2078087351893414295), [product utility](https://x.com/Yellow/status/2077374193218285825).
- **Confidence:** High for social cadence; Medium for long-form content.

## OriginTrail Voice

- **We are:** trust-first, thesis-led, verifiable, causal, technically credible.
- **We are not:** abstract without proof, token-price-led, or detached from provenance.
- **Default structure:** short contrast or trust problem → causal break → verifiable context → real-world proof.
- **Language:** strong contrast hooks, traceability, shared context, concrete examples.
- **Evidence:** [shared context contrast](https://x.com/origin_trail/status/2078063452996661578), [medical provenance](https://x.com/origin_trail/status/2076973514217709978), [threat analysis mechanism](https://x.com/origin_trail/status/2076757788097724762).
- **Confidence:** High for social cadence and terminology; Medium for tutorials.

## Squid Voice

- **We are:** brief, playful, human, product-sharp, community-aware.
- **We are not:** a formal press release, over-explained, generic cross-chain hype, or branded as “Squid Router.”
- **Default structure:** question or one-liner → product moment → compact payoff.
- **Language:** deliberate line breaks, short lists, occasional wit, `Squid` as the display name.
- **Banner localization:** treat the official creative as the final composition. Replace only meaningful visible copy with concise natural Korean in the same hierarchy and alignment. Never place localized text on a solid caption box or blurred image patch; cover the original copy only with a transparent outline and shadow that follows the replacement glyphs. Keep the source line count and visual width; a short repeated Latin keyword may remain when it carries the original rhythm. When the creative has no translatable copy, preserve the character and artwork without adding a headline, badge, footer, CTA, or duplicate logo.
- **Evidence:** [community one-liner](https://x.com/squidrouter/status/2078510114705997829), [XRP product hook](https://x.com/squidrouter/status/2077425019547005429), [capability list](https://x.com/squidrouter/status/2077817328901796275).
- **Confidence:** High for social cadence; Medium for technical education.

## Babylon Voice

- **We are:** Bitcoin-native, direct, technically exact, action-oriented, measured.
- **We are not:** speculative, yield-promising, vague about custody, or loose with launch status.
- **Default structure:** product state or user question → exact Bitcoin mechanism → steps or one-line CTA.
- **Language:** custody, collateral, staking status, network, and testnet/mainnet terms remain exact.
- **Evidence:** [native borrowing launch](https://x.com/babylonlabs_io/status/2061801513488429361), [staking status guide](https://x.com/babylonlabs_io/status/2077787192668160499), [one-line CTA](https://x.com/babylonlabs_io/status/2077787195469918330).
- **Confidence:** High for product and guide posts; Medium for broader thought leadership.

## Terminology and Hard Rules

- Use `Squid`, never `Squid Router`, as the human-facing display name.
- Preserve official identifiers such as `@SquidRouter`, `x.com/squidrouter`, and `#SquidRouter`.
- Preserve client product names, handles, cashtags, numbers, network names, and launch status.
- Never infer Korean availability, partnerships, rewards, compliance, performance, or adoption.
- Do not add CoinEasy branding or internal workflow labels to public content.
- If the source visual already contains the client logo, do not place a duplicate logo.
- If source facts are insufficient, output less copy instead of inventing context.

## Confidence Scores

| Section | Confidence | Basis |
|---|---|---|
| Source-fidelity policy | High | Explicit user direction repeated across the session |
| Social voice patterns | High | 9-14 recent official posts sampled per client |
| Terminology | High | Existing client glossaries plus official account usage |
| Channel matrix | Medium-High | User requirements plus observed social patterns |
| Long-form voice | Medium | Current research emphasized X rather than official blogs and docs |

## Open Questions and Recommendations

1. **Approved Korean examples**
   - What was found: Official global X patterns are clear, but a curated set of previously approved Korean posts is not yet stored in the engine.
   - Agent recommendation: Keep strict source-locked mode as the default and add approved Korean outputs to the reference set after human approval.
   - Need from team: Mark strong Korean outputs as approved rather than changing the global prompt ad hoc.

2. **Reference refresh cadence**
   - What was found: Brand social cadence can evolve around campaigns and launches.
   - Agent recommendation: Refresh recent official references monthly, while retaining approved evergreen examples.
   - Need from team: Confirm monthly refresh after the first month of production use.

## Data Gaps and Recommendations

- Add approved Korean X, Telegram, banner, and tutorial examples per client.
- Add official long-form blog examples for higher-confidence tutorial voice.
- Track rejected outputs and their rejection reason as negative examples.
