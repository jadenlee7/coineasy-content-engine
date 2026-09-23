# Exact producer-binding migration preparation

Status: **local/offline preparation only**. This folder contains no production
connection or apply CLI. It does not authorize or perform migration, deploy,
enablement, review-room delivery or public publication.

The only target migration is
`supabase/migrations/20260916190000_content_ops_review_producer_binding.sql`,
10,717 UTF-8/LF bytes with SHA-256
`dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6`.
Production currently has the older candidate body and no applied
`20260916190000` history entry. The read-only contract at
`supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql`
requires `legacy_no_history` before any apply and `corrected_exact_history`
afterward; unknown bodies and partial/mismatched history fail closed. Its
current SHA-256 is
`6e96c59a7b338698459d7f8212e78e88c0e816bf5ec35a2136e05bc8c5945c71`.

`atomic-apply-sql.mjs` is an offline builder for a single transactional SQL
statement set. It accepts only those exact migration bytes, strips that file's
outer transaction markers, and encloses the function replacement and exact
one-element migration-history source in one transaction. It guards the old
function hash, owner, ACL, nullable job link, missing history and exclusive
advisory lock; after writing, it checks the corrected hash, ACL and exact
history bytes before commit. A failed history insert or postcondition rolls
back both changes. It has no token, network, CLI execution or filesystem write
path. No generated SQL is checked in as a production apply instruction.

Disposable PostgreSQL 16.13 and 17.6 tests cover exact source acceptance,
changed-byte rejection, legacy/preflight state, one atomic apply, exact
postflight state, double-apply rejection, and complete rollback on history or
postcondition failure. The fixture includes the six observed hosted history
columns and rejects any unexpected, mandatory column without a default.
Both containers were removed; provider and production calls were zero.

Before any separately authorized production apply, the executor still needs
an exact GitHub-main/source-SHA check, scoped operator approval, durable
operation receipt, fresh read-only `legacy_no_history` readback, one-shot
network execution and read-only reconciliation on an uncertain response. Do
not blind-retry or use generic `supabase db push`/unrestricted migration-up:
the observed local and remote migration histories diverge. A successful
`corrected_exact_history` result would prove this migration boundary only;
the button-card owner, Storage reader, bot runtime and Telegram private canary
remain separate gates.

Local checks:

```sh
node --test ops/private-review-producer-binding/atomic-apply-sql.test.mjs
node scripts/verify_private_card_ledger_docker_local.mjs --local-only
node scripts/verify_private_card_ledger_docker_local.mjs --local-postgres17
```
