# Squid failed draft recovery

This runbook covers two narrowly allowlisted cases: a current-day Squid
official-X Daily News job exhausted all three attempts with either
`squid_visual_localization_incomplete` or
`squid_copy_discovery_unavailable`, but failed before Content Studio stored any
content item, version, asset, or Grok QA outbox row. Copy-discovery failures are
eligible only after the normal bounded retry budget is fully exhausted at 3/3;
the recovery still permits at most one additional exact-job generation call.

It is not a retry button and it is not a backfill mechanism.

## Safety contract

- The failed `jobs.id`, request UUID, source item, daily KST slot, source-state
  owner, and immutable style-reference pack remain unchanged.
- The recovery claim leaves `attempts == max_attempts == 3`. A generation
  failure therefore remains terminal and cannot return to the generic queue.
- A private grant permits exactly one targeted claim. A replay, concurrent
  caller, expired grant, or second recovery UUID cannot call the provider.
- The source must still be the current KST-day reservation, less than 24 hours
  old, and the newest recorded post in the active `@SquidRouter` feed. The feed
  must have a recent successful poll and a cursor covering that source; stalled
  intake cannot authorize recovery.
- Any existing catalog item/version/source link/Grok outbox row closes this
  path. That state requires read-only reconciliation, not another generation.
- The result can only become `needs_review`. Recovery creates no approval,
  publication, Telegram delivery, Typefully draft, or public post.

The original terminal failure is preserved in the immutable recovery grant.
Successful completion merges the normal receipt into the existing job output;
a second failure is stored separately as `recovery_failure`.

## Three separate operator gates

All commands require exact UUIDs. None searches for “latest” or selects a job.

Run them only inside the already deployed Official X container. Identify the
active production deployment instance and its exact commit first, then use
`railway ssh --service <service> --environment production
--deployment-instance <instance-id> <command>`. Do not use `railway run`: it
executes the local checkout with remote variables and is not artifact evidence.
The published Netlify production deploy must also be a linked-Git build of the
same commit. Its build command stamps Netlify's read-only build `COMMIT_REF`
into the Functions bundle; the authenticated capabilities endpoint reports that
immutable value. A manual artifact deploy or operator-set runtime variable is
not equivalent release evidence.
If the cron container is not active, creating a temporary one-shot service from
the exact image/SHA is a separate deployment approval; do not substitute a
local process.

### 1. Inspect (read-only)

```bash
python -m scripts.run_squid_failed_draft_recovery inspect \
  --job-id JOB_UUID \
  --recovery-id RECOVERY_OPERATION_UUID \
  --approval-id APPROVAL_RECEIPT_UUID \
  --approved-by OPERATOR_ID \
  --approved-at ISO_8601 \
  --expires-at ISO_8601_WITHIN_TWO_HOURS \
  --release-sha EXACT_DEPLOYED_GIT_SHA
```

The output contains bounded IDs and hashes only. Review the complete
`approval_subject` and preserve its `approval_subject_sha256`. It contains no
source text, source media URL, provider response, or secret.

### 2. Authorize (no claim or generation)

This is a production state change and needs a separate explicit approval. The
runtime must expose `OFFICIAL_X_RECOVERY_RELEASE_SHA` equal to the inspected
release SHA. It also fails closed unless Railway injects
`RAILWAY_ENVIRONMENT_NAME=production` and `RAILWAY_GIT_COMMIT_SHA` equals that
same SHA; an operator-set label alone is not deployment evidence.

```bash
python -m scripts.run_squid_failed_draft_recovery authorize \
  --job-id JOB_UUID \
  --recovery-id RECOVERY_OPERATION_UUID \
  --approval-id APPROVAL_RECEIPT_UUID \
  --approved-by OPERATOR_ID \
  --approved-at ISO_8601 \
  --expires-at ISO_8601_WITHIN_TWO_HOURS \
  --release-sha EXACT_DEPLOYED_GIT_SHA \
  --subject-sha256 INSPECTED_SHA256
```

Authorization records one immutable grant and an audit event. It does not
change the failed job, call Netlify/Railway, or enqueue Grok.

### 3. Run once

This is a provider-affecting production action and needs its own explicit
approval after authorization is verified.

```bash
python -m scripts.run_squid_failed_draft_recovery run-once \
  --job-id JOB_UUID \
  --recovery-id RECOVERY_OPERATION_UUID \
  --release-sha EXACT_DEPLOYED_GIT_SHA \
  --subject-sha256 INSPECTED_SHA256
```

`run-once` skips X polling, all four-client intake, normal queueing, and the
generic worker drain. It consumes the grant and invokes the existing generation
path once for the exact leased job. Before the grant is claimed, the worker
authenticates to Netlify and requires its build-stamped release SHA to equal the
same Railway release SHA. The generation request repeats that check and carries
the expected SHA.

## Verification

After a successful run, verify independently:

1. The original request UUID is the new `content_items.id`.
2. Exactly one new immutable `content_version_id` exists and is current.
3. Exactly one source link and one private Grok QA outbox row exist.
4. The content remains `needs_review`.
5. Approval and publication counts remain zero.
6. Any Grok QA canary targets the new content-version UUID, not the request UUID.

If generation succeeds but completion is uncertain, do not run the recovery
again. The consumed grant is the duplicate-provider fence. Inspect the catalog
and job ledger read-only, then use a separately reviewed reconciliation path.

## Explicit exclusions

This path does not reset attempts, delete or move the KST slot, clear the
source-state owner, create a sibling job/request UUID, replace the style pack,
enable recurring workers, update Grok release configuration, send Telegram,
approve content, or publish anywhere.
