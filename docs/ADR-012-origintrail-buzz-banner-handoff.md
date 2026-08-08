# ADR-012: OriginTrail Batch banner handoff to Buzz

- Status: Accepted
- Date: 2026-08-05

## Context

The first OriginTrail Production Shadow delivery proved the durable text path, but the review handoff had no visual artifact. CoinEasy already has a deterministic 1200×630 OriginTrail article-banner renderer. Buzz desktop-v0.5.4 supports file attachments, but its media boundary intentionally rejects SVG because SVG can carry active content.

The delivery worker must keep its narrow trust boundary: it may read one eligible review, upload one reviewed artifact, and record one relay receipt. It must not receive Supabase, Studio, OpenAI, publication, or deployment credentials. Automatic publication remains disabled.

## Decision

1. Derive the banner from the immutable Batch review detail with the existing OriginTrail article-banner SVG renderer.
2. Rasterize that SVG server-side to a bounded 1200×630 PNG before it crosses into Buzz.
3. Expose the PNG through two GET-only, no-store routes:
   - a Studio-session route for the human review screen;
   - a dedicated Buzz shadow-token route for the delivery worker.
4. Require standalone official-source evidence before rendering. URL-only results fail closed.
5. Have the delivery worker validate PNG signature, dimensions, byte limit, content type, and server content hash before claiming a delivery.
6. Bind the attachment filename, media type, and SHA-256 to the existing durable request fingerprint, then issue one exact `buzz messages send --file` call after the provider-create fence.
7. Keep Buzz review actions separate from this artifact-delivery slice. A future action adapter may record approve/edit/regenerate intent, but no Buzz action may directly publish content.

### 2026-08-08 review-pack materialization addendum

The banner is now also the asset of one immutable Content Studio review pack.
Before the V2 Buzz claim can be leased, Netlify:

1. re-reads the verified Batch detail and immutable X Article identity;
2. renders the same deterministic 1200×630 PNG;
3. stores that PNG under a deterministic UUID, accepting a retry only when the
   existing private object is byte-identical;
4. records one `content_item → content_version → asset` package using the
   original Batch `request_id` as the content item ID and preserves the exact
   `content_source_links` row; and
5. binds the Batch input hash, result hash, source-body hash, PNG hash, content
   version, asset, and source to an immutable review-pack receipt.

The Buzz delivery claim carries `attachment_sha256`. V2 is granted only when
that digest equals the materialized PNG digest, and the delivery receipt stores
the same digest. The feature is disabled by default through
`BUZZ_REVIEW_PACK_MATERIALIZATION_ENABLED=false` until the migrations and one
isolated staging result are approved. The V1 claim remains a rollback path, but
its null attachment digest cannot become a V2 review target.

Materialization creates neither a Studio approval nor a publication job.
Automatic publication remains OFF.

## Consequences

- The Studio screen and Buzz receive the same evidence-bound visual design.
- Buzz receives a safe inline image rather than blocked active SVG content.
- A renderer or attachment change produces a different durable request fingerprint and cannot silently reuse an earlier claim.
- PNG rendering adds one native image dependency to the Netlify function bundle.
- This decision expands Buzz from text-only review notification to a durable,
  source-preserving visual review package; it does not make Buzz a publication
  control plane.

## Rejected alternatives

- Uploading SVG directly to Buzz: rejected because Buzz correctly blocks SVG active content.
- Giving the Railway worker Studio or Supabase credentials: rejected because it widens the breach boundary.
- Generating a separate visual in the worker: rejected because it could drift from the Studio review artifact.
- Connecting a Buzz approval directly to publication: rejected because the Shadow pilot requires automatic publication to remain off.
