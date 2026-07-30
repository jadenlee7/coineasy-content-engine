# Content Studio API Contract

This is the target server contract for the Supabase-authenticated team application.
It is a scaffold, not a claim that the `/v1/workspaces/*` routes are already
deployed. The current Netlify console uses a separate signed team-access cookie and
the existing `/clients/{client_id}/generate/*` Railway routes while the Studio
worker is introduced.

## Authentication and scope

- Team calls use `Authorization: Bearer <Supabase user JWT>`.
- Every route is scoped by `workspace_id`; content routes also use `client_id`.
- The server verifies active workspace membership before doing work. RLS repeats the
  same boundary in PostgreSQL.
- Browser and Figma clients never receive `SUPABASE_SERVICE_ROLE_KEY` or the Railway
  `API_SECRET`.
- Mutating requests accept `Idempotency-Key`; retries return the same resource/job.
- Netlify routes map workflow changes to the database RPCs
  `queue_content_generation`, `review_content_version`,
  `request_content_publication`, and `record_approved_figma_link`; the browser
  cannot insert queue/publication/Figma-link rows or update workflow status
  directly.
- Browser reads use `content_studio_source_*`, `content_studio_job_statuses`,
  `content_studio_publication_statuses`, and `content_studio_activity`. Raw provider
  payloads, credential references, worker locks, and provider responses are
  server-only.

Roles:

| Role | Read | Draft/edit | Approve/publish request | Team/client admin |
|---|---:|---:|---:|---:|
| viewer | yes | no | no | no |
| editor | yes | yes | yes | no |
| admin | yes | yes | yes | yes |
| owner | yes | yes | yes | yes |

## Normalized content package

Every version stores the same envelope; `content` varies by mode.

```json
{
  "id": "content-version-uuid",
  "content_item_id": "content-uuid",
  "version_number": 3,
  "content_kind": "daily_news",
  "locale": "ko-KR",
  "prompt_version": "daily-news@1.0.0",
  "title": "오늘의 Squid 업데이트",
  "content": {},
  "channel_copy": {
    "telegram": "...",
    "x": "..."
  },
  "deliverables": {
    "primary_asset_id": "asset-uuid",
    "editable_asset_id": "asset-uuid"
  },
  "qa": {
    "source_fidelity": "passed",
    "brand_alignment": "passed",
    "manual_review_required": true
  }
}
```

Mode-specific `content`:

```json
{
  "daily_news": {
    "summary": "...",
    "items": [{"headline": "...", "why_it_matters": "...", "source_ids": ["..."]}]
  },
  "article": {
    "dek": "...",
    "sections": [{"heading": "...", "markdown": "...", "source_ids": ["..."]}],
    "references": [{"source_id": "...", "label": "..."}]
  },
  "tutorial": {
    "goal": "...",
    "prerequisites": ["..."],
    "steps": [{"title": "...", "instruction": "...", "check": "..."}],
    "troubleshooting": [{"symptom": "...", "resolution": "..."}]
  }
}
```

## Endpoints

### Intake

`POST /v1/workspaces/{workspace_id}/intake`

Accepts a public source URL, pasted text, or configured `source_feed_id`. The server
canonicalizes it and computes `source_hash`; duplicate intake returns the existing
source. It does not generate content implicitly unless `create_draft` is true.

`GET /v1/workspaces/{workspace_id}/sources?client_id=squid&after=...`

Returns a cursor-paginated source inbox. Always filter by workspace and client even
though RLS is enabled.

### Content and versions

`POST /v1/workspaces/{workspace_id}/content`

```json
{
  "client_id": "squid",
  "content_kind": "tutorial",
  "source_item_ids": ["source-uuid"],
  "generation_options": {"template_style": "remix"}
}
```

Creates a `content_items` row and a queued `generate` job. Returns `202 Accepted`
with `{content_id, job_id, status}`.

`GET /v1/workspaces/{workspace_id}/content?client_id=squid&status=needs_review`

`GET /v1/workspaces/{workspace_id}/content/{content_id}`

Returns the item, current version, source links, assets, approvals, publications,
and Figma links. Private assets use short-lived signed URLs.
Asset metadata and Storage object writes are server-managed to preserve approved
version history.

Current incremental Netlify bridge:

- `POST /api/news-card/{client_id}` stores one validated private PNG plus the
  localized specification and Telegram/X copy as `daily_news`.
- `POST /api/article/{client_id}` stores the source-locked Korean draft, Markdown,
  takeaways, and channel copy as `article`; it intentionally has no asset.
- `POST /api/tutorial/{client_id}` (Yellow and Squid) stores 1–12 validated private
  PNG pages in their immutable deliverable order as `tutorial`.
- `GET /api/library` returns a cursor-paginated, filterable team library, and
  `GET /api/library/{content_item_id}` returns the current immutable version,
  sanitized copy, latest review summary, Figma link metadata, and short-lived
  private asset URLs.
- `POST /api/library/{content_item_id}/review` accepts `approved` or `rejected`
  for the exact current version through the signed Studio session. It requires a
  UUID `Idempotency-Key`, appends an immutable approval row, and never publishes.
  Mock generations cannot be approved. Rejections carry at most five allowlisted
  reason codes and an optional bounded team comment.
- `POST /api/library/{content_item_id}/review-notification` sends the stored
  current version to the configured private Telegram reviewer. The request
  carries the exact `content_version_id` and may attach a generated article
  banner. Daily News and Tutorial use the first private stored PNG through a
  short-lived signed URL. The DM contains a review-only deep link; it cannot
  approve or publish without the signed Studio session.

