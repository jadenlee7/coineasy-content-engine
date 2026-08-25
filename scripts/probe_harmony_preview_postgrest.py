#!/usr/bin/env python3
"""Prove Harmony connector attestation through real signed-JWT PostgREST.

This probe is intentionally restricted to a disposable Supabase Preview
branch.  Direct PostgreSQL access is used only to seed an isolated Squid
workspace, derive the database-canonical payload hashes, and observe row
counts.  Every connector write goes through the public PostgREST RPC with a
short-lived HS256 JWT minted in memory.

The publishable project key and the Preview branch's legacy JWT secret are
accepted only through fixed environment variable names.  They are removed
from the process environment immediately after loading so child ``psql``
processes cannot inherit them.  Neither value is printed or written to disk.
HTTP transport ambiguity is fail-closed and is never retried.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Callable
from urllib import error, parse, request
import uuid


CONCURRENCY = 64
PUBLISHABLE_KEY_ENV = "HARMONY_PREVIEW_SUPABASE_PUBLISHABLE_KEY"
LEGACY_JWT_SECRET_ENV = "HARMONY_PREVIEW_SUPABASE_LEGACY_JWT_SECRET"
MAX_RESPONSE_BYTES = 262_144
RPC_NAME = "submit_preview_harmony_signal"
EXPECTED_NEGATIVE_STATUSES = frozenset({400, 401, 403, 404})
HEX_SHA40_PATTERN = re.compile(r"^[a-f0-9]{40}$")
HEX_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")


def _load_concurrency_probe():
    path = Path(__file__).with_name("probe_harmony_preview_concurrency.py")
    spec = importlib.util.spec_from_file_location(
        "harmony_preview_concurrency_probe_for_postgrest", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("harmony_preview_concurrency_probe_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = _load_concurrency_probe()


class CommitStateUnknown(RuntimeError):
    """An HTTP request may have committed; callers must not retry it."""


def _compact(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _mint_hs256_jwt(claims: dict[str, object], secret: str) -> str:
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("legacy JWT secret is unexpectedly short")
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        _b64url(_compact(header).encode("utf-8"))
        + "."
        + _b64url(_compact(claims).encode("utf-8"))
    )
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return signing_input + "." + _b64url(signature)


def _decode_jwt_json_segment(segment: str) -> dict[str, object] | None:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    return value if isinstance(value, dict) else None


def _validate_publishable_key(value: str) -> None:
    if value.startswith("sb_publishable_") and len(value) >= 24:
        return
    if value.startswith("sb_secret_"):
        raise ValueError("secret project keys are forbidden for the Preview apikey")
    segments = value.split(".")
    if len(segments) == 3:
        header = _decode_jwt_json_segment(segments[0])
        payload = _decode_jwt_json_segment(segments[1])
        if (
            header is not None
            and payload is not None
            and header.get("alg") == "HS256"
            and payload.get("iss") == "supabase"
            and payload.get("role") == "anon"
        ):
            return
        if payload is not None and payload.get("role") == "service_role":
            raise ValueError("service-role JWTs are forbidden for the Preview apikey")
    raise ValueError("Preview apikey must be an sb_publishable key or legacy anon JWT")


def _validated_project_url(
    project_url: str,
    expected_branch_ref: str | None,
    parent_project_ref: str | None,
) -> str:
    if not PROJECT_REF_PATTERN.fullmatch(expected_branch_ref or ""):
        raise ValueError("exact 20-character --expected-branch-ref is required")
    if not PROJECT_REF_PATTERN.fullmatch(parent_project_ref or ""):
        raise ValueError("exact 20-character --parent-project-ref is required")
    if expected_branch_ref == parent_project_ref:
        raise ValueError("refusing to run the PostgREST probe against Production")
    parsed = parse.urlsplit(project_url)
    expected_host = f"{expected_branch_ref}.supabase.co"
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "project URL must be exact https://<Preview-ref>.supabase.co"
        )
    return f"https://{expected_host}"


def _load_http_secrets() -> tuple[str, str]:
    publishable_key = os.environ.get(PUBLISHABLE_KEY_ENV, "")
    jwt_secret = os.environ.get(LEGACY_JWT_SECRET_ENV, "")
    try:
        _validate_publishable_key(publishable_key)
    except ValueError as exc:
        raise SystemExit(f"{PUBLISHABLE_KEY_ENV}: {exc}") from None
    if len(jwt_secret.encode("utf-8")) < 32:
        raise SystemExit(f"{LEGACY_JWT_SECRET_ENV} is missing or unexpectedly short")
    # Do not allow later psql subprocesses to inherit HTTP credentials.
    os.environ.pop(PUBLISHABLE_KEY_ENV, None)
    os.environ.pop(LEGACY_JWT_SECRET_ENV, None)
    return publishable_key, jwt_secret


class PostgrestClient:
    def __init__(
        self,
        project_url: str,
        publishable_key: str,
        timeout_seconds: float,
        *,
        opener: Callable[..., object] = request.urlopen,
    ) -> None:
        self.rpc_url = f"{project_url}/rest/v1/rpc/{RPC_NAME}"
        self.publishable_key = publishable_key
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    @staticmethod
    def _read_bounded(response: object) -> bytes:
        body = response.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("postgrest_response_too_large")
        return body

    def post(self, rpc_payload: dict[str, object], jwt: str) -> tuple[int, object]:
        encoded = _compact(rpc_payload).encode("utf-8")
        req = request.Request(
            self.rpc_url,
            data=encoded,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {jwt}",
                "Content-Type": "application/json",
                "User-Agent": "coineasy-harmony-preview-proof/1",
                "apikey": self.publishable_key,
            },
        )
        try:
            response = self.opener(req, timeout=self.timeout_seconds)
            with response:  # type: ignore[attr-defined]
                status = int(response.status)  # type: ignore[attr-defined]
                body = self._read_bounded(response)
        except error.HTTPError as exc:
            status = int(exc.code)
            body = self._read_bounded(exc)
        except (error.URLError, TimeoutError, OSError):
            raise CommitStateUnknown(
                "postgrest_commit_state_unknown_no_retry"
            ) from None
        try:
            decoded: object = json.loads(body) if body else None
        except json.JSONDecodeError:
            decoded = None
        return status, decoded


def _ensure_fence_sql(branch_ref: str, expires_at: str) -> str:
    return f"""
