# Private prompt runtime pre-apply catalog receipt

Observed 2026-09-23 13:49 UTC against the confirmed active, healthy
`coineasy-meme-engine` production Supabase project. The exact local SQL file
`supabase/proposals/content_ops_button_prompt_preapply_readonly.sql` had SHA-256
`d25ca23042799e5754da8e0a50c5c0c0943a723d94128e25d3ab0743efda1a5b`.
It was executed once using `supabase db query --linked --project-ref ... --file`
inside its own `BEGIN TRANSACTION READ ONLY` / `COMMIT` boundary. The CLI
returned `prompt_preapply=pass`, `read_only=true`, `changes=0`,
`provider_calls=0`, and `hosted_runtime_verified=false`.

The gate checks required table/column types, all eight signup-free principal
actor FKs and both exact composite FKs, principal and attempt FORCE RLS, the
two base-function signatures/owners/search paths/body SHA-256 values, bounded
restricted-role read and execute grants, direct INSERT denial, restrictive
INSERT policies, and absence of any same-name runtime wrapper. This is a
read-only catalog result, not a transaction through the proposed functions.

Disposable PostgreSQL 16.13 and 17.6 independently passed the intact fixture
and rejected a missing composite FK, missing read grant, and installed wrapper.
The disposable runtime transaction exercises the wrapper but is not the exact
hosted data/ACL environment. The runtime capability SQL remains an unapplied
proposal. No production migration, grant, deployment, flag change, Telegram
message, provider request, approval, or publication was performed. A fresh
pre-apply readback and separate explicit authorization are required before any
production apply or activation.
