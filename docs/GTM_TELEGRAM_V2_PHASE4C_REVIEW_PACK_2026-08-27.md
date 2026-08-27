# Telegram GTM v2 Phase 4C local review pack

## Review outcome

Phase 4C is complete as a local, offline contract implementation. Producer PR
#261 was reviewed and merged at
`0ffce811d2cad55bc7083d20c055801687927657`. The consumer vendors those exact
deterministic synthetic bytes, verifies a commit-pinned lock and producer
manifest, and derives a Korean triage item plus a prepared, unpersisted intake
receipt.

This is not a production or delivery receipt. No live Redis snapshot, Telegram
read or send, second poller, source ACK, database write, provider call,
publication, or deployment occurred.

## Source state

| Repository | Branch | Base HEAD | Contract state | Reviewed contract SHA |
| --- | --- | --- | --- | --- |
| `coineasydaily` | `main` | `6f4a137b889a8d159a64d97924bb0ffef784aae9` | PR head `8667eb3f6d3e5ecc024c3caa3cf7ad5ed8428265`, merged and reviewed | `0ffce811d2cad55bc7083d20c055801687927657` |
| `coineasy-content-engine` | `codex/telegram-v2-consumer-shadow-phase4c-20260827` | `1018687ea438c08b1869175ec635b74cba3c1961` | prepared for separate Draft PR review | pending |

The producer base is the first parent of the merge receipt; the reviewed SHA
is the immutable merge commit whose tree contains the locked bytes. The
consumer base identifies the clean `main` revision beneath this separate
review and is not itself a Consumer contract receipt.

Producer `source_commit_ref=commit:<64 hex>` values inside fixtures are intake
content identities. They are not Git commit SHAs.

## Producer-owned evidence

Producer directory:

```text
coineasydaily/tests/fixtures/gtm_v2_golden
```

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `community/gtm_v2_golden.py` | 23,359 | `6accd90853882398e2fadd7af6054b852e2d5ba60f91b68d016068c6cbee1134` |
| `one_emitted.json` | 12,085 | `25da0ff96764ac5040a14f1d25b15e12dbb6bc4680fe8260815b6f3c13fb07cf` |
| `hundred_emitted.json` | 264,242 | `8241fae57a5c4efa985a08b69f52da7287f0964a4651a6372bd39dff29d59e45` |
| `hundred_mixed.json` | 141,039 | `056b9c35daabb205c6fa4fa42d74529d5d4dd4668a08bc44bbea172a973dc32b` |
| `manifest.json` | 2,966 | `8f683690a9e11ae0d0f9a83a44dc58a48620216b11bb6c7d9a7d8edf824231ab` |

The generator calls the actual projection builder, source validator, event
builder, and promotion builder. Inputs are deterministic and synthetic. The
fixture and manifest explicitly state:

```text
generated_from_live_data=false
live_redis_readback_observed=false
live_atomic_redis_snapshot_observed=false
source_acknowledged=false
production_wiring_observed=false
```

The scenarios cover one emitted update, 100 emitted updates, and 100 mixed
updates with 34 emitted, 33 tombstoned, and 33 not applicable. Both 100-update
scenarios select source ordinal 99. Zero emitted projections remain unobserved
because no stream-row fixture can prove a source-terminal outcome.

## Consumer vendor lock

Consumer directory:

```text
tests/fixtures/vendor/coineasydaily/telegram_v2/v1
```

The four producer files compare byte for byte with the vendored copies. The
canonical consumer `LOCK.json` is 1,632 bytes with SHA-256:

```text
76547ac2bef33bff97233c191cc8cdcaecae5212cbea8968ecffe19f7d98e178
```

The lock separates the producer PR base from the reviewed merge SHA, records
`merged_reviewed` source state, and pins generator metadata, all three producer
contract-file hashes, the upstream manifest, and every vendored fixture byte
length and hash. Normal consumer tests use only the vendored directory and do
not read or import the sibling producer repository.

## Deterministic local shadow results

| Scenario | Snapshot SHA-256 | Shadow result SHA-256 | Receipt SHA-256 |
| --- | --- | --- | --- |
| one emitted | `be59c19dbd411a05b3abe94697ca4a74146d666490480f652582aa0a16adfce0` | `e18b6312e08b631c9d79afe4ac6ed07b7cb31a044e28d4a7cc4cd7e649bcc700` | `85e55134c6c309a48a65fea3666822b64cccd7e28fdfff2674e4a141762e4119` |
| 100 emitted | `e2cc0ed6fc6edacc5228c02bb7bda886bb78a30d7e09ade7422df866bd9881d9` | `a164c680c8f4ec6ca7d1931a1328826a27482d8565318e80be5ddda5123e9cac` | `edcab639285a9d3ffed04990d64e3249fdcc6198e3bf243f0bd8ed8e83f4786e` |
| 100 mixed | `290aa6e75a563d8403be1559aa714b4660e5c17bf7e006f72f7f3e76a4a42efb` | `8525024e1841de1e4d83b70be66d4479da6ab9f3b6ef4aef8ee08a058a4135e6` | `9d57f87c09b67204426337d1e697e161f38c2f94f5e7b020c5c13c9b84f70237` |

Every result reports verified reviewed-merge provenance while deliberately
leaving live atomic observation false:

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

## Verification receipts

Producer focused suite and GitHub Redis matrix:

```text
269 passed
redis-6.2.24: pass
redis-7.4.11: pass
```

Consumer complete local GTM contract suite:

```text
265 passed
```

The consumer suite includes the prior v1 contracts, strict v2 reader and
receipt contracts, naked-snapshot CLI boundary, three producer golden replays,
raw-byte drift, lock/manifest mismatch, six-object tampering, cross-fixture
splicing, 100-member reorder and truncation, v1/v2 confusion, privacy canary,
and no-sibling/no-live-I/O checks.

The focused suites are the valid receipts for this phase. A repository-wide
suite is not claimed clean because the existing workspace has unrelated
Python 3.9/3.12 binary and optional-dependency incompatibilities.

## Review files

Producer:

- `community/gtm_v2_golden.py`
- `tests/test_gtm_v2_golden.py`
- `tests/fixtures/gtm_v2_golden/*.json`

Consumer:

- `scripts/run_gtm_telegram_v2_shadow.py`
- `tests/test_gtm_intelligence_telegram_v2_shadow_cli.py`
- `tests/test_gtm_intelligence_telegram_v2_golden_contract.py`
- `tests/fixtures/vendor/coineasydaily/telegram_v2/v1/*.json`
- `docs/ADR-023-producer-owned-telegram-v2-golden-shadow.md`
- `docs/GROK_GTM_INTELLIGENCE_RUNBOOK.md`

## Next approval gate

The producer-first gate is complete. The next safe gate is this consumer
commit and Draft PR review, with the reviewed producer merge SHA kept exact in
the lock. Deployment, live Redis wiring, durable receipts, consumer-group or
ACK design, production credentials, and Telegram operations remain separate
later approvals.
