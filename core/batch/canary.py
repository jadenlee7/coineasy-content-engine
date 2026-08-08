from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


CONFIG_SCHEMA = "coineasy.batch.canary.config.v2"
DISPATCH_SCHEMA = "coineasy.batch.canary.dispatch.v1"
PILOT_DAY_SCHEMA = "coineasy.batch.production-shadow.day.v1"
CANARY_ENVIRONMENTS = frozenset({"staging", "production"})
CANARY_CLIENT = "origintrail"
CANARY_DAILY_CAP_USD = Decimal("0.05")
CANARY_WINDOW = timedelta(hours=48)
PRODUCTION_SHADOW_WINDOW = timedelta(days=7)
CANARY_APPROVAL_TTL = timedelta(hours=2)
PRODUCTION_SHADOW_APPROVAL_TTL = timedelta(days=8)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,119}$")


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("canary timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID")
    try:
        normalized = str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID") from exc
    if value != normalized:
        raise ValueError(f"{name} must use canonical UUID text")
    return normalized


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _receipt(raw: str, *, expected_keys: frozenset[str]) -> Mapping[str, object]:
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Batch canary approval receipt must be JSON") from exc
    if not isinstance(parsed, dict) or frozenset(parsed) != expected_keys:
        raise ValueError("Batch canary approval receipt fields are invalid")
    return parsed


def canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def config_subject(
    *,
    environment: str,
    release_sha: str,
    supabase_url: str,
    workspace_id: str,
    allowed_clients: frozenset[str],
    daily_cap_usd: Decimal,
    max_claims: int,
    max_requests_per_batch: int,
    experiment_start_at: datetime,
    experiment_end_at: datetime,
    timezone_name: str,
    production_shadow_auto_dispatch: bool = False,
) -> dict[str, object]:
    if environment not in CANARY_ENVIRONMENTS:
        raise ValueError(
            "BATCH_CANARY_ENVIRONMENT must be staging or production"
        )
    if _RELEASE_SHA_RE.fullmatch(release_sha) is None:
        raise ValueError("BATCH_CANARY_RELEASE_SHA must be 40 lowercase hex")
    parsed_supabase_url = urlsplit(supabase_url)
    if (
        parsed_supabase_url.scheme != "https"
        or parsed_supabase_url.hostname is None
        or not parsed_supabase_url.hostname.endswith(".supabase.co")
        or parsed_supabase_url.username is not None
        or parsed_supabase_url.password is not None
        or parsed_supabase_url.port is not None
        or parsed_supabase_url.path not in {"", "/"}
        or parsed_supabase_url.query
        or parsed_supabase_url.fragment
    ):
        raise ValueError("Batch canary Supabase URL is invalid")
    normalized_workspace_id = _uuid(workspace_id, "workspace_id")
    if allowed_clients != frozenset({CANARY_CLIENT}):
        raise ValueError("Batch canary allows OriginTrail only")
    if daily_cap_usd != CANARY_DAILY_CAP_USD:
        raise ValueError("Batch canary daily cap must be exactly 0.05 USD")
    if max_claims != 1 or max_requests_per_batch != 1:
        raise ValueError("Batch canary permits one claim and one request")
    if (
        not isinstance(experiment_start_at, datetime)
        or experiment_start_at.tzinfo is None
        or not isinstance(experiment_end_at, datetime)
        or experiment_end_at.tzinfo is None
    ):
        raise ValueError("Batch canary window must be timezone-aware")
    expected_window = (
        PRODUCTION_SHADOW_WINDOW
        if production_shadow_auto_dispatch
        else CANARY_WINDOW
    )
    if experiment_end_at - experiment_start_at != expected_window:
        raise ValueError(
            "Batch production shadow window must be exactly 7 days"
            if production_shadow_auto_dispatch
            else "Batch canary window must be exactly 48 hours"
        )
    if timezone_name != "Asia/Seoul":
        raise ValueError("Batch canary timezone must be Asia/Seoul")
    kst = ZoneInfo(timezone_name)
    start_kst = experiment_start_at.astimezone(kst)
    end_kst = experiment_end_at.astimezone(kst)
    if any((
        start_kst.hour,
        start_kst.minute,
        start_kst.second,
        start_kst.microsecond,
        end_kst.hour,
        end_kst.minute,
        end_kst.second,
        end_kst.microsecond,
    )):
        raise ValueError("Batch canary window must use exact KST midnights")
    return {
        "schema": CONFIG_SCHEMA,
        "environment": environment,
        "release_sha": release_sha,
        "supabase_url": supabase_url,
        "workspace_id": normalized_workspace_id,
        "allowed_clients": sorted(allowed_clients),
        "daily_cap_usd": "0.05",
        "max_claims": 1,
        "max_requests_per_batch": 1,
        "timezone": timezone_name,
        "experiment_start_at": _utc_text(experiment_start_at),
        "experiment_end_at": _utc_text(experiment_end_at),
        "route": {
            "agent_id": "origintrail_client_agent",
            "workflow_kind": "official_source_nonurgent_pack",
            "stage": "generate",
            "model": "gpt-5.6-luna",
            "max_job_cost_usd": "0.05",
        },
        "approval_mode": (
            "seven_day_daily_shadow"
            if production_shadow_auto_dispatch
            else "exact_one_shot"
        ),
        "authorized_provider_batches": (
            7 if production_shadow_auto_dispatch else 1
        ),
        "max_provider_batches_per_kst_day": 1,
        "production_shadow_auto_dispatch": production_shadow_auto_dispatch,
        "automatic_external_effects": False,
    }