do $fence$
begin
  if exists (
    select 1 from private.harmony_preview_environment_fence
    where branch_ref <> '{branch_ref}'
  ) then
    raise exception 'unexpected_preview_fence_ref';
  end if;
  if not exists (
    select 1 from private.harmony_preview_environment_fence
    where branch_ref = '{branch_ref}'
  ) then
    insert into private.harmony_preview_environment_fence(branch_ref, active, expires_at)
    values ('{branch_ref}', true, '{expires_at}'::timestamptz);
  end if;
end
$fence$;
select pg_catalog.jsonb_build_object(
  'rows', pg_catalog.count(*),
  'branch_ref', pg_catalog.min(branch_ref),
  'active', pg_catalog.bool_and(active),
  'expires_at', pg_catalog.to_char(
    pg_catalog.min(expires_at) at time zone 'UTC',
    'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'
  )
)::text
from private.harmony_preview_environment_fence;
"""


def _seed_workspace_sql(workspace_id: str, slug: str) -> str:
    return f"""
begin;
insert into public.workspaces(id, name, slug)
values ('{workspace_id}', 'Harmony signed JWT probe', 'harmony-jwt-{slug}');
insert into public.workspace_clients(workspace_id, client_id, display_name, active)
values ('{workspace_id}', 'squid', 'Squid', true);
commit;
select pg_catalog.jsonb_build_object(
  'workspace_rows', (select pg_catalog.count(*) from public.workspaces
    where id = '{workspace_id}'::uuid),
  'client_rows', (select pg_catalog.count(*) from public.workspace_clients
    where workspace_id = '{workspace_id}'::uuid and client_id = 'squid')
)::text;
"""


def _side_effect_baseline_sql() -> str:
    return """
select pg_catalog.jsonb_build_object(
  'approvals', (select pg_catalog.count(*) from public.approvals),
  'publications', (select pg_catalog.count(*) from public.publications),
  'buzz_delivery_receipts', (select pg_catalog.count(*) from agent_runtime.buzz_delivery_receipts),
  'buzz_review_decisions', (select pg_catalog.count(*) from agent_runtime.buzz_review_decisions),
  'buzz_review_ack_receipts', (select pg_catalog.count(*) from agent_runtime.buzz_review_ack_receipts),
  'grok_dispatch_outbox', (select pg_catalog.count(*) from private.grok_qa_dispatch_outbox),
  'grok_verdict_receipts', (select pg_catalog.count(*) from private.grok_qa_verdict_receipts)
)::text;
"""


def _ledger_counts_sql(workspace_id: str) -> str:
    return f"""
