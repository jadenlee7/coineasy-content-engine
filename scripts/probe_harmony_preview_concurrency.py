#!/usr/bin/env python3
"""Prove the disposable Harmony Preview slice with 64 real DB connections.

The probe is deliberately local/disposable only.  It seeds one immutable Squid
official-X review fixture, four revocable connector registrations, and a
five-role fixed-specialist roster.  It races signed request receipts, the plan,
private content, every durable Codex QA gate transition, the representative
inbox, Recap, and a separate failed-QA denial through 64 independent ``psql``
processes.  It then proves revocation removes currentness without deleting
history.  It never calls a provider, Buzz, approval, or publication routine.
The target database is expected to be discarded after the run.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable
import uuid


CLIENT_ID = "squid"
CONCURRENCY = 64
BRANCH_REF_LENGTH = 20
HEX_SHA40_PATTERN = re.compile(r"^[a-f0-9]{40}$")
HEX_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
CONNECTOR_REQUEST_DOMAIN = "coineasy:harmony:preview:connector-request:v1"
CONNECTOR_REQUEST_RPC = (
    "public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)"
)
CONNECTOR_REQUEST_DIGEST_VECTOR = {
    "workspace_id": "11111111-1111-4111-8111-111111111111",
    "client_id": "squid",
    "registration_id": "22222222-2222-4222-8222-222222222222",
    "connector_receipt_id": "33333333-3333-4333-8333-333333333333",
    "signal": {
        "attempts": 64,
        "automatic_publication": False,
        "client_id": "squid",
        "lane": "quiz_bot",
        "payload_sha256": (
            "a908c2820db28b21f5ef4caf467c3d0e"
            "ef274b96a5e35082d021834624b2e8c6"
        ),
        "producer_principal_id": "66666666-6666-4666-8666-666666666666",
        "schema_version": "agent-harmony-signal@1",
        "signal_id": "44444444-4444-4444-8444-444444444444",
        "signal_kind": "quiz_learning",
        "source_event_id": "55555555-5555-4555-8555-555555555555",
        "topic_codes": ["official_update", "퀴즈"],
        "workspace_id": "11111111-1111-4111-8111-111111111111",
    },
}
CONNECTOR_REQUEST_DIGEST_VECTOR_SHA256 = (
    "cfdf90b7d13d375ab4db44d32ab3fd11"
    "5f5c830ddf99d526251d1c642add9bb9"
)
DIRECT_SUPABASE_DB_HOST_PATTERN = re.compile(
    r"^db\.([a-z0-9]{20})\.supabase\.co$"
)


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith(
        ("/tmp/", "/private/tmp/")
    )


def _psql_child_environment(
    host: str,
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if source is None else source)
    if _is_local_host(host):
        return environment
    if environment.get("PGSSLMODE", "").strip().lower() != "verify-full":
        raise ValueError(
            "remote direct database requires PGSSLMODE=verify-full"
        )
    root_certificate = environment.get("PGSSLROOTCERT", "").strip()
    if not root_certificate:
        raise ValueError(
            "remote direct database requires explicit PGSSLROOTCERT trust"
        )
    if root_certificate.lower() != "system" and not (
        os.path.isabs(root_certificate)
        and os.path.isfile(root_certificate)
        and os.access(root_certificate, os.R_OK)
    ):
        raise ValueError(
            "remote direct database PGSSLROOTCERT must be system or "
            "an absolute readable file"
        )
    return environment


def _validated_disposable_preview_ref(
    host: str,
    port: int,
    expected_branch_ref: str | None,
    parent_project_ref: str | None,
) -> str:
    match = DIRECT_SUPABASE_DB_HOST_PATTERN.fullmatch(host.lower())
    if not match or port != 5432:
        raise ValueError(
            "disposable Preview mode requires the direct db.<branch-ref>.supabase.co:5432 host"
        )
    if not re.fullmatch(r"[a-z0-9]{20}", expected_branch_ref or ""):
        raise ValueError(
            "disposable Preview mode requires exact 20-character --expected-branch-ref"
        )
    if not re.fullmatch(r"[a-z0-9]{20}", parent_project_ref or ""):
        raise ValueError(
            "disposable Preview mode requires exact 20-character --parent-project-ref"
        )
    host_ref = match.group(1)
    if host_ref != expected_branch_ref:
        raise ValueError("direct database host does not match the approved Preview branch ref")
    if host_ref == parent_project_ref:
        raise ValueError("refusing to run the disposable Preview probe against Production")
    return host_ref


class Psql:
    def __init__(
        self,
        executable: str,
        host: str,
        port: int,
        user: str,
        database: str,
        timeout_seconds: float,
    ) -> None:
        self.command = [
            executable,
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            database,
            "-Atq",
        ]
        self.timeout_seconds = timeout_seconds
        self.environment = _psql_child_environment(host)

    def _execute(
        self,
        sql: str,
        *,
        timeout_seconds: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                self.command,
                input=sql,
                text=True,
                capture_output=True,
                check=False,
                env=self.environment,
                timeout=(
                    self.timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "psql_timeout_commit_state_unknown_no_retry"
            ) from exc

    def run(self, sql: str) -> str:
        result = self._execute(sql)
        if result.returncode:
            raise RuntimeError(
                f"psql_command_failed_rc_{result.returncode}: "
                "database_output=redacted"
            )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def expect_error(self, sql: str, expected: str) -> None:
        result = self._execute(sql)
        combined = result.stderr + result.stdout
        if result.returncode == 0 or expected not in combined:
            raise RuntimeError(
                "expected fail-closed database error was not observed: "
                f"expected={expected!r}, rc={result.returncode}, "
                "database_output=redacted"
            )

    def json(self, sql: str) -> dict[str, object]:
        try:
            value = json.loads(self.run(sql))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "psql_json_decode_failed: database_output=redacted"
            ) from exc
        if not isinstance(value, dict):
            raise RuntimeError(
                "psql_json_result_not_object: database_output=redacted"
            )
        return value


def _claims(
    *,
    workspace_id: str,
    branch_ref: str,
    role: str,
    capability: str,
    principal_id: str,
    release_sha: str,
    config_sha256: str,
    connector_id: str | None = None,
    attestation_registration_id: str | None = None,
    attestation_key_id: str | None = None,
    request_sha256: str | None = None,
    request_nonce: str | None = None,
) -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
    nonce = request_nonce or str(uuid.uuid4())
    claims: dict[str, object] = {
        "iss": "supabase",
        "aud": "authenticated",
        "sub": principal_id,
        "role": role,
        "workspace_id": workspace_id,
        "client_id": CLIENT_ID,
        "environment": "preview",
        "ref": branch_ref,
        "producer_principal_id": principal_id,
        "release_sha": release_sha,
        "config_sha256": config_sha256,
        "capability": capability,
        "jti": nonce,
        "iat": now,
        "exp": now + 3600,
        "automatic_publication": False,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
    }
    if connector_id is not None:
        claims["connector_id"] = connector_id
    attestation_values = (
        attestation_registration_id,
        attestation_key_id,
        request_sha256,
    )
    if any(value is not None for value in attestation_values):
        if not all(value is not None for value in attestation_values):
            raise ValueError("connector attestation claims must be supplied together")
        claims.update(
            {
                "attestation_registration_id": attestation_registration_id,
                "attestation_key_id": attestation_key_id,
                "request_nonce": nonce,
                "request_sha256": request_sha256,
            }
        )
    return claims


def _rpc_sql(claims: dict[str, object], expression: str) -> str:
    role = str(claims["role"])
    if role not in {
        "coineasy_harmony_connector",
        "coineasy_harmony_orchestrator",
        "coineasy_harmony_content",
        "coineasy_harmony_qa",
        "coineasy_harmony_operator",
        "coineasy_harmony_recap",
        "coineasy_harmony_dashboard",
    }:
        raise ValueError(f"unsupported Harmony role: {role}")
    return (
        "begin;\n"
        f"set local role {role};\n"
        "select pg_catalog.set_config('request.jwt.claims', "
        + _sql_literal(_compact(claims))
        + ", true);\nselect ("
        + expression
        + ")::text;\ncommit;\n"
    )


def _signal_body(
    *,
    workspace_id: str,
    signal_id: str,
    source_event_id: str,
    principal_id: str,
    signal_kind: str,
    lane: str,
    topic_codes: list[str],
    observed_at: str,
    expires_at: str,
    upstream_receipt_sha256: str,
    evidence_sha256: str,
    release_sha: str,
    config_sha256: str,
    extra: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "agent-harmony-signal@1",
        "signal_id": signal_id,
        "workspace_id": workspace_id,
        "client_id": CLIENT_ID,
        "signal_kind": signal_kind,
        "lane": lane,
        "source_event_id": source_event_id,
        "producer_principal_id": principal_id,
        "producer_release_sha": release_sha,
        "config_sha256": config_sha256,
        "upstream_receipt_sha256": upstream_receipt_sha256,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "evidence_sha256": evidence_sha256,
        "topic_codes": topic_codes,
        "content_factual_authority": signal_kind == "official_source",
        "raw_messages_included": False,
        "personal_data_included": False,
        "instructions_allowed": False,
        "advisory_only": True,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
        "automatic_publication": False,
        **extra,
    }


def _with_db_hash(psql: Psql, body: dict[str, object]) -> dict[str, object]:
    payload = psql.run(
        "select (value || pg_catalog.jsonb_build_object("
        "'payload_sha256', private.agent_json_sha256(value)))::text "
        "from (select "
        + _sql_literal(_compact(body))
        + "::jsonb as value) source;"
    )
    return json.loads(payload)


def _json_sha256(value: object) -> str:
    return hashlib.sha256(_compact(value).encode("utf-8")).hexdigest()


def _codex_work_key_from_lineage(lineage: dict[str, object]) -> str:
    """Mirror ``squid_codex_gate_work_key`` without assignment/time fields."""

    return _json_sha256({
        "client_id": lineage["client_id"],
        "content_snapshot_sha256": lineage["content_snapshot_sha256"],
        "official_content_version_id": lineage["official_content_version_id"],
        "official_source_binding_sha256": (
            lineage["official_source_binding_sha256"]
        ),
        "official_source_item_id": lineage["official_source_item_id"],
        "plan_id": lineage["plan_id"],
        "plan_receipt_sha256": lineage["plan_receipt_sha256"],
        "private_content_output_sha256": (
            lineage["private_content_output_sha256"]
        ),
        "private_content_receipt_sha256": (
            lineage["private_content_receipt_sha256"]
        ),
        "round_id": lineage["round_id"],
        "schema_version": "squid-codex-gate-work@1",
        "signal_input_set_sha256": lineage["signal_input_set_sha256"],
        "signal_manifest_sha256": lineage["signal_manifest_sha256"],
        "signal_producer_principal_ids": (
            lineage["signal_producer_principal_ids"]
        ),
        "stage": "independent_qa",
        "workspace_id": lineage["workspace_id"],
    })


def _codex_assignment_key(
    work_key: str,
    reviewer_binding_sha256: str,
) -> str:
    """Mirror ``squid_codex_gate_assignment_key`` exactly."""

    return _json_sha256({
        "reviewer_binding_sha256": reviewer_binding_sha256,
        "schema_version": "squid-codex-gate-assignment@1",
        "work_key": work_key,
    })


def _connector_request_payload(
    *,
    workspace_id: str,
    client_id: str,
    registration_id: str,
    connector_receipt_id: str,
    signal: dict[str, object],
) -> dict[str, object]:
    required_signal_text_fields = (
        "lane",
        "producer_principal_id",
        "signal_id",
        "signal_kind",
        "source_event_id",
    )
    if any(
        not isinstance(signal.get(field), str)
        for field in required_signal_text_fields
    ):
        raise ValueError("connector request signal identity fields must be text")
    signal_without_digest = {
        key: value for key, value in signal.items() if key != "payload_sha256"
    }
    signal_payload_sha256 = _json_sha256(signal_without_digest)
    supplied_signal_sha256 = signal.get("payload_sha256")
    if (
        supplied_signal_sha256 is not None
        and supplied_signal_sha256 != signal_payload_sha256
    ):
        raise ValueError(
            "connector request signal payload digest does not match "
            "the independent canonical hash"
        )
    return {
        "client_id": client_id,
        "connector_receipt_id": connector_receipt_id,
        "domain": CONNECTOR_REQUEST_DOMAIN,
        "lane": signal["lane"],
        "producer_principal_id": signal["producer_principal_id"],
        "registration_id": registration_id,
        "rpc": CONNECTOR_REQUEST_RPC,
        "signal_id": signal["signal_id"],
        "signal_kind": signal["signal_kind"],
        "signal_payload_sha256": signal_payload_sha256,
        "source_event_id": signal["source_event_id"],
        "workspace_id": workspace_id,
    }


def _connector_request_sha256(
    *,
    workspace_id: str,
    client_id: str,
    registration_id: str,
    connector_receipt_id: str,
    signal: dict[str, object],
) -> str:
    return _json_sha256(_connector_request_payload(
        workspace_id=workspace_id,
        client_id=client_id,
        registration_id=registration_id,
        connector_receipt_id=connector_receipt_id,
        signal=signal,
    ))


def _assert_connector_request_sha256_matches_database(
    psql: Psql,
    *,
    expected_sha256: str,
    workspace_id: str,
    client_id: str,
    registration_id: str,
    connector_receipt_id: str,
    signal: dict[str, object],
) -> None:
    value = psql.run(
        "select private.harmony_preview_connector_request_sha256("
        f"'{workspace_id}'::uuid, {_sql_literal(client_id)}, "
        f"'{registration_id}'::uuid, "
        f"'{connector_receipt_id}'::uuid, "
        + _sql_literal(_compact(signal))
        + "::jsonb);"
    )
    if not HEX_SHA256_PATTERN.fullmatch(value):
        raise RuntimeError("database returned an invalid connector request digest")
    if value != expected_sha256:
        raise RuntimeError(
            "independent Python and database connector request digests differ"
        )


def _assert_connector_request_digest_vector(psql: Psql) -> None:
    vector = CONNECTOR_REQUEST_DIGEST_VECTOR
    independent_sha256 = _connector_request_sha256(
        workspace_id=str(vector["workspace_id"]),
        client_id=str(vector["client_id"]),
        registration_id=str(vector["registration_id"]),
        connector_receipt_id=str(vector["connector_receipt_id"]),
        signal=vector["signal"],  # type: ignore[arg-type]
    )
    if independent_sha256 != CONNECTOR_REQUEST_DIGEST_VECTOR_SHA256:
        raise RuntimeError("independent connector request digest vector drifted")
    _assert_connector_request_sha256_matches_database(
        psql,
        expected_sha256=independent_sha256,
        workspace_id=str(vector["workspace_id"]),
        client_id=str(vector["client_id"]),
        registration_id=str(vector["registration_id"]),
        connector_receipt_id=str(vector["connector_receipt_id"]),
        signal=vector["signal"],  # type: ignore[arg-type]
    )


def _baseline_sql(workspace_id: str, content_version_id: str) -> str:
    return f"""
