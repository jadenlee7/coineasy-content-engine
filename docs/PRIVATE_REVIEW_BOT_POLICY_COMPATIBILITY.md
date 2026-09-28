# Existing review-bot compatibility (code only, default OFF)

## Why this change is needed

The existing callback owner verifies button signatures with an opaque
`policy.room_binding`. The new private-card courier originally signed with the
DB room HMAC instead. Both values serve different purposes: they are not
interchangeable. Separately, the chosen existing review bot may already be an
administrator in the private employee room, while the courier's default
preflight requires a member.

Changing the existing callback policy or demoting its bot can affect other
registered reviews and bot functions. This change preserves both and adds an
explicit, validated destination projection to the courier only. It neither
configures production nor authorizes a send.

## Contract

`CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON` is optional trusted server
configuration, never taken from a card, callback, source, Telegram response,
or user-supplied job. Its exact fields are:

| Field | Requirement |
| --- | --- |
| `schema` | `existing-review-bot-policy@1` |
| `bot_id` | Positive integer matching the configured bot token ID |
| `chat_id` | Private-supergroup numeric ID matching the configured destination |
| `room_binding` | Exact opaque signing scope from the existing callback policy; 1–128 ASCII letters, numbers, underscore or hyphen |
| `membership_status` | Exactly `member` or `administrator`, pinned to the independently verified existing role |

No reviewer list, credential, publication destination or extra field is
accepted. Duplicate JSON keys, malformed/empty configuration and identity
mismatches fail closed; they never fall back to the old mode. When the variable
is absent, the previous HMAC signing scope and member-only check are unchanged.
The projection does not include private source bodies and must not be printed
in logs, PRs or operator receipts.

Only button MACs use the projected opaque scope. Card registration, edit
replies, DB lookups and packet receipts retain the existing keyed numeric
bot/room/message bindings. Callback namespace stays `ce1:` and the six controls
remain private-only, with no publish/approve action. The callback-owner code,
reviewer allowlist, signing/edit keys and DB schema are not changed.

Telegram preflight still requires the exact `coineasy_review_bot` username and
token ID, exact private supergroup ID, no public usernames or linked channel,
and the exact configured role. Administrator is not an automatic fallback;
member/admin drift, creator, restricted, left and kicked all fail the applicable
role check. No bot permission API, webhook or second poller is added. The
general internal-relay rule forbidding administrator status is unchanged.

## Test plan and evidence boundaries

| Area | Test type | Required coverage |
| --- | --- | --- |
| Policy parsing | Unit, malformed and boundary inputs | Exact schema/identity, duplicate keys, empty/unknown fields, redacted errors |
| Consumer signature | Contract with existing ingress parser/signer | All six private buttons accepted with owner's opaque scope; DB digest rejected as signing scope |
| Bot/room role | Mock HTTP | Pinned admin accepted; default admin denied; role drift and public/wrong destinations denied before send |
| Card lifecycle | Synthetic owner/courier integration | Four-part registration retains HMAC room identity and packet hash binding |
| Runtime/image | Unit and network-disabled CI container | Same projection reaches candidate and sender; validate-only has zero external I/O; OFF bootstrap remains credential-free |

These tests do not prove live callback handling, latest-source eligibility,
authenticated runtime release SHA, or real Telegram delivery. Those remain
separate acceptance checks after explicit rollout approval.

## Operator handoff (not executed by this PR)

After an explicitly approved merge/deployment, project only the existing
callback policy's bot ID, chat ID and opaque room binding, plus its separately
verified role, into the new courier. Do not rotate keys, alter the source
policy, change bot permissions or copy its reviewer list. Use memory/stdin
secret handling; never write live identifiers or values into repository files.

Preserve feature OFF, cron absent and public publishers OFF. Validate exact
image/runtime/release SHA and credential boundaries; obtain authenticated
Netlify runtime release proof without silently provisioning broad Studio
access. Then recheck source freshness/current version and the existing callback
owner's enabled state. A successful OFF/validate-only check is not evidence
that callbacks are enabled. Only a separately approved exact-version private
canary may send; no automatic retry after ambiguous delivery.
