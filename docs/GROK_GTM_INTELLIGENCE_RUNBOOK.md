# Grok GTM intelligence inbox shadow runbook

## Purpose and current state

This runbook evaluates a future read-only Korean GTM intelligence inbox across
`ops`, `telegram_triage`, and `x_narrative_qa` without granting any action
authority.

**Current state:** local validation, rendering, bounded in-memory list/get, and
pure composition of saved, sanitized owner-projection fixtures. There is no
authenticated owner reader, live upstream connector, HTTP/MCP endpoint,
service credential, schedule, deploy, or provider connection. “Grok” names the
intended future reader; the first 14-day Squid shadow pilot makes no Grok, xAI,
or other provider call.

The governing decision is
`docs/ADR-021-read-only-gtm-intelligence-inbox.md`. Existing Grok QA and Harmony
runbooks continue to govern their own systems and remain unchanged.
The latest non-mutating owner-reader gap audit is
`docs/GTM_OWNER_READER_READINESS_2026-08-25.md`.

## Non-authority statement

The GTM intelligence inbox may explain what a human should inspect. It cannot:

- approve, acknowledge, claim, assign, decide, retry, or close work;
- call a model/provider or submit an advisory QA verdict;
- send Telegram, Buzz, email, X, Typefully, Naver, or any other message;
- publish, schedule, deploy, restart, or change a feature flag;
- mutate Railway, Netlify, Supabase, Content Studio, Harmony, or a bot;
- insert, update, delete, migrate, or write a receipt to a database;
- poll Telegram, register a webhook, or advance an update offset;
- retrieve or expose raw logs, credentials, private links, or private source
  payloads.

If an operator wants any of these actions, stop this runbook and use the owner
system's separately approved procedure.

## Scope of the first pilot

| Setting | Fixed value |
| --- | --- |
| Duration | 14 consecutive calendar days |
| Client allowlist | `squid` only, enforced by the Phase 0 CLI and broker |
| Domains | Exactly `ops`, `telegram_triage`, `x_narrative_qa`; no extension |
| Seed cursor | `next_cursor=null`; partial or paginated seeds rejected |
| Mode | Saved-fixture `shadow_read_only` or local sanitized source-bundle projection |
| Provider calls | 0 |
| External actions | 0 |
| Message sends | 0 |
| Publications | 0 |
| Deployments | 0 |
| Railway mutations | 0 |
| Database writes | 0 |
| New Telegram consumers | 0 |

Other clients, cross-client patterns, and historical backfills are out of
scope. An empty or unavailable source is valid and must appear as
`unobserved`; it is not a reason to widen access.

## Required source boundaries

### `ops`

Use only a separately reviewed safe health projection. Allowed fields are
bounded status/reason codes, observation timestamps, freshness, exact release
SHA where already safely exposed, aggregate counts, and receipt hashes.

Phase 0 does not contact Railway or a receipt owner and therefore cannot prove
that a supplied deployment SHA, runtime state, schedule tick, count, or receipt
is current or authentic. Treat hashes as consistency checksums only and compare
every claimed current/receipt fact with the authoritative owner surface during
the daily review.

`source_receipt_sha256` must exactly match a `runtime_receipt` evidence
checksum. This is a consistency binding only; it does not prove that the
receipt exists, belongs to the claimed run, or came from Railway.

Do not read Railway raw logs, environment variables, request bodies, process
output, deployment controls, or mutation APIs. The projection must not receive
a Railway write token.

### `telegram_triage`

Read only downstream of the existing owner consumer. Do not call
`getUpdates`, create another poller, register a webhook, read or change the
offset, or clear the owner's buffer.

The projection may contain only:

- a closed broad-topic code;
- a closed safety or escalation code;
- an opaque non-reversible item reference;
- a sanitized bounded Korean question summary;
- answer and FAQ-match state;
- an FAQ-bound reply draft that always requires human review.

