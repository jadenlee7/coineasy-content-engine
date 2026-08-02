# Supabase foundation

This directory is the single migration history for the shared
`coineasy-meme-engine` Supabase project (`isuqcqwxpojgzevxfdwr`). It includes the
original meme schema, the server-only legacy hardening, the Content Studio
foundation, the initial four-client workspace, the composite FK indexes, and
the review-only official-X worker contract.
Do not run independent migrations from the old meme-engine repository.

Current operating model:

1. `daily_memes` remains available only to the Railway services' `service_role`;
   `anon` and `authenticated` have no table or helper-view privileges.
2. The `coineasy-content-studio` workspace is service-managed until Supabase Auth
   is enabled. It intentionally has no fake `auth.users` row or human owner.
3. `yellow`, `origintrail`, `squid`, and `babylon` are registered in
   `workspace_clients`; API/provider secrets never belong in those rows.
4. Netlify uses the service role only inside server functions. The browser gets a
   signed, short-lived Studio session and never receives the service key.
5. Future schema changes must be added here, tested against a disposable database,
   and then applied as forward-only migrations.

The official-X RPCs are also service-role-only. They atomically deduplicate
official posts, recover sources committed before a worker crash, enforce one
draft per client and four total drafts per KST day, lease generation jobs, and
verify the final immutable catalog version is `needs_review`. They never create
approvals, publications, or Figma exports. See
`docs/OFFICIAL_X_AUTOMATION.md` for the operating flow.

Each scheduled request also freezes at most three earlier posts from that exact
official feed in a private immutable style-reference pack. The pack guides only
writing cadence and structure; its rows are never factual `content_source_links`.
The service role can execute the pack RPC but cannot read or mutate the private
table directly.

Environment boundary:

| Variable | Location | Browser-safe |
|---|---|---:|
| `SUPABASE_URL` | Netlify/Railway and web config | yes |
| `SUPABASE_PUBLISHABLE_KEY` | team web app | yes, with RLS |
| `SUPABASE_SERVICE_ROLE_KEY` | trusted Netlify/Railway server only | **no** |
| `CONTENT_STUDIO_WORKSPACE_ID` | trusted Netlify catalog routing only | no |
| `STUDIO_ACCESS_TOKEN` | Netlify team-session login only | **no** |
| `STUDIO_AUTOMATION_TOKEN` | trusted Netlify/Railway generation bridge only | **no** |
| `API_SECRET` | Netlify-to-Railway server relay only | **no** |

Do not prefix a browser bundle variable with the service-role value. Service-role
clients bypass RLS and must never be created in browser or Figma plugin code.

The private Storage object convention is:

```text
{workspace_uuid}/{client_id}/{asset_uuid}/{filename}
```

Use authenticated downloads or short-lived signed URLs. Do not change the
`content-studio` bucket to public.

The Netlify generation relays are deliberately fail-closed until `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, and `CONTENT_STUDIO_WORKSPACE_ID` are configured and
the private `content-studio` bucket exists. News and Tutorial copy every generated
PNG into that bucket only after confirming the workspace/client registration;
Article records a durable text version without an asset. The service-only,
idempotent `record_generated_content` and `record_tutorial_generation` RPCs create
immutable `needs_review` versions before a response is returned. The RPCs verify
every cataloged object and asset path. Browser-facing asset URLs are short-lived
and never point back to Railway's process-local output directory.

Retries call the corresponding service-only catalog lookup before starting Railway.
Tutorial lookup is driven by the immutable version's ordered
`deliverables.asset_ids`, not by whichever mutable asset rows happen to match. A
partial or deleted asset-backed result therefore fails closed instead of returning
a broken preview.

Every generation request UUID is payload-bound: Netlify stores the same SHA-256 request
hash in the content envelope and generation metadata, and the catalog RPC requires
them to agree. Reusing an idempotency UUID for different normalized input is a
conflict, not a request to return unrelated prior work. Mock generations are also
stored with `mock_mode: true`; review and publication RPCs reject those versions
even if a client fails to display the sample warning.

Authenticated clients receive only safe column projections for feeds, sources,
jobs, publications, and activity. Workflow transitions use membership-checked
public RPCs. Figma link creation uses `record_approved_figma_link` and accepts only
the current approved version; direct browser/plugin mutation is denied. Raw table
writes and asset/Storage mutations remain server-only. `content_versions`,
`approvals`, and `event_log` are append-only for the service role as well.

The signed Studio session records human decisions through the service-only
`record_studio_content_review_v2` RPC. Approval requires the exact version's
valid `double-fact-check@1` report plus separate `source_facts_verified` and
`output_claims_verified` attestations. The legacy Studio review RPC and
`review_content_version` cannot execute after the gate migration. These rows use
`reviewer_source = 'studio_session'` and a null `reviewer_id`; authenticated
Supabase users continue to use `reviewer_source = 'supabase_auth'` with their
real user ID. No fake `auth.users` identity is created. Review requests are
current-version checked and idempotent, and mock content cannot be approved.

`get_brand_review_guidance` returns at most three approved, non-mock excerpts for
the same workspace, client, and content kind plus aggregated allowlisted
rejection reason codes from the last 90 days. It never returns reviewer identity
or free-form comments. Netlify passes this bounded projection to Railway as
style-only context and stores only its IDs, codes, policy version, and hash in
generation metadata. The current source remains the factual boundary, and review
does not trigger publishing.

`tests/content_studio_security.sql` is a transactional integration smoke. It checks
cross-client and cross-version foreign keys, editor/viewer permissions, sensitive
column denial, immutable assets, tutorial catalog idempotency, and
generation/review/publication RPCs. Run it only
against a disposable local or staging database after applying the migration; it
finishes with `ROLLBACK`.

Architecture and route contracts:

- `docs/ADR-002-content-studio-data-foundation.md`
- `docs/CONTENT_STUDIO_API.md`