select pg_catalog.jsonb_build_object(
  'approvals', (select pg_catalog.count(*) from public.approvals),
  'publications', (select pg_catalog.count(*) from public.publications),
  'buzz_delivery_receipts', (select pg_catalog.count(*) from agent_runtime.buzz_delivery_receipts),
  'buzz_review_decisions', (select pg_catalog.count(*) from agent_runtime.buzz_review_decisions),
  'buzz_review_ack_receipts', (select pg_catalog.count(*) from agent_runtime.buzz_review_ack_receipts),
  'grok_fixture_sha256', (
    select private.agent_json_sha256(pg_catalog.to_jsonb(dispatch))
    from private.grok_qa_dispatch_outbox dispatch
    where dispatch.workspace_id = '{workspace_id}'::uuid
      and dispatch.content_version_id = '{content_version_id}'::uuid
  )
)::text;
"""


def _environment_preflight_sql() -> str:
    return """
select pg_catalog.jsonb_build_object(
  'fences', (select pg_catalog.count(*) from private.harmony_preview_environment_fence),
  'specialists', (select pg_catalog.count(*) from private.harmony_preview_squid_specialist_bindings),
  'signals', (select pg_catalog.count(*) from agent_runtime.harmony_signals),
  'connector_receipts', (select pg_catalog.count(*) from agent_runtime.harmony_connector_attestation_receipts),
  'connector_registrations', (select pg_catalog.count(*) from private.harmony_preview_connector_registrations),
  'connector_revocations', (select pg_catalog.count(*) from private.harmony_preview_connector_registration_revocations),
  'request_receipts', (select pg_catalog.count(*) from private.harmony_preview_connector_request_receipts),
  'qa_denial_receipts', (select pg_catalog.count(*) from private.harmony_preview_qa_denial_receipts),
  'rounds', (select pg_catalog.count(*) from agent_runtime.harmony_rounds),
  'plans', (select pg_catalog.count(*) from agent_runtime.harmony_plans),
  'stage_receipts', (select pg_catalog.count(*) from agent_runtime.harmony_stage_receipts),
  'operator_inbox', (select pg_catalog.count(*) from agent_runtime.harmony_operator_inbox),
  'codex_lineages', (select pg_catalog.count(*) from private.harmony_preview_codex_source_lineage_receipts),
  'codex_requests', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_requests),
  'codex_runs', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_runs),
  'codex_transitions', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_transitions),
  'codex_claims', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_claim_receipts),
  'codex_attempts', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_attempt_receipts),
  'codex_evidence', (select pg_catalog.count(*) from private.harmony_preview_codex_semantic_qa_evidence),
  'codex_results', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_result_receipts),
  'codex_verifications', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_verification_receipts),
  'codex_reconciliations', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_reconciliation_receipts),
  'codex_stage_links', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_stage_links),
  'max_connections', pg_catalog.current_setting('max_connections')::integer,
  'current_connections', (select pg_catalog.count(*) from pg_catalog.pg_stat_activity)
)::text;
"""


def _activate_fence_sql(branch_ref: str, expires_at: str) -> str:
    return f"""
insert into private.harmony_preview_environment_fence(branch_ref, active, expires_at)
values ('{branch_ref}', true, '{expires_at}'::timestamptz);
select pg_catalog.jsonb_build_object(
  'branch_ref', branch_ref,
  'active', active,
  'expires_at', pg_catalog.to_char(
    expires_at at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'
  )
)::text
from private.harmony_preview_environment_fence;
"""


def _seed_sql(ids: dict[str, str]) -> str:
    return f"""
begin;
insert into public.workspaces(id, name, slug)
values ('{ids['workspace']}', 'Harmony concurrency probe', 'harmony-{ids['slug']}');
insert into public.workspace_clients(workspace_id, client_id, display_name, active)
values ('{ids['workspace']}', 'squid', 'Squid', true);
insert into public.source_feeds(
  id, workspace_id, client_id, provider, name, source_url, handle,
  poll_interval_minutes, active
) values (
  '{ids['feed']}', '{ids['workspace']}', 'squid', 'x', 'Squid official X',
  'https://x.com/SquidRouter', '@SquidRouter', 15, true
);
insert into public.source_items(
  id, workspace_id, client_id, source_feed_id, external_id, source_type,
  canonical_url, author_handle, published_at, body, media, source_hash
) values (
  '{ids['source']}', '{ids['workspace']}', 'squid', '{ids['feed']}',
  '2083266484789514640', 'tweet',
  'https://x.com/SquidRouter/status/2083266484789514640', '@SquidRouter',
  statement_timestamp() - interval '1 hour',
  'Squid 공식 소스를 바탕으로 한국 커뮤니티가 이해하기 쉬운 프라이빗 검토 콘텐츠를 준비합니다.',
  '[]'::jsonb, 'harmony:{ids['slug']}:2083266484789514640'
);
insert into public.content_items(
  id, workspace_id, client_id, content_kind, title, status
) values (
  '{ids['item']}', '{ids['workspace']}', 'squid', 'daily_news',
  'Squid 한국 커뮤니티 업데이트', 'needs_review'
);
insert into public.content_versions(
  id, workspace_id, content_item_id, version_number, prompt_version,
  locale, title, content, channel_copy, deliverables, qa, generation_meta
) values (
  '{ids['version']}', '{ids['workspace']}', '{ids['item']}', 1,
  'harmony-preview-probe@1', 'ko-KR', 'Squid 한국 커뮤니티 업데이트',
  '{{"summary_ko":"공식 소스와 익명 집계 신호를 함께 검토해 다음 커뮤니티 콘텐츠 방향을 제안합니다."}}'::jsonb,
  '{{"telegram":"프라이빗 검토 전용"}}'::jsonb,
  '{{}}'::jsonb, '{{"fact_check":"pending_human_review"}}'::jsonb,
  '{{"mock_mode":false}}'::jsonb
);
update public.content_items set current_version_id = '{ids['version']}'::uuid
where workspace_id = '{ids['workspace']}'::uuid and id = '{ids['item']}'::uuid;
insert into public.content_items(
  id, workspace_id, client_id, content_kind, title, status
) values (
  '{ids['stale_item']}', '{ids['workspace']}', 'squid', 'daily_news',
  'Squid stale-result 격리 검증', 'needs_review'
);
insert into public.content_versions(
  id, workspace_id, content_item_id, version_number, prompt_version,
  locale, title, content, channel_copy, deliverables, qa, generation_meta
) values (
  '{ids['stale_version']}', '{ids['workspace']}', '{ids['stale_item']}', 1,
  'harmony-preview-stale-probe@1', 'ko-KR',
  'Squid stale-result 격리 검증',
  '{{"summary_ko":"stale-result reconciliation 격리 검증 전용 프라이빗 콘텐츠입니다."}}'::jsonb,
  '{{"telegram":"프라이빗 검토 전용"}}'::jsonb,
  '{{}}'::jsonb, '{{"fact_check":"pending_human_review"}}'::jsonb,
  '{{"mock_mode":false}}'::jsonb
);
update public.content_items
set current_version_id = '{ids['stale_version']}'::uuid
where workspace_id = '{ids['workspace']}'::uuid
  and id = '{ids['stale_item']}'::uuid;
insert into public.content_source_links(
  workspace_id, client_id, content_item_id, source_item_id, position
) values ('{ids['workspace']}', 'squid', '{ids['item']}', '{ids['source']}', 0);
insert into public.content_source_links(
  workspace_id, client_id, content_item_id, source_item_id, position
) values (
  '{ids['workspace']}', 'squid', '{ids['stale_item']}', '{ids['source']}', 0
);
insert into public.jobs(
  id, workspace_id, client_id, content_item_id, job_kind, status,
  input, output, idempotency_key
) values (
  '{ids['job']}', '{ids['workspace']}', 'squid', '{ids['item']}',
  'generate', 'succeeded',
  pg_catalog.jsonb_build_object(
    'workflow', 'official_x_review_draft_v1',
    'source_item_ids', pg_catalog.jsonb_build_array('{ids['source']}')
  ),
  pg_catalog.jsonb_build_object(
    'content_item_id', '{ids['item']}', 'content_version_id', '{ids['version']}'
  ), 'harmony:{ids['slug']}:official-x'
);
insert into public.event_log(
  workspace_id, entity_type, entity_id, event_type, data
) values (
  '{ids['workspace']}', 'content_item', '{ids['item']}',
  'official_x_review_draft_completed',
  pg_catalog.jsonb_build_object(
    'job_id', '{ids['job']}', 'content_version_id', '{ids['version']}',
    'source_item_ids', pg_catalog.jsonb_build_array('{ids['source']}')
  )
);
insert into public.jobs(
  id, workspace_id, client_id, content_item_id, job_kind, status,
  input, output, idempotency_key
) values (
  '{ids['stale_job']}', '{ids['workspace']}', 'squid',
  '{ids['stale_item']}', 'generate', 'succeeded',
  pg_catalog.jsonb_build_object(
    'workflow', 'official_x_review_draft_v1',
    'source_item_ids', pg_catalog.jsonb_build_array('{ids['source']}')
  ),
  pg_catalog.jsonb_build_object(
    'content_item_id', '{ids['stale_item']}',
    'content_version_id', '{ids['stale_version']}'
  ), 'harmony:{ids['slug']}:stale-official-x'
);
insert into public.event_log(
  workspace_id, entity_type, entity_id, event_type, data
) values (
  '{ids['workspace']}', 'content_item', '{ids['stale_item']}',
  'official_x_review_draft_completed',
  pg_catalog.jsonb_build_object(
    'job_id', '{ids['stale_job']}',
    'content_version_id', '{ids['stale_version']}',
    'source_item_ids', pg_catalog.jsonb_build_array('{ids['source']}')
  )
);
commit;
select pg_catalog.jsonb_build_object(
  'ok', true,
  'grok_rows', (select pg_catalog.count(*) from private.grok_qa_dispatch_outbox
    where workspace_id = '{ids['workspace']}'::uuid),
  'publication_rows', (select pg_catalog.count(*) from public.publications
    where workspace_id = '{ids['workspace']}'::uuid)
)::text;
"""


def _seed_specialists_sql(
    ids: dict[str, str],
    principals: dict[str, str],
    branch_ref: str,
    release_sha: str,
    config_sha256: str,
    expires_at: str,
) -> str:
    rows = (
        (
            "plan",
            "squid_planner",
            "coineasy_harmony_orchestrator",
            "harmony_plan",
            "grok_bot",
        ),
        (
            "private_content",
            "squid_private_content_producer",
            "coineasy_harmony_content",
            "harmony_prepare_private_content",
            "content_engine",
        ),
        (
            "independent_qa",
            "squid_independent_qa",
            "coineasy_harmony_qa",
            "harmony_independent_qa",
            "codex",
        ),
        (
            "operator_inbox",
            "coineasy_representative_inbox",
            "coineasy_harmony_operator",
            "harmony_operator_inbox",
            "human_operator_inbox",
        ),
        (
            "recap",
            "squid_recap",
            "coineasy_harmony_recap",
            "harmony_recap",
            "coineasy_recap",
        ),
    )
    values = ",\n".join(
        "(" + ", ".join(
            (
                _sql_literal(branch_ref),
                f"'{ids['workspace']}'::uuid",
                "'squid'",
                _sql_literal(stage),
                _sql_literal(specialist_code),
                _sql_literal(role),
                _sql_literal(capability),
                _sql_literal(actor),
                f"'{principals[stage]}'::uuid",
                _sql_literal(release_sha),
                _sql_literal(config_sha256),
                f"'{expires_at}'::timestamptz",
            )
        ) + ")"
        for stage, specialist_code, role, capability, actor in rows
    )
    return f"""
insert into private.harmony_preview_squid_specialist_bindings(
  branch_ref, workspace_id, client_id, stage, specialist_code,
  role_name, capability, actor, principal_id, producer_release_sha,
  config_sha256, expires_at
) values
{values};
select pg_catalog.jsonb_build_object(
  'specialists', pg_catalog.count(*),
  'distinct_principals', pg_catalog.count(distinct specialist.principal_id),
  'all_current', pg_catalog.bool_and(
      specialist.expires_at > statement_timestamp()
      and specialist.expires_at <= fence.expires_at
      and fence.active
  )
)::text
from private.harmony_preview_squid_specialist_bindings specialist
join private.harmony_preview_environment_fence fence
  on fence.branch_ref = specialist.branch_ref
where specialist.workspace_id = '{ids['workspace']}'::uuid
  and specialist.client_id = 'squid';
"""


def _seed_connector_registrations_sql(
    *,
    workspace_id: str,
    branch_ref: str,
    registrations: list[dict[str, str]],
    expires_at: str,
) -> str:
    values = ",\n".join(
        "(" + ", ".join(
            (
                _sql_literal(branch_ref),
                f"'{workspace_id}'::uuid",
                "'squid'",
                f"'{registration['registration_id']}'::uuid",
                _sql_literal(registration["lane"]),
                _sql_literal(registration["capability"]),
                _sql_literal(registration["connector_id"]),
                f"'{registration['principal_id']}'::uuid",
                _sql_literal(registration["release_sha"]),
                _sql_literal(registration["config_sha256"]),
                _sql_literal(registration["attestation_key_id"]),
                f"'{expires_at}'::timestamptz",
            )
        ) + ")"
        for registration in registrations
    )
    return f"""