The source adapter additionally requires `question_observed_at` separately
from the later owner projection time, an explicit safety class, and a canonical
FAQ receipt when a draft exists. The receipt binds the opaque question
reference, exact FAQ source checksum, FAQ match class, and exact draft hash and
is retained as typed evidence. Omitted safety classification, Telegram-like
6–19 digit identifier runs, altered drafts, stale question evidence, or FAQ
receipt mismatches fail closed.

`digest_scheme` must equal `hmac-sha256-v1`. The `question_ref` is the keyed
HMAC over owner-controlled canonical correlation material and must not be a raw
hash of message text, a chat/message ID, or a user identifier. The HMAC key
never enters a fixture, output, log, reference, or operator note. The question
evidence reference must bind to the same keyed-HMAC value. Phase 0 validates
the scheme marker, shape, and cross-binding but cannot recompute authenticity
without the owner key. This provides pseudonymous correlation, not a signature
or proof that the owner message exists.

It must not contain Telegram user/chat/message IDs, usernames, display names,
callback data, invite links, private message links, raw update payloads,
verbatim excerpts, raw answers, DM text, bot tokens, wallet data, or hashed user
identifiers. Sanitized question wording must contain no handle, phone, wallet,
URL, or private identifier.

### `x_narrative_qa`

Use only claimed current public-X signals in the Squid review scope: the Squid
official source, configured public competitor/KOL accounts, and exact content
QA projection. A public X URL must have the exact form
`https://x.com/{handle}/status/{numeric_post_id}` with no query, fragment,
credentials, trailing path, or alternate host. The URL path handle must always
equal `source_account`. `official_source` and `content_qa` additionally bind to
the configured Phase 0 Squid official handle `SquidRouter`; `competitor` and
`kol` retain their own public source account. Content item/version references,
content/banner checksums, timestamps, and bounded review status must remain
internally consistent.

For content QA, the content checksum and any banner checksum must bind to exact
evidence entries. A non-pending verdict requires `qa_receipt_sha256` and an
exact `qa_receipt` evidence binding; a pending verdict must not claim one.

A completed verdict also requires `qa_receipt_subject_sha256` over the source
URL, content item/version, content checksum, optional banner checksum, verdict,
and issue codes. The receipt time must not precede any bound source, content,
or banner observation. `qa_receipt_sha256` must equal the deterministic digest
of a versioned envelope containing that subject checksum, preventing reuse of
one receipt value for a different QA subject. Pending QA cannot carry a receipt or receipt subject.
Owner-record title, summary, claim, and comparison text pass the privacy,
secret, and prompt checks before projection, not only at final rendering.

The URL binding does not prove that a post exists or that its content is true.
Content/banner hashes are checksums, not signatures. Verify the official post
and every claimed current content item/version in the owner surface during the
daily review.

Community demand, style references, performance signals, and model output may
prioritize human review but cannot become factual evidence. Existing Grok QA
tools, verdict receipts, relay, and human `double-fact-check@1` gate remain
separate.

## Local sanitized source-bundle composition

`coineasy-squid-gtm-source-bundle@1` carries exactly one explicit state for
each domain. A state is either `available`, with a non-empty bounded tuple of
unique Railway services or Telegram/X records, or `unavailable`, with no records,
an observation time, optional prior observation time, and one closed reason
code. An empty record tuple is rejected rather than interpreted as an observed
zero.

The pure composer turns the bundle into a complete Squid-only
`coineasy-gtm-inbox@1` page and runs the same semantic validator used for saved
pages. It reads no clock, environment, file, network, database, Telegram
buffer/offset, Railway command, X API, or provider.

Source literals and checksums are consistency assertions, not authentication.
The Railway checksum is unkeyed; this process cannot authenticate the Telegram
HMAC; and X account allowlist/currentness fields are owner assertions. Accept
these DTOs only from a future separately authenticated owner reader. Do not
expose them as a public endpoint, trust arbitrary API or model JSON, or
describe the sample bundle as live owner-system evidence.