@dataclass(frozen=True)
class CanaryConfigApproval:
    approval_id: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    subject_sha256: str

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        expected_subject_sha256: str,
        maximum_ttl: timedelta = CANARY_APPROVAL_TTL,
    ) -> "CanaryConfigApproval":
        value = _receipt(raw, expected_keys=frozenset({
            "version",
            "approval_id",
            "approved_by",
            "approved_at",
            "expires_at",
            "subject_sha256",
        }))
        if value["version"] != CONFIG_SCHEMA:
            raise ValueError("Batch canary config receipt version is invalid")
        approval_id = _uuid(value["approval_id"], "approval_id")
        approved_by = value["approved_by"]
        if (
            not isinstance(approved_by, str)
            or _APPROVER_RE.fullmatch(approved_by) is None
        ):
            raise ValueError("approved_by is invalid")
        approved_at = _timestamp(value["approved_at"], "approved_at")
        expires_at = _timestamp(value["expires_at"], "expires_at")
        if (
            expires_at <= approved_at
            or expires_at - approved_at > maximum_ttl
        ):
            raise ValueError(
                "Batch canary approval TTL must be at most 2 hours"
                if maximum_ttl == CANARY_APPROVAL_TTL
                else "Batch Production Shadow approval TTL is too long"
            )
        subject_sha256 = _sha256(value["subject_sha256"], "subject_sha256")
        if not hmac.compare_digest(subject_sha256, expected_subject_sha256):
            raise ValueError("Batch canary config receipt does not match settings")
        return cls(
            approval_id=approval_id,
            approved_by=approved_by,
            approved_at=approved_at,
            expires_at=expires_at,
            subject_sha256=subject_sha256,
        )

    def phase(self, now: datetime) -> str:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Batch canary clock must be timezone-aware")
        if now < self.approved_at:
            return "not_started"
        if now >= self.expires_at:
            return "expired"
        return "active"

    def public_summary(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "approved_by": self.approved_by,
            "approved_at": _utc_text(self.approved_at),
            "expires_at": _utc_text(self.expires_at),
            "subject_sha256": self.subject_sha256,
        }


def dispatch_subject(
    *,
    config_subject_sha256: str,
    config_approval_id: str,
    job_id: str,
    input_sha256: str,
    request_sha256: str,
) -> dict[str, object]:
    return {
        "schema": DISPATCH_SCHEMA,
        "config_subject_sha256": _sha256(
            config_subject_sha256,
            "config_subject_sha256",
        ),
        "config_approval_id": _uuid(
            config_approval_id,
            "config_approval_id",
        ),
        "job_id": _uuid(job_id, "job_id"),
        "input_sha256": _sha256(input_sha256, "input_sha256"),
        "request_sha256": _sha256(request_sha256, "request_sha256"),
        "authorized_provider_batches": 1,
        "authorized_total_usd": "0.05",
    }


_PILOT_DAY_CONFIG_NAMESPACE = uuid.UUID(
    "b7878b09-8601-47c8-9423-42a84f0cf158"
)
_PILOT_DAY_DISPATCH_NAMESPACE = uuid.UUID(
    "c30c836c-359c-40f8-979c-acd0bf9b04d4"
)


