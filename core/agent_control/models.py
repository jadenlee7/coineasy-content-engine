from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)


class AgentIdentity(str, Enum):
    HUMAN_OPERATOR = "human_operator"
    GROK_BOT = "grok_bot"
    GROK_BUILD = "grok_build"
    BUZZ = "buzz"
    DEVIN = "devin"
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    RAILWAY_WORKER = "railway_worker"


class WorkType(str, Enum):
    STRATEGY = "strategy"
    RESEARCH = "research"
    CONTENT_PREPARE = "content_prepare"
    CONTENT_QA = "content_qa"
    ENGINEERING = "engineering"
    RELEASE = "release"
    INCIDENT = "incident"
    ANALYTICS = "analytics"


class ForbiddenAction(str, Enum):
    BRANCH_PUSH = "branch_push"
    DRAFT_PR_CREATE = "draft_pr_create"
    MERGE = "merge"
    PREVIEW_DEPLOY = "preview_deploy"
    PRODUCTION_DEPLOY = "production_deploy"
    PRODUCTION_DATABASE_WRITE = "production_database_write"
    CREDENTIAL_CHANGE = "credential_change"
    PAID_PROVIDER_CALL = "paid_provider_call"
    PUBLIC_MESSAGE = "public_message"
    PUBLICATION = "publication"