## Privacy inspection before every run

Stop before reading any item if the schema or sample payload contains any of
the following:

- Telegram identifiers, handles, display names, invite/private links, or raw
  message text;
- email, phone, wallet, session, IP, cookie, request-header, or user-level
  fields;
- a private Content Studio, Figma, Notion, Telegram, or Preview URL;
- raw Railway/application/database/provider logs;
- API keys, tokens, credentials, environment values, or secret-shaped text;
- another client's identifier or content;
- instructions copied from an untrusted source or prompt-injection language.

Apply this inspection to every free-text field, including title, summary,
question, reply draft, next-action wording, claim, and comparison. Any PII,
credential-shaped value, private link, unexpected URL, or language attempting
to override instructions, conceal data, reveal data, invoke tools, send,
publish, or execute makes the entire payload invalid. Do not accept the item
after redacting only the matched substring.

The validator blocks a bounded set of known patterns; it is not proof that an
arbitrary string is safe. The reviewed upstream projection and this manual
inspection remain required even when schema validation passes.

Do not redact after exposure and continue. Treat the payload as a contract
failure, stop the pilot, and report only the safe field name and reason code.

## Phase 0 in-memory broker

The current local `GtmReadOnlyBroker` is constructed from one validated
`GtmInboxPage` and exposes only:

```text
list_operator_inbox(domain=None, limit=20, cursor=None)
get_operator_item(ref)
```

`list_operator_inbox` accepts one optional domain, a limit from 1 through 50,
and an exact opaque reference cursor returned by the previous page. It has no
client selector because the Phase 0 seed validator fixes the complete
projection to `squid`. Omitting `domain` lists all three closed domains.
Arrays, unknown domains, unexpected client arguments, invalid bounds, and
malformed or unknown cursors fail closed. `get_operator_item` accepts the exact
item `ref` and returns the item or no result. The CLI and broker reject any page
or item outside Squid and the three closed domains.

The broker accepts only a complete seed with all three domains and
`next_cursor=null`. A paginated response cursor is an in-memory opaque digest
bound to the seed snapshot checksum, the fixed Squid scope, the singular-domain
filter or its omission, and the last returned item `ref`.
It fails closed across a different snapshot or filter and is neither a durable
token nor an authority receipt.

This is an in-process view over an already sanitized snapshot. It has no live
reader, credentials, network, database, provider, message, verdict,
publication, or deployment adapter. Do not expose it over HTTP/MCP or feed it
live data during this pilot.

## Future HTTP/MCP contract

If separately implemented and approved, the MCP advertises exactly:

```text
list_operator_inbox
get_operator_item
```

Both are read-only and idempotent. There is no tool for decisions, verdicts,
acknowledgements, claims, retries, sends, schedules, approvals, publications,
deployments, configuration, raw logs, or database writes.

Expected list input:

```json
{
  "domain": "ops",
  "limit": 20,
  "cursor": null
}
```

Omit `domain` to list all three domains. There is no client selector; the
projection is fixed to Squid. Do not send a `domains` array or client field.

Expected detail input:

```json
{"ref":"exact-operator-item-ref"}
```

The server must reject unknown fields, client selectors, domain arrays,
unknown domains, limits outside the tested bound, malformed cursors, and item
references not returned by the current Squid-scoped projection.

During the local shadow pilot these calls are represented by validated static
fixtures. Do not install an MCP plugin, add an upstream connector, or connect
Grok as part of this runbook.

## Local shadow commands

These commands parse only the named local file. They do not call Railway,
Telegram, X, a database, or a model provider.

