# Fixed-window YBO private courier — local implementation

This does not activate a service, install a schedule, or send a message. It is a
one-time Oct 14, 2026 UTC 12:44:51–14:44:51 (KST 21:44:51–23:44:51)
private delivery consumer, not the recurring
daily workflow. The callback bot and official publication remain OFF.

## Hosted entrypoint

The existing isolated `Dockerfile.private-review-card` now packages
`scripts.run_trial_private_review_cards`. Its default command is unchanged.
The optional `ops/content-ops/railway.trial-private-card.json` selects:

```text
pre-deploy: python -m scripts.run_trial_private_review_cards --validate-only
start:      python -m scripts.run_trial_private_review_cards
restart:    NEVER
schedule:   none
```

`CONTENT_OPS_TRIAL_COURIER_ENABLED` defaults to `false`: the ordinary start path
reads only that flag and exits disabled, with no network, DB or credential read.
Validate-only checks the existing image stamp/runtime/source configuration and
exact policy locally, without constructing HTTP clients or senders. It does not
verify live membership or grant permission. Before the fixed window an enabled
process waits without I/O; after the deadline it exits. Every effect also checks
the shorter deadline from the immutable manifest.

Retain the existing private courier credentials and identity. No API secret,
database key or provider key is added to this service. Existing signing/binding
keys are used for the established owner receipts; the delivery-only packet does
not mint callbacks. The existing policy's exact private destination and pinned
bot role are unchanged.

## Configuration contract (values are not logged)

- Existing source/image/runtime SHA validation and `CONTENT_OPS_REVIEW_RELEASE_SHA`.
- Existing `CONTENT_OPS_REVIEW_MODE=daily` and
  `CONTENT_OPS_REVIEW_PACKET_MODE=daily_button_card_v1` are retained for the
  established isolated configuration validator. The dedicated transport sends
  only wire mode `trial`, packet `trial_delivery_only_v1`, and exact version IDs;
  it never invokes daily wildcard discovery.
- `CONTENT_OPS_REVIEW_ENABLED`, `CONTENT_OPS_BUTTON_CARD_ENABLED` and
  `CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED` stay `false` in the courier.
- `CONTENT_OPS_TRIAL_ID`, `CONTENT_OPS_TRIAL_PLAN_SHA256` and
  `CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256` pin the approved owner plan.
  Destination policy hash is SHA-256 of sorted compact ASCII JSON for the existing
  `CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON`. It is not the image estimate-policy hash.
- Netlify uses these same three pins, `CONTENT_OPS_TRIAL_GATEWAY_ENABLED`, its
  current published release and the existing dedicated gateway token. All four
  general gateway/review flags stay literally `false`.
- Only Netlify uses its existing explicit `RAILWAY_API_URL` and `API_SECRET` to
  resolve the API durable owner/manifest. The API's
  `YBO_TRIAL_DESTINATION_POLICY_SHA256` must equal the same destination hash.

No flag, date or request header creates authority. The root-owned API controller
must already have admitted the durable trial, bound each completed generated
artifact and reserved its review/send phase before publishing its immutable
manifest. Paid generation continues to use the existing account-bound $10
estimated daily ledger; this courier never reserves spend or calls a provider.

## Exact delivery and stop behavior

The authenticated, read-only `content-ops-trial-state` endpoint exposes only a
fresh active manifest or exact pending-readiness 409. The courier polls only the
exact pending result. Invalid, stale, inactive, expired or ambiguous responses
stop execution. There is no retry of an uncertain send or acknowledgment.

The manifest allows one Yellow, Babylon and OriginTrail entry each, at most
three. Job/request/source/item/version and source/banner/Telegram/X hashes are
bound. Netlify separately compares the existing DB projection before every
owner operation; the existing SQL owner repeats its freshness/current-version
guards. The Python sender additionally resolves fresh owner state immediately
before every Telegram HTTP call. It cannot cancel a request already in flight.

Each card has four existing durable reserve-before-send parts: canonical PNG,
Telegram copy, X copy and delivery-only controls. Controls truthfully state
`needs_review`, callback bot OFF and public posts held. They have only a source
URL button, not functioning edit/approve buttons. Structural checks are not
human visual/factual approval.

Only a validated existing DB terminal receipt (`sent`, exact card/outbox,
four confirmed parts) triggers Netlify's close-only API acknowledgment. The API
closes the already-reserved send phase and turns the durable trial OFF after all
listed entries complete. A lost ACK remains unknown and never reopens a send.
The courier's `invocation_dispatch_enabled=false` means this process stopped;
it is not a claim that shared environment flags have changed. Root owner status
is the independent global terminal proof. After completion all trial flags may
still need an explicitly authorized configuration cleanup; time/owner gates
already forbid another trial admission or delayed effect.

## Local verification

- 148 Python tests: trial runtime, static packet, existing owner/canary/sender,
  gateway/candidate and daily-runtime regressions.
- 111 JavaScript tests: exact trial projection, resolver, state endpoint,
  close-only receipt and existing review gateway.
- `git diff --check` passed.

All transports were mocked; no production DB/provider/Telegram calls occurred.
Image build, exact deployed release, server configuration, owner admission and
actual staff delivery are separate rollout evidence, not claimed here.
