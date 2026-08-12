# Client news-card brand profiles

Every generated news card is bound to a server-owned brand profile after copy
generation. The model may select wording within the client voice guide, but it
cannot select a different client's colors, official assets, type system, or
visual profile.

| Client | Visual profile | Brand tokens | Official asset pack | Voice anchor |
| --- | --- | --- | --- | --- |
| Yellow | `yellow/institutional-market-infrastructure@1` | `yellow-brand-tokens@1` | `yellow-official-brand-assets@1` | Institutional, analytical clearing and settlement infrastructure |
| OriginTrail | `origintrail/verifiable-knowledge@1` | `origintrail-brand-tokens@1` | `origintrail-official-brand-assets@1` | Trust, provenance, connected context, and verifiable proof |
| Babylon | `babylon/bitcoin-native-infrastructure@1` | `babylon-brand-tokens@1` | `babylon-official-brand-assets@1` | Bitcoin-native, technically precise product status and mechanics |
| Squid generated | `squid/full-bleed-character-type@2` | `squid-brand-tokens@1` | `squid-local-approved@1` | Official SQUIB/form-language world with sparse, direct Korean copy |
| Squid source remix | Source-native; no generated profile | `squid-brand-tokens@1` | `official-source-media@1` | Preserve the exact official creative and localize only verified copy regions |

The standard-client policy version is `client-news-brand-profiles@1`. Each
render also records a style-specific template version such as
`yellow-news-classic@1` or `origintrail-news-remix@1`.

## Enforcement

- `core/brand_profiles.py` removes model-supplied profile metadata and applies
  the profile that belongs to the selected client and actual render style.
- Netlify independently validates the Railway response. A Yellow profile cannot
  pass as OriginTrail or Babylon, and a classic profile cannot pass as remix.
- The profile identity is part of the request hash, so a new approved profile
  cannot silently replay an older generated asset.
- Pre-policy catalog records remain readable for an idempotent retry. Every new
  generation must carry the current profile contract.
- Official logo files and their public copies remain governed by
  `OFFICIAL_BRAND_ASSETS.md`; missing server assets fail closed.

## Change control

Do not copy Squid's lavender/SQUIB visual language into another client. “Same
branding rigor” means that every client stays inside its own system:

- Yellow: yellow/black, restrained institutional structure, no crypto hype.
- OriginTrail: purple/navy, traceable problem-to-proof logic, no abstract AI
  spectacle without source evidence.
- Babylon: orange/dark blue, exact Bitcoin custody/collateral/staking language,
  no price, yield, reward, or availability implications absent from the source.
- Squid: the approved v5 full-bleed composition or exact official source remix;
  no generic publisher card and no invented character/brand assets.

Any visual change requires a profile or template version bump, cross-client
validation tests, and reviewed PNG plus editable-SVG previews before release.