```bash
PYTHONPATH=. .venv/bin/python scripts/run_gtm_intelligence.py \
  --input examples/gtm-intelligence-squid-shadow.json \
  --snapshot-json

PYTHONPATH=. .venv/bin/python scripts/run_gtm_intelligence.py \
  --input examples/gtm-intelligence-squid-shadow.json \
  --dashboard

PYTHONPATH=. .venv/bin/python scripts/run_gtm_intelligence.py \
  --input examples/gtm-intelligence-squid-source-bundle.json \
  --source-bundle \
  --dashboard

PYTHONPATH=. .venv/bin/python scripts/run_gtm_intelligence.py --print-schema

PYTHONPATH=. .venv/bin/python scripts/run_gtm_intelligence.py \
  --print-source-schema

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_gtm_intelligence.py \
  tests/test_gtm_intelligence_sources.py
```

`--print-schema` emits the Phase 0 schema, including a Squid-only item `const`,
`next_cursor=null`, per-domain `contains` constraints, and the
`x-coineasy-phase0` semantic-validator metadata. The renderer independently
calls the same Phase 0 validator before producing a dashboard.

The source-bundle example is synthetic contract data, not a live Railway,
Telegram, X, Content Studio, or QA receipt. Do not replace either fixture path
with a raw Telegram export, Railway log, database dump, or private provider
payload. If the input supplies
`item_sha256` or `snapshot_sha256`, the validator recomputes the canonical
checksum and rejects a mismatch. Snapshot output contains item and page
checksums for comparison; these are not signatures or receipts that authorize
action.

## Observation semantics

An observed zero requires an explicit authoritative measurement for the stated
window. Missing, stale, failed, blocked, rate-limited, or inaccessible data
uses `status=unobserved` plus `coineasy-unobserved-detail@1`, where
`observed_count` is always `null`. Never convert it to zero for totals,
percentages, rankings, summaries, or charts.

For `ops`, an observed healthy runtime requires `failure_count=0`; an observed
degraded or failed runtime requires `failure_count>=1` plus a safe failure code;
an unobserved runtime requires `failure_count=null` and no failure code.
Scheduled observations require bounded `schedule_interval_seconds` and
`schedule_grace_seconds`. The last/next tick interval and the `on_time`, `late`,
or `missed` window must agree with the item observation. `not_scheduled` and
`unobserved` cannot carry ticks or interval values.

A derived envelope count such as zero validated inbox items is not a claim that
the source had zero pending items. Record the source as `unobserved` whenever
that distinction cannot be proved.

## Freshness and truth boundary

Every item `observed_at` and every evidence `observed_at` must be no older than
24 hours at page `generated_at`. Timestamps more than five minutes in the
future also fail, and evidence cannot be later than its item beyond that same
clock-skew allowance. A stale item or stale evidence record invalidates the
page; do not keep the item and add a warning.

Phase 0 proves only schema validity, Squid/domain binding, freshness, privacy,
and internal consistency. Item/page/content/banner/evidence/receipt hashes are
consistency checksums, not signatures or attestations. Phase 0 cannot prove:

- the current Railway deployment SHA, runtime state, or schedule state;
- that an external receipt exists or belongs to the claimed run;
- that a Content Studio item/version is still current;
- that an X post exists, remains unchanged, or states a true fact.

Verify every such claim manually in its authoritative owner surface on every
pilot day. If the owner surface is unavailable or ambiguous, record the claim
as `unobserved` and stop that day's pass evaluation.

## Day 0 preflight

Complete every check before starting the 14-day clock:

- [ ] ADR-021 and this runbook reviewed by the CoinEasy operator.
- [ ] The CLI and broker reject every client other than `squid`.
- [ ] The CLI and broker accept only `ops`, `telegram_triage`, and
      `x_narrative_qa`, and the daily page accounts for all three.
- [ ] The Phase 0 seed has `next_cursor=null`; it is not a partial broker page.
- [ ] Telegram data comes downstream of the existing sole owner consumer.
- [ ] No second `getUpdates` call, poller, webhook, or offset writer exists.
- [ ] Telegram has `digest_scheme=hmac-sha256-v1`; question/evidence references
      use the same keyed HMAC; no raw identifier, unhashed ID, HMAC key, private
      link, raw text, or user-level field appears in fixtures or output.
