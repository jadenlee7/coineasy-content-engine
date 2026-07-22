# ADR-002: Supabase-backed Content Studio

**Status:** Implemented locally; external deployment pending
**Date:** 2026-07-22
**Deciders:** CoinEasy product, design, and content operations leads

## Context

The Railway service generates branded assets, but generation is synchronous and its
files live under an ephemeral output directory. The Netlify console has no shared
team identity, durable history, version review, approval trail, or publishing
calendar. Figma receives manually downloaded SVGs and cannot tell which approved
content version a frame represents.

The next product needs three understandable modes—Daily News, Article, and
Tutorial—while keeping the existing renderer and client configuration as the
execution layer. Team members must be able to collect a source once, review a
version, approve it, publish it, and find the exact asset later.

Constraints:

- Existing Railway generation and client branding remain authoritative.
- Supabase Auth identities and workspace membership define human access.
- No database/service-role secret may be exposed to the browser or a Figma plugin.
- Generated assets are private until an explicit publication step.
- Figma is an editing destination, not the content database.
- Automatic collection and drafting are allowed; publishing starts human-approved.

## Decision

Add Supabase as the Content Studio system of record and keep Railway as a stateless
worker. Netlify becomes the authenticated team application and uses short-lived user
sessions. A Railway worker claims durable PostgreSQL jobs with
`FOR UPDATE SKIP LOCKED`, stores generated assets in a private Storage bucket, and
records every output as a new immutable content version.

The migration in
`supabase/migrations/20260722090000_content_studio_foundation.sql` establishes:

- workspaces, members, and registered client configurations;
- feed definitions and deduplicated source items;
- content items, source links, immutable versions, and private assets;
- durable jobs with retry leases and idempotency;
- append-only approvals and event history;
- publications and version-specific Figma links.

The normal state path is:

```text
source inbox → queued → generating → needs_review → approved
                                              ↘ rejected
approved → scheduled → published
    ↘ generation/publishing failure → failed → retry
```

`content_kind` selects one normalized package contract:

| Kind | Main editable output | Visual deliverable |
|---|---|---|
| `daily_news` | concise Korean briefing + Telegram/X copy | news card PNG/SVG |
| `article` | headline, dek, sections, references | hero/card PNG/SVG |
| `tutorial` | goal, prerequisites, numbered steps, checks | carousel PNG/SVG pages |

All tables in the exposed `public` schema have explicit grants and RLS. Policies
derive authorization from active `workspace_members` rows—not mutable JWT user
metadata. Security-definer helpers live in the unexposed `private` schema with a
blank search path. Authenticated editors can create work, but only the trusted
server can advance workers, publication results, and the append-only event log.
Raw feed credentials, provider payloads, job input/output/lease fields, publication
provider payloads, and event details are not table-readable by the web role. The UI
reads `security_invoker` safe views backed by column-level grants. Generation,
review, and publication transitions use membership-checked RPCs; direct edits to
content status and direct job/publication inserts are not granted.

Storage uses a private `content-studio` bucket. Object keys begin with the workspace
UUID (`{workspace_uuid}/{client_id}/{asset_uuid}/{filename}`); RLS validates that
path against membership. Team downloads use an authenticated request or an
expiring signed URL. Asset metadata and Storage object mutation are server-managed,
so an editor cannot overwrite or delete an approved version's file.

The current Tutorial relay is the first incremental bridge onto this model. It
copies all Railway-rendered PNG pages to private Storage, verifies their PNG
dimensions and SHA-256 digests, and calls the service-only
`record_tutorial_generation` RPC. That transaction creates one `needs_review`
content item, immutable version, page assets, and event entry before the browser
receives any slide URL. The caller supplies a stable request UUID in
`Idempotency-Key`; a retry first loads the exact immutable deliverable list and
verifies that every corresponding private Storage object still exists. An
uncertain network response can therefore be retried without duplicating the
tutorial or returning dead slide links. The request UUID is cryptographically
bound to the normalized submitted payload in both content and generation metadata.
For URL-only imports that binding deliberately uses the submitted URL, not mutable
remote content; a different payload under the same UUID fails with `409`. The
relay reserves persistence time inside
a 55-second end-to-end Netlify budget. A definite pre-catalog failure removes every
attempted upload path, including an upload whose success response was lost, while
an ambiguous catalog result preserves the files so a committed catalog can never
reference objects the relay deleted.