insert into private.harmony_preview_connector_registrations(
  branch_ref, workspace_id, client_id, registration_id, lane, capability,
  connector_id, producer_principal_id, producer_release_sha, config_sha256,
  attestation_key_id, expires_at
) values
{values};
select pg_catalog.jsonb_build_object(
  'registrations', pg_catalog.count(*),
  'distinct_principals', pg_catalog.count(distinct registration.producer_principal_id),
  'distinct_keys', pg_catalog.count(distinct registration.attestation_key_id),
  'all_current', pg_catalog.bool_and(
      registration.created_at <= statement_timestamp()
      and registration.expires_at > statement_timestamp()
  ),
  'all_within_fence', pg_catalog.bool_and(
      registration.expires_at <= fence.expires_at and fence.active
  )
)::text
from private.harmony_preview_connector_registrations registration
join private.harmony_preview_environment_fence fence
  on fence.branch_ref = registration.branch_ref
where registration.workspace_id = '{workspace_id}'::uuid
  and registration.client_id = 'squid';
"""


def _revoke_connector_registration_sql(
    *,
    workspace_id: str,
    registration_id: str,
    revocation_id: str,
    reason_code: str = "connector_disabled",
) -> str:
    return f"""
insert into private.harmony_preview_connector_registration_revocations(
  workspace_id, client_id, revocation_id, registration_id,
  registration_sha256, reason_code, revoked_at
)
select
  registration.workspace_id,
  registration.client_id,
  '{revocation_id}'::uuid,
  registration.registration_id,
  registration.registration_sha256,
  {_sql_literal(reason_code)},
  statement_timestamp()
from private.harmony_preview_connector_registrations registration
where registration.workspace_id = '{workspace_id}'::uuid
  and registration.client_id = 'squid'
  and registration.registration_id = '{registration_id}'::uuid;
select pg_catalog.jsonb_build_object(
  'revocations', pg_catalog.count(*),
  'registration_id', pg_catalog.min(revocation.registration_id::text),
  'reason_code', pg_catalog.min(revocation.reason_code)
)::text
from private.harmony_preview_connector_registration_revocations revocation
where revocation.workspace_id = '{workspace_id}'::uuid
  and revocation.client_id = 'squid'
  and revocation.registration_id = '{registration_id}'::uuid;
"""


def _wait_for_database_activity(
    psql: Psql,
    *,
    marker: str,
    waiting_on_lock: bool,
    timeout_seconds: float,
) -> None:
    if not re.fullmatch(r"[a-z0-9_]+", marker):
        raise ValueError("database activity marker must be lowercase and nonsecret")
    deadline = time.monotonic() + timeout_seconds
    wait_clause = (
        "and activity.wait_event_type = 'Lock'"
        if waiting_on_lock
        else ""
    )
    query = f"""
select pg_catalog.count(*)
from pg_catalog.pg_stat_activity activity
where activity.pid <> pg_catalog.pg_backend_pid()
  and activity.state = 'active'
  and pg_catalog.strpos(activity.query, {_sql_literal(marker)}) > 0
  {wait_clause};