- [ ] Telegram safety classification is explicit; question time is distinct
      from projection time; every FAQ draft has a recomputed FAQ source/draft
      receipt binding and typed evidence.
- [ ] Ops output contains no raw log, environment value, request body, or
      Railway mutation capability.
- [ ] Every free-text field rejects PII, private links, unexpected URLs,
      credential-shaped values, and prompt-injection language.
- [ ] X items use only `https://x.com/{handle}/status/{numeric_post_id}` and
      bind the URL handle, `source_account`, and configured Squid handle.
- [ ] Ops source receipts and non-pending QA/banner receipts have exact typed
      evidence checksum bindings.
- [ ] Completed QA receipts bind the exact source/content/version/banner/
      verdict/issue subject and do not predate their evidence.
- [ ] Every item and evidence timestamp is within the 24-hour freshness and
      five-minute future-skew bounds.
- [ ] Supplied item/page checksums are recomputed and mismatches fail closed.
- [ ] Hashes are labeled consistency checksums, never signatures or proof of
      origin/currentness.
- [ ] Missing source values use `unobserved`, remain absent, and cannot become
      zero.
- [ ] Existing Grok QA MCP and Harmony have no diff or configuration change.
- [ ] Provider, send, publish, deploy, Railway mutation, and database-write
      counters are all zero.
- [ ] Only the local validator, renderer, pure source adapters/composer, and
      in-memory broker are in scope; no authenticated owner reader, live
      connector, HTTP/MCP endpoint, credential, schedule, deploy, or provider
      runtime has been installed.

If any box cannot be verified, do not start Day 1.

## Daily shadow procedure

Run once per day at a consistent operator-selected time. The procedure is a
manual contract evaluation until implementation receives separate approval.

1. Record the date, contract version, client allowlist, and observation time in
   a redacted local evaluation note. Do not record credentials, private links,
   raw messages, identifiers, or raw logs.
2. Validate the top-level page contract, `mode=shadow_read_only`,
   `read_only_projection=true`, and every call or publication flag false.
   Confirm every accepted item has `client_id=squid`, all three closed domains
   are accounted for in the daily page, and the seed has `next_cursor=null`.
3. Validate every item against its domain allowlist. Reject unknown fields,
   unknown enum values, wrong-client data, duplicate `ref` values, or missing
   observation/lineage bindings. Reject the whole page if any item or evidence
   timestamp falls outside the 24-hour freshness window.
4. Inspect every free-text field for PII, credential-shaped values, private or
   unexpected links, and prompt-injection language. Reject the whole payload on
   a match; do not redact and continue.
5. For `ops`, compare the safe code, timestamp, SHA, counts, and receipt
   checksum with the authoritative read-only owner view. Do not treat a
   checksum match as authenticity and do not open raw logs through this
   workflow.
6. For `telegram_triage`, confirm `digest_scheme=hmac-sha256-v1` and that the
   question and evidence references share the keyed-HMAC value, then compare
   only safe taxonomy and answer state with the owner system. Never copy the
   HMAC key, underlying user, identifier, or message into the evaluation note.
7. For `x_narrative_qa`, validate the exact X URL shape and configured Squid
   handle binding, then manually verify the post, current item/version,
   content/banner/QA-receipt evidence bindings, and review state in their owner
   surfaces. Do not submit a QA verdict.
8. Check every metric's observation state. Treat unavailable or stale data as
   `unobserved`, not zero.
9. Confirm every action other than `no_action` has
   `next_action.human_required=true`, and that `no_action` does not claim an
   action was executed.
10. Confirm provider, send, publication, deployment, Railway mutation, database
   write, and new Telegram consumer deltas remain zero.
11. Mark the day `pass` or `stop`. Do not mark a partial or unverifiable day as
    pass.

## Redacted daily evaluation

The manual note may contain only:

