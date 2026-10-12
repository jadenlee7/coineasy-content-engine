# Signup-free final queue: local compatibility proof

The hosted private-review actor foreign keys use `content_ops_review_principals`,
not `auth.users`. The local legacy fixture originally inserted both rows. The
separate test-only principal fixture reproduces the relevant hosted constraints:
seven actor FKs, reviewer `(workspace_id, actor_id)`, identity
`(workspace_id, bot_binding, human_binding, actor_id)`, nonzero principal IDs and
principal immutability. No historical or production migration is changed.

Prepare the normal disposable PG16 full-schema database through all final queue
proposals, then clone it to a fresh `coineasy_*_test` database. Both verifiers
require explicit `--local-only`, a `/tmp` or `/private/tmp` UNIX socket, and that
test database name. They discard inherited connection options and credentials.
Never point them at a hosted database; network hosts are rejected.

With `PGHOST`, `PGPORT`, `PGUSER` and `PGDATABASE` explicitly set for the clone:

```sh
node scripts/verify_final_queue_signup_free_local.mjs --local-only
node scripts/verify_final_queue_signup_free_concurrency_local.mjs --local-only
```

The first command reuses the current 16-scenario atomic-queue fixture while
removing its sole reviewer Auth seed, then checks all four client receipts and
zero Auth rows for every principal. It rolls back all fixture changes. Negative
checks reject Auth-only actors, cross-workspace reviewers, wrong bot/human
bindings and principal mutation.

The second command seeds one synthetic card in the expendable clone, holds its
item lock, and starts two independent exact-callback transactions. It must
observe both sessions waiting on a real lock before release, then prove identical
receipts (`reused=false/true`) and exactly one decision, approval, publication,
publish job and Typefully slot, with two handoffs and no reviewer Auth row.
This concurrency seed is committed and retained in the clone; use a fresh clone
for another run. It performs no provider or production call.

Verified locally on PostgreSQL 16.13: 16 scenarios, four clients, and two-session
same-card serialization passed, including the final Typefully reviewer/identity
revocation and review state/epoch refusal checks. CLI safety tests reject omitted
opt-in, network hosts and ordinary database names before opening psql. This new
concurrency proof covers the same card and callback key, not every different-card
daily-slot race.

This is a relevant FK-subset proof, **not** a full hosted PostgreSQL 17 catalog,
role/ACL, release-hash, callback authentication, runtime mount, route identity,
provider delivery, or activation proof. It omits the separate banner-request FK
and the hosted edit-function replacement because neither is exercised by atomic
final queue admission. The original hosted migration's catalog seals must not be
disabled to make a local full-schema PG17 test pass. Exact hosted compatibility
and least-privilege execution remain separate pre-deployment checks.