select pg_catalog.jsonb_build_object(
  'signals', (select pg_catalog.count(*) from agent_runtime.harmony_signals
    where workspace_id = '{workspace_id}'::uuid and client_id = 'squid'),
  'connector_receipts', (select pg_catalog.count(*)
    from agent_runtime.harmony_connector_attestation_receipts
    where workspace_id = '{workspace_id}'::uuid and client_id = 'squid')
)::text;
"""


def _global_ledger_counts_sql() -> str:
    return """
select pg_catalog.jsonb_build_object(
  'signals', (select pg_catalog.count(*) from agent_runtime.harmony_signals),
  'connector_receipts', (select pg_catalog.count(*)
    from agent_runtime.harmony_connector_attestation_receipts)
)::text;
"""


def _rpc_payload(
    workspace_id: str,
    receipt_id: str,
    signal: dict[str, object],
    *,
    target_client_id: str = "squid",
) -> dict[str, object]:
    return {
        "target_workspace_id": workspace_id,
        "target_client_id": target_client_id,
        "target_receipt_id": receipt_id,
        "target_signal": signal,
    }


def _new_quiz_signal(
    psql: object,
    *,
    workspace_id: str,
    observed_at: str,
    expires_at: str,
    release_sha: str,
    config_sha256: str,
    salt: str,
) -> tuple[dict[str, object], dict[str, object], str]:
    principal_id = str(uuid.uuid4())
    body = BASE._signal_body(
        workspace_id=workspace_id,
        signal_id=str(uuid.uuid4()),
        source_event_id=str(uuid.uuid4()),
        principal_id=principal_id,
        signal_kind="quiz_learning",
        lane="quiz_bot",
        topic_codes=["official_update"],
        observed_at=observed_at,
        expires_at=expires_at,
        upstream_receipt_sha256=hashlib.sha256(
            ("quiz-upstream:" + salt).encode("utf-8")
        ).hexdigest(),
        evidence_sha256=hashlib.sha256(
            ("quiz-evidence:" + salt).encode("utf-8")
        ).hexdigest(),
        release_sha=release_sha,
        config_sha256=config_sha256,
        extra={
            "data_classification": "aggregate_anonymous",
            "attempts": 64,
            "participants": 16,
            "accuracy_basis_points": 7500,
            "tutorial_priority_basis_points": 8100,
        },
    )
    signal = BASE._with_db_hash(psql, body)
    claims = BASE._claims(
        workspace_id=workspace_id,
        branch_ref="placeholderplaceholder",
        role="coineasy_harmony_connector",
        capability="harmony_submit_quiz_bot",
        principal_id=principal_id,
        release_sha=release_sha,
        config_sha256=config_sha256,
        connector_id="squid_quiz_signed_jwt_probe",
    )
    return signal, claims, str(uuid.uuid4())


def _negative_cases(
    psql: object,
    *,
    workspace_id: str,
    branch_ref: str,
    parent_ref: str,
    observed_at: str,
    expires_at: str,
    release_sha: str,
    config_sha256: str,
) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    now = int(datetime.now(UTC).timestamp())
    for label in (
        "wrong_client",
        "wrong_workspace",
        "wrong_lane",
        "wrong_role",
        "future_jwt",
        "expired_jwt",
        "service_role",
        "wrong_ref",
        "tampered_payload",
    ):
        signal, claims, receipt_id = _new_quiz_signal(
            psql,
            workspace_id=workspace_id,
            observed_at=observed_at,
            expires_at=expires_at,
            release_sha=release_sha,
            config_sha256=config_sha256,
            salt=label + uuid.uuid4().hex,
        )
        claims["ref"] = branch_ref
        if label == "wrong_client":
            claims["client_id"] = "yellow"
        elif label == "wrong_workspace":
            claims["workspace_id"] = str(uuid.uuid4())
        elif label == "wrong_lane":
            claims["capability"] = "harmony_submit_community_ops"
        elif label == "wrong_role":
            claims["role"] = "authenticated"
        elif label == "future_jwt":
            claims["iat"] = now + 120
            claims["exp"] = now + 300
        elif label == "expired_jwt":
            claims["iat"] = now - 120
            claims["exp"] = now - 1
        elif label == "service_role":
            claims["role"] = "service_role"
        elif label == "wrong_ref":
            claims["ref"] = parent_ref
        elif label == "tampered_payload":
            signal = {
                **signal,
                "evidence_sha256": hashlib.sha256(
                    ("tampered:" + uuid.uuid4().hex).encode("utf-8")
                ).hexdigest(),
            }
        cases.append(
            {
                "label": label,
                "claims": claims,
                "rpc_payload": _rpc_payload(workspace_id, receipt_id, signal),
            }
        )
    return cases


def _validate_success(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("postgrest_success_response_not_object")
    receipt = value.get("connector_receipt")
    signal = value.get("signal")
    if (
        value.get("ok") is not True
        or value.get("external_calls") is not False
        or value.get("provider_calls") is not False
        or value.get("publication_calls") is not False
        or value.get("automatic_publication") is not False
        or not isinstance(receipt, dict)
        or receipt.get("verification_method") != "jwt"
        or receipt.get("side_effects_performed") is not False
        or receipt.get("automatic_publication") is not False
        or not isinstance(signal, dict)
    ):
        raise RuntimeError("postgrest_attestation_response_invalid")
    for key in (
        "payload_sha256",
        "verification_reference_sha256",
        "signal_payload_sha256",
    ):
        if not HEX_SHA256_PATTERN.fullmatch(str(receipt.get(key, ""))):
            raise RuntimeError("postgrest_attestation_digest_invalid")
    return value


def _run_race(
    client: PostgrestClient,
    rpc_payload: dict[str, object],
    jwt: str,
    *,
    concurrency: int = CONCURRENCY,
) -> list[dict[str, object]]:
    barrier = threading.Barrier(concurrency)

    def submit(_: int) -> dict[str, object]:
        barrier.wait(timeout=30)
        status, decoded = client.post(rpc_payload, jwt)
        if not 200 <= status < 300:
            raise RuntimeError(f"postgrest_positive_request_failed_status_{status}")
        return _validate_success(decoded)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(submit, range(concurrency)))


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    if not args.confirm_disposable_preview:
        raise SystemExit("--confirm-disposable-preview is required")
    try:
        project_url = _validated_project_url(
            args.project_url, args.expected_branch_ref, args.parent_project_ref
        )
        branch_ref = BASE._validated_disposable_preview_ref(
            args.host,
            args.port,
            args.expected_branch_ref,
            args.parent_project_ref,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not HEX_SHA40_PATTERN.fullmatch(args.release_sha or ""):
        raise SystemExit("--release-sha must be an exact lowercase 40-hex Git SHA")
    if not HEX_SHA256_PATTERN.fullmatch(args.config_sha256 or ""):
        raise SystemExit("--config-sha256 must be an exact lowercase 64-hex digest")
    if not 1 <= args.command_timeout_seconds <= 120:
        raise SystemExit("--command-timeout-seconds must be between 1 and 120")
    if not 1 <= args.http_timeout_seconds <= 30:
        raise SystemExit("--http-timeout-seconds must be between 1 and 30")
    if not 5 <= args.fence_ttl_minutes <= 120:
        raise SystemExit("--fence-ttl-minutes must be between 5 and 120")
    # Load and scrub credentials before constructing Psql or spawning any child
    # process.  The local variables remain in this process's memory only.
    publishable_key, jwt_secret = _load_http_secrets()
    executable = args.psql or BASE.shutil.which("psql")
    if not executable:
        raise SystemExit("psql executable not found")
    psql = BASE.Psql(
        executable,
        args.host,
        args.port,
        args.user,
        args.database,
        args.command_timeout_seconds,
    )

    requested_fence_expiry = (
        datetime.now(UTC) + timedelta(minutes=args.fence_ttl_minutes)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    fence = psql.json(_ensure_fence_sql(branch_ref, requested_fence_expiry))
    if (
        fence.get("rows") != 1
        or fence.get("branch_ref") != branch_ref
        or fence.get("active") is not True
    ):
        raise RuntimeError(f"Preview fence validation failed closed: {fence}")
    fence_expiry = datetime.fromisoformat(
        str(fence.get("expires_at", "")).replace("Z", "+00:00")
    )
    requested_fence_expiry_value = datetime.fromisoformat(
        requested_fence_expiry.replace("Z", "+00:00")
    )
    now = datetime.now(UTC)
    if fence_expiry <= now + timedelta(minutes=4):
        raise RuntimeError("approved Preview fence expires too soon")
    if fence_expiry > requested_fence_expiry_value + timedelta(seconds=1):
        raise RuntimeError("existing Preview fence exceeds the approved TTL")

    workspace_id = str(uuid.uuid4())
    slug = uuid.uuid4().hex[:12]
    seeded = psql.json(_seed_workspace_sql(workspace_id, slug))
    if seeded != {"workspace_rows": 1, "client_rows": 1}:
        raise RuntimeError(f"isolated Squid workspace seed failed: {seeded}")
    side_effect_before = psql.json(_side_effect_baseline_sql())
    observed = (now - timedelta(seconds=5)).replace(microsecond=0)
    signal_expiry = min(fence_expiry - timedelta(minutes=1), now + timedelta(minutes=30))
    observed_at = observed.isoformat().replace("+00:00", "Z")
    expires_at = signal_expiry.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    positive_signal, positive_claims, positive_receipt_id = _new_quiz_signal(
        psql,
        workspace_id=workspace_id,
        observed_at=observed_at,
        expires_at=expires_at,
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
        salt="positive:" + slug,
    )
    positive_claims["ref"] = branch_ref
    positive_claims["exp"] = min(
        int(positive_claims["exp"]), int(signal_expiry.timestamp())
    )
    negative_cases = _negative_cases(
        psql,
        workspace_id=workspace_id,
        branch_ref=branch_ref,
        parent_ref=args.parent_project_ref,
        observed_at=observed_at,
        expires_at=expires_at,
        release_sha=args.release_sha,
        config_sha256=args.config_sha256,
    )
    client = PostgrestClient(
        project_url, publishable_key, args.http_timeout_seconds
    )
    positive_jwt = _mint_hs256_jwt(positive_claims, jwt_secret)
    raced = _run_race(
        client,
        _rpc_payload(workspace_id, positive_receipt_id, positive_signal),
        positive_jwt,
    )
    new_count = sum(row.get("reused") is False for row in raced)
    reused_count = sum(row.get("reused") is True for row in raced)
    if (new_count, reused_count) != (1, CONCURRENCY - 1):
        raise RuntimeError(
            f"signed PostgREST exactly-once race failed: new={new_count}, reused={reused_count}"
        )
    identities = {
        (
            row["signal"]["signal_id"],  # type: ignore[index]
            row["signal"]["payload_sha256"],  # type: ignore[index]
            row["connector_receipt"]["receipt_id"],  # type: ignore[index]
            row["connector_receipt"]["payload_sha256"],  # type: ignore[index]
            row["connector_receipt"]["verification_reference_sha256"],  # type: ignore[index]
        )
        for row in raced
    }
    if len(identities) != 1:
        raise RuntimeError("signed PostgREST race returned divergent identities")
    stable_counts = psql.json(_ledger_counts_sql(workspace_id))
    if stable_counts != {"signals": 1, "connector_receipts": 1}:
        raise RuntimeError(f"signed PostgREST race wrote duplicate rows: {stable_counts}")
    stable_global_counts = psql.json(_global_ledger_counts_sql())

    negative_results: dict[str, int] = {}
    for case in negative_cases:
        label = str(case["label"])
        token = _mint_hs256_jwt(case["claims"], jwt_secret)  # type: ignore[arg-type]
        status, _decoded = client.post(case["rpc_payload"], token)  # type: ignore[arg-type]
        counts_after_case = psql.json(_ledger_counts_sql(workspace_id))
        global_counts_after_case = psql.json(_global_ledger_counts_sql())
        if counts_after_case != stable_counts:
            raise RuntimeError(f"negative gate wrote rows: {label}: {counts_after_case}")
        if global_counts_after_case != stable_global_counts:
            raise RuntimeError(
                f"negative gate wrote cross-workspace rows: {label}: "
                f"{global_counts_after_case}"
            )
        if status not in EXPECTED_NEGATIVE_STATUSES:
            raise RuntimeError(
                f"negative gate returned an unexpected status: {label}: {status}"
            )
        negative_results[label] = status

    side_effect_after = psql.json(_side_effect_baseline_sql())
    if side_effect_after != side_effect_before:
        raise RuntimeError(
            f"forbidden provider/Buzz/approval/publication delta: "
            f"{side_effect_before} -> {side_effect_after}"
        )
    return {
        "ok": True,
        "schema_version": "harmony-preview-postgrest-proof@1",
        "branch_ref": branch_ref,
        "workspace_id": workspace_id,
        "release_sha": args.release_sha,
        "config_sha256": args.config_sha256,
        "connections": CONCURRENCY,
        "new": new_count,
        "reused": reused_count,
        "counts": stable_counts,
        "verification_method": "jwt",
        "negative_matrix": negative_results,
        "negative_row_delta": 0,
        "side_effect_baseline_unchanged": True,
        "automatic_publication": False,
        "external_calls": False,
        "provider_calls": False,
        "buzz_calls": False,
        "approval_decisions": False,
        "publication_calls": False,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-url", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="postgres")
    parser.add_argument("--psql")
    parser.add_argument("--confirm-disposable-preview", action="store_true")
    parser.add_argument("--expected-branch-ref", required=True)
    parser.add_argument("--parent-project-ref", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--command-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--http-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--fence-ttl-minutes", type=int, default=120)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = run_probe(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
