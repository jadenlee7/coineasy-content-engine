# CoinEasy Content QA connector

This local Grok/Cursor plugin connects the `CoinEasy Content QA` agent to the
production Content Studio through a dedicated least-privilege bearer token.

It exposes exactly three tools:

- list up to five non-mock `needs_review` items;
- read one exact current version with sanitized copy, official source URLs,
  automated QA, and an in-band banner preview;
- submit one durable advisory PASS/WARN/BLOCK verdict to the fixed private
  Content Ops relay destination.

The connector cannot approve a Studio item, publish to Telegram or X, use
Typefully, choose a chat destination, read raw submitted source text, or obtain
signed storage URLs. The bearer value belongs in the plugin configuration UI,
never in a prompt, Routine, screenshot, repository, or chat message.
