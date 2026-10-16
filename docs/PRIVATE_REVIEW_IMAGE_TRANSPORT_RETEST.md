# Private review image transport repair and retest gates

Status: local repair and retest preparation only, 2026-10-16. No merge,
production release, activation, new claim, recovery or send is implied.

## Evidence and causal limit

- The 2026-10-15 trial stopped after claiming an outbox, before a review/card,
  delivery-part reservation or Telegram delivery was recorded. The immutable
  PNG, version and forecast ledger remain preserved.
- Newly retrieved historical Supabase edge logs show the claim RPC returned
  HTTP 200 at 16:53:57.793Z and the image-locator RPC returned HTTP 200 at
  16:54:02.522Z. The latter has JSON content type and a recorded Content-Length
  of 348. No content-encoding attribute was recorded. These timestamps are
  consistent with the failed courier invocation, but are not a propagated
  cross-service request ID.
- This makes a failure before the locator dispatch an unsupported explanation
  for that request. Failure is narrowed to the locator response/validation
  boundary; the historical response body and encoding remain unobserved.
- A real loopback HTTP test proves a transport defect: Node fetch decodes a
  gzip body but retains its compressed Content-Length. The previous locator
  request did not negotiate identity while its bounded reader required the
  decoded byte count to equal that header. This rejects a valid compressed
  locator before the Storage GET. It is consistent with the incident, not
  conclusive proof of the historical response's encoding.

## Minimal local repair

The image-locator POST now requests `Accept-Encoding: identity` and JSON.
Only HTTP 200, JSON, identity encoding and no Content-Range are accepted.
Unexpected compression or partial responses still fail closed, without retry.
The 4 KiB locator bound, exact declared-size check, exact claim/version/asset
binding, 10 MB PNG bound, signature and SHA-256 checks are unchanged. Owner
freshness, trial window, source eligibility and approval gates are unchanged.

Regression coverage includes a real Node fetch against a synthetic local HTTP
server: default gzip produces compressed-header/decoded-body length mismatch;
the repaired identity request succeeds once and returns the exact PNG. A
server ignoring identity, unknown encoding, range response and malformed
declared length do not cause a Storage read or second locator request.
Fixtures use no production credentials, provider, database or Telegram.

## Retest preparation — not execution

1. Review the minimal diff and pass focused and full JavaScript tests, relevant
   Python tests and `git diff --check`. CI/merge and OFF deployment require the
   operator's release authorization; local tests are not production proof.
2. For an approved OFF release, attest each actual build/deployment SHA and
   authenticated gateway release. Do not infer distributed readiness from a
   Netlify-only patch while the API/courier run an older SHA.
3. Read the original outbox, private reviews/cards/parts and owner/forecast
   ledgers again. Keep the original expired trial, token, banner/version and
   all reservations unchanged. Do not call `claim` as a diagnostic: its expiry
   cleanup changes expired claimed rows to `delivery_unknown`.
4. The expired claim is **not executable retest authority**. Existing code
   does not reopen it. No direct status reset, lease extension, replacement
   content/outbox or newly minted identity may bypass deduplication. A future
   exact-asset recovery, if desired, needs its own reviewed, explicitly
   authorized path and evidence that no send was reserved or attempted.
5. Revalidate the latest official source, age under 24h, active recent poll,
   current immutable version, exact canonical PNG/copy hashes, intended
   private destination and forecast budget before selecting any retest item.
   A preserved but stale item is archival evidence, not a new daily post.
6. Before any activation or send, report the exact version/asset, source age,
   destination policy, single-attempt plan, budget availability, runtime SHA
   and duplicate check to the operator. Stop pending approval. Keep Yellow
   exclusion, at most one per authorized project, US$10 forecast daily cap,
   private-room only, no public publishing or repeated schedule.
7. A separately authorized trial must reserve durably before sending, never
   retry an unknown result, and restore saved/runtime OFF on success, failure
   or deadline. Report provider confirmation separately from room UI readback.

The repair itself needs no migration, new asset, paid generation, key rotation,
staff permission change, cron change or publication enablement.
