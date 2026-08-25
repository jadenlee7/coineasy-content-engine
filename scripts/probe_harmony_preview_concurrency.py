#!/usr/bin/env python3
"""Prove the disposable Harmony Preview slice with 64 real DB connections.

The probe is deliberately local/disposable only.  It seeds one immutable Squid
official-X review fixture and a five-role fixed-specialist roster, then races
the same quiz signal, plan, and each of the four downstream stages through 64
independent ``psql`` processes.  It never calls a provider, Buzz, approval, or
publication routine.  The target database is expected to be discarded after
the run.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
from typing import Callable
import uuid


CLIENT_ID = "squid"
CONCURRENCY = 64
BRANCH_REF_LENGTH = 20
HEX_SHA40_PATTERN = re.compile(r"^[a-f0-9]{40}$")
HEX_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
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

    def _execute(self, sql: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                self.command,
                input=sql,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "psql_timeout_commit_state_unknown_no_retry"
            ) from exc

    def run(self, sql: str) -> str:
        result = self._execute(sql)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def expect_error(self, sql: str, expected: str) -> None:
        result = self._execute(sql)
        combined = result.stderr + result.stdout
        if result.returncode == 0 or expected not in combined:
            raise RuntimeError(
                f"expected fail-closed error {expected!r}, got rc={result.returncode}: {combined.strip()}"
            )

    def json(self, sql: str) -> dict[str, object]:
        return json.loads(self.run(sql))


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
) -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
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
        "jti": str(uuid.uuid4()),
        "iat": now - 30,
        "exp": now + 3600,
        "automatic_publication": False,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
    }
    if connector_id is not None:
        claims["connector_id"] = connector_id
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
  'rounds', (select pg_catalog.count(*) from agent_runtime.harmony_rounds),
  'plans', (select pg_catalog.count(*) from agent_runtime.harmony_plans),
  'stage_receipts', (select pg_catalog.count(*) from agent_runtime.harmony_stage_receipts),
  'operator_inbox', (select pg_catalog.count(*) from agent_runtime.harmony_operator_inbox),
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
insert into public.content_source_links(
  workspace_id, client_id, content_item_id, source_item_id, position
) values ('{ids['workspace']}', 'squid', '{ids['item']}', '{ids['source']}', 0);
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


def _race_exactly_once(
    operation: str,
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
    preflight = psql.json(_environment_preflight_sql())
    expected_empty = {
        "fences": 0,
        "specialists": 0,
        "signals": 0,
        "connector_receipts": 0,
        "rounds": 0,
        "plans": 0,
        "stage_receipts": 0,
        "operator_inbox": 0,
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
        "inbox": uid(), "slug": uuid.uuid4().hex[:12],
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
    if seed != {"ok": True, "grok_rows": 1, "publication_rows": 0}:
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
    signals: list[tuple[str, str, dict[str, object], dict[str, object], str]] = []
    for lane, kind, capability, extra in lane_specs:
        principal = uid()
        body = _signal_body(
            workspace_id=ids["workspace"], signal_id=uid(), source_event_id=uid(),
            principal_id=principal, signal_kind=kind, lane=lane, topic_codes=[topic],
            observed_at=observed_at, expires_at=expires_at,
            upstream_receipt_sha256=hashlib.sha256((lane + ids["slug"]).encode()).hexdigest(),
            evidence_sha256=hashlib.sha256((kind + ids["slug"]).encode()).hexdigest(),
            release_sha=args.release_sha, config_sha256=args.config_sha256,
            extra=extra,
        )
        signals.append((lane, capability, _with_db_hash(psql, body), _claims(
            workspace_id=ids["workspace"], branch_ref=branch_ref,
            role="coineasy_harmony_connector", capability=capability,
            principal_id=principal, release_sha=args.release_sha,
            config_sha256=args.config_sha256, connector_id=f"{lane}_probe",
        ), uid()))

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
        "harmony_preview_connector_scope_invalid",
    )
    psql.expect_error(
        _rpc_sql(
            {**signals[1][3], "capability": "harmony_submit_quiz_bot"},
            _submit_expression(ids, signals[1][4], signals[1][2]),
        ),
        "harmony_preview_connector_scope_invalid",
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
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid')
)::text;
""")
    if race_rows != {"signals": 1, "connector_receipts": 1}:
        raise RuntimeError(f"race wrote more than one ledger row: {race_rows}")

    for lane in signals[1:]:
        result = psql.json(_rpc_sql(lane[3], _submit_expression(ids, lane[4], lane[2])))
        if result.get("ok") is not True or result.get("reused") is not False:
            raise RuntimeError(f"typed signal submission failed: {lane[0]}: {result}")

    source_body_sha = psql.run(
        f"select pg_catalog.encode(extensions.digest(pg_catalog.convert_to(body, 'UTF8'), 'sha256'), 'hex') "
        f"from public.source_items where id = '{ids['source']}'::uuid;"
    )
    source_principal = uid()
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
    source_claims = _claims(
        workspace_id=ids["workspace"], branch_ref=branch_ref,
        role="coineasy_harmony_connector", capability="harmony_submit_content_source",
        principal_id=source_principal, release_sha=args.release_sha,
        config_sha256=args.config_sha256, connector_id="official_source_probe",
    )
    source_receipt = uid()
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
            "wrong plan principal preempted the fixed specialist: "
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
            "independent_qa",
            "coineasy_harmony_qa",
            "harmony_independent_qa",
        ),
        (
            "operator_inbox",
            "coineasy_harmony_operator",
            "harmony_operator_inbox",
        ),
        ("recap", "coineasy_harmony_recap", "harmony_recap"),
    ]
    stage_results: list[dict[str, object]] = []
    wrong_principal_preemption_rows = 0
    operator_inbox_stage4_delta = 0
    recap_operator_inbox_delta = 0
    for stage, role, capability in stage_specs:
        qa_evidence: dict[str, object] | None = None
        inbox_id = ids["inbox"] if stage in {"operator_inbox", "recap"} else None
        if stage == "independent_qa":
            previous_receipt = stage_results[-1].get("stage_receipt")
            if not isinstance(previous_receipt, dict):
                raise RuntimeError("private-content race returned no receipt")
            reviewed = str(previous_receipt["output_sha256"])
            qa_evidence = {
                "schema_version": "harmony-independent-qa-evidence@1",
                "reviewed_output_sha256": reviewed,
                "criteria": {"automatic_publication": False, "factual_binding": True,
                             "no_external_calls": True, "private_only": True},
                "findings": [], "verdict": "passed",
                "verifier_version": "harmony-deterministic-qa@1",
            }
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
                _stage_expression(ids, stage, uid(), inbox_id, qa_evidence),
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
                _stage_expression(ids, stage, uid(), inbox_id, qa_evidence),
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
        stage_results.append(result)

    after = psql.json(_baseline_sql(ids["workspace"], ids["version"]))
    if after != before:
        raise RuntimeError(f"forbidden approval/publication/Buzz/provider delta: {before} -> {after}")
    counts = psql.json(f"""
select pg_catalog.jsonb_build_object(
  'signals', (select pg_catalog.count(*) from agent_runtime.harmony_signals
    where workspace_id = '{ids['workspace']}'::uuid and client_id = 'squid'),
  'connector_receipts', (select pg_catalog.count(*) from agent_runtime.harmony_connector_attestation_receipts
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
        "signals": 4, "connector_receipts": 4, "rounds": 1, "plans": 1,
        "specialists": 5, "distinct_specialist_principals": 5,
        "stage_receipts": 5, "distinct_operation_keys": 5,
        "operator_inbox": 1,
        "qa_principal_independent": True, "automatic_publication": False,
        "recap_cost_microusd": 0,
    }
    if counts != expected:
        raise RuntimeError(f"vertical slice ledger mismatch: {counts}")
    return {
        "ok": True,
        "schema_version": "harmony-preview-concurrency-proof@2",
        "connections": CONCURRENCY,
        "release_sha": args.release_sha,
        "config_sha256": args.config_sha256,
        "fence_expires_at": fence_expiry,
        "new": new_count,
        "reused": reused_count,
        "operation_races": operation_races,
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
