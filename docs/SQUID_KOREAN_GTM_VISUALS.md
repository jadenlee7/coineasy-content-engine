# Squid Korean GTM visual contract

This contract keeps generated Korean content recognizably Squid while preserving
the official source as the factual and visual boundary.

## Source-first routing

1. Use media attached directly to the official `@SquidRouter` post. For a
   video, use its canonical official poster frame for the static card; never
   present that frame as if the output retained the source animation.
2. If the post is a quote with no direct media, inherit media only when the
   quoted post resolves to the same verified X account identity.
3. A source creative enters `remix`: preserve its complete aspect ratio and
   crop, character, logo, product UI, and hierarchy. Translate only meaningful
   copy in its audited footprint. Failed cleanup must keep the full untouched
   source, never crop away the original caption as a recovery shortcut.
   The primary Squid X asset uses the source-native aspect ratio (bounded to a
   1200 px long edge) instead of shrinking a landscape poster into a square
   letterbox. A square version is a separate channel derivative, never the
   authoritative remix.

   The Telegram launch status `2083266484789514640` has one exceptional,
   source-bound localization approval: a versioned text-free clean plate plus
   two immutable Korean replacement regions (`Squid가`,
   `텔레그램에\n왔어요`). The clean plate is an AI-assisted derivative and is
   documented as such; it is not an official export and cannot be selected by
   a request or reused for another post. Exact source/media/byte hashes and
   dimensions are verified before use.
   Source density is part of that hierarchy. Oversized type, tight leading,
   edge crops, and deliberate type-to-character overlap must not be "cleaned
   up" into generic whitespace. The official Telegram poster is the reviewed
   square example: its upper and lower display blocks remain dominant around
   the SQUIB and paper plane when the copy is localized.
4. Use `classic` only when no trustworthy official source visual exists.
5. Never borrow media from a partner or community quote merely because it is
   visually relevant.

This is the default for every ordinary Squid official-X `daily_news` post, not
only launch or campaign posters. Once verified official media exists, neither
its visual family nor its subject matter may route the post to generated GTM
artwork: the output keeps the official composition and changes only audited
in-image copy to Korean. If copy discovery, placement, cleanup, or browser fit
is unsafe, the renderer keeps the untouched original, records the failure for
critical brand review, and the durable item remains `needs_review`; it never
silently redesigns or publishes the post.

The Studio defaults Squid news creation to `원문 우선`. Scheduled automation
pins the already-selected official X media URL through the automation-only
boundary, binds that normalized URL to the idempotency hash, and stores its
media-resolution status with the immutable result. Railway echoes the exact
validated URL and SHA-256 of the prepared source bytes; Netlify rejects a
mismatch before durable storage. The server does not perform a second media
selection that could swap the creative between intake and render. If X media
lookup is unavailable, `remix` fails closed; an explicit `classic` request
remains the image-free fallback.

The durable asset is currently the final PNG, not a private copy of the raw X
image. The URL and prepared-byte digest prove what the first render consumed,
while a future editable regeneration still depends on the external source (and,
for translated creatives, the temporary cleaned source). Persisting a private
source asset is a separate retention upgrade.

Legacy `editorial` and `signal` requests are canonicalized to `classic` at the
renderer, Netlify editable boundary, and Studio UI. The original requested
style remains audit metadata, but a generic publisher layout is never emitted.

## Squid Korea Figma stage (reference only)

The current Korea-stage treatment was visually reviewed in the CoinEasy
Management Figma file `hsRSASQjEMxl5NMLH9y5Wm`, Projects nodes `2910:2690`,
`2910:2700`, `2918:2587`, `2918:2597`, `2918:2616`, `2918:2624`,
`2918:2631`, `2918:2638`, and `2918:2645`. These nodes are
`reference_only`: they define composition language, not an approved reusable
template and not a factual source.

Generated square cards adapt the reviewed grammar as a full-bleed
lavender-to-white halo with an oversized fixed `Squid` frame word, a dominant
cropped official SQUIB, approved bubbles/form-language, and at most two dense
lines of source-grounded Korean display copy. The prior white oval, bottom
generic frame word, small logo, support panel, and public source footer are not
part of the v4 profile. Accent lime remains the canonical `#E6FA36`; no
decorative frame position may introduce a product claim.

