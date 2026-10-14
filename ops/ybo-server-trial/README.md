# Rollback-only exact natural claim proof

These are synthetic disposable-database tests, **not an automatic production
migration or installation command**. The prior exact function was installed once
with the superseded window. This package now proves a time-only rewindow candidate.

`exact-natural-claim.rollback.sql` preserves the installed signature, owner intent,
service-role ACL and all checks, changing only its two fixed time constants to
**2026-10-14T06:22:17Z–2026-10-14T08:22:17Z**. It ends in `ROLLBACK`.
Rewindow candidate function-body SHA-256:

`04524f35cbb1496dc08282e1548a4acc9b23b32ba288863b001cb7a792fe28c3`

Prior hosted body SHA-256:
`e319cfa49556c7d70837a01693a2a751fd986aea22b81adfdc499ed6784c5005`.
This file is not proof that the candidate has been applied to the hosted RPC.

Never run the fixture SQL, fixture clock or test harness against production.
The harness requires an explicit disposable database confirmation plus a local
Unix socket or the fixed loopback CI service. Its filename prevents collection
by the ordinary Python test suite. It rejects alternate service/host routes.

The dedicated `ybo-trial.yml` workflow uses disposable PostgreSQL **17.6**. It
compiles the untouched rollback SQL first, then substitutes only the three clock
calls in a second local-only copy so future-window positive and negative cases
can run without changing the deployed body. It covers concurrent claims,
existing-row invariance, stale source/feed, window expiry after lock/validation,
role/argument checks, and Python versus real `jsonb::text` hash equality.

Run only after bootstrapping the disposable dependency schema exactly as the
workflow specifies:

```sh
python ops/ybo-server-trial/exact_claim_local_unittest.py --local-only
```

The workflow has read-only repository permissions and no deployment/provider/
Telegram or production database credentials. CI proof is not hosted runtime or
delivery proof. Local PostgreSQL 16.13/17.11 claim tests passed during preparation;
the new digest comparison and exact 17.6 CI require their own successful result.
