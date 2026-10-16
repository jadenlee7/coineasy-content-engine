# Standalone Babylon readiness trial — local proposal

Trial `7b27e277-0641-4aef-b739-761f86547287` is Babylon-only, maximum one natural
daily-slot initial claim. Readiness fixes one server-observed whole-second
anchor at or after `2026-10-16T19:45:03Z`. It must be inserted within 60 seconds,
match the actual current KST day, and end exactly two hours later within that
same day. There is no hard-coded KST calendar date. No row is inserted by the
installation proposal; the new table is empty, private, RLS-enabled and has no
runtime-role privileges. Existing anchors, grants, jobs, output and budgets are
not reset or reused.

`standalone_babylon_trial.rollback.sql` defaults to rollback. It preserves both
existing routine signatures, OIDs, owners, ACLs, security-definer settings and
search paths, guarded by exact preimage body hashes:

- `claim_exact_natural_review_draft_job`: `0fc3dfa2ffa4b9e4619996c0faa28b2af5719f8c40f660eceedd48291dd68152`
- `get_quality_banner_generation_context`: `57249c5210b32ead5e529a5dad3eab1c9213563550f3e50a09fd1083ff419e2b`

Only the new exact worker gains an anchor branch. The new worker's quality
context is window-gated for both attached-media and explicit-empty-media logo
mode. Every previous worker's behavior remains byte-equivalent after removing
the added branches. Latest source, 24-hour freshness, recent active feed,
natural slot, initial-attempt, exact request/input/style-pack and duplicate
checks remain unchanged. The existing source recorder rejects replies/RTs but
does not persist quote flags. Accordingly the new producer must inspect the
complete live timeline, exclude only proven replies/RTs, and stop on a newest
quote before recording/queueing; it must never substitute an older post. SQL
still rejects a source if a different newer persisted source exists. This is
not a new DB-side standalone attestation.

Run all predecessor proofs followed by this suite in a disposable local PG17:

```sh
node scripts/verify_ybo_standalone_local.mjs --local-only
```

For the existing PG17 CI service, run
`python ops/ybo-standalone-babylon/standalone_local_unittest.py --local-only`
after `ops/ybo-readiness-babylon/readiness_local_unittest.py`. This checks exact
compile/rollback, wrong preimages, preservation, future-date admission,
freshness/identity/expiry, exclusive concurrent insert/claim, media/logo paths,
ACL/RLS/immutability and old-worker compatibility. Fixture clocks never enter
the production proposal. Local proof does not authorize installation,
readiness insertion, activation, generation, spending or sending.
