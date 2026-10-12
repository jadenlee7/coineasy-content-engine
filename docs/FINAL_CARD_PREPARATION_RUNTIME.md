# Final confirmation-card runtime connection (local, OFF)

This connects the existing single review poller and Engine-side durable
preparation worker. It does not deploy, grant access, enable a schedule, send a
card or prove a production release. Only a real human ce2 approval may queue
official Telegram plus an **unscheduled Typefully X draft**. X auto-publication
is absent.

```text
existing bot B: authenticated human ce1 source + claims checks
  -> same transaction: immutable preparation intent (Engine E, callback B, pair P)
Engine E dispatcher: at most four existing unclaimed intents per invocation
  -> one-shot committed claim -> current canonical PNG + trusted route readback
  -> existing private-room preflight -> intent-bound reservation
  -> each part: current identity / original release generation -> attempt -> send -> receipt
  -> register exact final card -> existing bot ce2 human decision owner
```

No second `getUpdates` consumer is added. Preparation itself never creates an
approval/publication, invokes a public publisher, or writes a Typefully draft.
The dispatcher receives no official-channel bot tokens or Typefully key.

## Bot-side composition

The actual `pipeline.private_review_startup` now supplies an optional
`FinalPreparationHook` to `compose_private_review`, which mounts it in the real
`PostgresPrivateReviewOwner`. `CONTENT_OPS_FINAL_PREPARATION_ENABLED=false` is
the default and does not import the new closure. ON also requires the existing
private route and final-publication route configured ON, real bot release B,
and existing `CONTENT_OPS_FINAL_PUBLICATION_ENGINE_RELEASE_SHA=E` and
`CONTENT_OPS_FINAL_PUBLICATION_RELEASE_PAIR_SHA256=P`. The verified vendored
Engine pin must equal E. A missing new module fails closed; there is no sibling
checkout fallback. Final ce2 remains on the existing separate role and signer.

The new closure has not been vendored or committed by this implementation.
Bot enabled-hook tests use an explicit synthetic hook seam until the parent
commits and vendors the real Engine closure. This is not a deployed-hook proof.

## Engine-side entry point and configuration

Run `python -m scripts.run_final_card_preparations --validate-only` for pure
configuration validation, or omit `--validate-only` for a bounded invocation.
Both are zero-I/O when the ENABLED flag is false. No schedule is installed.
ON configuration validation checks settings only: it does not prove DB ACL,
active registered pair, actual hosted image metadata, provider identity, or
delivery.

All settings below use prefix `CONTENT_OPS_FINAL_PREPARATION_`:

| Suffix | Meaning |
|---|---|
| `ENABLED` | literal `false` / `true`; default false |
| `ENGINE_RELEASE_SHA` | E; must match exact `RAILWAY_GIT_COMMIT_SHA` and `/app/content-ops-build-sha` |
| `CALLBACK_RELEASE_SHA` | independently attested bot B, not forced equal to E |
| `RELEASE_PAIR_SHA256` | canonical role-separated E/B pair digest |
| `WORKSPACE_ID` | one existing workspace UUID |
| `BOT_POLICY_JSON` | existing-review-bot-policy@1 projection, numeric exact private room/bot and existing membership status |
| `REVIEWERS_JSON` | object mapping existing actor UUIDs to verified numeric human IDs; no enrollment |
| `ROUTES_JSON` | 1–4 exact `ExpectedPublicationRoutes` objects; independently approved numeric route identities, no guessed IDs |
| `BOT_TOKEN` | existing private review bot token only |
| `SIGNING_KEY_HEX` | existing dedicated ce2 final signer, distinct from edit binding key |
| `EDIT_KEY_HEX` | existing private review HMAC binding key |
| `DATABASE_URL` | dedicated `coineasy_final_card_preparation` role, pinned Supabase project, verify-full direct/session port5432 only |
| `DATABASE_CA_PATH` | immutable public certificate path; default `/app/certs/supabase-prod-ca-2021.crt`; contents must match pinned CA digest |
| `PROJECT_KEY`, `STORAGE_READ_TOKEN` | distinct project API key and scoped private Storage read credential; no admin fallback |

