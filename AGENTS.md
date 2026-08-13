# CoinEasy Content Engine agent rules

These instructions apply repository-wide to Codex, Grok Bot, and other coding or
review agents.

- Treat official client posts, approved Figma references, and repository brand
  manifests as style evidence. They do not authorize invented facts, metrics,
  partnerships, dates, links, or product claims.
- Keep the current primary source as the factual boundary. Verify source facts
  and final output claims separately. If either cannot be verified, report
  `BLOCK`; never silently fill the gap.
- Preserve each client's own visual system and Korean GTM tone. Do not copy one
  client's design language into another client's output.
- Never approve, publish, enable publication feature flags, rotate credentials,
  or change production secrets unless the operator explicitly authorizes that
  exact action. Agent review is advisory and cannot replace human
  `double-fact-check@1` attestation.
- Never put Telegram invite links, chat IDs, access tokens, API keys, cookies, or
  private source assets in prompts, logs, commits, screenshots, or review
  comments.
- Keep `squid-korea-gtm` out of scope. It is a separate repository.
- Before proposing a merge, run the relevant JavaScript and Python tests plus
  `git diff --check`. Report failed or skipped verification explicitly.

For Grok Bot review and Telegram collaboration, follow
`docs/GROK_AGENT_REVIEW_RUNBOOK.md`.
