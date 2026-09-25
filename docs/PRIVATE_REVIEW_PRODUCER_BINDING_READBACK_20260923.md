# Producer-binding correction: read-only production receipt

Observed on 2026-09-23. The production query used
`supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql`
(SHA-256 `6e96c59a7b338698459d7f8212e78e88c0e816bf5ec35a2136e05bc8c5945c71`).
At approximately 14:28 UTC it returned exactly
`producer_binding_contract=legacy_no_history`, `read_only=true`, `changes=0`,
`provider_calls=0`. The earlier catalog check observed
`current_user=session_user=postgres` on the write SQL surface, and migration
history columns `version`, `statements`, `name`, `created_by`,
`idempotency_key`, and `rollback`. The target migration history row was absent.
These observations do **not** authorize a write.

At approximately 14:49 UTC, the proposed runner's official Supabase
`/database/query/read-only` transport was exercised directly with this exact
SQL. It returned an array containing `legacy_no_history`, `read_only=true`,
`changes=0`, and `provider_calls=0`. No write endpoint was called. This proves
the hosted read-only endpoint accepts the contract shape; the result still
must be refreshed immediately before any separately authorized application.

The hosted legacy candidate function body SHA-256 was
`5de6d755095e63f3f7e03eb9f53db5d7fdada0911e222237fd459f12eaa98ffb`.
The checked-in corrected body SHA-256 is
`491114d72507197cb680794cc04d5755950f257f34574de4c88d0fe15f7a541d`.
The one target migration, `20260916190000_content_ops_review_producer_binding.sql`,
is 10,717 bytes, SHA-256
`dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6`.
At approximately 14:27 UTC, GitHub `main` was
`25e11b7046020d288ff7cc2a105cf6671523795e`, and that migration file's
Git blob SHA `86db4122957a1702f444371c28b081ccc0fb73ce` matched the local
file. Both GitHub and database observations must be refreshed at apply time.

The [isolated application pack](../ops/private-review-producer-binding/README.md)
is local preparation. Its disposable PostgreSQL 16 and 17 tests prove a single
atomic function/history commit, duplicate rejection, and rollback when history
registration or postcondition fails. Its mocked request tests prove only the
runner's control flow. No production migration, deployment, enablement,
Telegram delivery, provider call or publication was performed.

The target SQL and local/remote migration histories differ; generic
`supabase db push` or unrestricted migration-up would exceed this scope.
After separately authorized application, require a fresh read-only
`corrected_exact_history` receipt before treating this blocker as resolved.
