# Exact Telegram resolution: inspect-only tool

This tool prepares and, only under separate explicit authorization, inspects
one Squid `delivery_unknown` attempt. Its default mode validates a local
request without reading credentials, minting a token, creating an attempt
marker, or making a network or database call. It cannot approve, resolve,
claim, requeue, resend, publish, enable a flag, or deploy anything.

Use Node.js 24 and `scripts/inspect-telegram-delivery-unknown.mjs`. The core
validation module is `scripts/lib/telegram-resolution-inspect.mjs`; the I/O
boundary is `scripts/lib/telegram-resolution-inspect-io.mjs`. They use Node
built-ins, not a provider SDK.

The [exact Telegram publication runbook](TELEGRAM_PUBLICATION_RUNBOOK.md#delivery_unknown)
remains the operational authority. Installing this tool, reviewing its PR, or
applying its database migration is not credential-issuance, inspection,
resolution, or publication approval.

## Start with local validation

```bash
node scripts/inspect-telegram-delivery-unknown.mjs \
  --request /absolute/path/to/operator-request.json
```

A valid request returns `mode=validate_only`, `ok=true`, `request_sha256`,
`public_audit_sha256`, and `release_sha`, with `credential_issued=false`,
`database_calls=0`, and `provider_calls=0`. This proves local structural and
time-bound validation only. It does not prove database eligibility, a trusted
signing key, a real human audit, or that Telegram failed to deliver anything.

The checked-in [request](../examples/telegram-resolution-inspect-request.json)
and [authorization](../examples/telegram-resolution-inspect-authorization.json)
are **synthetic, deliberately expired documentation fixtures**. Their January
1, 2026 timestamps, project reference, UUIDs, release hash, message range,
snapshot hash, and zero-match claims are not production evidence. Running the
request today must fail freshness/expiry validation. Do not refresh its dates
or copy its zero-match claims to manufacture a valid audit. Tests may supply
an explicit historical clock; operational users must provide fresh evidence.

Keep actual operator packets outside the repository. Never put private keys,
JWTs, publishable keys, production identifiers, raw provider responses, private
asset URLs, or original caption/image content in these example files.

## Request and audit contract

The request has an exact key set; unknown or missing keys fail closed:

| Field | Required meaning |
| --- | --- |
| `schema_version` | `telegram-resolution-inspect-request@1` |
| `project_ref` | Exact approved Supabase project reference, 20 lowercase letters |
| `environment`, `client_id` | Literal `production`, `squid` |
| `release_sha` | Exact operator-approved 40-character lowercase Git SHA |
| `workspace_id`, `content_item_id`, `content_version_id`, `publication_id`, `job_id` | Exact existing database tuple, read without changing it |
| `resolution_id`, `operator_approval_id` | Distinct UUIDs reserved for this proposed resolution and its later approval |
| `inspected_by`, `approved_by` | Inspector and intended approver principals; these names are not proof of human approval |
| `expires_at` | Explicit UTC-seconds `Z` timestamp, in the future and no more than two hours away |
| `public_audit` | Fresh bounded audit object described below |

The immutable source tuple and its existing delivery/approval/asset evidence
must be read first. Do not update a job, change the current version, edit a
publication, reset a lease, or invoke another RPC to make the tuple eligible.
Inspection rechecks database state; passing local validation is not a
substitute for that check.

The human operator must compare the exact stored caption and PNG against the
canonical public channel around the original `delivery_started_at`. Preserve
a bounded, non-secret audit snapshot and hash its actual bytes. The tool does
not fetch Telegram, read private assets, or perform this comparison itself.

`public_audit` contains exactly these ten keys:

| Field | Required value or bound |
| --- | --- |
| `schema_version` | `telegram-public-channel-audit@1` |
| `scan_source` | `public_telegram_web_history` |
| `public_channel` | `squid_kor_update` |
| `checked_at` | Actual check time, UTC-seconds `Z`, fresh within 30 minutes |
| `first_message_id`, `last_message_id` | Canonical positive decimal **strings**, ordered and within PostgreSQL `bigint` |
| `message_count` | Integer 1–1000, no greater than the inclusive message-ID range |
| `snapshot_sha256` | 64-character lowercase SHA-256 of the actual audit snapshot |
| `caption_match_count`, `png_match_count` | Numeric zero only when the exact comparison actually observed zero matches |

The server additionally requires the audit to be at least ten minutes after
the delivery attempt began. No matching message in this bounded audit means
only `not_observed_at_checked_at`; it is not proof of non-delivery. If a
matching message is found, do not force the counts to zero: follow the existing
positive-observation path. That path supports only a still-current exact
version; historical positive observation needs its own reviewed support.

Request and audit digests are SHA-256 over their PostgreSQL-compatible
`jsonb::text` representations, not source-file bytes or ordinary compact
`JSON.stringify` output. `request_sha256` binds the entire request, including
its audit and expiry. Editing any field invalidates the authorization binding.
The snapshot digest is different: it identifies the actual captured snapshot
bytes, not the audit object's JSON serialization.

## Separate inspection authorization

After reviewing the exact request hash, obtain the user's explicit approval
to issue an inspect-only credential and make one exact inspect RPC call. Only
then prepare the non-secret authorization file, with its exact key set:

| Field | Required value or binding |
| --- | --- |
| `schema_version` | `telegram-resolution-inspect-authorization@1` |
| `authorization_id` | Fresh authorization UUID; also the local attempt-marker identity |
| `request_sha256` | Validated full-request digest |
| `authorized_by` | Operator principal corresponding to the separate explicit approval |
| `authorized_at` | Actual UTC-seconds authorization time, within the past 30 minutes |
| `expires_at` | Exactly the request's expiry |
| `scope` | `issue_inspect_jwt_and_call_once` |
| `signing_key_id` | UUID of the already trusted/imported project signing key |
| `max_rpc_calls` | Numeric `1` |
| `resend_authorized`, `automatic_publication` | Boolean `false` |

A plain JSON authorization file is an operator-intent binding, **not a
cryptographic human-approval receipt**. Anyone who can edit the file can write
a principal and timestamps into it. The file never replaces separate explicit
user approval or an approved secret-access procedure. Do not treat a successful
file validation as permission to fetch a key, mint a token, or call production.

## Signing prerequisites and secret handling

Inspect-once accepts a private ES256 JWK only through an already-open file
descriptor numbered at least 3. Its key must already be imported/trusted by
the exact approved Supabase project, and its signing-key identity must match
the authorized `signing_key_id`. Signing is in memory; the resulting JWT is
never returned, printed, persisted, or passed as a command-line argument.

The separate API key input is accepted only as an `sb_publishable_` key through
a different already-open descriptor numbered at least 3. A publishable key
alone does not grant the dedicated database role; the signed JWT provides that
phase-scoped identity.

There is no automatic key lookup, secret retrieval, key import, registration,
rotation, or project configuration. Do not reuse a service-role token, legacy
JWT secret, Telegram token, Studio credential, or publication worker token.
If an approved ES256 key is not already trusted or cannot be supplied through
the approved descriptor mechanism, stop. Key provisioning or changing a
project's signing setup requires its own explicit approval; it is outside this
tool and this PR.

Consult Supabase's [JWT signing keys documentation](https://supabase.com/docs/guides/auth/signing-keys)
for the project's signing-key lifecycle. The link is a reference, not evidence
that a particular key is currently trusted. Project trust must be verified
separately for the actual key and environment before credential issuance.

## Future inspect-once invocation

Only after the prerequisites and exact user approval above are satisfied:

```bash
node scripts/inspect-telegram-delivery-unknown.mjs \
  --request /absolute/path/to/operator-request.json \
  --inspect-once \
  --authorization /absolute/path/to/operator-authorization.json \
  --signing-key-fd 3 \
  --publishable-key-fd 4 \
  --attempt-ledger-dir /absolute/path/to/protected-attempt-ledger
```

This is a command shape, not authorization to run it. Descriptors 3 and 4 must
already be opened by an approved secret-handling launcher. Do not substitute
secret literals, shell history, environment variables, or temporary key files
for the descriptor inputs.

Use a clean, trusted Node process without debug instrumentation or TLS-key
logging. Live mode rejects insecure TLS and nonempty `NODE_DEBUG`,
`NODE_DEBUG_NATIVE`, `NODE_OPTIONS`, or `SSLKEYLOGFILE` settings before reading
keys; common inspector/preload/key-log command-line modes are also rejected.
These guards do not establish trust in a compromised launcher or runtime.

The inspect JWT uses `role=coineasy_telegram_resolution`,
`capability=telegram_delivery_unknown_inspect`, the exact workspace and
inspector `sub`, `environment=production`, and exact approved release SHA. It
pins the item/version/publication/job tuple, resolution and approval UUIDs,
intended approver, audit hash, and expiry. Its `jti` is the resolution UUID;
automatic publication and resend are false and external actions are zero.
It cannot be reused for the approve or resolve phases.

Transport targets only
`https://<approved-project-ref>.supabase.co/rest/v1/rpc/inspect_exact_telegram_delivery_unknown_resolution`.
No arbitrary URL, redirect, proxy, retry, Telegram call, or other RPC is allowed.
Responses are bounded and validated against a strict allowed shape. The
complete bounded `approval_subject`, its server hash, and exact bindings must
verify before success is reported. Forensic hash fields must have the required
format; this tool does not independently re-hash the original database rows.
Unexpected or malformed
responses fail closed; raw response bodies are not printed as diagnostics.

### One local attempt, not global exactly-once execution

The attempt-ledger directory is protected with mode `0700`. An exclusive
non-secret `authorization_id.json` marker is written before the POST. The same
marker prevents another call under that local authorization, including after a
timeout, transport error, authentication rejection, or response-validation
failure. No JWT, private key, publishable key, private caption, or asset bytes
belong in that marker.

This is a local attempt fence, not a global server exactly-once guarantee.
Another machine or ledger directory is not coordinated by it. Do not delete a
marker, move to another directory, or change an authorization UUID to bypass a
failed/uncertain attempt. Preserve the marker, reconcile the observation, and
obtain separate explicit approval before any further attempt. An uncertain
response is not evidence that the server did not receive the request.

## Readback and later gates

An accepted response proves only that the exact non-mutating inspection
returned and passed local verification. It is not delivery evidence, human
approval, a resolution receipt, permission to resend, or permission to activate
publication. The original publication remains `delivery_unknown` and the
original job remains `failed`.

1. Read back the relevant database evidence without modifying it. The
   inspection must not append an approval, resolution, or event.
2. Present the full bounded subject/hash, exact tuple, resolution/approval UUIDs,
   audit window, expiry, and release SHA to the operator.
3. Obtain a separate explicit approval before any approve-phase credential or
   `approve_exact_telegram_delivery_unknown_resolution` call. That phase appends
   a durable approval and is deliberately not implemented by this CLI.
4. Obtain the required separate resolve authorization before issuing a
   resolve-phase credential or calling
   `resolve_exact_telegram_delivery_unknown_without_resend`. This CLI has no
   such command. Resolution needs a durable unexpired approval and still makes
   no original attempt resendable.
5. Publication activation, any new publication/canary, configuration changes,
   and deployments remain separate gates even after resolution.

A next inspection approval should name the exact project, request hash,
publication/job/item/version tuple, resolution/approval UUIDs, signing-key ID,
audit window, expiry, and release, and explicitly limit authority to
**inspect-only credential issuance plus one inspect call**. It must exclude
approve/resolve, key import/rotation, deployment, provider calls, and resend.

## Test and review plan

Tests use synthetic tuples, historical clocks, ephemeral local test keys, and
mock transport only. No production credential or provider call is required.

- Validate exact request/authorization key sets, UUIDs/principals, UTC timestamps,
  expired/future/stale windows, hashes, bigint message-ID strings, count/range,
  explicit zero matches, and project/client/release constraints.
- Compare supported canonical JSON and digests with PostgreSQL `jsonb::text`;
  reject ambiguous/unsupported input rather than silently changing a binding.
- Prove default validation never opens secret descriptors, issues a credential,
  creates a marker, or calls transport; reject live flags in invalid combinations.
- Test ES256 identity/binding, wrong/malformed keys, service-role/legacy-key
  substitution rejection, descriptor bounds, and credential/log redaction.
- Prove the canonical host/path and one POST, no redirects/proxy/retry, bounded
  response, strict field/subject hash checks, and fail-closed malformed responses.
- Prove exclusive attempt markers, protected-directory checks, replay rejection,
  and marker retention on uncertain or rejected responses.
- Confirm no approve/resolve command and no claim, resend, delivery, configuration,
  migration, or deployment path exists.

Code/CI success authorizes none of the future operational gates above.
