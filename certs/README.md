# Final-preparation database public trust anchor

This is a public CA certificate, not a credential or client key. Its bytes are
reused unchanged from the existing review bot's pinned
`certs/supabase-prod-ca-2021.crt`; no new download or trust-on-first-use occurred.

The existing provenance record says it was downloaded on 2026-09-22 from the
official dashboard-configured certificate URL:

https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt

Official source: `supabase/supabase` commit
`01ba39163f82c162da9b3f791413b45edc74338e`,
`apps/studio/hooks/custom-content/custom-content.json`, blob
`0f094e3788cfc6de65aa2fedf7383b721f127507`, key `ssl:certificate_url`.

- PEM SHA-256: `700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`
- DER SHA-256: `807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa`
- Subject: Supabase Root 2021 CA
- Recorded validity: through 2031-04-26 10:56:53 UTC

The narrow Docker image and restricted connector both verify the exact PEM
hash. TLS hostname verification remains required. This file is not installed
in a machine/browser trust store; there is no runtime CA download or insecure
fallback. Rotation requires a separately reviewed source update and fresh TLS
evidence. Local hash verification is not a live hosted TLS validation.