"""
    while time.monotonic() < deadline:
        if int(psql.run(query) or "0") == 1:
            return
        time.sleep(0.05)
    state = "lock_wait" if waiting_on_lock else "active"
    raise RuntimeError(f"revocation_race_{state}_marker_not_observed")


def _revocation_lock_winner_race(
    psql: Psql,
    *,
    revocation_sql: str,
    loser_sql: str,
    expected_loser_error: str,
) -> dict[str, object]:
    winner_marker = "harmony_revocation_winner_" + uuid.uuid4().hex
    loser_marker = "harmony_revocation_loser_" + uuid.uuid4().hex
    marked_loser_sql = loser_sql.replace(
        "select (", f"select /* {loser_marker} */ (", 1
    )
    if marked_loser_sql == loser_sql:
        raise ValueError("revocation race loser must be a typed RPC expression")
    winner_sql = (
        "begin;\n"
        + revocation_sql
        + "\nselect pg_catalog.pg_sleep(2) /* "
        + winner_marker
        + " */;\ncommit;\n"
    )
    bounded_timeout = max(psql.timeout_seconds, 10.0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(
            psql._execute,
            winner_sql,
            timeout_seconds=bounded_timeout,
        )
        _wait_for_database_activity(
            psql,
            marker=winner_marker,
            waiting_on_lock=False,
            timeout_seconds=5.0,
        )
        loser = pool.submit(
            psql._execute,
            marked_loser_sql,
            timeout_seconds=bounded_timeout,
        )
        _wait_for_database_activity(
            psql,
            marker=loser_marker,
            waiting_on_lock=True,
            timeout_seconds=5.0,
        )
        winner_result = winner.result(timeout=bounded_timeout + 2.0)
        loser_result = loser.result(timeout=bounded_timeout + 2.0)
    if winner_result.returncode != 0:
        raise RuntimeError(
            "revocation lock winner did not commit: database_output=redacted"
        )
    loser_output = loser_result.stderr + loser_result.stdout
    if (
        loser_result.returncode == 0
        or expected_loser_error not in loser_output
    ):
        raise RuntimeError(
            "revocation lock loser was not rejected after recheck: "
            f"expected={expected_loser_error!r}, "
            f"rc={loser_result.returncode}, database_output=redacted"
        )
    return {
        "connections": 2,
        "revocation_lock_acquired_first": True,
        "typed_loser_waited_on_lock": True,
        "typed_loser_rejected_after_recheck": True,
    }


def _assert_no_forbidden_side_effects(
    operation: str,
    rows: list[dict[str, object]],
) -> None:
    if any(
        row.get("ok") is not True
        or row.get("external_calls") is not False
        or row.get("provider_calls") is not False
        or row.get("publication_calls") is not False
        or row.get("automatic_publication") is not False
        for row in rows
    ):
        raise RuntimeError(f"{operation} race reported a forbidden side effect")


def _race_rows(
    invoke: Callable[[int], dict[str, object]],
) -> list[dict[str, object]]:
    barrier = threading.Barrier(CONCURRENCY)

    def race(index: int) -> dict[str, object]:
        barrier.wait(timeout=30)
        return invoke(index)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        return list(pool.map(race, range(CONCURRENCY)))


def _race_exactly_once(
    operation: str,
    invoke: Callable[[int], dict[str, object]],
) -> tuple[dict[str, object], dict[str, int]]:
    rows = _race_rows(invoke)
    new_count = sum(row.get("reused") is False for row in rows)
    reused_count = sum(row.get("reused") is True for row in rows)
    if (new_count, reused_count) != (1, CONCURRENCY - 1):
        raise RuntimeError(
            f"{operation} exactly-once race failed: "
            f"new={new_count}, reused={reused_count}"
        )
    _assert_no_forbidden_side_effects(operation, rows)
    identities = {
        _compact(row.get("stage_receipt"))
        for row in rows
    }
    if len(identities) != 1:
        raise RuntimeError(f"{operation} race returned divergent receipts")
    canonical = rows[0]
    receipt = canonical.get("stage_receipt")
    if not isinstance(receipt, dict):
        raise RuntimeError(f"{operation} race returned no stage receipt")
    if not HEX_SHA256_PATTERN.fullmatch(str(receipt.get("operation_key_sha256", ""))):
        raise RuntimeError(f"{operation} race returned no stable operation key")
    return canonical, {"new": new_count, "reused": reused_count}


def _race_codex_idempotent(
    operation: str,
    invoke: Callable[[int], dict[str, object]],
    identity_keys: tuple[str, ...],
) -> tuple[dict[str, object], dict[str, int]]:
    rows = _race_rows(invoke)
    new_count = sum(row.get("reused") is False for row in rows)
    reused_count = sum(row.get("reused") is True for row in rows)
    if (new_count, reused_count) != (1, CONCURRENCY - 1):
        raise RuntimeError(
            f"{operation} durable exactly-once race failed: "
            f"new={new_count}, reused={reused_count}"
        )
    identities = {
        tuple(str(row.get(key, "")) for key in identity_keys)
        for row in rows
    }
    if len(identities) != 1 or any(not value for value in next(iter(identities))):
        raise RuntimeError(f"{operation} durable race returned divergent identity")
    return rows[0], {"new": new_count, "reused": reused_count}


def _race_codex_claim(
    invoke: Callable[[int], dict[str, object]],
) -> tuple[dict[str, object], dict[str, int]]:
    rows = _race_rows(invoke)
    claimed = [row for row in rows if row.get("claimed") is True]
    not_claimed = [row for row in rows if row.get("claimed") is False]
    if (len(claimed), len(not_claimed)) != (1, CONCURRENCY - 1):
        raise RuntimeError(
            "Codex QA claim race failed: "
            f"claimed={len(claimed)}, not_claimed={len(not_claimed)}"
        )
    winner = claimed[0]
    for key in ("work_key", "request_key", "claim_fence_sha256"):
        if not HEX_SHA256_PATTERN.fullmatch(str(winner.get(key, ""))):
            raise RuntimeError(f"Codex QA claim winner omitted {key}")
    return winner, {
        "claimed": len(claimed),
        "not_claimed": len(not_claimed),
    }


def _race_codex_start(
    invoke: Callable[[int], dict[str, object]],
) -> tuple[dict[str, object], dict[str, int]]:
    rows = _race_rows(invoke)
    authorized = [row for row in rows if row.get("execute_authorized") is True]
    non_authorizing = [
        row for row in rows if row.get("execute_authorized") is False
    ]
    new_count = sum(row.get("reused") is False for row in rows)
    reused_count = sum(row.get("reused") is True for row in rows)
    if (
        len(authorized),
        len(non_authorizing),
        new_count,
        reused_count,
    ) != (1, CONCURRENCY - 1, 1, CONCURRENCY - 1):
        raise RuntimeError(
            "Codex QA start race failed: "
            f"authorized={len(authorized)}, "
            f"non_authorizing={len(non_authorizing)}, "
            f"new={new_count}, reused={reused_count}"
        )
    if authorized[0].get("reused") is not False:
        raise RuntimeError("Codex QA replay incorrectly authorized execution")
    identities = {
        (
            str(row.get("work_key", "")),
            str(row.get("attempt_fence_sha256", "")),
        )
        for row in rows
    }
    if (
        len(identities) != 1
        or not all(HEX_SHA256_PATTERN.fullmatch(value) for value in next(iter(identities)))
    ):
        raise RuntimeError("Codex QA start race returned divergent identity")
    return authorized[0], {
        "authorized": len(authorized),
        "replay_non_authorizing": len(non_authorizing),
    }


def _submit_expression(
    ids: dict[str, str],
    receipt_id: str,
    signal: dict[str, object],
    target_client_id: str = CLIENT_ID,
) -> str:
    return (
        "public.submit_preview_harmony_signal("
        f"'{ids['workspace']}'::uuid, {_sql_literal(target_client_id)}, "
        f"'{receipt_id}'::uuid, "
        + _sql_literal(_compact(signal))
        + "::jsonb)"
    )


def _stage_expression(
    ids: dict[str, str],
    stage: str,
    receipt_id: str,
    inbox_id: str | None = None,
    qa_evidence: dict[str, object] | None = None,
) -> str:
    inbox = "null::uuid" if inbox_id is None else f"'{inbox_id}'::uuid"
    evidence = "null::jsonb" if qa_evidence is None else _sql_literal(_compact(qa_evidence)) + "::jsonb"
    return (
        "public.append_preview_harmony_squid_stage("
        f"'{ids['workspace']}'::uuid, 'squid', '{ids['round']}'::uuid, "
        f"'{ids['plan']}'::uuid, '{stage}', '{receipt_id}'::uuid, {inbox}, {evidence})"
    )


def _codex_prepare_expression(
    ids: dict[str, str],
    approved_cost_cap_microusd: int = 0,
) -> str:
    return (
        "public.prepare_preview_harmony_squid_codex_qa("
        f"'{ids['workspace']}'::uuid, 'squid', '{ids['round']}'::uuid, "
        f"'{ids['plan']}'::uuid, {approved_cost_cap_microusd}::bigint)"
    )


def _codex_claim_expression(
    ids: dict[str, str],
    lease_seconds: int = 900,
) -> str:
    return (
        "public.claim_preview_harmony_squid_codex_qa("
        f"'{ids['workspace']}'::uuid, 'squid', {lease_seconds})"
    )


def _codex_start_expression(
    ids: dict[str, str],
    work_key: str,
    claim_fence_sha256: str,
) -> str:
    return (
        "public.start_preview_harmony_squid_codex_qa_attempt("
        f"'{ids['workspace']}'::uuid, 'squid', {_sql_literal(work_key)}, "
        f"{_sql_literal(claim_fence_sha256)})"
    )


def _codex_submit_result_expression(
    ids: dict[str, str],
    work_key: str,
    attempt_fence_sha256: str,
    criteria: dict[str, object],
    *,
    qa_output_sha256: str,
    verdict: str,
    finding_codes: list[str],
) -> str:
    findings = (
        "array[]::text[]"
        if not finding_codes
        else "array["
        + ",".join(_sql_literal(value) for value in finding_codes)
        + "]::text[]"
    )
    return (
        "public.submit_preview_harmony_squid_codex_qa_result("
        f"'{ids['workspace']}'::uuid, 'squid', {_sql_literal(work_key)}, "
        f"{_sql_literal(attempt_fence_sha256)}, "
        f"{_sql_literal(_compact(criteria))}::jsonb, "
        f"{_sql_literal(qa_output_sha256)}, {findings}, "
        f"{_sql_literal(verdict)})"
    )


def _codex_verify_expression(
    ids: dict[str, str],
    work_key: str,
) -> str:
    return (
        "public.verify_preview_harmony_squid_codex_qa_result("
        f"'{ids['workspace']}'::uuid, 'squid', {_sql_literal(work_key)})"
    )


def _codex_reconcile_expression(
    ids: dict[str, str],
    batch_limit: int = 64,
) -> str:
    return (
        "public.reconcile_preview_harmony_squid_codex_qa_lease("
        f"'{ids['workspace']}'::uuid, 'squid', {batch_limit})"
    )


def _qa_denial_expression(
    ids: dict[str, str],
    denial_receipt_id: str,
    qa_evidence: dict[str, object],
) -> str:
    return (
        "public.record_preview_harmony_squid_qa_denial("
        f"'{ids['workspace']}'::uuid, 'squid', '{ids['round']}'::uuid, "
        f"'{ids['plan']}'::uuid, '{denial_receipt_id}'::uuid, "
        + _sql_literal(_compact(qa_evidence))
        + "::jsonb)"
    )


def _race_qa_denial(
    invoke: Callable[[int], dict[str, object]],
) -> tuple[dict[str, object], dict[str, int]]:
    barrier = threading.Barrier(CONCURRENCY)

    def race(index: int) -> dict[str, object]:
        barrier.wait(timeout=30)
        return invoke(index)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        rows = list(pool.map(race, range(CONCURRENCY)))
    new_count = sum(row.get("reused") is False for row in rows)
    reused_count = sum(row.get("reused") is True for row in rows)
    if (new_count, reused_count) != (1, CONCURRENCY - 1):
        raise RuntimeError(
            "independent QA denial exactly-once race failed: "
            f"new={new_count}, reused={reused_count}"
        )
    if any(
        row.get("ok") is not False
        or row.get("denied") is not True
        or row.get("external_calls") is not False
        or row.get("provider_calls") is not False
        or row.get("publication_calls") is not False
        or row.get("automatic_publication") is not False
        or not isinstance(row.get("qa_denial_receipt"), dict)
        for row in rows
    ):
        raise RuntimeError("independent QA denial reported a forbidden side effect")
    identities = {
        _compact(row.get("qa_denial_receipt"))
        for row in rows
    }
    if len(identities) != 1:
        raise RuntimeError("independent QA denial race returned divergent receipts")
    return rows[0], {"new": new_count, "reused": reused_count}


def _race_codex_reconciliation(
    invoke: Callable[[int], dict[str, object]],
    *,
    expected_work_key: str,
) -> tuple[dict[str, object], dict[str, int]]:
    barrier = threading.Barrier(CONCURRENCY)

    def race(index: int) -> dict[str, object]:
        barrier.wait(timeout=30)
        return invoke(index)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        rows = list(pool.map(race, range(CONCURRENCY)))
    winners = [row for row in rows if row.get("reconciled") is True]
    no_ops = [row for row in rows if row.get("reconciled") is False]
    if (len(winners), len(no_ops)) != (1, CONCURRENCY - 1):
        raise RuntimeError(
            "Codex stale-result reconciliation exactly-once race failed: "
            f"reconciled={len(winners)}, no_op={len(no_ops)}"
        )
    winner = winners[0]
    if winner != {
        "blocked": True,
        "outcome_unknown": False,
        "pending": False,
        "reconciled": True,
        "status": "blocked",
        "work_key": expected_work_key,
    }:
        raise RuntimeError(
            "Codex stale-result reconciliation returned an invalid winner: "
            f"{winner}"
        )
    expected_no_op = {
        "blocked": False,
        "outcome_unknown": False,
        "pending": False,
        "reconciled": False,
        "work_key": None,
    }
    if any(row != expected_no_op for row in no_ops):
        raise RuntimeError(
            "Codex stale-result reconciliation returned a divergent no-op"
        )
    return winner, {
        "reconciled": len(winners),
        "no_op": len(no_ops),
    }


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    is_local = _is_local_host(args.host)
    branch_ref: str | None = None
    if args.confirm_disposable_local == args.confirm_disposable_preview:
        raise SystemExit(
            "choose exactly one disposable target confirmation"
        )
    if args.confirm_disposable_local and not is_local:
        raise SystemExit("--confirm-disposable-local requires localhost or a local socket")
    if args.confirm_disposable_preview:
        if is_local:
            raise SystemExit("disposable Preview mode cannot use a local database host")
        try:
            branch_ref = _validated_disposable_preview_ref(
                args.host,
                args.port,
                args.expected_branch_ref,
                args.parent_project_ref,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    executable = args.psql or shutil.which("psql")
    if not executable:
        raise SystemExit("psql executable not found")
    if not HEX_SHA40_PATTERN.fullmatch(args.release_sha or ""):
        raise SystemExit("--release-sha must be an exact lowercase 40-hex Git SHA")
    if not HEX_SHA256_PATTERN.fullmatch(args.config_sha256 or ""):
        raise SystemExit("--config-sha256 must be an exact lowercase 64-hex digest")
    if not 1 <= args.command_timeout_seconds <= 120:
        raise SystemExit("--command-timeout-seconds must be between 1 and 120")
    if not 1 <= args.fence_ttl_minutes <= 120:
        raise SystemExit("--fence-ttl-minutes must be between 1 and the approved 120-minute TTL")
    psql = Psql(
        executable,
        args.host,
        args.port,
        args.user,
        args.database,
        args.command_timeout_seconds,
    )
    _assert_connector_request_digest_vector(psql)
    preflight = psql.json(_environment_preflight_sql())
    expected_empty = {
        "fences": 0,
        "specialists": 0,
        "signals": 0,
        "connector_receipts": 0,
        "connector_registrations": 0,
        "connector_revocations": 0,
        "request_receipts": 0,
        "qa_denial_receipts": 0,
        "rounds": 0,
        "plans": 0,
        "stage_receipts": 0,
        "operator_inbox": 0,
        "codex_lineages": 0,
        "codex_requests": 0,
        "codex_runs": 0,
        "codex_transitions": 0,
        "codex_claims": 0,
        "codex_attempts": 0,
        "codex_evidence": 0,
        "codex_results": 0,
        "codex_verifications": 0,
        "codex_reconciliations": 0,
        "codex_stage_links": 0,
    }
    if any(preflight.get(key) != value for key, value in expected_empty.items()):
        raise RuntimeError(f"Preview ledger preflight was not empty: {preflight}")
    max_connections = int(preflight.get("max_connections", 0))
    current_connections = int(preflight.get("current_connections", max_connections))
    if max_connections - current_connections < CONCURRENCY + 8:
        raise RuntimeError(
            "insufficient_direct_connection_capacity_for_64_way_probe"
        )
    uid = lambda: str(uuid.uuid4())
    ids = {
        "workspace": uid(), "feed": uid(), "source": uid(), "item": uid(),
        "version": uid(), "job": uid(), "round": uid(), "plan": uid(),
        "inbox": uid(), "stale_item": uid(), "stale_version": uid(),
        "stale_job": uid(), "slug": uuid.uuid4().hex[:12],
    }
    specialist_principals = {
        stage: uid()
        for stage in (
            "plan",
            "private_content",
            "independent_qa",
            "operator_inbox",
            "recap",
        )
    }
    branch_ref = branch_ref or uuid.uuid4().hex[:BRANCH_REF_LENGTH]
    fence_expiry = (
        datetime.now(UTC) + timedelta(minutes=args.fence_ttl_minutes)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    fence = psql.json(_activate_fence_sql(branch_ref, fence_expiry))
    if fence != {
        "branch_ref": branch_ref,
        "active": True,
        "expires_at": fence_expiry,
    }:
        raise RuntimeError(f"Preview fence activation failed closed: {fence}")
    seed = psql.json(_seed_sql(ids))
    if seed != {"ok": True, "grok_rows": 2, "publication_rows": 0}:
        raise RuntimeError(f"fixture seed failed closed: {seed}")
    specialist_seed = psql.json(_seed_specialists_sql(
        ids,
        specialist_principals,
        branch_ref,
        args.release_sha,
        args.config_sha256,
        fence_expiry,
    ))
    if specialist_seed != {
        "specialists": 5,
        "distinct_principals": 5,
        "all_current": True,
    }:
        raise RuntimeError(
            f"fixed-specialist roster failed closed: {specialist_seed}"
        )
    before = psql.json(_baseline_sql(ids["workspace"], ids["version"]))

    observed = (datetime.now(UTC) - timedelta(seconds=5)).replace(microsecond=0)
    expires = datetime.fromisoformat(
        fence_expiry.replace("Z", "+00:00")
    ) - timedelta(minutes=1)
    if expires <= observed:
        raise RuntimeError("approved Preview fence TTL is too short for the probe")
    observed_at = observed.isoformat().replace("+00:00", "Z")
    expires_at = expires.isoformat().replace("+00:00", "Z")
    topic = "official_update"
    lane_specs = [
        (
            "quiz_bot", "quiz_learning", "harmony_submit_quiz_bot",
            {"data_classification": "aggregate_anonymous", "attempts": 64,
             "participants": 16, "accuracy_basis_points": 7500,
             "tutorial_priority_basis_points": 8100},
        ),
        (
            "community_ops", "community_demand", "harmony_submit_community_ops",
            {"data_classification": "aggregate_anonymous", "room_mapping_count": 1,
             "sample_size": 32, "demand_score_basis_points": 8200},
        ),
        (
            "recap", "recap_metric", "harmony_submit_recap",
            {"data_classification": "aggregate_anonymous",
             "period_start": (observed - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
             "period_end": observed_at,
             "metrics": [
                 {"metric_code": "community_questions", "unit": "count", "observed": True, "value": 12},
                 {"metric_code": "qa_cost", "unit": "microusd", "observed": False, "value": None},
             ]},
        ),
    ]
    connector_specs = [
        (lane, capability, f"{lane}_probe")
        for lane, _kind, capability, _extra in lane_specs
    ] + [
        (
            "content_source",
            "harmony_submit_content_source",
            "official_source_probe",
        )
    ]
    connector_registrations = {
        lane: {
            "lane": lane,
            "capability": capability,
            "connector_id": connector_id,
            "principal_id": uid(),
            "registration_id": uid(),
            "attestation_key_id": f"harmony-preview-{lane}-{ids['slug']}",
            "release_sha": args.release_sha,
            "config_sha256": args.config_sha256,
        }
        for lane, capability, connector_id in connector_specs
    }
    registration_seed = psql.json(_seed_connector_registrations_sql(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        registrations=list(connector_registrations.values()),
        expires_at=fence_expiry,
    ))
    if registration_seed != {
        "registrations": 4,
        "distinct_principals": 4,
        "distinct_keys": 4,
        "all_current": True,
        "all_within_fence": True,
    }:
        raise RuntimeError(
            f"connector registration seed failed closed: {registration_seed}"
        )
    signals: list[tuple[str, str, dict[str, object], dict[str, object], str]] = []
    for lane, kind, capability, extra in lane_specs:
        registration = connector_registrations[lane]
        principal = registration["principal_id"]
        body = _signal_body(
            workspace_id=ids["workspace"], signal_id=uid(), source_event_id=uid(),
            principal_id=principal, signal_kind=kind, lane=lane, topic_codes=[topic],
            observed_at=observed_at, expires_at=expires_at,
            upstream_receipt_sha256=hashlib.sha256((lane + ids["slug"]).encode()).hexdigest(),
            evidence_sha256=hashlib.sha256((kind + ids["slug"]).encode()).hexdigest(),
            release_sha=args.release_sha, config_sha256=args.config_sha256,
            extra=extra,
        )
        signal = _with_db_hash(psql, body)
        receipt_id = uid()
        request_sha256 = _connector_request_sha256(
            workspace_id=ids["workspace"],
            client_id=CLIENT_ID,
            registration_id=registration["registration_id"],
            connector_receipt_id=receipt_id,
            signal=signal,
        )
        _assert_connector_request_sha256_matches_database(
            psql,
            expected_sha256=request_sha256,
            workspace_id=ids["workspace"],
            client_id=CLIENT_ID,
            registration_id=registration["registration_id"],
            connector_receipt_id=receipt_id,
            signal=signal,
        )
        connector_claims = _claims(
            workspace_id=ids["workspace"], branch_ref=branch_ref,
            role="coineasy_harmony_connector", capability=capability,
            principal_id=principal, release_sha=args.release_sha,
            config_sha256=args.config_sha256,
            connector_id=registration["connector_id"],
            attestation_registration_id=registration["registration_id"],
            attestation_key_id=registration["attestation_key_id"],
            request_sha256=request_sha256,
        )
        connector_claims["exp"] = min(
            int(connector_claims["exp"]), int(expires.timestamp())
        )
        signals.append(
            (lane, capability, signal, connector_claims, receipt_id)
        )

    quiz = signals[0]
    psql.expect_error(
        "begin; set local role coineasy_harmony_connector; "
        "select * from agent_runtime.harmony_signals; rollback;",
        "permission denied",
    )
    psql.expect_error(
        _rpc_sql(
            quiz[3],
            _submit_expression(
                ids,
                quiz[4],
                {**quiz[2], "client_id": "yellow"},
                target_client_id="yellow",
            ),
        ),
        "harmony_preview_connector_trust_claim_invalid",
    )
    psql.expect_error(
        _rpc_sql(
            {**signals[1][3], "capability": "harmony_submit_quiz_bot"},
            _submit_expression(ids, signals[1][4], signals[1][2]),
        ),
        "harmony_preview_connector_registration_invalid",
    )
    barrier = threading.Barrier(CONCURRENCY)

    def race(_: int) -> dict[str, object]:
        barrier.wait(timeout=30)
        return psql.json(_rpc_sql(quiz[3], _submit_expression(ids, quiz[4], quiz[2])))

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        raced = list(pool.map(race, range(CONCURRENCY)))
    new_count = sum(row.get("reused") is False for row in raced)
    reused_count = sum(row.get("reused") is True for row in raced)
    if (new_count, reused_count) != (1, CONCURRENCY - 1):
        raise RuntimeError(f"exactly-once race failed: new={new_count}, reused={reused_count}")
    if any(
        row.get("ok") is not True
        or row.get("external_calls") is not False
        or row.get("provider_calls") is not False
        or row.get("publication_calls") is not False
        or row.get("automatic_publication") is not False
        for row in raced
    ):
        raise RuntimeError("race response reported a forbidden side effect")
    race_identity = {
        (
            row["signal"]["signal_id"],
            row["signal"]["payload_sha256"],
            row["connector_receipt"]["receipt_id"],
            row["connector_receipt"]["payload_sha256"],
            row["connector_receipt"]["verification_reference_sha256"],
            row["connector_request_receipt"]["request_receipt_id"],
            row["connector_request_receipt"]["payload_sha256"],
            row["connector_request_receipt"]["request_nonce"],
            row["connector_request_receipt"]["request_sha256"],
        )
        for row in raced
    }
    if len(race_identity) != 1:
        raise RuntimeError(f"race returned divergent identities: {race_identity}")
    race_rows = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'signals', (select pg_catalog.count(*) from agent_runtime.harmony_signals
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'connector_receipts', (select pg_catalog.count(*)
    from agent_runtime.harmony_connector_attestation_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'request_receipts', (select pg_catalog.count(*)
    from private.harmony_preview_connector_request_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid')
)::text;
""")
    if race_rows != {
        "signals": 1,
        "connector_receipts": 1,
        "request_receipts": 1,
    }:
        raise RuntimeError(f"race wrote more than one ledger row: {race_rows}")

    changed_digest_nonce = uid()
    valid_request_sha256 = str(quiz[3]["request_sha256"])
    changed_request_sha256 = (
        ("0" if valid_request_sha256[0] != "0" else "1")
        + valid_request_sha256[1:]
    )
    changed_digest_claims = {
        **quiz[3],
        "jti": changed_digest_nonce,
        "request_nonce": changed_digest_nonce,
        "request_sha256": changed_request_sha256,
    }
    psql.expect_error(
        _rpc_sql(
            changed_digest_claims,
            _submit_expression(ids, quiz[4], quiz[2]),
        ),
        "harmony_preview_connector_trust_claim_invalid",
    )
    pre_registration_claims = {
        **quiz[3],
        "iat": int(quiz[3]["iat"]) - 60,
    }
    psql.expect_error(
        _rpc_sql(
            pre_registration_claims,
            _submit_expression(ids, quiz[4], quiz[2]),
        ),
        "harmony_preview_connector_registration_invalid",
    )
    same_nonce_drift_claims = {
        **quiz[3],
        "exp": int(quiz[3]["exp"]) - 1,
    }
    psql.expect_error(
        _rpc_sql(
            same_nonce_drift_claims,
            _submit_expression(ids, quiz[4], quiz[2]),
        ),
        "harmony_preview_connector_request_idempotency_conflict",
    )
    replay_nonce = uid()
    new_nonce_same_digest_claims = {
        **quiz[3],
        "jti": replay_nonce,
        "request_nonce": replay_nonce,
    }
    psql.expect_error(
        _rpc_sql(
            new_nonce_same_digest_claims,
            _submit_expression(ids, quiz[4], quiz[2]),
        ),
        "harmony_preview_connector_request_replay_conflict",
    )
    trust_negative_rows = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'signals', (select pg_catalog.count(*) from agent_runtime.harmony_signals
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'connector_receipts', (select pg_catalog.count(*)
    from agent_runtime.harmony_connector_attestation_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'request_receipts', (select pg_catalog.count(*)
    from private.harmony_preview_connector_request_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid')
)::text;
""")
    if trust_negative_rows != race_rows:
        raise RuntimeError(
            "connector trust negative cases changed domain rows: "
            f"{race_rows} -> {trust_negative_rows}"
        )

    for lane in signals[1:]:
        result = psql.json(_rpc_sql(lane[3], _submit_expression(ids, lane[4], lane[2])))
        if result.get("ok") is not True or result.get("reused") is not False:
            raise RuntimeError(f"typed signal submission failed: {lane[0]}: {result}")

    source_body_sha = psql.run(
        f"select pg_catalog.encode(extensions.digest(pg_catalog.convert_to(body, 'UTF8'), 'sha256'), 'hex') "
        f"from public.source_items where id = '{ids['source']}'::uuid;"
    )
    source_registration = connector_registrations["content_source"]
    source_principal = source_registration["principal_id"]
    source_body = _signal_body(
        workspace_id=ids["workspace"], signal_id=uid(), source_event_id=ids["version"],
        principal_id=source_principal, signal_kind="official_source", lane="content_source",
        topic_codes=[topic], observed_at=observed_at, expires_at=expires_at,
        upstream_receipt_sha256="0" * 64, evidence_sha256=source_body_sha,
        release_sha=args.release_sha, config_sha256=args.config_sha256,
        extra={"data_classification": "public_official", "source_item_id": ids["source"],
               "source_body_sha256": source_body_sha, "source_kind": "x_post_text",
               "source_verified": True, "eligible_content_kinds": ["daily_news"]},
    )
    source_binding = psql.run(
        "select private.harmony_preview_squid_official_source_binding("
        + _sql_literal(_compact(source_body)) + "::jsonb);"
    )
    if len(source_binding) != 64:
        raise RuntimeError("official source binding was not derived from the immutable ledger")
    source_body["upstream_receipt_sha256"] = source_binding
    source_signal = _with_db_hash(psql, source_body)
    source_receipt = uid()
    source_request_sha256 = _connector_request_sha256(
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=source_registration["registration_id"],
        connector_receipt_id=source_receipt,
        signal=source_signal,
    )
    _assert_connector_request_sha256_matches_database(
        psql,
        expected_sha256=source_request_sha256,
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=source_registration["registration_id"],
        connector_receipt_id=source_receipt,
        signal=source_signal,
    )
    source_claims = _claims(
        workspace_id=ids["workspace"], branch_ref=branch_ref,
        role="coineasy_harmony_connector", capability="harmony_submit_content_source",
        principal_id=source_principal, release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        connector_id=source_registration["connector_id"],
        attestation_registration_id=source_registration["registration_id"],
        attestation_key_id=source_registration["attestation_key_id"],
        request_sha256=source_request_sha256,
    )
    source_claims["exp"] = min(
        int(source_claims["exp"]), int(expires.timestamp())
    )
    source_result = psql.json(
        _rpc_sql(source_claims, _submit_expression(ids, source_receipt, source_signal))
    )
    if source_result.get("reused") is not False:
        raise RuntimeError(f"official source submission failed: {source_result}")

    signal_hashes = sorted(
        [row[2]["payload_sha256"] for row in signals]
        + [source_signal["payload_sha256"]]
    )
    array_sql = "array[" + ",".join(_sql_literal(value) for value in signal_hashes) + "]::text[]"

    def plan_expression(
        receipt_id: str,
        round_id: str = ids["round"],
        plan_id: str = ids["plan"],
    ) -> str:
        return (
            "public.create_preview_harmony_squid_plan("
            f"'{ids['workspace']}'::uuid, 'squid', '{round_id}'::uuid, "
            f"'{plan_id}'::uuid, '{receipt_id}'::uuid, {array_sql}, '{topic}')"
        )

    wrong_plan_claims = _claims(
        workspace_id=ids["workspace"], branch_ref=branch_ref,
        role="coineasy_harmony_orchestrator", capability="harmony_plan",
        principal_id=uid(), release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    missing_epoch_plan_claims = _claims(
        workspace_id=ids["workspace"], branch_ref=branch_ref,
        role="coineasy_harmony_orchestrator", capability="harmony_plan",
        principal_id=specialist_principals["plan"],
        release_sha=args.release_sha, config_sha256=args.config_sha256,
    )
    missing_epoch_plan_claims.pop("iat")
    missing_epoch_plan_claims.pop("exp")
    psql.expect_error(
        _rpc_sql(missing_epoch_plan_claims, plan_expression(uid())),
        "harmony_preview_plan_scope_invalid",
    )
    missing_subject_plan_claims = _claims(
        workspace_id=ids["workspace"], branch_ref=branch_ref,
        role="coineasy_harmony_orchestrator", capability="harmony_plan",
        principal_id=specialist_principals["plan"],
        release_sha=args.release_sha, config_sha256=args.config_sha256,
    )
    missing_subject_plan_claims.pop("sub")
    psql.expect_error(
        _rpc_sql(missing_subject_plan_claims, plan_expression(uid())),
        "harmony_preview_plan_scope_invalid",
    )
    psql.expect_error(
        _rpc_sql(wrong_plan_claims, plan_expression(uid())),
        "harmony_preview_plan_scope_invalid",
    )
    plan_after_wrong_principal = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'rounds', (select pg_catalog.count(*) from agent_runtime.harmony_rounds
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'plans', (select pg_catalog.count(*) from agent_runtime.harmony_plans
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'plan_receipts', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and stage = 'plan')
)::text;
""")
    if plan_after_wrong_principal != {
        "rounds": 0,
        "plans": 0,
        "plan_receipts": 0,
    }:
        raise RuntimeError(
            "invalid plan claims preempted the fixed specialist: "
            f"{plan_after_wrong_principal}"
        )

    def invoke_plan(_: int) -> dict[str, object]:
        claims = _claims(
            workspace_id=ids["workspace"], branch_ref=branch_ref,
            role="coineasy_harmony_orchestrator", capability="harmony_plan",
            principal_id=specialist_principals["plan"],
            release_sha=args.release_sha, config_sha256=args.config_sha256,
        )
        return psql.json(_rpc_sql(claims, plan_expression(uid())))

    plan, plan_race = _race_exactly_once("plan", invoke_plan)
    operation_races: dict[str, dict[str, int]] = {"plan": plan_race}
    plan_rows = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'rounds', (select pg_catalog.count(*) from agent_runtime.harmony_rounds
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'plans', (select pg_catalog.count(*) from agent_runtime.harmony_plans
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'plan_receipts', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and stage = 'plan')
)::text;
""")
    if plan_rows != {"rounds": 1, "plans": 1, "plan_receipts": 1}:
        raise RuntimeError(f"plan race wrote more than one ledger row: {plan_rows}")
    conflicting_ids = {
        **ids,
        "round": uid(),
        "plan": uid(),
    }
    orchestrator = _claims(
        workspace_id=ids["workspace"], branch_ref=branch_ref,
        role="coineasy_harmony_orchestrator", capability="harmony_plan",
        principal_id=specialist_principals["plan"],
        release_sha=args.release_sha, config_sha256=args.config_sha256,
    )
    psql.expect_error(
        _rpc_sql(
            orchestrator,
            plan_expression(
                uid(), conflicting_ids["round"], conflicting_ids["plan"]
            ),
        ),
        "harmony_preview_plan_idempotency_conflict",
    )

    stage_specs = [
        (
            "private_content",
            "coineasy_harmony_content",
            "harmony_prepare_private_content",
        ),
        (
            "operator_inbox",
            "coineasy_harmony_operator",
            "harmony_operator_inbox",
        ),
        ("recap", "coineasy_harmony_recap", "harmony_recap"),
    ]
    codex_qa_races: dict[str, dict[str, int]] = {}
    codex_work_key = ""
    wrong_principal_preemption_rows = 0
    operator_inbox_stage4_delta = 0
    recap_operator_inbox_delta = 0
    for stage, role, capability in stage_specs:
        inbox_id = ids["inbox"] if stage in {"operator_inbox", "recap"} else None
        before_stage = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'stage_rows', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{ids['plan']}'::uuid and stage = '{stage}'),
  'operator_inbox', (select pg_catalog.count(*)
    from agent_runtime.harmony_operator_inbox
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{ids['plan']}'::uuid)
)::text;
""")
        if before_stage.get("stage_rows") != 0:
            raise RuntimeError(f"stage was not empty before race: {stage}: {before_stage}")
        wrong_claims = _claims(
            workspace_id=ids["workspace"], branch_ref=branch_ref,
            role=role, capability=capability, principal_id=uid(),
            release_sha=args.release_sha, config_sha256=args.config_sha256,
        )
        psql.expect_error(
            _rpc_sql(
                wrong_claims,
                _stage_expression(ids, stage, uid(), inbox_id),
            ),
            "harmony_preview_stage_claim_invalid",
        )
        after_wrong_principal = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'stage_rows', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{ids['plan']}'::uuid and stage = '{stage}'),
  'operator_inbox', (select pg_catalog.count(*)
    from agent_runtime.harmony_operator_inbox
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{ids['plan']}'::uuid)
)::text;
""")
        if after_wrong_principal != before_stage:
            wrong_principal_preemption_rows += int(
                after_wrong_principal.get("stage_rows", 0)
            ) - int(before_stage.get("stage_rows", 0))
            raise RuntimeError(
                f"wrong principal preempted fixed specialist {stage}: "
                f"{before_stage} -> {after_wrong_principal}"
            )

        def invoke_stage(_: int) -> dict[str, object]:
            claims = _claims(
                workspace_id=ids["workspace"], branch_ref=branch_ref,
                role=role, capability=capability,
                principal_id=specialist_principals[stage],
                release_sha=args.release_sha,
                config_sha256=args.config_sha256,
            )
            return psql.json(_rpc_sql(
                claims,
                _stage_expression(ids, stage, uid(), inbox_id),
            ))

        result, stage_race = _race_exactly_once(stage, invoke_stage)
        operation_races[stage] = stage_race
        after_stage = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'stage_rows', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{ids['plan']}'::uuid and stage = '{stage}'),
  'operator_inbox', (select pg_catalog.count(*)
    from agent_runtime.harmony_operator_inbox
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{ids['plan']}'::uuid)
)::text;
""")
        if after_stage.get("stage_rows") != 1:
            raise RuntimeError(
                f"{stage} race wrote more than one receipt: {after_stage}"
            )
        inbox_delta = int(after_stage["operator_inbox"]) - int(
            before_stage["operator_inbox"]
        )
        if stage == "operator_inbox":
            operator_inbox_stage4_delta = inbox_delta
            if inbox_delta != 1:
                raise RuntimeError(
                    f"stage 4 did not create exactly one inbox: {after_stage}"
                )
        elif stage == "recap":
            recap_operator_inbox_delta = inbox_delta
            if inbox_delta != 0:
                raise RuntimeError(
                    f"recap created or removed an inbox: {after_stage}"
                )
        elif inbox_delta != 0:
            raise RuntimeError(
                f"{stage} changed the representative inbox early: {after_stage}"
            )
        if stage == "private_content":
            private_receipt = result.get("stage_receipt")
            if not isinstance(private_receipt, dict):
                raise RuntimeError("private-content race returned no receipt")
            private_output_sha256 = str(private_receipt["output_sha256"])

            def qa_claims(principal_id: str) -> dict[str, object]:
                return _claims(
                    workspace_id=ids["workspace"], branch_ref=branch_ref,
                    role="coineasy_harmony_qa",
                    capability="harmony_independent_qa",
                    principal_id=principal_id,
                    release_sha=args.release_sha,
                    config_sha256=args.config_sha256,
                )

            codex_before_wrong = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'requests', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_requests
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'runs', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_runs
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid')
)::text;
""")
            for missing_claim in ("capability", "jti", "max_cost_microusd"):
                incomplete_claims = qa_claims(
                    specialist_principals["independent_qa"]
                )
                incomplete_claims.pop(missing_claim)
                psql.expect_error(
                    _rpc_sql(
                        incomplete_claims,
                        _codex_prepare_expression(ids),
                    ),
                    "harmony_preview_codex_qa_scope_invalid",
                )
            psql.expect_error(
                _rpc_sql(qa_claims(uid()), _codex_prepare_expression(ids)),
                "harmony_preview_codex_qa_scope_invalid",
            )
            if psql.json(f"""
