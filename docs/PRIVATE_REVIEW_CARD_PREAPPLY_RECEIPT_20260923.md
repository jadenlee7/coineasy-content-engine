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
Its disposable PostgreSQL 16.13 and 17.6 runs classify the old migration as
`legacy`, the checked-in correction as `corrected`, and reject an unknown body
or a nonnullable `jobs.content_item_id`. It has **not** produced a production
`corrected` receipt; the migration remains unapplied.

**Decision: BLOCK for the button-card owner path.** The unapplied correction
must receive separate exact production migration authorization. After any
application, re-run this read-only gate against the then-current hosted catalog
and continue the remaining owner/Storage/Telegram private-canary checks. This
receipt neither authorizes applying the migration nor enables or sends a card.
The observed local/remote migration histories diverge, so generic `db push`
or an unrestricted migration-up command is not a scoped apply method here.
