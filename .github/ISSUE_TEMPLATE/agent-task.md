---
name: Agent implementation task
about: A bounded work order for Devin, Claude Code, Codex, or Grok Build
title: "[agent-task] "
labels: []
assignees: []
---

## Authorization

- Work order ID:
- Scope SHA-256:
- Status: `planning_only`
- Proposed owner:
- Proposed independent reviewer:
- Expires at:

This issue template is a planning packet, not an approval receipt. No agent may
edit, push, create a PR, deploy, send a message, or call a provider from it.

## Objective

State one measurable outcome.

## Immutable scope

- Repository:
- Exact base SHA:
- Branch:
- Allowed paths:
- Evidence:
- Client or company objective:

Only the named owner edits this branch. Other agents review read-only.

## Forbidden external actions

- [x] Merge
- [x] Branch push
- [x] Draft PR creation
- [x] Preview deployment
- [x] Production deployment
- [x] Production database write
- [x] Credential change or disclosure
- [x] Paid provider or model call
- [x] Public/customer message
- [x] Publication

## Acceptance and verification

- Acceptance criteria:
- Exact focused tests:
- `git diff --check`

## Required handoff

Return scope questions, path conflicts, test feasibility, and a proposed local
implementation plan. Do not edit files. Execution requires a future durable
approval receipt and a separate authorized work order state.
