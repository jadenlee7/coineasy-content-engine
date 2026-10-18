# Babylon reply exclusion — local preparation, 2026-10-18

## Confirmed failure and narrow repair

The separately authorized 2026-10-17 no-send diagnostic completed one X
collection (two GETs, four normalized posts) and failed `post_not_reply`.
The deployed XClient returns explicit boolean classification flags even when
the provider request asks to exclude replies/retweets. The result proves the
current failed predicate, not the contents of earlier unretained responses.

`core/automation/babylon_source_intake.py` now validates the entire bounded
batch before excluding explicitly classified replies and retweets. Missing or
non-boolean flags, malformed IDs, duplicate IDs, invalid previous cursors,
rows at/before the cursor and more than 100 rows fail closed. Malformed rows
are not hidden just because they would be excluded. Quotes remain candidates;
ordering, source objects and media are not modified.

Only Babylon's `OfficialXDailyRunner._intake_client` uses this new policy.
Shared XClient, Squid and OriginTrail policies are unchanged. Both persisted
payloads and dry-run selection use the accepted result; the next cursor is
computed only from accepted posts. Reply-only/retweet-only polls retain the
previous cursor. Accepted payloads explicitly include false reply/retweet
flags. Errors expose only fixed predicate codes, not source content.

Completeness, freshness, duplicate-delivery, human approval and OFF controls
are unchanged. This does not make the expired existing banner sendable.

## Consumed attempts and separate operator preparation

The operator folder's existing source-refresh and no-send diagnostic helpers,
fixed intents, results and consumed journals were not edited or reopened.
The separate `babylon-reply-filter-preparation.mjs` is a pure builder/parser:
it verifies exact policy and source-code hashes, embeds the same policy before
the old collection predicates and preserves every existing guard. It has no
CLI, provider/DB call or live retry entry point. Its generated source string
is preparation for a future source-only operation, not permission to run one.
The old source freshness guard still prevents use with the expired banner.

## Verification

- Focused Python selection/intake/X/repository regression: 241 passed.
- Full Python suite: 6,005 passed; five existing deprecation warnings.
- Independent review requested a stronger non-dry-run mock assertion for
  malformed excluded rows. Added it and reran all nine new runner integration
  cases: nine passed, proving validation stops before the mock record call.
- Full JavaScript suite: 882 passed, zero failed, three opt-in tests skipped
  (Managed Auth live, managed-inspect browser, PostgreSQL inspect integration).
  Initial sandbox run could not bind local loopback test servers; the local
  loopback-enabled rerun passed. No production endpoints were invoked.
- Operator preparation/diagnostic regressions: 33 passed, including normal
  and optimized Python, all old guards, source hashes, journal preservation,
  quotes, mixed/excluded-only input and malformed/cursor cases.
- `git diff --check`: passed. Independent code/AST/parser review: no blocker.

Commands used the existing local test environment; no dependencies were
installed or production settings changed. Hosted CI, Docker builds, push,
PR, merge and deployment were not performed by this local-only change.

## Boundary before another attempt

This turn performed no live X collection, source refresh, production DB write,
generation, activation, employee send or public publication. Existing banner
and ledger state were not accessed for mutation; OFF settings were not changed.
The last live protected-state readback belongs to the prior diagnostic, not a
new runtime verification today.

Before any actual retry or send, notify the operator and establish a fresh,
separately authorized scope. Never replay consumed attempts or send the expired
banner. A future rollout needs exact-release verification and a fresh source/
duplicate check; local regression success alone does not authorize it.