def pilot_day_authorization(
    *,
    pilot_subject_sha256: str,
    pilot_approval_id: str,
    kst_date: date,
    job_id: str,
    input_sha256: str,
    request_sha256: str,
) -> dict[str, str]:
    """Derive an exact one-shot grant from the approved seven-day pilot.

    The derivation is deterministic so an hourly retry reuses the same grant,
    while the database keeps a unique KST-day fence for the pilot.
    """
    normalized_pilot_subject = _sha256(
        pilot_subject_sha256,
        "pilot_subject_sha256",
    )
    normalized_pilot_approval = _uuid(
        pilot_approval_id,
        "pilot_approval_id",
    )
    normalized_job_id = _uuid(job_id, "job_id")
    normalized_input_sha = _sha256(input_sha256, "input_sha256")
    normalized_request_sha = _sha256(request_sha256, "request_sha256")
    if not isinstance(kst_date, date) or isinstance(kst_date, datetime):
        raise ValueError("kst_date must be a date")
    date_text = kst_date.isoformat()
    binding = {
        "schema": PILOT_DAY_SCHEMA,
        "pilot_subject_sha256": normalized_pilot_subject,
        "pilot_approval_id": normalized_pilot_approval,
        "kst_date": date_text,
        "job_id": normalized_job_id,
        "input_sha256": normalized_input_sha,
        "request_sha256": normalized_request_sha,
        "authorized_provider_batches": 1,
        "authorized_total_usd": "0.05",
        "automatic_external_effects": False,
    }
    daily_config_subject_sha256 = canonical_sha256(binding)
    config_approval_id = str(uuid.uuid5(
        _PILOT_DAY_CONFIG_NAMESPACE,
        canonical_json(binding),
    ))
    exact_dispatch = dispatch_subject(
        config_subject_sha256=daily_config_subject_sha256,
        config_approval_id=config_approval_id,
        job_id=normalized_job_id,
        input_sha256=normalized_input_sha,
        request_sha256=normalized_request_sha,
    )
    dispatch_subject_sha256 = canonical_sha256(exact_dispatch)
    dispatch_approval_id = str(uuid.uuid5(
        _PILOT_DAY_DISPATCH_NAMESPACE,
        canonical_json(exact_dispatch),
    ))
    return {
        "pilot_subject_sha256": normalized_pilot_subject,
        "pilot_approval_id": normalized_pilot_approval,
        "kst_date": date_text,
        "config_subject_sha256": daily_config_subject_sha256,
        "config_approval_id": config_approval_id,
        "dispatch_subject_sha256": dispatch_subject_sha256,
        "dispatch_approval_id": dispatch_approval_id,
        "job_id": normalized_job_id,
        "input_sha256": normalized_input_sha,
        "request_sha256": normalized_request_sha,
    }


@dataclass(frozen=True)
class CanaryDispatchApproval:
    dispatch_approval_id: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    job_id: str
    input_sha256: str
    request_sha256: str
    subject_sha256: str

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        config_subject_sha256: str,
        config_approval_id: str,
    ) -> "CanaryDispatchApproval":
        value = _receipt(raw, expected_keys=frozenset({
            "version",
            "dispatch_approval_id",
            "approved_by",
            "approved_at",
            "expires_at",
            "job_id",
            "input_sha256",
            "request_sha256",
            "subject_sha256",
        }))
        if value["version"] != DISPATCH_SCHEMA:
            raise ValueError("Batch canary dispatch receipt version is invalid")
        dispatch_approval_id = _uuid(
            value["dispatch_approval_id"],
            "dispatch_approval_id",
        )
        approved_by = value["approved_by"]
        if (
            not isinstance(approved_by, str)
            or _APPROVER_RE.fullmatch(approved_by) is None
        ):
            raise ValueError("approved_by is invalid")
        approved_at = _timestamp(value["approved_at"], "approved_at")
        expires_at = _timestamp(value["expires_at"], "expires_at")
        if (
            expires_at <= approved_at
            or expires_at - approved_at > CANARY_APPROVAL_TTL
        ):
            raise ValueError("Batch canary dispatch TTL must be at most 2 hours")
        job_id = _uuid(value["job_id"], "job_id")
        input_sha256 = _sha256(value["input_sha256"], "input_sha256")
        request_sha256 = _sha256(value["request_sha256"], "request_sha256")
        expected_subject = canonical_sha256(dispatch_subject(
            config_subject_sha256=config_subject_sha256,
            config_approval_id=config_approval_id,
            job_id=job_id,
            input_sha256=input_sha256,
            request_sha256=request_sha256,
        ))
        subject_sha256 = _sha256(value["subject_sha256"], "subject_sha256")
        if not hmac.compare_digest(subject_sha256, expected_subject):
            raise ValueError("Batch canary dispatch receipt does not match job")
        return cls(
            dispatch_approval_id=dispatch_approval_id,
            approved_by=approved_by,
            approved_at=approved_at,
            expires_at=expires_at,
            job_id=job_id,
            input_sha256=input_sha256,
            request_sha256=request_sha256,
            subject_sha256=subject_sha256,
        )

    def phase(self, now: datetime) -> str:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Batch canary clock must be timezone-aware")
        if now < self.approved_at:
            return "not_started"
        if now >= self.expires_at:
            return "expired"
        return "active"

    def public_summary(self) -> dict[str, object]:
        return {
            "dispatch_approval_id": self.dispatch_approval_id,
            "approved_by": self.approved_by,
            "approved_at": _utc_text(self.approved_at),
            "expires_at": _utc_text(self.expires_at),
            "job_id": self.job_id,
            "input_sha256": self.input_sha256,
            "request_sha256": self.request_sha256,
            "subject_sha256": self.subject_sha256,
        }