select pg_catalog.jsonb_build_object(
  'requests', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_requests
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'runs', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_runs
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid')
)::text;
""") != codex_before_wrong:
                raise RuntimeError("wrong QA principal preempted the Codex gate")

            def invoke_prepare(_: int) -> dict[str, object]:
                return psql.json(_rpc_sql(
                    qa_claims(specialist_principals["independent_qa"]),
                    _codex_prepare_expression(ids),
                ))

            prepared, codex_qa_races["prepare"] = _race_codex_idempotent(
                "Codex QA prepare", invoke_prepare, ("work_key", "request_key")
            )
            codex_work_key = str(prepared["work_key"])
            request_key = str(prepared["request_key"])
            if not all(HEX_SHA256_PATTERN.fullmatch(value) for value in (
                codex_work_key, request_key
            )):
                raise RuntimeError("Codex QA prepare returned invalid identity")
            canonical_identity = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'assignment_key', request.assignment_key,
  'reviewer_binding_sha256', request.reviewer_specialist_binding_sha256,
  'source_lineage', lineage.payload,
  'work_key', request.work_key
)::text
from private.harmony_preview_codex_gate_requests request
join private.harmony_preview_codex_source_lineage_receipts lineage
  on lineage.workspace_id = request.workspace_id
 and lineage.client_id = request.client_id
 and lineage.lineage_receipt_id = request.lineage_receipt_id
where request.workspace_id = '{ids['workspace']}'::uuid
  and request.client_id = 'squid'
  and request.plan_id = '{ids['plan']}'::uuid;
""")
            source_lineage = canonical_identity.get("source_lineage")
            if not isinstance(source_lineage, dict):
                raise RuntimeError("Codex QA request omitted source lineage")
            expected_work_key = _codex_work_key_from_lineage(source_lineage)
            reviewer_binding_sha256 = str(
                canonical_identity.get("reviewer_binding_sha256", "")
            )
            expected_assignment_key = _codex_assignment_key(
                expected_work_key, reviewer_binding_sha256
            )
            if (
                canonical_identity.get("work_key") != expected_work_key
                or canonical_identity.get("assignment_key")
                    != expected_assignment_key
            ):
                raise RuntimeError(
                    "Codex QA DB identity drifted from the offline runner"
                )

            def invoke_claim(_: int) -> dict[str, object]:
                return psql.json(_rpc_sql(
                    qa_claims(specialist_principals["independent_qa"]),
                    _codex_claim_expression(ids),
                ))

            claimed, codex_qa_races["claim"] = _race_codex_claim(invoke_claim)
            if (claimed["work_key"], claimed["request_key"]) != (
                codex_work_key, request_key
            ):
                raise RuntimeError("Codex QA claim selected another request")
            claim_fence = str(claimed["claim_fence_sha256"])

            # Exercise the frozen-actor prefilter against an actually eligible
            # reconciliation candidate.  A claimed run is candidate-eligible;
            # a pending/current run would produce the same no-op before the
            # reviewer binding predicates are reached.
            reconciliation_before_wrong_actor = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'requests', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_requests
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'runs', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_runs
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'reconciliations', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_reconciliation_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'status', (select pg_catalog.min(status)
    from private.harmony_preview_codex_gate_runs
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and work_key = '{codex_work_key}')
)::text;
""")
            wrong_actor_reconciliation = psql.json(_rpc_sql(
                qa_claims(uid()), _codex_reconcile_expression(ids)
            ))
            if wrong_actor_reconciliation != {
                "blocked": False,
                "outcome_unknown": False,
                "pending": False,
                "reconciled": False,
                "work_key": None,
            }:
                raise RuntimeError(
                    "wrong QA actor observed or locked a frozen reconciliation"
                )
            reconciliation_after_wrong_actor = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'requests', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_requests
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'runs', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_runs
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'reconciliations', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_reconciliation_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'status', (select pg_catalog.min(status)
    from private.harmony_preview_codex_gate_runs
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and work_key = '{codex_work_key}')
)::text;
""")
            if reconciliation_after_wrong_actor != reconciliation_before_wrong_actor:
                raise RuntimeError(
                    "wrong QA actor changed the frozen reconciliation ledger"
                )

            def invoke_start(_: int) -> dict[str, object]:
                return psql.json(_rpc_sql(
                    qa_claims(specialist_principals["independent_qa"]),
                    _codex_start_expression(ids, codex_work_key, claim_fence),
                ))

            started, codex_qa_races["start"] = _race_codex_start(invoke_start)
            attempt_fence = str(started["attempt_fence_sha256"])
            criteria = {
                "automatic_publication_off": True,
                "factual_binding": True,
                "no_external_calls": True,
                "output_contract_valid": True,
                "private_boundary_preserved": True,
                "source_lineage_complete": True,
            }
            qa_output_sha256 = hashlib.sha256(
                f"durable-codex-qa:{private_output_sha256}".encode()
            ).hexdigest()

            def invoke_submit(_: int) -> dict[str, object]:
                return psql.json(_rpc_sql(
                    qa_claims(specialist_principals["independent_qa"]),
                    _codex_submit_result_expression(
                        ids, codex_work_key, attempt_fence, criteria,
                        qa_output_sha256=qa_output_sha256,
                        verdict="pass", finding_codes=[],
                    ),
                ))

            _, codex_qa_races["submit"] = _race_codex_idempotent(
                "Codex QA submit", invoke_submit, ("work_key", "result_sha256")
            )

            def invoke_verify(_: int) -> dict[str, object]:
                return psql.json(_rpc_sql(
                    qa_claims(specialist_principals["independent_qa"]),
                    _codex_verify_expression(ids, codex_work_key),
                ))

            verified, codex_qa_races["verify"] = _race_codex_idempotent(
                "Codex QA verify", invoke_verify,
                ("work_key", "verification_receipt_sha256"),
            )
            verified_stage = verified.get("stage_receipt")
            if (
                verified.get("status") != "operator_review_pending"
                or not isinstance(verified_stage, dict)
                or verified_stage.get("stage") != "independent_qa"
                or verified_stage.get("plan_id") != ids["plan"]
            ):
                raise RuntimeError(
                    "Codex QA verify did not atomically create the QA stage"
                )
            operation_races["independent_qa"] = codex_qa_races["verify"]

    denial_ids = {
        **ids,
        "round": uid(),
        "plan": uid(),
        "inbox": uid(),
    }
    denial_quiz_registration = connector_registrations["quiz_bot"]
    denial_quiz_body = _signal_body(
        workspace_id=ids["workspace"],
        signal_id=uid(),
        source_event_id=uid(),
        principal_id=denial_quiz_registration["principal_id"],
        signal_kind="quiz_learning",
        lane="quiz_bot",
        topic_codes=[topic],
        observed_at=observed_at,
        expires_at=expires_at,
        upstream_receipt_sha256=hashlib.sha256(
            ("denial-quiz:" + ids["slug"]).encode()
        ).hexdigest(),
        evidence_sha256=hashlib.sha256(
            ("denial-evidence:" + ids["slug"]).encode()
        ).hexdigest(),
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        extra={
            "data_classification": "aggregate_anonymous",
            "attempts": 32,
            "participants": 8,
            "accuracy_basis_points": 5000,
            "tutorial_priority_basis_points": 9000,
        },
    )
    denial_quiz_signal = _with_db_hash(psql, denial_quiz_body)
    denial_quiz_receipt_id = uid()
    denial_quiz_request_sha256 = _connector_request_sha256(
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=denial_quiz_registration["registration_id"],
        connector_receipt_id=denial_quiz_receipt_id,
        signal=denial_quiz_signal,
    )
    _assert_connector_request_sha256_matches_database(
        psql,
        expected_sha256=denial_quiz_request_sha256,
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=denial_quiz_registration["registration_id"],
        connector_receipt_id=denial_quiz_receipt_id,
        signal=denial_quiz_signal,
    )
    denial_quiz_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_connector",
        capability="harmony_submit_quiz_bot",
        principal_id=denial_quiz_registration["principal_id"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        connector_id=denial_quiz_registration["connector_id"],
        attestation_registration_id=(
            denial_quiz_registration["registration_id"]
        ),
        attestation_key_id=denial_quiz_registration["attestation_key_id"],
        request_sha256=denial_quiz_request_sha256,
    )
    denial_quiz_claims["exp"] = min(
        int(denial_quiz_claims["exp"]), int(expires.timestamp())
    )
    denial_signal_result = psql.json(_rpc_sql(
        denial_quiz_claims,
        _submit_expression(
            ids,
            denial_quiz_receipt_id,
            denial_quiz_signal,
        ),
    ))
    if (
        denial_signal_result.get("ok") is not True
        or denial_signal_result.get("reused") is not False
        or not isinstance(
            denial_signal_result.get("connector_request_receipt"), dict
        )
    ):
        raise RuntimeError(
            f"denial-round quiz signal failed: {denial_signal_result}"
        )

    denial_signal_hashes = sorted(
        [denial_quiz_signal["payload_sha256"]]
        + [row[2]["payload_sha256"] for row in signals[1:]]
        + [source_signal["payload_sha256"]]
    )
    denial_array_sql = (
        "array["
        + ",".join(_sql_literal(value) for value in denial_signal_hashes)
        + "]::text[]"
    )

    def denial_plan_expression(receipt_id: str) -> str:
        return (
            "public.create_preview_harmony_squid_plan("
            f"'{ids['workspace']}'::uuid, 'squid', "
            f"'{denial_ids['round']}'::uuid, '{denial_ids['plan']}'::uuid, "
            f"'{receipt_id}'::uuid, {denial_array_sql}, '{topic}')"
        )

    denial_plan_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_orchestrator",
        capability="harmony_plan",
        principal_id=specialist_principals["plan"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    denial_plan = psql.json(_rpc_sql(
        denial_plan_claims,
        denial_plan_expression(uid()),
    ))
    if denial_plan.get("ok") is not True or denial_plan.get("reused") is not False:
        raise RuntimeError(f"denial-round plan failed: {denial_plan}")

    denial_content_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_content",
        capability="harmony_prepare_private_content",
        principal_id=specialist_principals["private_content"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    denial_content = psql.json(_rpc_sql(
        denial_content_claims,
        _stage_expression(denial_ids, "private_content", uid()),
    ))
    denial_content_receipt = denial_content.get("stage_receipt")
    if (
        denial_content.get("ok") is not True
        or denial_content.get("reused") is not False
        or not isinstance(denial_content_receipt, dict)
    ):
        raise RuntimeError(
            f"denial-round private content failed: {denial_content}"
        )
    denied_output_sha256 = str(denial_content_receipt["output_sha256"])
    failed_qa_evidence = {
        "schema_version": "harmony-independent-qa-evidence@1",
        "reviewed_output_sha256": denied_output_sha256,
        "criteria": {
            "automatic_publication": False,
            "factual_binding": False,
            "no_external_calls": True,
            "private_only": True,
        },
        "findings": ["factual_binding_failed"],
        "verdict": "failed",
        "verifier_version": "harmony-deterministic-qa@1",
    }
    denial_downstream_sql = f"""
select pg_catalog.jsonb_build_object(
  'passed_qa_stages', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{denial_ids['plan']}'::uuid
      and stage = 'independent_qa'),
  'operator_inbox', (select pg_catalog.count(*)
    from agent_runtime.harmony_operator_inbox
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{denial_ids['plan']}'::uuid),
  'recap_stages', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{denial_ids['plan']}'::uuid and stage = 'recap'),
  'qa_denials', (select pg_catalog.count(*)
    from private.harmony_preview_qa_denial_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'
      and plan_id = '{denial_ids['plan']}'::uuid)
)::text;
"""
    denial_downstream_before = psql.json(denial_downstream_sql)
    if denial_downstream_before != {
        "passed_qa_stages": 0,
        "operator_inbox": 0,
        "recap_stages": 0,
        "qa_denials": 0,
    }:
        raise RuntimeError(
            f"denial round had premature downstream rows: {denial_downstream_before}"
        )

    def invoke_qa_denial(_: int) -> dict[str, object]:
        claims = _claims(
            workspace_id=ids["workspace"],
            branch_ref=branch_ref,
            role="coineasy_harmony_qa",
            capability="harmony_independent_qa",
            principal_id=specialist_principals["independent_qa"],
            release_sha=args.release_sha,
            config_sha256=args.config_sha256,
        )
        return psql.json(_rpc_sql(
            claims,
            _qa_denial_expression(denial_ids, uid(), failed_qa_evidence),
        ))

    qa_denial, qa_denial_race = _race_qa_denial(invoke_qa_denial)
    qa_denial_receipt = qa_denial.get("qa_denial_receipt")
    if (
        not isinstance(qa_denial_receipt, dict)
        or qa_denial_receipt.get("denied_output_sha256")
            != denied_output_sha256
        or qa_denial_receipt.get("reviewer_principal_id")
            != specialist_principals["independent_qa"]
        or qa_denial_receipt.get("finding_codes")
            != ["factual_binding_failed"]
        or not HEX_SHA256_PATTERN.fullmatch(
            str(qa_denial_receipt.get("payload_sha256", ""))
        )
    ):
        raise RuntimeError(f"QA denial receipt binding mismatch: {qa_denial}")
    denial_downstream_after = psql.json(denial_downstream_sql)
    if denial_downstream_after != {
        "passed_qa_stages": 0,
        "operator_inbox": 0,
        "recap_stages": 0,
        "qa_denials": 1,
    }:
        raise RuntimeError(
            "failed QA changed downstream rows or duplicated denials: "
            f"{denial_downstream_before} -> {denial_downstream_after}"
        )
    denial_qa_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_qa",
        capability="harmony_independent_qa",
        principal_id=specialist_principals["independent_qa"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    psql.expect_error(
        _rpc_sql(
            denial_qa_claims,
            _codex_prepare_expression(denial_ids),
        ),
        "harmony_preview_qa_output_already_denied",
    )
    if psql.json(denial_downstream_sql) != denial_downstream_after:
        raise RuntimeError("denied output created a passed or downstream row")

    # Build a second passing Codex result, then supersede its bound content
    # version before verification.  Sixty-four reconcilers must converge on a
    # single immutable ``result_not_current`` receipt, leaving no QA stage,
    # representative inbox, or recap for the stale plan.
    stale_ids = {
        **ids,
        "item": ids["stale_item"],
        "version": ids["stale_version"],
        "job": ids["stale_job"],
        "round": uid(),
        "plan": uid(),
        "inbox": uid(),
    }
    stale_source_body = _signal_body(
        workspace_id=ids["workspace"],
        signal_id=uid(),
        source_event_id=stale_ids["version"],
        principal_id=source_principal,
        signal_kind="official_source",
        lane="content_source",
        topic_codes=[topic],
        observed_at=observed_at,
        expires_at=expires_at,
        upstream_receipt_sha256="0" * 64,
        evidence_sha256=source_body_sha,
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        extra={
            "data_classification": "public_official",
            "source_item_id": ids["source"],
            "source_body_sha256": source_body_sha,
            "source_kind": "x_post_text",
            "source_verified": True,
            "eligible_content_kinds": ["daily_news"],
        },
    )
    stale_source_binding = psql.run(
        "select private.harmony_preview_squid_official_source_binding("
        + _sql_literal(_compact(stale_source_body))
        + "::jsonb);"
    )
    if len(stale_source_binding) != 64:
        raise RuntimeError(
            "stale-result official source binding was not derived from ledger"
        )
    stale_source_body["upstream_receipt_sha256"] = stale_source_binding
    stale_source_signal = _with_db_hash(psql, stale_source_body)
    stale_source_receipt_id = uid()
    stale_source_request_sha256 = _connector_request_sha256(
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=source_registration["registration_id"],
        connector_receipt_id=stale_source_receipt_id,
        signal=stale_source_signal,
    )
    _assert_connector_request_sha256_matches_database(
        psql,
        expected_sha256=stale_source_request_sha256,
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=source_registration["registration_id"],
        connector_receipt_id=stale_source_receipt_id,
        signal=stale_source_signal,
    )
    stale_source_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_connector",
        capability="harmony_submit_content_source",
        principal_id=source_principal,
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        connector_id=source_registration["connector_id"],
        attestation_registration_id=source_registration["registration_id"],
        attestation_key_id=source_registration["attestation_key_id"],
        request_sha256=stale_source_request_sha256,
    )
    stale_source_claims["exp"] = min(
        int(stale_source_claims["exp"]), int(expires.timestamp())
    )
    stale_source_result = psql.json(_rpc_sql(
        stale_source_claims,
        _submit_expression(
            stale_ids,
            stale_source_receipt_id,
            stale_source_signal,
        ),
    ))
    if (
        stale_source_result.get("ok") is not True
        or stale_source_result.get("reused") is not False
        or not isinstance(
            stale_source_result.get("connector_request_receipt"), dict
        )
    ):
        raise RuntimeError(
            f"stale-result official source submission failed: "
            f"{stale_source_result}"
        )
    stale_quiz_registration = connector_registrations["quiz_bot"]
    stale_quiz_body = _signal_body(
        workspace_id=ids["workspace"],
        signal_id=uid(),
        source_event_id=uid(),
        principal_id=stale_quiz_registration["principal_id"],
        signal_kind="quiz_learning",
        lane="quiz_bot",
        topic_codes=[topic],
        observed_at=observed_at,
        expires_at=expires_at,
        upstream_receipt_sha256=hashlib.sha256(
            ("stale-result-quiz:" + ids["slug"]).encode()
        ).hexdigest(),
        evidence_sha256=hashlib.sha256(
            ("stale-result-evidence:" + ids["slug"]).encode()
        ).hexdigest(),
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        extra={
            "data_classification": "aggregate_anonymous",
            "attempts": 48,
            "participants": 12,
            "accuracy_basis_points": 6900,
            "tutorial_priority_basis_points": 8500,
        },
    )
    stale_quiz_signal = _with_db_hash(psql, stale_quiz_body)
    stale_quiz_receipt_id = uid()
    stale_quiz_request_sha256 = _connector_request_sha256(
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=stale_quiz_registration["registration_id"],
        connector_receipt_id=stale_quiz_receipt_id,
        signal=stale_quiz_signal,
    )
    _assert_connector_request_sha256_matches_database(
        psql,
        expected_sha256=stale_quiz_request_sha256,
        workspace_id=ids["workspace"],
        client_id=CLIENT_ID,
        registration_id=stale_quiz_registration["registration_id"],
        connector_receipt_id=stale_quiz_receipt_id,
        signal=stale_quiz_signal,
    )
    stale_quiz_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_connector",
        capability="harmony_submit_quiz_bot",
        principal_id=stale_quiz_registration["principal_id"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        connector_id=stale_quiz_registration["connector_id"],
        attestation_registration_id=stale_quiz_registration["registration_id"],
        attestation_key_id=stale_quiz_registration["attestation_key_id"],
        request_sha256=stale_quiz_request_sha256,
    )
    stale_quiz_claims["exp"] = min(
        int(stale_quiz_claims["exp"]), int(expires.timestamp())
    )
    stale_signal_result = psql.json(_rpc_sql(
        stale_quiz_claims,
        _submit_expression(
            ids,
            stale_quiz_receipt_id,
            stale_quiz_signal,
        ),
    ))
    if (
        stale_signal_result.get("ok") is not True
        or stale_signal_result.get("reused") is not False
        or not isinstance(
            stale_signal_result.get("connector_request_receipt"), dict
        )
    ):
        raise RuntimeError(
            f"stale-result quiz signal failed: {stale_signal_result}"
        )

    stale_signal_hashes = sorted(
        [stale_quiz_signal["payload_sha256"]]
        + [row[2]["payload_sha256"] for row in signals[1:]]
        + [stale_source_signal["payload_sha256"]]
    )
    stale_array_sql = (
        "array["
        + ",".join(_sql_literal(value) for value in stale_signal_hashes)
        + "]::text[]"
    )
    stale_plan_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_orchestrator",
        capability="harmony_plan",
        principal_id=specialist_principals["plan"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    stale_plan = psql.json(_rpc_sql(
        stale_plan_claims,
        "public.create_preview_harmony_squid_plan("
        f"'{ids['workspace']}'::uuid, 'squid', "
        f"'{stale_ids['round']}'::uuid, '{stale_ids['plan']}'::uuid, "
        f"'{uid()}'::uuid, {stale_array_sql}, '{topic}')",
    ))
    if stale_plan.get("ok") is not True or stale_plan.get("reused") is not False:
        raise RuntimeError(f"stale-result plan failed: {stale_plan}")

    stale_content_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_content",
        capability="harmony_prepare_private_content",
        principal_id=specialist_principals["private_content"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    stale_content = psql.json(_rpc_sql(
        stale_content_claims,
        _stage_expression(stale_ids, "private_content", uid()),
    ))
    stale_content_receipt = stale_content.get("stage_receipt")
    if (
        stale_content.get("ok") is not True
        or stale_content.get("reused") is not False
        or not isinstance(stale_content_receipt, dict)
    ):
        raise RuntimeError(
            f"stale-result private content failed: {stale_content}"
        )
    stale_private_output_sha256 = str(
        stale_content_receipt["output_sha256"]
    )
    stale_qa_claims = _claims(
        workspace_id=ids["workspace"],
        branch_ref=branch_ref,
        role="coineasy_harmony_qa",
        capability="harmony_independent_qa",
        principal_id=specialist_principals["independent_qa"],
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    stale_prepared = psql.json(_rpc_sql(
        stale_qa_claims,
        _codex_prepare_expression(stale_ids),
    ))
    stale_work_key = str(stale_prepared.get("work_key", ""))
    if (
        stale_prepared.get("reused") is not False
        or not HEX_SHA256_PATTERN.fullmatch(stale_work_key)
    ):
        raise RuntimeError(
            f"stale-result Codex prepare failed: {stale_prepared}"
        )
    stale_claimed = psql.json(_rpc_sql(
        stale_qa_claims,
        _codex_claim_expression(stale_ids),
    ))
    stale_claim_fence = str(stale_claimed.get("claim_fence_sha256", ""))
    if (
        stale_claimed.get("claimed") is not True
        or stale_claimed.get("work_key") != stale_work_key
        or not HEX_SHA256_PATTERN.fullmatch(stale_claim_fence)
    ):
        raise RuntimeError(
            f"stale-result Codex claim failed: {stale_claimed}"
        )
    stale_started = psql.json(_rpc_sql(
        stale_qa_claims,
        _codex_start_expression(stale_ids, stale_work_key, stale_claim_fence),
    ))
    stale_attempt_fence = str(
        stale_started.get("attempt_fence_sha256", "")
    )
    if (
        stale_started.get("reused") is not False
        or stale_started.get("work_key") != stale_work_key
        or not HEX_SHA256_PATTERN.fullmatch(stale_attempt_fence)
    ):
        raise RuntimeError(
            f"stale-result Codex start failed: {stale_started}"
        )
    stale_criteria = {
        "automatic_publication_off": True,
        "factual_binding": True,
        "no_external_calls": True,
        "output_contract_valid": True,
        "private_boundary_preserved": True,
        "source_lineage_complete": True,
    }
    stale_qa_output_sha256 = hashlib.sha256(
        f"stale-durable-codex-qa:{stale_private_output_sha256}".encode()
    ).hexdigest()
    stale_submitted = psql.json(_rpc_sql(
        stale_qa_claims,
        _codex_submit_result_expression(
            stale_ids,
            stale_work_key,
            stale_attempt_fence,
            stale_criteria,
            qa_output_sha256=stale_qa_output_sha256,
            verdict="pass",
            finding_codes=[],
        ),
    ))
    if (
        stale_submitted.get("reused") is not False
        or stale_submitted.get("status") != "result_submitted"
        or stale_submitted.get("work_key") != stale_work_key
    ):
        raise RuntimeError(
            f"stale-result Codex submit failed: {stale_submitted}"
        )

    stale_round_current_sql = f"""
select private.harmony_preview_round_inputs_current(
  round.workspace_id, round.client_id, round.signal_manifest
)::text
from agent_runtime.harmony_rounds round
where round.workspace_id = '{ids['workspace']}'::uuid
  and round.client_id = 'squid'
  and round.round_id = '{stale_ids['round']}'::uuid;
"""
    if psql.run(stale_round_current_sql) != "true":
        raise RuntimeError(
            "stale-result round was not current before content supersession"
        )

    superseding_version_id = uid()
    superseded = psql.json(f"""
insert into public.content_versions(
  id, workspace_id, content_item_id, version_number, prompt_version,
  locale, title, content, channel_copy, deliverables, qa, generation_meta
)
select
  '{superseding_version_id}'::uuid, workspace_id, content_item_id, 2,
  'harmony-preview-probe@2', locale, title, content, channel_copy,
  deliverables, qa, generation_meta
from public.content_versions
where workspace_id = '{ids['workspace']}'::uuid
  and content_item_id = '{stale_ids['item']}'::uuid
  and id = '{stale_ids['version']}'::uuid;
update public.content_items
set current_version_id = '{superseding_version_id}'::uuid
where workspace_id = '{ids['workspace']}'::uuid
  and client_id = 'squid'
  and id = '{stale_ids['item']}'::uuid;
select pg_catalog.jsonb_build_object(
  'current_version_id', current_version_id,
  'versions', (select pg_catalog.count(*) from public.content_versions version
    where version.workspace_id = item.workspace_id
      and version.content_item_id = item.id)
)::text
from public.content_items item
where workspace_id = '{ids['workspace']}'::uuid
  and client_id = 'squid'
  and id = '{stale_ids['item']}'::uuid;
""")
    if superseded != {
        "current_version_id": superseding_version_id,
        "versions": 2,
    }:
        raise RuntimeError(
            f"stale-result content supersession failed: {superseded}"
        )

    def invoke_stale_reconciliation(_: int) -> dict[str, object]:
        return psql.json(_rpc_sql(
            stale_qa_claims,
            _codex_reconcile_expression(stale_ids),
        ))

    _, stale_reconciliation_race = _race_codex_reconciliation(
        invoke_stale_reconciliation,
        expected_work_key=stale_work_key,
    )
    stale_reconciliation_receipt = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'run_status', run.status,
  'reconciliations', pg_catalog.count(distinct reconciliation.reconciliation_receipt_id),
  'action', pg_catalog.min(reconciliation.reconciliation_action),
  'attempt_bound', pg_catalog.bool_and(
    reconciliation.attempt_receipt_id = result.attempt_receipt_id
  ),
  'result_bound', pg_catalog.bool_and(
    reconciliation.result_receipt_id = result.result_receipt_id
  ),
  'transition_kind', pg_catalog.min(transition.transition_kind),
  'transition_from', pg_catalog.min(transition.from_state),
  'transition_to', pg_catalog.min(transition.to_state),
  'terminal_reason', pg_catalog.min(transition.terminal_reason),
  'reconciler_principal_id', pg_catalog.min(
    reconciliation.payload ->> 'reconciler_principal_id'
  ),
  'qa_stages', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts stage
    where stage.workspace_id = request.workspace_id
      and stage.client_id = request.client_id
      and stage.plan_id = request.plan_id
      and stage.stage = 'independent_qa'),
  'verifications', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_verification_receipts verification
    where verification.workspace_id = request.workspace_id
      and verification.client_id = request.client_id
      and verification.request_id = request.request_id),
  'stage_links', (select pg_catalog.count(*)
    from private.harmony_preview_codex_gate_stage_links link
    where link.workspace_id = request.workspace_id
      and link.client_id = request.client_id
      and link.request_id = request.request_id),
  'operator_inbox', (select pg_catalog.count(*)
    from agent_runtime.harmony_operator_inbox inbox
    where inbox.workspace_id = request.workspace_id
      and inbox.client_id = request.client_id
      and inbox.plan_id = request.plan_id),
  'recap_stages', (select pg_catalog.count(*)
    from agent_runtime.harmony_stage_receipts stage
    where stage.workspace_id = request.workspace_id
      and stage.client_id = request.client_id
      and stage.plan_id = request.plan_id
      and stage.stage = 'recap')
)::text
from private.harmony_preview_codex_gate_requests request
join private.harmony_preview_codex_gate_runs run
  on run.workspace_id = request.workspace_id
 and run.client_id = request.client_id
 and run.request_id = request.request_id
join private.harmony_preview_codex_gate_result_receipts result
  on result.workspace_id = request.workspace_id
 and result.client_id = request.client_id
 and result.request_id = request.request_id
join private.harmony_preview_codex_gate_reconciliation_receipts reconciliation
  on reconciliation.workspace_id = request.workspace_id
 and reconciliation.client_id = request.client_id
 and reconciliation.request_id = request.request_id
join private.harmony_preview_codex_gate_transitions transition
  on transition.workspace_id = reconciliation.workspace_id
 and transition.client_id = reconciliation.client_id
 and transition.request_id = reconciliation.request_id
 and transition.transition_id = reconciliation.transition_id
where request.workspace_id = '{ids['workspace']}'::uuid
  and request.client_id = 'squid'
  and request.plan_id = '{stale_ids['plan']}'::uuid
group by request.workspace_id, request.client_id, request.request_id,
  request.plan_id, run.status;
""")
    if stale_reconciliation_receipt != {
        "run_status": "blocked",
        "reconciliations": 1,
        "action": "result_not_current",
        "attempt_bound": True,
        "result_bound": True,
        "transition_kind": "reconcile",
        "transition_from": "result_submitted",
        "transition_to": "blocked",
        "terminal_reason": "request_not_current",
        "reconciler_principal_id": specialist_principals["independent_qa"],
        "qa_stages": 0,
        "verifications": 0,
        "stage_links": 0,
        "operator_inbox": 0,
        "recap_stages": 0,
    }:
        raise RuntimeError(
            "stale-result reconciliation receipt binding mismatch: "
            f"{stale_reconciliation_receipt}"
        )
    psql.expect_error(
        _rpc_sql(
            stale_qa_claims,
            _codex_verify_expression(stale_ids, stale_work_key),
        ),
        "harmony_preview_codex_gate_not_current",
    )

    round_current_sql = f"""
select private.harmony_preview_round_inputs_current(
  round.workspace_id, round.client_id, round.signal_manifest
)::text
from agent_runtime.harmony_rounds round
where round.workspace_id = '{ids['workspace']}'::uuid
  and round.client_id = 'squid'
  and round.round_id = '{ids['round']}'::uuid;
"""
    denial_round_current_sql = f"""
select private.harmony_preview_round_inputs_current(
  round.workspace_id, round.client_id, round.signal_manifest
)::text
from agent_runtime.harmony_rounds round
where round.workspace_id = '{ids['workspace']}'::uuid
  and round.client_id = 'squid'
  and round.round_id = '{denial_ids['round']}'::uuid;
"""
    if psql.run(round_current_sql) != "true":
        raise RuntimeError("completed round was not current before revocation")
    if psql.run(denial_round_current_sql) != "true":
        raise RuntimeError("denial round was not current before revocation")
    if psql.run(stale_round_current_sql) != "false":
        raise RuntimeError(
            "superseded stale-result round remained current before revocation"
        )
    quiz_registration = connector_registrations["quiz_bot"]
    revocation_id = uid()
    revocation_race = _revocation_lock_winner_race(
        psql,
        revocation_sql=_revoke_connector_registration_sql(
            workspace_id=ids["workspace"],
            registration_id=quiz_registration["registration_id"],
            revocation_id=revocation_id,
        ),
        loser_sql=_rpc_sql(
            denial_qa_claims,
            _codex_prepare_expression(denial_ids),
        ),
        expected_loser_error="harmony_preview_plan_input_not_current",
    )
    revocation = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'revocations', pg_catalog.count(*),
  'registration_id', pg_catalog.min(revocation.registration_id::text),
  'reason_code', pg_catalog.min(revocation.reason_code)
)::text
from private.harmony_preview_connector_registration_revocations revocation
where revocation.workspace_id = '{ids['workspace']}'::uuid
  and revocation.client_id = 'squid'
  and revocation.registration_id = '{quiz_registration['registration_id']}'::uuid;
""")
    if revocation != {
        "revocations": 1,
        "registration_id": quiz_registration["registration_id"],
        "reason_code": "connector_disabled",
    }:
        raise RuntimeError(f"connector revocation failed closed: {revocation}")
    if psql.run(round_current_sql) != "false":
        raise RuntimeError("revoked connector left the completed round current")
    if psql.run(denial_round_current_sql) != "false":
        raise RuntimeError("revoked connector left the denial round current")
    if psql.run(stale_round_current_sql) != "false":
        raise RuntimeError("revoked connector left the stale-result round current")

    revoked_negative_before = psql.json(denial_downstream_sql)
    psql.expect_error(
        _rpc_sql(
            denial_qa_claims,
            _codex_prepare_expression(denial_ids),
        ),
        "harmony_preview_plan_input_not_current",
    )
    psql.expect_error(
        _rpc_sql(
            denial_qa_claims,
            _codex_verify_expression(ids, codex_work_key),
        ),
        "harmony_preview_codex_gate_not_current",
    )
    psql.expect_error(
        _rpc_sql(
            denial_qa_claims,
            _qa_denial_expression(denial_ids, uid(), failed_qa_evidence),
        ),
        "harmony_preview_plan_input_not_current",
    )
    if psql.json(denial_downstream_sql) != revoked_negative_before:
        raise RuntimeError(
            "revoked stage or denial typed-negative changed downstream rows"
        )

    after = psql.json(_baseline_sql(ids["workspace"], ids["version"]))
    if after != before:
        raise RuntimeError(f"forbidden approval/publication/Buzz/provider delta: {before} -> {after}")
    counts = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'signals', (select pg_catalog.count(*) from agent_runtime.harmony_signals
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'connector_receipts', (select pg_catalog.count(*) from agent_runtime.harmony_connector_attestation_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'connector_registrations', (select pg_catalog.count(*)
    from private.harmony_preview_connector_registrations
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'request_receipts', (select pg_catalog.count(*)
    from private.harmony_preview_connector_request_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'connector_revocations', (select pg_catalog.count(*)
    from private.harmony_preview_connector_registration_revocations
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'qa_denial_receipts', (select pg_catalog.count(*)
    from private.harmony_preview_qa_denial_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'rounds', (select pg_catalog.count(*) from agent_runtime.harmony_rounds
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'plans', (select pg_catalog.count(*) from agent_runtime.harmony_plans
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'specialists', (select pg_catalog.count(*)
    from private.harmony_preview_squid_specialist_bindings
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'distinct_specialist_principals', (select pg_catalog.count(distinct principal_id)
    from private.harmony_preview_squid_specialist_bindings
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'stage_receipts', (select pg_catalog.count(*) from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'distinct_operation_keys', (select pg_catalog.count(distinct operation_key_sha256)
    from agent_runtime.harmony_stage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'operator_inbox', (select pg_catalog.count(*) from agent_runtime.harmony_operator_inbox
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_lineages', (select pg_catalog.count(*) from private.harmony_preview_codex_source_lineage_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_requests', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_requests
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_runs', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_runs
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_transitions', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_transitions
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_claims', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_claim_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_attempts', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_attempt_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_evidence', (select pg_catalog.count(*) from private.harmony_preview_codex_semantic_qa_evidence
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_results', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_result_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_verifications', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_verification_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_reconciliations', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_reconciliation_receipts
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'codex_stage_links', (select pg_catalog.count(*) from private.harmony_preview_codex_gate_stage_links
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'qa_principal_independent', (
    select qa.principal_id <> plan.principal_id and qa.principal_id <> content.principal_id
    from agent_runtime.harmony_stage_receipts qa
    join agent_runtime.harmony_stage_receipts plan using (workspace_id, client_id, plan_id)
    join agent_runtime.harmony_stage_receipts content using (workspace_id, client_id, plan_id)
    where qa.workspace_id = '{ids['workspace']}'::uuid and qa.stage = 'independent_qa'
      and plan.stage = 'plan' and content.stage = 'private_content'
  ),
  'automatic_publication', (select pg_catalog.bool_or(automatic_publication)
    from agent_runtime.harmony_rounds where workspace_id = '{ids['workspace']}'::uuid),
  'recap_cost_microusd', (select artifact -> 'actual_cost_microusd'
    from agent_runtime.harmony_stage_receipts where workspace_id = '{ids['workspace']}'::uuid and stage = 'recap')
)::text;
""")
    expected = {
        "signals": 7, "connector_receipts": 7,
        "connector_registrations": 4, "request_receipts": 7,
        "connector_revocations": 1,
        "qa_denial_receipts": 1,
        "rounds": 3, "plans": 3,
        "specialists": 5, "distinct_specialist_principals": 5,
        "stage_receipts": 9, "distinct_operation_keys": 9,
        "operator_inbox": 1,
        "codex_lineages": 2, "codex_requests": 2, "codex_runs": 2,
        "codex_transitions": 11, "codex_claims": 2, "codex_attempts": 2,
        "codex_evidence": 2, "codex_results": 2,
        "codex_verifications": 1, "codex_reconciliations": 1,
        "codex_stage_links": 1,
        "qa_principal_independent": True, "automatic_publication": False,
        "recap_cost_microusd": 0,
    }
    if counts != expected:
        raise RuntimeError(f"vertical slice ledger mismatch: {counts}")
    return {
        "ok": True,
        "schema_version": "harmony-preview-concurrency-proof@3",
        "connections": CONCURRENCY,
        "release_sha": args.release_sha,
        "config_sha256": args.config_sha256,
        "fence_expires_at": fence_expiry,
        "new": new_count,
        "reused": reused_count,
        "connector_request_race": {
            "new": new_count,
            "reused": reused_count,
        },
        "connector_trust_negative_cases": {
            "changed_digest_rejected": True,
            "same_nonce_changed_claims_rejected": True,
            "new_nonce_same_digest_rejected": True,
            "domain_row_delta": 0,
        },
        "revocation_currentness": {
            "before": True,
            "after": False,
            "history_preserved": True,
            "denial_round_before": True,
            "denial_round_after": False,
            "stale_result_round_before_supersession": True,
            "stale_result_round_before_revocation": False,
            "stale_result_round_after": False,
            "stage_after_revocation_rejected": True,
            "denial_after_revocation_rejected": True,
            "typed_negative_row_delta": 0,
        },
        "revocation_lock_winner_race": revocation_race,
        "qa_denial_race": qa_denial_race,
        "codex_result_not_current_race": stale_reconciliation_race,
        "codex_result_not_current_receipt": stale_reconciliation_receipt,
        "qa_denial_downstream_delta": {
            "qa_denial_receipts": 1,
            "passed_qa_stages": 0,
            "operator_inbox": 0,
            "recap_stages": 0,
            "approval_decisions": 0,
            "publication_rows": 0,
        },
        "operation_races": operation_races,
        "codex_qa_races": codex_qa_races,
        "codex_qa_execute_authorization": codex_qa_races["start"],
        "codex_qa_stage_atomic": True,
        "codex_qa_work_key": codex_work_key,
        "plan_exact_replay": plan_race == {
            "new": 1,
            "reused": CONCURRENCY - 1,
        },
        "plan_conflict_rejected": True,
        "stage_concurrency_proofs": 4,
        "wrong_principal_attempts": 5,
        "wrong_principal_preemption_rows": wrong_principal_preemption_rows,
        "operator_inbox_stage4_delta": operator_inbox_stage4_delta,
        "recap_operator_inbox_delta": recap_operator_inbox_delta,
        "counts": counts,
        "side_effect_baseline_unchanged": True,
        "automatic_publication": False,
        "external_calls": False,
        "provider_calls": False,
        "publication_calls": False,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        required=True,
        help="local socket/localhost, or exact direct db.<Preview-ref>.supabase.co",
    )
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="postgres")
    parser.add_argument("--psql", type=str)
    parser.add_argument("--confirm-disposable-local", action="store_true")
    parser.add_argument("--confirm-disposable-preview", action="store_true")
    parser.add_argument("--expected-branch-ref")
    parser.add_argument("--parent-project-ref")
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--command-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--fence-ttl-minutes", type=int, default=120)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = run_probe(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
