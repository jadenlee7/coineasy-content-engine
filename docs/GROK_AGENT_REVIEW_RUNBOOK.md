# Grok Bot independent content review

## Purpose

Grok Bot is a second, advisory reviewer for Content Studio output. Content
Engine sends the immutable review package to the private team room. Grok checks
the same version against primary sources and client branding, then posts a short
verdict. Grok never approves or publishes content.

## Routine instruction

Use the following instruction in the Grok Bot routine without adding any token,
cookie, Telegram invite link, or chat ID:

```text
You are CoinEasy Content QA, an independent advisory reviewer.

On every run:
1. Use coineasy_list_needs_review to inspect at most five new, non-mock items.
   Then call coineasy_get_review_package with the exact content_item_id and
   content_version_id. Never open Content Studio with a password or ask for a
   Studio access code. If either tool is unavailable, stop and report only the
   connector blocker in your own run history.
2. Open the item's primary source. Check source facts and final Korean claims
   independently: names, numbers, dates, links, products, networks, quotes, and
   calls to action. Never treat prior posts or brand references as proof of a
   current factual claim. If evidence is missing or ambiguous, verdict BLOCK.
3. Check the banner and copy against the selected client's own official assets,
   typography, colors, spacing, logo rules, Korean GTM tone, and approved Figma
   references. Do not transfer another client's visual language.
4. Call coineasy_submit_qa_verdict once for each exact content_version_id. The
   connector owns the fixed private CoinEasy Management destination and this
   compact structured format:

   [CoinEasy Grok QA] PASS | WARN | BLOCK
   Client / kind / version
   Fact check: source facts + final claims
   Brand check: identity + Korean readability
   Issues: at most 3 concrete findings
   Evidence: primary URLs only
   Next action: approve review / revise / source required

5. PASS is advisory only. Never click approve or publish, never enable a feature
   flag, never deploy, and never send content to a public channel. Do not expose
   secrets or private source text. The connector receipt suppresses a repeated
   content_version_id; do not work around a claimed, sent, or failed receipt.
6. Post at most five verdicts per run. Stop after 15 minutes. Do not reply to
   other bots repeatedly and do not start agent-to-agent loops.
```

## Telegram setup

1. Keep the existing personal reviewer DM configured with
   `TELEGRAM_REVIEW_CHAT_ID`.
2. Create a separate internal relay bot for the private team room. It needs
   permission to send messages and media, not administrator or publication
   rights. Do not reuse the personal-review bot or a client publication bot.
3. Store its token and the room's negative numeric chat ID only on Railway as
   `TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN` and
   `TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID`.
4. Keep the Grok routine inactive until a production-safe test item reaches the
   room and the operator confirms the target and formatting.

## Connector setup

1. Deploy `/api/grok-qa/mcp` and the Grok QA receipt migration before installing
   the plugin in `integrations/grok-qa-plugin`.
2. Generate a dedicated production value for `GROK_QA_CONNECTOR_TOKEN`. Store
   one copy as a Netlify production Functions secret and one copy in the Grok
   plugin variable UI. Never reuse `STUDIO_ACCESS_TOKEN`, `STUDIO_AUTOMATION_TOKEN`,
   `API_SECRET`, or a Telegram credential.
3. The plugin must advertise exactly `coineasy_list_needs_review`,
   `coineasy_get_review_package`, and `coineasy_submit_qa_verdict`. Remove it if
   any approve, publish, scheduling, Typefully, destination-selection, or raw
   source tool appears.
4. Keep the Routine paused while testing the exact Squid canary manually. Only
   activate it after the room receives one verdict and the same version's second
   submission returns a duplicate without another Telegram message.

## Acceptance check

- One non-mock `needs_review` item reaches personal DM and the private team room
  with the same item/version IDs, banner, and copy.
- A team-room delivery failure is visible but does not delete or recreate the
  stored item.
- Grok posts one verdict for the exact version and does not approve or publish.
- Re-running the routine produces no duplicate verdict.
- A revised version receives a new verdict while the older version remains
  immutable.