All three generation calls require a UUID `Idempotency-Key`. The UUID is bound to a
SHA-256 digest of the normalized submitted request, not mutable content later
fetched from X. An exact retry loads the committed version before Railway is called;
reusing the UUID for changed input returns a mode-specific
`*_idempotency_conflict` with HTTP 409. Successful responses include
`content_item_id` and `content_version_id`; asset-backed modes also include their
asset IDs. Every generated item starts in `needs_review`, and generation never
implies approval. Missing catalog rows, metadata mismatches, or missing private
objects fail closed rather than returning an untracked temporary result.

Approved non-mock output excerpts are available to later generation only for the
same client and content kind. They guide Korean cadence, terminology, and channel
structure; the current source remains the sole factual boundary. Recent rejection
reason codes add static guardrails. Free-form review comments never enter model
prompts or Railway generation requests. The selected guidance IDs, reason codes,
policy version, and payload hash are stored in generation metadata for audit.

Source content is stored in full and test-mode generations are explicitly marked
in generation metadata, the generation response, and the team library.
`review_content_version` refuses to approve a version with `mock_mode: true`, and
`request_content_publication` repeats that check as a server-side backstop. These
incremental `/api/*` routes use the signed team cookie described in
`docs/STUDIO_ACCESS.md`; the future `/v1/workspaces/*` contract above will use
Supabase user JWTs.

`POST /v1/workspaces/{workspace_id}/content/{content_id}/regenerate`

Queues a new version; it never overwrites a prior version. Optional input contains
review instructions and an explicit base version.

### Review

Current signed-session bridge:

`POST /api/library/{content_item_id}/review`

```json
{
  "content_version_id": "version-uuid",
  "decision": "rejected",
  "reason_codes": ["off_brand_tone", "awkward_korean"],
  "comment": "문장 리듬을 브랜드 기준에 맞춰주세요."
}
```

The route calls the service-only `record_studio_content_review` RPC. Studio
session reviews are identified explicitly as `reviewer_source = studio_session`;
they do not create a fake Supabase Auth user. An approval becomes a bounded
positive style example. For a rejection, only allowlisted reason codes—not the
free-form comment—can influence later generation.

### Telegram review notification

Current signed-session bridge:

`POST /api/library/{content_item_id}/review-notification`

Multipart fields:

- `content_version_id`: required current version UUID;
- `banner`: optional PNG/JPEG/WebP up to 10 MB, used for the transient Article
  hero preview.

The server ignores browser-supplied title, body, client, and channel copy. It
reloads the immutable current version from the catalog, refuses mock or
non-`needs_review` items, and sends a private photo plus the stored Telegram
copy. `TELEGRAM_REVIEW_BOT_TOKEN` and numeric `TELEGRAM_REVIEW_CHAT_ID` are
Functions-only Netlify secrets. Telegram delivery failure never rolls back or
publishes the stored content.

`POST /v1/workspaces/{workspace_id}/content/{content_id}/approve`

```json
{"content_version_id": "version-uuid", "comment": "브랜드 톤 확인 완료"}
```

The transaction verifies that the version belongs to the item, appends an approval,
and moves the item to `approved`. Reject uses the same invariant:

`POST /v1/workspaces/{workspace_id}/content/{content_id}/reject`

Review records are append-only. A corrected draft is a new content version.

### Jobs

`GET /v1/workspaces/{workspace_id}/jobs/{job_id}`

Returns public progress fields only. Worker lock owner, raw provider payloads, and
secret references are not returned.

Worker-only operations are internal, service-authenticated routes. A worker claims
one row in a transaction:

```sql
select id
from public.jobs
where status in ('queued', 'retrying')
  and available_at <= now()
order by priority desc, available_at, created_at
for update skip locked
limit 1;
```

It sets `running`, `locked_by`, `locked_at`, and `lease_expires_at`, then records
success or a bounded retry. The job `idempotency_key` prevents duplicate scheduled
work.

### Schedule and publishing

`POST /v1/workspaces/{workspace_id}/content/{content_id}/schedule`

Requires an approved version and explicit channel/time. It creates one publication
per channel; scheduling X does not consume the approval or prevent a later Telegram
schedule. The item keeps the earliest pending schedule and the same approved
`current_version_id`. A retry with the same `Idempotency-Key` returns the original
publication even after its scheduled time passes.

`POST /v1/workspaces/{workspace_id}/content/{content_id}/publish`

Queues immediate publication. Provider IDs/URLs and sanitized responses are stored
on completion. Provider credentials remain in server secret storage and are never
persisted in request payloads.

### Figma

`GET /v1/workspaces/{workspace_id}/figma/queue?client_id=squid`

Returns approved, not-yet-synced versions and signed editable-SVG URLs.

`POST /v1/workspaces/{workspace_id}/content/{content_id}/figma-links`

```json
{
  "content_version_id": "version-uuid",
  "file_key": "figma-file-key",
  "node_id": "123:456",
  "page_name": "2026 W30",
  "section_name": "Squid"
}
```

The server calls `record_approved_figma_link`, which verifies active editor access,
the exact current version, and its approval record before upserting the link. Direct
authenticated writes to `figma_links` are denied. Figma frame plugin data can mirror
`content_id` and `content_version_id` for navigation, but the database row is
canonical.

## Error format

```json
{
  "error": {
    "code": "version_not_approved",
    "message": "승인된 버전만 예약할 수 있습니다.",
    "request_id": "request-uuid",
    "retryable": false
  }
}
```

Use `409` for idempotency/state conflicts, `422` for invalid source/package data,
`429` for rate/cost limits, and `503` only for retryable provider failures.
