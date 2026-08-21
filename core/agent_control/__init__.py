"""Bounded contracts for coordinating CoinEasy employee agents."""

from .models import (
    AgentIdentity,
    AgentWorkOrder,
    EvidenceReference,
    ForbiddenAction,
    WorkType,
)
from .control_room import (
    AgentRouteProjection,
    ControlRoomCounts,
    ControlRoomSnapshot,
    PlanningBlocker,
    build_control_room_snapshot,
)
from .io import load_verified_agent_work_order, resolve_repository_root
from .render import (
    render_buzz_dry_run_receipt,
    render_devin_task_packet,
    render_operator_dashboard,
    render_owner_planning_packet,
    render_reviewer_planning_packet,
)

__all__ = [
    "AgentIdentity",
    "AgentRouteProjection",
    "AgentWorkOrder",
    "ControlRoomCounts",
    "ControlRoomSnapshot",
    "EvidenceReference",
    "ForbiddenAction",
    "PlanningBlocker",
    "WorkType",
    "build_control_room_snapshot",
    "load_verified_agent_work_order",
    "render_buzz_dry_run_receipt",
    "render_devin_task_packet",
    "render_operator_dashboard",
    "render_owner_planning_packet",
    "render_reviewer_planning_packet",
    "resolve_repository_root",
]