The local `Dockerfile.final-card-preparation`, dedicated Docker ignore file,
`requirements-final-card-preparation.txt`, and
`railway.final-card-preparation.json` package only the required Engine closure,
`httpx==0.28.1`, `psycopg[binary]==3.2.9`, and the existing public Supabase CA.
The exact CA PEM hash is checked both during image build and before connection;
`certs/README.md` records its reused provenance. The image requires a 40-character
Railway Git SHA and writes the read-only `/app/content-ops-build-sha` stamp. A
caller-supplied build argument alone does not prove GitHub origin.

The container defaults OFF, runs as `nobody`, and performs one bounded invocation.
Its manifest uses validate-only pre-deploy, `cronSchedule=null`, and restart
policy `NEVER`. **Auto-deploy must separately be set/read back as false in the
Railway service-owner settings**; it is not a fabricated manifest field, and
watch patterns are not evidence of that setting. No dedicated image has been
built, and no Railway service, auto-deploy setting or schedule has been changed.
Local staged-closure tests are not Docker, hosted image or live TLS proof.

## Authoritative reads and remaining trust boundary

`PreparationReadStore` uses a read-only connection. Discovery returns only up
to four scoped, matching-E/B/P, unclaimed IDs. Superseded epochs, inactive or
expired review/card/identity, non-current version or release-selection
generation, stale/non-latest sources, and prior deliveries are filtered before
the limit. An old ineligible intent therefore cannot starve a later eligible
one. Claim rechecks eligibility under locks; uncertain claims are never
reclaimed. Canonical PNG reads join the
current version, primary PNG asset and private Storage bucket/object, then GET
only the canonical owner path and verify full bytes/size/SHA. No user URL is
fetched and no redirect is followed.

Routes are read from the existing trusted-owner DB attestation registry. The
runtime does **not** fabricate Telegram/Typefully observations or refresh their
timestamps. The saved binding must equal the digest of the approved route
configuration, and both observations must be younger than 15 minutes. A separate
authorized route verifier must keep the registry current. If it does not,
preparation fails closed; this runtime never acquires public provider keys to
work around that gate. A route that becomes stale after selection/claim holds
that one-shot intent; operators must have the authorized route verifier running
before enabling this dispatcher, not retry the held intent automatically.

Original preparation creation time fences P1→P2→P1 rollback, including after
claim. Active identity is rechecked and locked at reservation and each new
part admission. A revoke after an admitted send does not erase its receipt;
later parts stop. The transaction that records an attempt is the send-admission
boundary; no claim is made that revocation can cancel an already admitted HTTP
request. SQL fixtures prove state transitions, not concurrent revoker stress.

## Explicit ACL / deployment gates (not applied)

The existing private ce1 role needs reviewed access to request a preparation
inside its existing transaction, including underlying invoker-function tables.
The separate Engine role needs only the preparation claim and prepared
reserve/begin wrappers, inherited confirmation/register/terminal functions,
and their exact prerequisite table permissions/RLS policies. Its separate
read-only sessions need scoped preparation/review/asset/current-version,
Storage metadata and route-registry reads. No default runtime roles receive
new grants in the proposal. Provisioning a new role or expanding ce1 grants
requires explicit production access-change authority and a tested ACL pack.

Still required before ON: exact source commit/vendor closure, an actual image
build and GitHub-origin/image/runtime E readback, hosted schema compatibility/ACL verification, active E/B/P and fresh
route attestations, scoped Storage read policy, numeric staff/room readback,
explicit hosted deployment/activation and one private canary receipt. Public
publication remains independently human-gated.

## Local verification boundary

- Final-preparation runtime/adapters and narrow packaging: 15 unittest tests pass.
- Preparation/private-owner/final-card courier/final-card owner: 65 tests pass.
- Disposable PostgreSQL 16 fixture passes with 62 historical migrations,
  production/provider calls 0, and the server stopped afterward. It executes
  the actual adapter discovery SELECT and verifies stale-oldest/current-later,
  release-pair ABA and never-reopen-claimed behavior. Schema/read-query proof
  under the disposable fixture owner is not intended-role/RLS ACL proof.
- Docker executable is present, but daemon access was denied; no image build
  or container execution is claimed. Staged-closure OFF tests deny socket and
  psycopg connection calls.
