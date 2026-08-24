from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from itertools import combinations
from pathlib import PurePosixPath
from typing import Callable, Iterable, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)

from .models import (
    CODING_AGENTS,
    REVIEW_AGENTS,
    AgentIdentity,
    AgentWorkOrder,
    _contains_secret,
)


_CONTROL_ROOM_CLIENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")


class PlanningBlocker(str, Enum):
    NOT_YET_ACTIVE = "not_yet_active"
    EXPIRED = "expired"
    IDEMPOTENCY_COLLISION = "idempotency_collision"
    BRANCH_COLLISION = "branch_collision"
    PATH_COLLISION = "path_collision"


class AgentRouteProjection(BaseModel):
    """A read-only route projection. It grants no execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    work_order_id: UUID4
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    branch_scope_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str
    title: str
    client_id: Optional[str] = Field(
        default=None,
        pattern=_CONTROL_ROOM_CLIENT_ID_PATTERN,
    )
    repository: str
    branch_name: str
    expires_at: datetime
    owner: AgentIdentity
    reviewer: AgentIdentity
    operator_desk: Literal[AgentIdentity.GROK_BOT] = AgentIdentity.GROK_BOT
    audit_transport: Literal[AgentIdentity.BUZZ] = AgentIdentity.BUZZ
    status: Literal["ready_for_scope_review", "blocked"]
    blocker_codes: tuple[PlanningBlocker, ...] = Field(default=(), max_length=4)
    next_gate: Literal["human_scope_review", "resolve_planning_blocker"]
    execution_authorized: Literal[False] = False
    dry_run: Literal[True] = True
    external_calls: Literal[False] = False
    database_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and _contains_secret(value):
            raise ValueError("agent_control_room_client_id_invalid")
        return value

    @model_validator(mode="after")
    def validate_projection(self) -> "AgentRouteProjection":
        if (
            self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
            or self.expires_at.microsecond != 0
        ):
            raise ValueError("agent_control_room_route_invalid")
        if (
            self.owner not in CODING_AGENTS
            or self.reviewer not in REVIEW_AGENTS
            or self.owner == self.reviewer
            or len(set(self.blocker_codes)) != len(self.blocker_codes)
        ):
            raise ValueError("agent_control_room_route_invalid")
        if self.status == "ready_for_scope_review":
            if self.blocker_codes or self.next_gate != "human_scope_review":
                raise ValueError("agent_control_room_route_invalid")
        elif (
            not self.blocker_codes
            or self.next_gate != "resolve_planning_blocker"
        ):
            raise ValueError("agent_control_room_route_invalid")
        return self


class ControlRoomCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=1, le=32)
    ready_for_scope_review: int = Field(ge=0, le=32)
    blocked: int = Field(ge=0, le=32)
    expired: int = Field(ge=0, le=32)


class ControlRoomSnapshot(BaseModel):
    """Deterministic, local-only projection of proposed work orders."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-control-room@1"] = "agent-control-room@1"
    observed_at: datetime
    routes: tuple[AgentRouteProjection, ...] = Field(min_length=1, max_length=32)
    counts: ControlRoomCounts
    planning_only: Literal[True] = True
    dry_run: Literal[True] = True
    execution_authorized: Literal[False] = False
    external_calls: Literal[False] = False
    database_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ControlRoomSnapshot":
        if (
            self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
            or self.observed_at.microsecond != 0
        ):
            raise ValueError("agent_control_room_observed_at_invalid")
        route_ids = [str(route.work_order_id) for route in self.routes]
        if route_ids != sorted(route_ids) or len(set(route_ids)) != len(route_ids):
            raise ValueError("agent_control_room_route_order_invalid")
        blocked = sum(route.status == "blocked" for route in self.routes)
        expired = sum(
            PlanningBlocker.EXPIRED in route.blocker_codes
            for route in self.routes
        )
        if self.counts != ControlRoomCounts(
            total=len(self.routes),
            ready_for_scope_review=len(self.routes) - blocked,
            blocked=blocked,
            expired=expired,
        ):
            raise ValueError("agent_control_room_counts_invalid")
        return self

    def canonical_snapshot(self) -> dict[str, object]:
        return {
            "automatic_publication": self.automatic_publication,
            "counts": self.counts.model_dump(mode="json"),
            "database_calls": self.database_calls,
            "dry_run": self.dry_run,
            "execution_authorized": self.execution_authorized,
            "external_calls": self.external_calls,
            "max_cost_microusd": self.max_cost_microusd,
            "max_external_actions": self.max_external_actions,
            "observed_at": _utc_z(self.observed_at),
            "planning_only": self.planning_only,
            "provider_calls": self.provider_calls,
            "publication_calls": self.publication_calls,
            "routes": [
                {
                    **route.model_dump(mode="json"),
                    "expires_at": _utc_z(route.expires_at),
                }
                for route in self.routes
            ],
            "schema_version": self.schema_version,
        }

    @property
    def snapshot_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.canonical_snapshot(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {
            **self.canonical_snapshot(),
            "snapshot_sha256": self.snapshot_sha256,
        }


def _utc_seconds(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.microsecond != 0:
        raise ValueError("agent_control_room_observed_at_invalid")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("agent_control_room_observed_at_invalid") from exc


def _utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _path_overlaps(left: str, right: str) -> bool:
    # The local macOS workspace is commonly case-insensitive. Preserve the
    # Phase-zero scope bytes, but detect planning collisions conservatively.
    left_parts = tuple(part.casefold() for part in PurePosixPath(left).parts)
    right_parts = tuple(part.casefold() for part in PurePosixPath(right).parts)
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def _mark_group_collisions(
    active_orders: list[AgentWorkOrder],
    blockers: dict[str, set[PlanningBlocker]],
    *,
    key_for: Callable[[AgentWorkOrder], str],
    code: PlanningBlocker,
) -> None:
    groups: dict[str, list[AgentWorkOrder]] = {}
    for order in active_orders:
        key = key_for(order)
        groups.setdefault(key, []).append(order)
    for group in groups.values():
        if len(group) > 1:
            for order in group:
                blockers[str(order.work_order_id)].add(code)


def build_control_room_snapshot(
    orders: Iterable[AgentWorkOrder],
    *,
    observed_at: datetime,
) -> ControlRoomSnapshot:
    """Project local proposals into one deterministic, non-actionable snapshot."""
    normalized_observed_at = _utc_seconds(observed_at)
    ordered = sorted(list(orders), key=lambda item: str(item.work_order_id))
    if not 1 <= len(ordered) <= 32:
        raise ValueError("agent_control_room_order_count_invalid")

    order_ids = [str(order.work_order_id) for order in ordered]
    if len(set(order_ids)) != len(order_ids):
        raise ValueError("agent_control_room_work_order_duplicate")

    blockers: dict[str, set[PlanningBlocker]] = {
        order_id: set() for order_id in order_ids
    }
    active_orders: list[AgentWorkOrder] = []
    for order in ordered:
        if order.created_at > normalized_observed_at:
            blockers[str(order.work_order_id)].add(PlanningBlocker.NOT_YET_ACTIVE)
        elif order.expires_at <= normalized_observed_at:
            blockers[str(order.work_order_id)].add(PlanningBlocker.EXPIRED)
        else:
            active_orders.append(order)

    _mark_group_collisions(
        active_orders,
        blockers,
        key_for=lambda order: order.idempotency_key,
        code=PlanningBlocker.IDEMPOTENCY_COLLISION,
    )
    _mark_group_collisions(
        active_orders,
        blockers,
        key_for=lambda order: "\x00".join((
            order.repository.casefold(),
            order.branch_name.casefold(),
        )),
        code=PlanningBlocker.BRANCH_COLLISION,
    )

    for left, right in combinations(active_orders, 2):
        if left.repository.casefold() != right.repository.casefold():
            continue
        if any(
            _path_overlaps(left_path, right_path)
            for left_path in left.allowed_paths
            for right_path in right.allowed_paths
        ):
            blockers[str(left.work_order_id)].add(PlanningBlocker.PATH_COLLISION)
            blockers[str(right.work_order_id)].add(PlanningBlocker.PATH_COLLISION)

    routes: list[AgentRouteProjection] = []
    for order in ordered:
        codes = sorted(
            blockers[str(order.work_order_id)],
            key=lambda item: item.value,
        )
        blocked = bool(codes)
        routes.append(AgentRouteProjection(
            work_order_id=order.work_order_id,
            scope_sha256=order.scope_sha256,
            branch_scope_key=order.branch_scope_key,
            idempotency_key=order.idempotency_key,
            title=order.title,
            client_id=order.client_id,
            repository=order.repository,
            branch_name=order.branch_name,
            expires_at=order.expires_at,
            owner=order.owner,
            reviewer=order.reviewer,
            status="blocked" if blocked else "ready_for_scope_review",
            blocker_codes=tuple(codes),
            next_gate=(
                "resolve_planning_blocker" if blocked else "human_scope_review"
            ),
        ))

    blocked_count = sum(route.status == "blocked" for route in routes)
    return ControlRoomSnapshot(
        observed_at=normalized_observed_at,
        routes=tuple(routes),
        counts=ControlRoomCounts(
            total=len(routes),
            ready_for_scope_review=len(routes) - blocked_count,
            blocked=blocked_count,
            expired=sum(
                PlanningBlocker.EXPIRED in route.blocker_codes
                for route in routes
            ),
        ),
    )


__all__ = [
    "AgentRouteProjection",
    "ControlRoomCounts",
    "ControlRoomSnapshot",
    "PlanningBlocker",
    "build_control_room_snapshot",
]
