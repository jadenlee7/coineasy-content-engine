# Exact producer-binding migration record

Status as of 2026-09-25: **this exact migration was applied once; runtime OFF**.
Operation `0371b727-df20-47a4-a628-5878daefebdb` completed with
`corrected_exact_history` and an independent read-only postflight. The final
receipt SHA-256 is
`9c3b8b23f784d741321a0c0a69c9b1e79e6c477a29d05a1546798884c62e7a02`.
Do not rerun the one-shot apply. This result does not authorize a deploy,
enablement, review-room delivery or publication; see the
[post-correction readback](../../docs/PRIVATE_REVIEW_CARD_PREAPPLY_RECEIPT_20260925.md).

The only target migration is
`supabase/migrations/20260916190000_content_ops_review_producer_binding.sql`,
10,717 UTF-8/LF bytes with SHA-256
`dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6`.
Production previously had the older candidate body and no applied
`20260916190000` history entry. The read-only contract at
`supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql`
requires `legacy_no_history` before any apply and `corrected_exact_history`
afterward; unknown bodies and partial/mismatched history fail closed. Its
current SHA-256 is
`6e96c59a7b338698459d7f8212e78e88c0e816bf5ec35a2136e05bc8c5945c71`.
The exact contract returned `legacy_no_history` through the official
production read-only query endpoint on 2026-09-23; see the historical
[readback receipt](../../docs/PRIVATE_REVIEW_PRODUCER_BINDING_READBACK_20260923.md).

`atomic-apply-sql.mjs` is an offline builder for a single transactional SQL
statement set. It accepts only those exact migration bytes, strips that file's
outer transaction markers, and encloses the function replacement and exact
one-element migration-history source in one transaction. It guards the old
function hash, owner, ACL, nullable job link, missing history and exclusive
advisory lock; after writing, it checks the corrected hash, ACL and exact
history bytes before commit. A failed history insert or postcondition rolls
back both changes. It has no token, network, CLI execution or filesystem write
path. No generated SQL is checked in as a production apply instruction.

The default-off runner validates a canonical, maximum-two-hour operator
approval packet bound to exact release SHA, project, migration and read-only
contract SHA. `--apply` additionally needs the separately approved subject
SHA-256, an exact clean GitHub `main` checkout, a matching live `main` ref,
an existing private receipt root and a Management API token. Before the one
write, it checks `legacy_no_history` twice through the read-only endpoint and
verifies the write executor is `postgres`. It writes a durable send-intent
receipt before the single atomic SQL request. A lost or invalid ACK triggers
one read-only reconciliation and **never** a retry; a successful ACK still
requires `corrected_exact_history` postflight. All receipt states keep runtime
activation false. The approval hash is an operator-reviewed binding, not a
cryptographic signature or a substitute for the user's separate approval.

Disposable PostgreSQL 16.13 and 17.6 tests cover exact source acceptance,
changed-byte rejection, legacy/preflight state, one atomic apply, exact
postflight state, double-apply rejection, and complete rollback on history or
postcondition failure. The fixture includes the six observed hosted history
columns and rejects any unexpected, mandatory column without a default.
Both containers were removed; provider and production calls were zero.

The exact-main, bounded approval and one-shot apply gates were satisfied for
operation `0371b727-df20-47a4-a628-5878daefebdb`. Do not blind-retry or use
generic `supabase db push`/unrestricted migration-up: the observed local and
remote migration histories diverged. The `corrected_exact_history` result
proves this migration boundary only;
the button-card owner, Storage reader, bot runtime and Telegram private canary
remain separate gates.

Local checks:

```sh
node --test ops/private-review-producer-binding/atomic-apply-sql.test.mjs
node --test ops/private-review-producer-binding/production-apply.test.mjs
node ops/private-review-producer-binding/verify-docker-local.mjs --pg16
node ops/private-review-producer-binding/verify-docker-local.mjs --pg17
node scripts/verify_private_card_ledger_docker_local.mjs --local-only
node scripts/verify_private_card_ledger_docker_local.mjs --local-postgres17
node ops/private-review-producer-binding/production-apply.mjs --template
```

Both Docker verifiers mount this checkout read-only, disable networking and
host ports, and remove their disposable containers. CI runs the isolated
`producer-binding-one-migration` job and the broader private card ledger job.

The template contains an invalid actor placeholder and is **not** an approval.
Only `--validate --approval /absolute/canonical.json` is available as a
copy/paste review command. The production `--apply` form is deliberately not
shown here; a separate exact user authorization is mandatory.
