# ADR-001: Original Visual Remix Pipeline

**Status:** Accepted  
**Date:** 2026-07-19  
**Deciders:** CoinEasy content engine team

## Context

The existing news-card pipeline converts post text into fixed brand templates. For partner and product announcements, the original post banner often contains the most aligned product UI, campaign art, logos, and launch copy. CoinEasy needs a Korean GTM treatment that preserves that composition instead of replacing it with a generic card.

The browser must not fetch arbitrary source images, expose server credentials, or forward unbounded image payloads. The renderer must also degrade safely when a public X post has no photo or X's public embed metadata is unavailable.

## Decision

Add `remix` as a fourth news-card style and make it the recommended console default.

1. The Netlify function resolves a public X status and reads its first photo URL from X's public syndication metadata.
2. Only `https://pbs.twimg.com` media URLs are forwarded to Railway.
3. Railway validates the host again, downloads at most 8 MB, decodes with Pillow, bounds the pixel count, auto-orients, and resizes to at most 1800 px.
4. The prepared image is sent to Claude as a vision block so visible product names, UI states, token pairs, and numbers can support the Korean copy.
5. The same vision response reports whether the current client's official logo or wordmark is already visible in the source image.
6. The renderer keeps the full original image with `object-fit: contain` and adds a separate Korean localization panel below it, with no agency mark.
7. If the official logo is visible in the source image, the renderer preserves it and omits an extra logo. Otherwise it places the official logo once in the localization panel's safe area.
8. If no valid image is available, a generic manual remix automatically renders
   with the `classic` template and reports the actual style in the response.
   Scheduled source-remix requests for all four active clients prove the official account,
   status ID, and attached media together and fail closed once `remix` is
   selected. Replacing a verified official campaign creative with a generic
   card would violate the approved brand-localization contract.

## Options Considered

### A. Deterministic framed remix (selected)

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Cost | Low; one existing vision-capable LLM call |
| Brand fidelity | High; original pixels and composition remain intact |
| Reliability | High with classic fallback |

**Pros:** Preserves the original visual, avoids generative drift, works with the existing Playwright renderer, and remains easy to audit.  
**Cons:** Korean copy is added in a dedicated panel rather than rewritten inside every location in the source artwork.

### B. Generative image editing

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Cost | Medium to high |
| Brand fidelity | Variable |
| Reliability | Lower; product UI and logos may drift |

**Pros:** Can replace English text in-place and produce more varied compositions.  
**Cons:** Risks modifying product screenshots, token symbols, partner logos, and other factual visual details.

### C. Browser-side composition

| Dimension | Assessment |
|---|---|
| Complexity | Low initially |
| Security | Poor |
| Reliability | Poor due to CORS and remote-media availability |

**Pros:** Minimal server changes.  
**Cons:** Exposes media handling to the browser, cannot enforce consistent validation, and makes high-resolution output unreliable.

## Consequences

- Original artwork remains visually authoritative while a clearly separated Korean editorial layer provides the localization treatment without an agency mark.
- Official-logo presence is checked on every vision-backed remix to prevent redundant logo placement.
- X syndication metadata is an external dependency that may change. Scheduled
  photo- or video-poster-backed remixes for every active client surface a
  retry/review failure rather than silently changing the verified composition.
  Text-only sources retain the relevant client `classic` treatment.
- Only the first public photo is used in v1. Multi-image selection, video posters, manual uploads, and in-place translation can be added later.
- Article and blog URLs still require pasted text and an approved image ingestion path.

## Action Items

1. Monitor the rate of `remix` requests that fall back to `classic`.
2. Add manual image upload only after defining storage lifetime and rights/approval rules.
3. Consider controlled in-place text replacement only for approved first-party campaign assets.
