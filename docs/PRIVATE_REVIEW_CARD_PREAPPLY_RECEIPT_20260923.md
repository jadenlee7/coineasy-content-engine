# Private button-card pre-apply readback

Observed 2026-09-23 13:53–13:58 UTC against the active, healthy
`coineasy-meme-engine` production project. All SQL in this readback ran inside
read-only transactions; no row, role, migration, deployment, flag, provider or
Telegram state was changed.

The earlier signature/ACL-only form of
`supabase/proposals/content_ops_button_card_preapply_readonly.sql` passed, but
that result is **superseded**: it did not pin the function bodies. The stronger
file (SHA-256 `2a08dce306ee50b9f4e992321ca264abdcd26b22f8caaa05a9d817cafd913334`)
checks return type, owner, volatility, security mode, empty search path and
SHA-256 body for all six base functions. On production it stopped with
`button_card_preapply_function_contract_mismatch`.

The single mismatch is `private.content_ops_review_candidate(uuid,uuid,uuid)`:
production has body SHA-256
`5de6d755095e63f3f7e03eb9f53db5d7fdada0911e222237fd459f12eaa98ffb`.
The checked-in producer-binding correction expects
`491114d72507197cb680794cc04d5755950f257f34574de4c88d0fe15f7a541d`.
The other five base-function hashes and inspected owner/security/search-path
attributes matched the local migration replay. The read-only migration list
showed local `20260916190000` but no remote applied version. The checked-in
`20260916190000_content_ops_review_producer_binding.sql` file has SHA-256
`dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6`.

Disposable PostgreSQL 16.13 and 17.6 independently reproduced the production
legacy body hash from the earlier migration, rejected it through the stronger
gate, then passed after the checked-in producer-binding correction. An altered
body was also rejected. Both full synthetic verifier runs finished with zero
provider and production calls and removed their containers.

At 2026-09-23 14:03 UTC, the separate
`content_ops_review_producer_binding_contract_readonly.sql` pre/post gate
(SHA-256 `bf90178ae04722e37d153431b7797bea55bebcf8bde396d96aadfca438714c4f`)
classified production as `legacy`, with `read_only=true` and `changes=0`.
That initial classification is superseded by the stronger function-plus-history
gate (SHA-256 `3ec505b7c0250ffd3364b928e031f938ba8f8f113a13cd49db1a34d3a10afe85`),
which returned `legacy_no_history`, `read_only=true`, `changes=0` on production
at 2026-09-23 14:08 UTC. Disposable PostgreSQL 16.13 and 17.6 classify the
old function/no history as `legacy_no_history`, the corrected function/exact
one-element source history as `corrected_exact_history`, and reject mismatched
function, nonnullable job link or partial/wrong history. It has **not**
produced a production `corrected_exact_history` receipt.

At 2026-09-23 14:19 UTC, the final strengthened gate (SHA-256
`6e96c59a7b338698459d7f8212e78e88c0e816bf5ec35a2136e05bc8c5945c71`)
again returned `legacy_no_history`, `read_only=true`, `changes=0` on
production. A separate read-only catalog check observed the six migration
history columns, primary key on `version`, and an optional unique
`idempotency_key`; the SQL surface reported `current_user=session_user=postgres`.
The local atomic fixture mirrors that history shape and rejects an unexpected
mandatory column. This remains pre-apply evidence only.

The [single-migration preparation pack](../ops/private-review-producer-binding/README.md)
locally proved that the function replacement and exact history registration
can commit together. Failed history insertion and failed postcondition each
rolled both back; a second execution was denied. A default-OFF, exact-main,
approval-hash-gated one-shot runner now has mocked endpoint tests, including
uncertain-ACK read-only reconciliation without a retry. It has not sent a
production write or applied the migration.

At 2026-09-23 14:27 UTC, the GitHub read-only connector reported `main` at
`25e11b7046020d288ff7cc2a105cf6671523795e`. The migration file's Git blob
SHA `86db4122957a1702f444371c28b081ccc0fb73ce` matched the local file.
That observation is not a future apply-time exact-main check.

At approximately 2026-09-23 14:28 UTC, the same read-only production SQL
contract again returned `legacy_no_history`, `read_only=true`, `changes=0`,
`provider_calls=0`. It did not invoke the apply runner.

At approximately 2026-09-23 14:56 UTC, a separate read-only partial gate
(`content_ops_button_card_remaining_preapply_readonly.sql`, SHA-256
`731864daf29e2a64c2b93aebc7eecb74af1181c2cb4326973555a1eb00f3fc91`)
passed through the official Supabase read-only query endpoint. It checks the
four existing service-role RPC ACLs, outbox ACL/FORCE RLS and absence of a
partially installed button-card owner. The bounded result was
`remaining_preapply_contract=pass`, `producer_binding_checked=false`,
`overall_ready=false`, `read_only=true`, `changes=0`, `provider_calls=0`.
Disposable PostgreSQL 16.13/17.6 also passed and rejected a missing base RPC
grant and a partially installed owner. This narrows the remaining hosted
uncertainty; it does **not** override the failing full gate or authorize an
owner migration.

**Decision: BLOCK for the button-card owner path.** The unapplied correction
must receive separate exact production migration authorization. After any
application, re-run this read-only gate against the then-current hosted catalog
and continue the remaining owner/Storage/Telegram private-canary checks. This
receipt neither authorizes applying the migration nor enables or sends a card.
The observed local/remote migration histories diverge, so generic `db push`
or an unrestricted migration-up command is not a scoped apply method here.