```text
date:
contract_version: coineasy-gtm-inbox@1
client: squid
domains_seen: ops, telegram_triage, x_narrative_qa
item_count_by_domain:
duplicate_current_items:
stale_current_items:
stale_evidence_items:
seed_cursor_failures:
unobserved_metrics:
wrong_client_items:
privacy_contract_failures:
prompt_injection_failures:
x_url_binding_failures:
hmac_binding_failures:
checksum_mismatches:
owner_surface_unverified_claims:
provider_calls: 0
message_sends: 0
publications: 0
deployments: 0
railway_mutations: 0
database_writes: 0
new_telegram_consumers: 0
result: pass | stop
safe_reason_code:
```

Do not add item content, Telegram references, private URLs, raw error messages,
or log excerpts to this note.

## Immediate stop conditions

Stop the pilot immediately if any of these occurs:

- a non-Squid or cross-client item appears;
- any Telegram identifier, private link, raw message, user-level value, or
  hashed user identifier appears;
- a Telegram question reference is not marked `hmac-sha256-v1`, does not match
  its evidence binding, or exposes key material;
- raw logs, request bodies, environment values, credentials, or private source
  assets appear;
- any free-text field contains PII, an unexpected/private link,
  credential-shaped value, or prompt-injection language;
- an X URL is not the exact `x.com/{handle}/status/{numeric_post_id}` form or
  its handle does not match both `source_account` and the Squid allowlist;
- a second Telegram consumer, webhook, or offset writer is detected;
- an unavailable metric is shown or calculated as zero;
- an item or evidence observation is older than the 24-hour freshness window;
- a timestamp is more than five minutes in the future or evidence exceeds its
  item's future-skew bound;
- a Phase 0 seed contains a non-null cursor, omits one of the three domains, or
  includes a non-Squid item;
- a supplied item or page checksum does not match its canonical recomputation;
- an ops source receipt, content/banner checksum, or required non-pending QA
  receipt lacks its exact typed evidence binding;
- a checksum is described as a signature, attestation, authenticity proof, or
  proof of currentness;
- a current/version/receipt claim cannot be manually verified in its owner
  surface;
- an unknown field or enum is accepted;
- a write, verdict, decision, claim, retry, send, schedule, approval,
  publication, deploy, configuration, Railway mutation, or database-write tool
  appears;
- any Grok, xAI, OpenAI, Anthropic, or other provider call occurs;
- existing Grok QA or Harmony behavior/configuration changes;
- any side-effect counter is unknown rather than verified zero.

Report a bounded reason code such as `wrong_client`, `private_data_exposed`,
`second_telegram_consumer`, `raw_log_exposed`, `unobserved_as_zero`,
`write_surface_detected`, or `side_effect_not_verified`. Do not include the
offending private value.

## Pause and recovery

Because the pilot has no executor or database writer, its normal rollback is
to stop reading and leave owner systems unchanged.

1. End the daily run and mark it `stop` with a safe reason code.
2. Do not retry with broader credentials, raw data, a different client, or a
   direct Telegram/Railway connection.
3. Verify separately that existing Grok QA, Harmony, Telegram owner consumers,
   Content Studio, and Railway configuration were not changed.
4. If a future read route or credential exists, ask the operator for separate
   incident authority before disabling a deployment or revoking it. This
   runbook itself authorizes no mutation.
5. Correct the contract in a reviewed code change, rerun privacy and no-write
   tests, and restart a new 14-day pilot from Day 1 only after explicit approval.

## Completion gate

The pilot completes only after 14 passing daily evaluations with:

- Squid-only scope and all three domains represented when observed;
- zero private-data, wrong-client, raw-log, and duplicate-current-item events;
- zero keyed-HMAC, prompt-injection, X-URL binding, and 24-hour freshness
  failures;
- zero seed-cursor, future-skew, checksum-recomputation, and typed-evidence
  binding failures;
- all unavailable metrics preserved as `unobserved`;
- current/version/receipt claims manually verified in the authoritative owner
  surfaces on all 14 days;