Never copy a Projects node's headline, date, figure, event, launch status, or
other content-specific wording into an output. Every published claim must be
derived from the current verified official source and pass the normal factual
review. The approved legacy `[KEEP] Banner_Squid_Sample` registry node
`1479:1954` remains unchanged. None of the `2910:*` or `2918:*` Projects nodes
may be added to `config/figma_templates.json` or the Netlify Figma allowlist
without a separate explicit template approval.

## User-selected Telegram art direction (reference only)

The selected Korean direction is pinned by
`docs/reference-manifests/squid-telegram-korean-style-v1.json`. Its exact
1254×1254 PNG digest is
`92a761fe9da400920f44d55c56007361854b2baeb8bead5fbac552051d03bb0b`.
It is an OpenAI-generated localization derivative, not an official Squid
export, source-bound clean plate, reusable runtime asset, or publication
approval. The raster stays outside the runtime repository; the manifest keeps
the approved art direction and provenance boundary reviewable.

The protected design grammar is full-bleed lavender-to-white glow, a dominant
acid-lime SQUIB, oversized condensed black display type, tight stacked leading,
assertive crops, and intentional type-to-character overlap. That overlap is not
a spacing defect. The paper plane is part of the official Telegram source, but
generated cards may use it only after a reviewed transparent asset is approved.
Until then, ordinary generated cards absorb the hierarchy without inventing a
plane. They use existing reviewed Squid assets and remain visually distinct
from an official-source `remix`.

## Generated-card families

`squid-visual-routing@1` classifies immutable source text on the server. The
browser and LLM cannot select or override the family. New results store the
family, policy version, reviewed reference-pack ID/version, channel profile,
template/asset/token versions, and font status in the immutable spec. Generated
cards additionally bind `squid/full-bleed-character-type@2`; source-remix specs
must not contain that generated design profile. PNG and editable SVG consume
the same contract.

| Family | Use | Reviewed official reference |
|---|---|---|
| `editorial_big_type` | announcements and articles | [A NEW ERA](https://x.com/squidrouter/status/2079999207956500971) |
| `milestone_metric` | a source-verifiable scaled metric | [5m milestone](https://x.com/squidrouter/status/2082889008385044897) |
| `status_progress` | bounded status, phase, eligibility, or product explanation | [TGE status](https://x.com/squidrouter/status/2080668216792129968) |
| `product_proof` | a concrete product action without invented UI | [MiniPay product proof](https://x.com/squidrouter/status/2079628218403803481), [Telegram launch poster](https://x.com/squidrouter/status/2083266484789514640) |
| `worldbuilding` | mascot, mood, or meme | [SQUIB world](https://x.com/squidrouter/status/2083583547353501977) |

Generated cards use the canonical config tokens lavender `#BC8EE4`, acid lime
`#E6FA36`, black/white, approved local Squid assets, and Korean-safe Pretendard
spacing. They use one strong hierarchy and intentional overlap; the selected
full-bleed profile must not be diluted into a white information panel or
generic whitespace. They contain no publisher panel, generic CTA, duplicate
logo, fake product UI, or public CoinEasy mark. A milestone shows a large
number only when the exact scaled metric was copied from the source. Product
proof uses official form language, not a fabricated screen.

The Korea-stage geometry is versioned as `squid-generated-gtm@5`. This version
promotes the user-selected Telegram direction into the generated-card geometry:
the official SQUIB is larger and higher, decorative bubbles are removed from
the visible stage, and short Korean hooks are centered in the lower type stack.
The version is stored in the immutable spec and idempotency inputs so a prior `@4` result
cannot be replayed as if it used the reviewed stage geometry.

Verified official media always changes the render strategy to `source_remix`,
regardless of family. Text-only `worldbuilding` fails closed for manual review
because the approved local asset pack cannot reproduce the official 3D scene.
The generated family layouts deliberately record `figma_template: null`: the
one legacy Squid frame in the Figma registry is not approval evidence for the
reference-only Projects variants.

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
- Do not synthesize a reusable replacement mascot or redraw the official logo.
  A source-specific AI-assisted clean plate is allowed only when its use is
  disclosed, human-reviewed, exact-source bound, byte-pinned, and fail-closed.
- A generated art-direction reference remains outside both runtime asset roots.
  Its manifest may inform layout, but the raster itself must not enter the
  clean-plate registry, Figma allowlist, render payload, or publication flow.
- `BagossCondensed.woff2` activates only when CoinEasy has a redistribution-safe
  licensed file at `clients/squid/assets/BagossCondensed.woff2`. Never copy the
  font from the public website merely because it is downloadable.
- Every output still requires human review. Telegram publication feature flags
  remain disabled while visual work is under review.
