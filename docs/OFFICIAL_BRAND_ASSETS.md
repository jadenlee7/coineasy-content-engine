# Official brand assets

The files below are the reviewed brand marks used by the Content Engine. Client
assets are canonical. The copies under `web/console/assets/brands` must remain
byte-for-byte identical so Netlify Functions and the static console use the
same artwork.

| Client | Canonical files | Reviewed provenance | Usage |
| --- | --- | --- | --- |
| Yellow | `clients/yellow/assets/logo_dark.svg`, `logo_light.svg` | Yellow Korea Figma branding export | White wordmark on dark surfaces; black wordmark on Yellow/light surfaces |
| OriginTrail | `clients/origintrail/assets/logo_dark.png`, `logo_light.png` | OriginTrail Korea Figma branding export | White wordmark on dark surfaces; black wordmark on light surfaces |
| Squid | `clients/squid/assets/logo_dark.png`, `logo_light.png` | Squid Korea Figma `Squid_Logo_White` / `Squid_Logo_Black` exports | Preserve the official mark and never duplicate it over an official source creative |
| Babylon | `clients/babylon/assets/logo_dark.png`, `logo_light.png` | Babylon Korea GTM Figma symbol export | The official symbol may be paired with a separate `Babylon Korea` market-name text layer; the text is not part of the official logo |

## Enforcement

- `tests/test_official_brand_assets.py` pins the reviewed SHA-256 values and
  verifies every public copy.
- Server-rendered news cards and Netlify article/editable SVG endpoints fail
  closed when a required official logo is unavailable.
- Transparent padding is preserved. Per-client render boxes compensate for
  optical size without cropping, stretching, recoloring, or redrawing a mark.
- A source creative that already contains the client logo must not receive a
  second logo.
- Replacing an official export requires a brand review, updating both copies,
  updating the pinned hash, and rendering all affected templates.

## Squid article illustration kit

The Squid article hero uses official illustration exports from CoinEasy
Management Figma file `hsRSASQjEMxl5NMLH9y5Wm`, reviewed frame `2910:2690`.
The transparent source artwork was proportionally downsampled for the web
without cropping, recoloring, redrawing, or changing opacity data.

| Role | Canonical file | Public Netlify copy |
| --- | --- | --- |
| Purple form language | `clients/squid/assets/form-language-purple.png` | `web/console/assets/brands/squid-form-language-purple.png` |
| SQUIB TokenJuggle character | `clients/squid/assets/squib-token-juggle.png` | `web/console/assets/brands/squid-squib-token-juggle.png` |
| SQUIB Parts Bubbles A | `clients/squid/assets/squib-bubbles.png` | `web/console/assets/brands/squid-squib-bubbles.png` |

The Squid hero renderer fails closed when any of these three files is
unavailable. Generic character substitutes are not permitted.

Frame `2910:2690` and the reviewed Projects variants `2910:2700`,
`2918:2587`, `2918:2597`, `2918:2616`, `2918:2624`, `2918:2631`,
`2918:2638`, and `2918:2645` are visual `reference_only` material. Their
shared pale field, white oval, lavender halo, cropped black `SQUID` and generic
family frame words, official SQUIB/bubbles, and centered Pretendard Korean may
guide composition.
Their node-specific copy, dates, figures, events, and claims must never be
reused as facts.

The approved legacy registry node remains `1479:1954`
(`[KEEP] Banner_Squid_Sample`). Projects nodes `2910:*` and `2918:*` must not
enter `config/figma_templates.json` or the Netlify Figma allowlist without a
separate explicit template approval. Korea-stage generated cards use canonical
lime `#E6FA36`; the pale field may use `#E8E6EA` or `#EBEBEB`.

Composition, source-routing, Korean copy-density, and release rules are defined
in [SQUID_KOREAN_GTM_VISUALS.md](./SQUID_KOREAN_GTM_VISUALS.md).

## Licensed type

`clients/squid/assets/BagossCondensed.woff2` is activated automatically only
when a properly licensed webfont file is placed at that path. Until then,
Squid Korean headlines use the reviewed Pretendard fallback. Do not substitute
an unlicensed Bagoss file.
