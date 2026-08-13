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
1. Open the CoinEasy Content Studio team library and inspect only new, non-mock
   items with status needs_review. Use content_version_id as the deduplication
   key. If login is unavailable, stop and report the blocker only in your own
   run history.
2. Open the item's primary source. Check source facts and final Korean claims
   independently: names, numbers, dates, links, products, networks, quotes, and
   calls to action. Never treat prior posts or brand references as proof of a
   current factual claim. If evidence is missing or ambiguous, verdict BLOCK.
3. Check the banner and copy against the selected client's own official assets,
   typography, colors, spacing, logo rules, Korean GTM tone, and approved Figma
   references. Do not transfer another client's visual language.
4. In the private CoinEasy Management Telegram room, reply once for each new
   content_version_id using exactly this compact format:

   [CoinEasy Grok QA] PASS | WARN | BLOCK
   Client / kind / version
   Fact check: source facts + final claims
   Brand check: identity + Korean readability
   Issues: at most 3 concrete findings
   Evidence: primary URLs only
   Next action: approve review / revise / source required

5. PASS is advisory only. Never click approve or publish, never enable a feature
   flag, never deploy, and never send content to a public channel. Do not expose
   secrets or private source text. If the same content_version_id was already
   reported, do nothing.
6. Post at most five verdicts per run. Stop after 15 minutes. Do not reply to
   other bots repeatedly and do not start agent-to-agent loops.
```

## Telegram setup

1. Keep the existing personal reviewer DM configured with
   `TELEGRAM_REVIEW_CHAT_ID`.
2. Add the same review bot to the private team room. It needs permission to send
   messages and media, not administrator or publication rights.
3. Obtain the room's negative numeric chat ID through the Bot API and store it
   only on Railway as `TELEGRAM_COLLAB_REVIEW_CHAT_ID`.
4. Keep the Grok routine inactive until a production-safe test item reaches the
   room and the operator confirms the target and formatting.

## Acceptance check

- One non-mock `needs_review` item reaches personal DM and the private team room
  with the same item/version IDs, banner, and copy.
- A team-room delivery failure is visible but does not delete or recreate the
  stored item.
- Grok posts one verdict for the exact version and does not approve or publish.
- Re-running the routine produces no duplicate verdict.
- A revised version receives a new verdict while the older version remains
  immutable.
