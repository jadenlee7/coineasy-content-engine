# Supabase foundation

`migrations/20260722090000_content_studio_foundation.sql` is the proposed Content
Studio schema. It has not been applied to an external project by this repository.

Before applying it:

1. Create a staging Supabase project and enable the required team Auth provider.
2. Review the migration and run the repository's static migration tests.
3. Link the local Supabase CLI to **staging**, then apply migrations there first.
4. Create the initial workspace through an authenticated session. Its insert trigger
   atomically creates the owner membership.
5. Register `yellow`, `origintrail`, `squid`, and `babylon` in
   `workspace_clients`; do not copy API/provider secrets into those rows.
6. Verify viewer/editor/admin/owner access and private asset downloads with separate
   test users before promoting the migration.

Environment boundary:

| Variable | Location | Browser-safe |
|---|---|---:|
| `SUPABASE_URL` | Netlify/Railway and web config | yes |
| `SUPABASE_PUBLISHABLE_KEY` | team web app | yes, with RLS |
| `SUPABASE_SERVICE_ROLE_KEY` | trusted Netlify/Railway server only | **no** |
| `CONTENT_STUDIO_WORKSPACE_ID` | trusted Netlify tutorial routing only | no |
| `API_SECRET` | Netlify-to-Railway server relay only | **no** |

Do not prefix a browser bundle variable with the service-role value. Service-role
clients bypass RLS and must never be created in browser or Figma plugin code.

The private Storage object convention is:

```text
{workspace_uuid}/{client_id}/{asset_uuid}/{filename}
```

Use authenticated downloads or short-lived signed URLs. Do not change the
`content-studio` bucket to public.

The Netlify tutorial relay is deliberately fail-closed until `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, and `CONTENT_STUDIO_WORKSPACE_ID` are configured and
the private `content-studio` bucket exists. It copies every generated PNG into that
bucket only after confirming that the selected workspace/client registration exists,
then records the source, immutable version, PNG metadata, and review state through
the service-only, idempotent `record_tutorial_generation` RPC before returning slide
URLs. The RPC verifies that every cataloged object exists, that slide order is
contiguous, and that each asset UUID matches its Storage folder. The browser-facing
URL is HMAC-scoped to the
durable object and redirects to a short-lived Supabase signed URL; it never points
back to Railway's process-local output directory.

Retries call the service-only `get_tutorial_generation` RPC before starting Railway.
That lookup is driven by the immutable version's ordered `deliverables.asset_ids`,
not by whichever mutable asset rows happen to match. It requires the primary asset,
every scoped asset row, and every corresponding `storage.objects` row to agree; a
partial or deleted tutorial therefore fails closed instead of returning a broken
gallery.

The tutorial request UUID is payload-bound: Netlify stores the same SHA-256 request
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

`tests/content_studio_security.sql` is a transactional integration smoke. It checks
cross-client and cross-version foreign keys, editor/viewer permissions, sensitive
column denial, immutable assets, tutorial catalog idempotency, and
generation/review/publication RPCs. Run it only
against a disposable local or staging database after applying the migration; it
finishes with `ROLLBACK`.

Architecture and route contracts:

- `docs/ADR-002-content-studio-data-foundation.md`
- `docs/CONTENT_STUDIO_API.md`
