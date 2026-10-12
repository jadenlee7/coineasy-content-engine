# Quality-first local validation receipt

Date: 2026-10-12. Scope: local changes only, no production operation.

## Latest initial quality-banner to staff-card integration

Current engine baseline: `20dd17bdf0150b075762207f7b9c5ab5a530af59`.
The later integration changes are local/uncommitted. The review-bot checkout
and its existing exact engine dependency pin were not changed in this increment.

Verdict: **LOCAL INITIAL-CARD CONNECTION VERIFIED / HOSTED ROLLOUT NOT DONE**.

- Requested twelve-file Python acceptance: **598 passed** (193 quality service,
  API, provider, persistent image/request-journal tests; 405 natural producer,
  private-review/runtime, vision, Korean-GTM and template regressions).
- Four additional API scope/tutorial/file/retention regression files: **27
  passed**. Total across the sixteen tested files: **625 passed**, with three
  existing deprecation warnings. This is not a complete repository/hosted-CI run.
- Netlify handler/quality-path JavaScript: **57 passed** (46 legacy and 11
  quality cases). Full mocked handler and catalog replay use real native opaque
  2880px PNGs, including a >5MB fixture, without inline base64 automation output.
- Disposable socket-only PostgreSQL16 proof passed with all 62 historical
  migrations unchanged plus the read-only context proposal. Three clients ×
  nine rejection cases; real natural completion and the existing initial-card
  reconciler register exactly one review row. No additional jobs, approvals or
  publications. Temporary server stopped and socket removed.
- `git diff --check` passed after the implementation and focused checks.

Final review found and repaired two issues before this acceptance: a retry
could switch between quality and legacy paid paths, and private source/style/
guidance markers could reach the copy prompt. A shared v2 immutable mode fence
now blocks either switch, including when the stable guard is disabled; recursive
prompt validation rejects before copy reservation/model invocation. Regression
tests exercise concurrent opposite modes, restart, pending GET-only resume,
old-schema refusal and zero network/reservation on forbidden prompt content.

All provider requests were mocked; SQL and SQLite checks were disposable local
fixtures. No live image generation, production SQL/provisioning/configuration,
deploy/enablement, staff send or public send occurred. Hosted storage/ACL,
immutable public references, approved monetary policy, image build/provenance
and actual private card arrival/button behavior still require a controlled
rollout. Quality edit/revision admission and final-card triggering remain
separate incomplete work. See [the connection contract](QUALITY_NEWS_TO_STAFF_CARD.md).

## Earlier October 12 receipt (historical)

Historical receipt for the first October 12 increment. The later local
[independent release-pair implementation](CONTENT_OPS_RELEASE_PAIR.md)
supersedes this receipt's single-SHA release-block verdict. The incomplete
full-suite attempts below remain historical failures, not retroactive passes.

Engine checkout: `coineasy-daily-approval-integration`, baseline
`c32ee9d7c0df2d1a3aace02a945b08cf74eafbe2`. Changes are uncommitted.
Bot checkout: `coineasy-review-bot-integration`, baseline
`bde0646ba9655f3e5cef58899ed35a0b1c9043aa`. Its pre-existing unrelated dirty
docs/access-plan files were preserved.

## Observed checks

- New provider adapter plus persistent journal: **55 passed** after the scope
  binding repair. Python 3.11.15, mocked provider transport, synthetic references
  and a temporary private SQLite journal. No actual provider/production calls.
- Bot final route/startup focused suite: **14 passed**, including a distinct
  bot/engine release rejection before imports or DB factory construction.
  Same-SHA synthetic success is not a real cross-repository deployment proof.
- Bot complete suite after that guard: **97 passed, 1 existing subprocess
  timeout**, repeated on the full rerun. The isolated startup suite passed
  **21/21**, and the direct fail-closed command exited in 2.88s; the 10-second
  deadline was not weakened. Bot JavaScript **8/8**, TypeScript, exact-source
  vendor regeneration and package-hash verification passed. The bot full
  suite is not reported green for this final tree.
- Engine Python full-suite attempt: **2,211 passed, 1 failed**, then deliberately
  interrupted after 22m41s; remaining tests were not completed. The failure was
  the existing subprocess's 10-second deadline in
  `test_durable_ledger_survives_new_python_process`. Its isolated rerun passed
  **1/1** without changing code or the deadline. Do not report a green full suite.
- Engine JavaScript credential-free run (concurrency 2): **552 passed, 1 failed,
  3 skipped**. Initial sandboxed localhost-listen failures disappeared outside
  the sandbox. The remaining existing held-open FIFO timing test also failed
  in isolation; its test code was not changed.
- FIFO diagnostic, not a replacement acceptance test: the 1-second producer
  returned `secret_descriptor_empty`; a 5-second producer returned
  `secret_descriptor_timeout` in 329ms. This is consistent with delayed reader
  startup on this host; it is not counted as a pass for the original deadline.
- New Python modules compile; both checkouts pass `git diff --check`.

Python dependencies were installed into a disposable local venv; no system
dependency or production setting was changed. Local Docker build/run, hosted
CI, migration compatibility/ACL, current production configuration and real
Telegram callback acceptance were not established by these checks.

## Operational verdict

**LOCAL COMPONENTS VERIFIED / DAILY END-TO-END NOT CONNECTED / RELEASE BLOCK.**

The paid adapter is not imported by the natural generator. The final bot path
is default OFF and refuses the current incompatible cross-repository release
contract. The required source/request propagation, revision admission,
final-card trigger and signed release-pair work are recorded in
[the decision record](ADR_QUALITY_FIRST_DAILY_APPROVAL.md).

No push, PR, merge, deployment, migration, key/role change, schedule change,
activation, image charge, staff-room send or public send occurred in this
increment. A successful local test is neither recurring-spend authorization
nor permission to publish unapproved content.