- all hashes treated only as consistency checksums;
- human-required next actions only;
- verified zero provider calls, sends, publications, deployments, Railway
  mutations, database writes, and new Telegram consumers.

Completion authorizes only a review of the results. It does not authorize MCP
installation, Grok/provider connection, a scheduler, another client, a durable
ledger, a write tool, or any external action. Each requires a separate decision
and explicit operator approval.

## Separate Telegram v2 local contract check

ADR-022 adds a separate reader-derived v2 object and intake receipt. It does
not change this Phase 0 saved-page pilot, which remains v1-only. Run only the
local contract test:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_gtm_intelligence_telegram_v2.py
```

Do not replace the test snapshot with arbitrary JSON or call a v2 projector
with a bare projection. A valid input must represent one atomic read snapshot
of the stream row, current event index, source index, promotion marker, intake
marker, and sanitized gate. Every snapshot boundary field must be explicit.
The receipt builder revalidates that full snapshot and derives its own triage
item; it does not accept a rehydrated eligible object or caller-supplied item.
The local receipt repository remains disabled by default, requires a literal
boolean, and its enabled test mode accepts only the locked process-memory
store. A prepared receipt is not persistence proof; the append method performs
exact process-memory readback internally. Its created/reused
boolean is flow control, not durable evidence.

Stop if any evidence object is missing or changed, a v1/v2 version pair is
mixed, raw/private data appears, the append lacks exact readback, or any Redis
client, consumer group, ACK, database, network, provider, Telegram, deploy, or
publication surface is introduced. Zero-event promotions remain unobserved
until a separate ordered source-control receipt exists.

### Producer-owned golden fixture shadow

ADR-023 adds a stricter offline path using fixture bytes generated by
`coineasydaily` and vendored into this repository. Run a single scenario with
its adjacent lock:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_gtm_telegram_v2_shadow.py \
  --fixture \
  tests/fixtures/vendor/coineasydaily/telegram_v2/v1/one_emitted.json \
  --lock \
  tests/fixtures/vendor/coineasydaily/telegram_v2/v1/LOCK.json
```

The same command may select `hundred_emitted.json` or `hundred_mixed.json`.
The CLI verifies the lock, producer manifest, and all three vendored fixture
byte hashes before extracting the selected six-object snapshot. A successful
result must retain:

```text
input_kind=locked_vendor_fixture
vendor_lock_verified=true
producer_fixture_provenance_verified=true
live_atomic_redis_snapshot_observed=false
receipt_prepared=true
receipt_persisted=false
exact_readback_observed=false
source_acknowledged=false
production_wiring_observed=false
```

`producer_fixture_provenance_verified` is true only for the vendored bundle
whose byte hashes are pinned to reviewed producer merge commit
`0ffce811d2cad55bc7083d20c055801687927657`. It does not claim live input or
Redis observation. `source_commit_ref=commit:<64 hex>` is an intake content
identity, not a Git SHA. The fixture's internal `atomic_snapshot=true` is
synthetic contract input and is not evidence of a live Redis read.

The legacy `--input` form remains available only for an asserted naked local
snapshot. It deliberately reports `input_kind=asserted_local_v2_snapshot` and
cannot claim vendor or producer provenance.

Run the complete local GTM contract set with:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_gtm_intelligence.py \
  tests/test_gtm_intelligence_sources.py \
  tests/test_gtm_intelligence_telegram_v2.py \
  tests/test_gtm_intelligence_telegram_v2_shadow_cli.py \
  tests/test_gtm_intelligence_telegram_v2_golden_contract.py
```

Do not replace vendored files with raw Telegram exports or live Redis output.
Any fixture, manifest, lock, ordered-member, or six-object mutation must fail
closed without echoing the supplied content. Updating the fixture bundle
requires a separate review that regenerates producer bytes first, vendors them
byte for byte, and updates the lock. The normal consumer suite must never read
a sibling producer checkout.