## Options Considered

### Option A: Supabase system of record + PostgreSQL jobs (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Operating cost | Low at current volume |
| Team history and review | Strong |
| Fit with current Railway/Netlify stack | Strong |

**Pros:** one identity/data/storage boundary, transactional version history, no
additional queue service, incremental adoption.

**Cons:** the worker needs lease/retry discipline; RLS and migrations require tests
and review.

### Option B: Keep generated manifests/files only

| Dimension | Assessment |
|---|---|
| Complexity | Low initially |
| Operating cost | Low |
| Team history and review | Weak |
| Fit with automation | Weak |

**Pros:** almost no new infrastructure.

**Cons:** ephemeral history, no user authorization, no reliable deduplication or
approvals, and Figma links drift from the generated source.

### Option C: Dedicated workflow stack (Temporal/Celery + Redis)

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Operating cost | Medium |
| Team history and review | Strong |
| Fit with current load | Excessive |

**Pros:** sophisticated long-running workflows and observability.

**Cons:** additional services and operational skills before volume justifies them.

## Figma Boundary

An internal Figma plugin should list **approved** versions through a server endpoint,
download their editable SVG using a signed URL, and import with
`figma.createNodeFromSvg()`. It then records `file_key`, `node_id`, and the exact
`content_version_id` through the membership-checked
`record_approved_figma_link` RPC. Direct browser/plugin mutation of `figma_links` is
not granted. Plugin data on the frame may mirror those IDs for navigation, but
Supabase remains authoritative.

Edits made in Figma do not silently overwrite approved content. “Return final to
Studio” creates a new asset or content version and re-enters review. A weekly Figma
section can be generated for team convenience without becoming the archive.

## Consequences

- Team members get one searchable history and approval source across all clients.
- Daily collection can run on a schedule without blocking a browser request.
- Regeneration creates a version instead of overwriting approved work.
- Storage, job cleanup, and event retention need explicit operating policies.
- Tutorial generations are durably cataloged today; Daily News and Article still
  need the same version-persistence bridge before they can claim shared history.
- Railway's process-local `edu_*` and `news_*` runs are kept long enough for
  Netlify downloads, then bounded by an exact-path TTL and size policy; durable
  Tutorial assets remain in private Supabase Storage.
- Publishing remains human-approved until quality metrics justify per-client
  auto-approval.
- Versions marked `generation_meta.mock_mode = true` are test artifacts: the
  database rejects both approval and publication even if a future UI omits the
  visible `샘플 · 게시 금지` warning.
- Version, approval, and event rows are append-only even for the service role;
  corrections create new records instead of rewriting history.
- A live Supabase project, Auth providers, allowed redirect URLs, and a destination
  Figma file still require an operator decision; this ADR does not create them.

## Delivery Phases

1. Apply the reviewed migration to a non-production Supabase project; configure Auth,
   the first workspace, four clients, and private Storage.
2. Replace the current shared team-access code with Supabase Auth and add
   Today/Review/Library views plus version/approval APIs.
3. Persist Daily News and Article results and change scheduled Railway generation
   to claim durable jobs.
4. Add source-feed scheduling, deduplication, and review-first publishing.
5. Ship the internal Figma plugin and link approved versions.
6. Add scheduling/publishing metrics and consider scoped auto-approval per client.

## Security Review Checklist

- [ ] Browser receives only the Supabase publishable key and user session.
- [ ] `SUPABASE_SERVICE_ROLE_KEY` exists only in trusted server environments.
- [ ] `CONTENT_STUDIO_WORKSPACE_ID` selects the reviewed Studio workspace for
      server-side tutorial asset paths.
- [ ] Every API query includes explicit `workspace_id` and `client_id` filters in
      addition to RLS.
- [ ] Storage URLs are authenticated or short-lived signed URLs.
- [ ] Worker job claims use a transaction, lease expiry, bounded retries, and an
      idempotency key.
- [ ] Figma plugin tokens cannot call service-role endpoints.
- [ ] Production migration and restore are rehearsed on staging first.

## References

- [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase Storage access control](https://supabase.com/docs/guides/storage/security/access-control)
- [Supabase private buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals)
- [Supabase Storage ownership](https://supabase.com/docs/guides/storage/security/ownership)
