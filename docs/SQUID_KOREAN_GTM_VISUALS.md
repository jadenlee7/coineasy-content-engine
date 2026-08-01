# Squid Korean GTM visual contract

This contract keeps generated Korean content recognizably Squid while preserving
the official source as the factual and visual boundary.

## Source-first routing

1. Use media attached directly to the official `@SquidRouter` post.
2. If the post is a quote with no direct media, inherit media only when the
   quoted post resolves to the same verified X account identity.
3. A source creative enters `remix`: preserve its crop, character, logo, product
   UI, and hierarchy. Translate only meaningful copy in its audited footprint.
4. Use `classic` only when no trustworthy official source visual exists.
5. Never borrow media from a partner or community quote merely because it is
   visually relevant.

Legacy `editorial` and `signal` requests are canonicalized to `classic` at the
renderer, Netlify editable boundary, and Studio UI. The original requested
style remains audit metadata, but a generic publisher layout is never emitted.

## Generated-card family

The default 1080×1080 Korean card follows the approved CoinEasy Management
Squid frame geometry but uses current official Squid visual tokens:

- light lavender `#E6CCFC`, acid lime `#EFFF5A`, white haze, and black;
- one official SQUIB as the dominant visual, optionally with official bubbles;
- 60–72 px outer safe area;
- one topic eyebrow, one normally 84–108 px headline of at most two lines
  (64 px floor at the length boundary), one or two 28–34 px supporting lines,
  and tiny source metadata;
- headline copy is limited to 28 characters; each supporting line is limited
  to 23 characters so PNG and editable SVG stay visually identical;
- no white news panel, bullet cards, generic CTA pill, dark analytics grid, or
  duplicate logo;
- total visible Korean copy should normally stay below about 70 characters.

If the source is a mood post, preserve it with no added explainer. A milestone
may use a single large verified number and one label. Product-proof creatives
must retain the real UI or partner mark from the source rather than drawing a
generic diagram.

## Korean voice

- Preserve a question or one-line reaction instead of expanding it into a press
  release.
- Natural 해요체 is allowed for banner and social hooks. Long factual article
  explanations may use 합니다/습니다.
- Prefer a product action and one verified fact. Do not invent Korean launch
  availability, speed, savings, liquidity, or market impact.
- Avoid generic publisher language such as `간편하게 탐색할 수 있습니다`,
  `소식을 전합니다`, `소개합니다`, `핵심 변화`, `최신 소식`, and `전체 맥락`.
- Telegram defaults to the hook, one or two support lines, the original link,
  `#Squid`, and one topic hashtag.

## Article visuals

Squid article heroes and inline figures stay in the same light official world.
Their motif may change the SQUIB, form-language, bubble, and lime-path
composition, but must not fall back to the shared dark grid, rail, dashboard,
or three-card diagram. Visual copy remains sparse and source-grounded: a
20-character headline, a 50-character caption, and exactly two evidence points
of at most 26 characters each.

## Asset and release safety

- Reviewed files live under `clients/squid/assets` and
  `web/console/assets/brands`.
- Do not synthesize a replacement mascot or redraw the official logo.
- `BagossCondensed.woff2` activates only when CoinEasy has a redistribution-safe
  licensed file at `clients/squid/assets/BagossCondensed.woff2`. Never copy the
  font from the public website merely because it is downloadable.
- Every output still requires human review. Telegram publication feature flags
  remain disabled while visual work is under review.