REQUIRED_PHASE_ZERO_PROHIBITIONS = frozenset(ForbiddenAction)
CODING_AGENTS = frozenset({
    AgentIdentity.DEVIN,
    AgentIdentity.CLAUDE_CODE,
    AgentIdentity.CODEX,
    AgentIdentity.GROK_BUILD,
})
REVIEW_AGENTS = frozenset({
    AgentIdentity.CODEX,
    AgentIdentity.CLAUDE_CODE,
    AgentIdentity.HUMAN_OPERATOR,
})

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{2,119}$")
_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9:._/-]{7,199}$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_STRUCTURE_PATTERN = re.compile(r"[\r\n\t]")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|xai)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsb_(?:secret|publishable)_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bnsec1[023456789acdefghjklmnpqrstuvwxyz]{50,}\b", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}\b", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b[0-9]{6,}:[A-Za-z0-9_-]{20,}\b"),
    # Git/object hashes belong only in typed digest fields. A raw 40/64-hex
    # value in free text or metadata could be a credential and is rejected.
    re.compile(r"(?<![A-Fa-f0-9])(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})(?![A-Fa-f0-9])"),
)
_TOKEN_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+=]{32,}(?![A-Za-z0-9])")
_SIDE_EFFECT_COMMANDS = re.compile(
    r"(?:^|\s)(?:"
    r"rm\s+-rf|"
    r"git\s+(?:push|merge|reset)|"
    r"gh\s+(?:api|pr\s+(?:create|merge))|"
    r"netlify\s+(?:deploy|api)|"
    r"railway\s+(?:up|redeploy|variable)|"
    r"supabase\s+(?:db|functions|branches)|"
    r"curl(?:\s|$)|wget(?:\s|$)"
    r")",
    re.I,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _contains_secret(value: str) -> bool:
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        return True
    # Untyped 32+ character tokens are never needed in rendered planning data.
    # Typed SHA/UUID fields bypass this free-text validator explicitly.
    return _TOKEN_CANDIDATE.search(value) is not None


def _safe_text(
    value: str,
    code: str,
    minimum: int,
    maximum: int,
    *,
    single_line: bool = False,
) -> str:
    normalized = value.strip()
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(code) from exc
    if (
        not minimum <= len(encoded) <= maximum
        or _CONTROL_PATTERN.search(normalized)
        or (single_line and _LINE_STRUCTURE_PATTERN.search(normalized))
        or _contains_secret(normalized)
    ):
        raise ValueError(code)
    return normalized


def _safe_repo_path(value: str, code: str) -> str:
    normalized = value.strip()
    if (
        not _PATH_PATTERN.fullmatch(normalized)
        or ".." in normalized.split("/")
        or normalized == ".git"
        or normalized.startswith(".git/")
        or _contains_secret(normalized)
    ):
        raise ValueError(code)
    return normalized


def _safe_reference_uri(value: str) -> str:
    normalized = _safe_text(value, "agent_work_order_reference_invalid", 1, 2_048)
    # Phase zero can prove only local repository evidence. Remote evidence
    # needs a future fetch receipt and is rejected instead of being mislabeled
    # immutable or verified.
    if normalized.startswith(("http://", "https://")):
        raise ValueError("agent_work_order_reference_invalid")
    return _safe_repo_path(normalized, "agent_work_order_reference_invalid")


def _utc_seconds(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.microsecond != 0:
        raise ValueError(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError(code) from exc


def _utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str = Field(min_length=1, max_length=2_048)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        return _safe_reference_uri(value)


class AgentWorkOrder(BaseModel):
    """A planning-only, immutable scope. It cannot prove authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-work-order@1"] = "agent-work-order@1"
    work_order_id: UUID4
    objective_id: UUID4
    parent_work_order_id: Optional[UUID4] = None
    causation_id: UUID4
    idempotency_key: str = Field(min_length=8, max_length=200)
    created_at: datetime
    expires_at: datetime
    requested_by: Literal[AgentIdentity.HUMAN_OPERATOR] = (
        AgentIdentity.HUMAN_OPERATOR
    )
    owner: AgentIdentity
    reviewer: AgentIdentity
    work_type: Literal[WorkType.ENGINEERING] = WorkType.ENGINEERING
    risk_tier: Literal["R1"] = "R1"
    allowed_environment: Literal["local"] = "local"
    title: str = Field(min_length=3, max_length=160)
    objective: str = Field(min_length=10, max_length=2_000)
    client_id: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,39}$")
    repository: str = Field(max_length=200)
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    branch_name: str = Field(max_length=120)
    allowed_paths: list[str] = Field(min_length=1, max_length=32)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=16)
    expected_artifacts: list[str] = Field(min_length=1, max_length=12)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=16)
    verification_commands: list[str] = Field(min_length=1, max_length=16)
    forbidden_actions: list[ForbiddenAction]
    max_runtime_seconds: int = Field(ge=60, le=86_400)
    max_handoffs: Literal[1] = 1
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_work_order_created_at_invalid")

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_work_order_expires_at_invalid")

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not _KEY_PATTERN.fullmatch(normalized) or _contains_secret(normalized):
            raise ValueError("agent_work_order_idempotency_key_invalid")
        return normalized

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _safe_text(
            value,
            "agent_work_order_title_invalid",
            3,
            160,
            single_line=True,
        )

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, value: str) -> str:
        return _safe_text(value, "agent_work_order_objective_invalid", 10, 2_000)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not _REPOSITORY_PATTERN.fullmatch(normalized)
            or _contains_secret(normalized)
        ):
            raise ValueError("agent_work_order_repository_invalid")
        return normalized

    @field_validator("branch_name")
    @classmethod
    def validate_branch_name(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not _BRANCH_PATTERN.fullmatch(normalized)
            or normalized.endswith("/")
            or ".." in normalized
            or "//" in normalized
            or _contains_secret(normalized)
        ):
            raise ValueError("agent_work_order_branch_invalid")
        return normalized

    @field_validator("allowed_paths")
    @classmethod
    def validate_allowed_paths(cls, values: list[str]) -> list[str]:
        normalized = [
            _safe_repo_path(value, "agent_work_order_allowed_path_invalid")
            for value in values
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError("agent_work_order_allowed_path_duplicate")
        return normalized

    @field_validator("expected_artifacts")
    @classmethod
    def validate_expected_artifacts(cls, values: list[str]) -> list[str]:
        normalized = [
            _safe_text(
                value,
                "agent_work_order_artifact_invalid",
                3,
                200,
                single_line=True,
            )
            for value in values
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError("agent_work_order_artifact_duplicate")
        return normalized

    @field_validator("acceptance_criteria")
    @classmethod
    def validate_acceptance_criteria(cls, values: list[str]) -> list[str]:
        normalized = [
            _safe_text(
                value,
                "agent_work_order_acceptance_invalid",
                3,
                500,
                single_line=True,
            )
            for value in values
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError("agent_work_order_acceptance_duplicate")
        return normalized

    @field_validator("verification_commands")
    @classmethod
    def validate_verification_commands(cls, values: list[str]) -> list[str]:
        normalized = [
            _safe_text(
                value,
                "agent_work_order_command_invalid",
                2,
                500,
                single_line=True,
            )
            for value in values
        ]
        if (
            len(set(normalized)) != len(normalized)
            or any(_SIDE_EFFECT_COMMANDS.search(value) for value in normalized)
        ):
            raise ValueError("agent_work_order_command_invalid")
        return normalized

    @field_validator("forbidden_actions")
    @classmethod
    def validate_forbidden_actions(
        cls,
        values: list[ForbiddenAction],
    ) -> list[ForbiddenAction]:
        if (
            set(values) != REQUIRED_PHASE_ZERO_PROHIBITIONS
            or len(values) != len(set(values))
        ):
            raise ValueError("agent_work_order_prohibition_invalid")
        return values

    @model_validator(mode="after")
    def validate_scope(self) -> "AgentWorkOrder":
        if (
            self.expires_at <= self.created_at
            or self.expires_at > self.created_at + timedelta(days=14)
        ):
            raise ValueError("agent_work_order_window_invalid")
        if (
            self.owner not in CODING_AGENTS
            or self.reviewer not in REVIEW_AGENTS
            or self.owner == self.reviewer
        ):
            raise ValueError("agent_work_order_separation_invalid")
        if self.parent_work_order_id == self.work_order_id:
            raise ValueError("agent_work_order_parent_invalid")
        return self

    def canonical_scope(self) -> dict[str, object]:
        return {
            "acceptance_criteria": list(self.acceptance_criteria),
            "allowed_environment": self.allowed_environment,
            "allowed_paths": list(self.allowed_paths),
            "automatic_publication": self.automatic_publication,
            "base_sha": self.base_sha,
            "branch_name": self.branch_name,
            "causation_id": str(self.causation_id),
            "client_id": self.client_id,
            "created_at": _utc_z(self.created_at),
            "evidence": [item.model_dump(mode="json") for item in self.evidence],
            "expected_artifacts": list(self.expected_artifacts),
            "expires_at": _utc_z(self.expires_at),
            "forbidden_actions": [item.value for item in self.forbidden_actions],
            "idempotency_key": self.idempotency_key,
            "max_cost_microusd": self.max_cost_microusd,
            "max_external_actions": self.max_external_actions,
            "max_handoffs": self.max_handoffs,
            "max_runtime_seconds": self.max_runtime_seconds,
            "objective": self.objective,
            "objective_id": str(self.objective_id),
            "owner": self.owner.value,
            "parent_work_order_id": (
                str(self.parent_work_order_id)
                if self.parent_work_order_id is not None
                else None
            ),
            "repository": self.repository,
            "requested_by": self.requested_by.value,
            "reviewer": self.reviewer.value,
            "risk_tier": self.risk_tier,
            "schema_version": self.schema_version,
            "title": self.title,
            "verification_commands": list(self.verification_commands),
            "work_order_id": str(self.work_order_id),
            "work_type": self.work_type.value,
        }

    @property
    def scope_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_scope())).hexdigest()

    @property
    def branch_scope_key(self) -> str:
        payload = "\x00".join((self.repository, self.branch_name)).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
