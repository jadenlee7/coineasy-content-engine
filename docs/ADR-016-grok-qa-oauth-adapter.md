# ADR-016: Grok QA MCP OAuth adapter

- Status: Proposed; code and deploy-preview only
- Date: 2026-08-13
- Production activation: Not approved

## Context

The bounded CoinEasy QA MCP server already exposes exactly three tools behind a
dedicated static bearer. Grok Build can use that server directly, but the Grok
Bot custom-connector install flow expects OAuth discovery and authorization.
Copying a long-lived bearer into a user-facing connector is both awkward and
hard to audit.

The adapter must not expand the QA server's authority. In particular, it must
not add approval, publication, destination selection, Batch, model, deploy, or
Routine capabilities.

## Decision

Add a default-off OAuth 2.1 authorization-code adapter with S256 PKCE and
dynamic client registration:

- `/.well-known/oauth-protected-resource/api/grok-qa/mcp` advertises the exact
  MCP resource.
- `/.well-known/oauth-authorization-server` advertises authorization, token,
  and registration endpoints.
- Client IDs are stateless, signed, expiry-bounded envelopes containing only
  the registered name and exact redirect URIs.
- A human operator must enter a separate high-entropy operator code on the
  CoinEasy consent page.
- The random authorization code is stored only as a SHA-256 digest, expires in
  five minutes, binds client, redirect URI, resource, scope, and PKCE challenge,
  and can be consumed once.
- Successful exchange returns the existing dedicated
  `GROK_QA_CONNECTOR_TOKEN`; it does not mint a broader credential.
- The MCP server continues to expose exactly
  `coineasy_list_needs_review`, `coineasy_get_review_package`, and
  `coineasy_submit_qa_verdict`.

The adapter requires all of the following before it is configured:

- `GROK_QA_OAUTH_ENABLED=true` exactly;
- an issuer equal to the request origin;
- an explicit redirect-origin allowlist;
- mutually distinct connector, operator, signing, project, and scoped database
  credentials;
- `SUPABASE_GROK_QA_OAUTH_KEY`, whose role can execute only the create and
  consume code RPCs.

The authorization-code table has FORCE RLS, no policies, and no direct grants.
Its role is `NOLOGIN`, `NOINHERIT`, and `NOBYPASSRLS`. The two security-definer
RPCs use an empty search path and cannot mutate content, approvals,
publications, provider jobs, or external systems.

## Rollout

1. Merge and deploy with `GROK_QA_OAUTH_ENABLED=false`.
2. Apply the migration to a disposable Supabase Preview branch and mint a
   branch-scoped role JWT.
3. Run one OAuth registration, consent, PKCE exchange, replay rejection, and
   three-tool discovery test in deploy-preview.
4. Delete the Preview branch and its Preview-only secrets.
5. Request a separate Production approval binding the exact main SHA, issuer,
   redirect-origin allowlist, scoped role, and rollback.

Routine, automatic publication, and all publishing flags remain OFF throughout.

## Rollback

Set `GROK_QA_OAUTH_ENABLED=false` and trigger a Git-backed deploy of the exact
approved SHA. This removes OAuth discovery from the MCP challenge without
changing the existing connector token or the bounded three-tool server. The
additive table and role may remain inert; authorization-code rows expire and
cannot authorize content actions.

## Consequences

Grok Bot gets a standard OAuth install experience while CoinEasy retains one
human consent boundary and the existing least-privilege QA tool surface. The
trade-off is that the OAuth access token remains long-lived until the dedicated
connector token is rotated; refresh tokens are deliberately not supported in
this slice.
