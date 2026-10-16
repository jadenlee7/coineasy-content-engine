# Exact standalone Babylon recovery successor

The proposal extends the existing append-only recovery to one additional exact
outbox/version pair. It installs no grant and performs no original-row, owner,
budget, source, copy, asset, job, approval, publication, schedule or role write.

1. Read `preflight.sql` against the owner database and retain the receipt. Both
   historical outboxes must still be expired `claimed` rows with no begin/send,
   review, owner, card or part. Recovery tables must be empty. Do not call the
   ordinary claim/reconcile/begin RPCs: they can mutate expired original claims.
2. Audit the exact approved SQL and its SHA-256. The proposal pins all nine
   predecessor recovery function bodies, discovers the two old CHECK names by
   exact definitions, keeps their original tuple, and adds only the new tuple.
   Function OIDs/signatures/owners/ACLs/search paths, untouched bodies, table
   ACL/RLS, grant triggers, both original rows and both anchor tables are checked
   again inside the same transaction. Any drift rolls the transaction back.
3. Apply once only through a journaled owner operator. An ambiguous result is
   readback-only; the unchanged preimage digest and old constraints must not be
   force-restored to permit retry. This proposal deliberately rejects replay.
4. After exact-SHA OFF deployment and fresh live source/candidate/readiness
   proof, separately register one immutable `private-review-recovery@1` grant.
   The plan pins the existing outbox/version/item/source/job/asset/PNG, both
   original copy hashes, old trial/plan, current release/destination policy,
   canonical original outbox digest, and a new UTC whole-second <=2h window.
   Use the existing canonical helper, not the PostgreSQL JSON text digest.
5. Preserve the singleton grant limit and old claim. Use the recovery courier
   once, with all normal/trial/public flags OFF. Require all four confirmed
   delivery parts plus the recovery terminal before claiming delivery. Restore
   the prior courier commands and all recovery flags OFF; no automatic retry.

The protected original standalone trial is `7b27e277-0641-4aef-b739-761f86547287`;
new delivery does not reset/extend it or complete its old `send=reserved` phase.
The original outbox remains `claimed` even after a recovery terminal is recorded.
Do not apply unrelated pending Railway changes as part of release/cleanup.

## Disposable test boundary

Run `python ops/private-review-recovery-successor/run_local.py --local-only`.
It creates a PostgreSQL 17 cluster in a new `/private/tmp` directory with its own
Unix socket and TCP disabled, creates one unique database, and stops/removes only
that cluster. It never connects to the existing `/private/tmp:5432` server.
`--ci-only` is restricted to GitHub Actions' disposable PG17 service and creates
a new unique database. Both modes print the actual PostgreSQL version.

Tests apply the production proposal unchanged for OFF installation/preimages/
ACLs and verify that synthetic copy fails the real fixed copy pins. Positive
grant tests explicitly replace only the two fixed copy-hash literals in the
in-memory SQL with hashes of the inherited fixture's `fixed telegram`/`fixed x`.
No production SQL file is rewritten. This is structural grant/ownership proof,
not evidence of production copy bytes, current source freshness, hosted schema,
runtime activation or Telegram delivery. Those require fresh owner readbacks.
