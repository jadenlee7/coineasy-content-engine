from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Protocol

import httpx

from core.autonomous_ops import (
    AUTONOMOUS_OPS_PROTOCOL_VERSION,
    AutonomousOpsPlan,
    AutonomousOpsRunResult,
    AutonomousOpsSnapshot,
    AutonomousOpsTask,
    plan_snapshot,
)


_HASH = re.compile(r"^[a-f0-9]{64}$")
_CATEGORIES = {
    "unexpected_publication", "batch_cost_overage", "batch_failed",
    "batch_stale", "buzz_delivery_unknown", "buzz_delivery_failed",
    "review_ack_unknown", "operations_response_unknown",
}
_SEVERITIES = {"medium", "high", "critical"}


class AutonomousOpsError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AutonomousOpsControl(Protocol):
    async def observe(self) -> AutonomousOpsSnapshot: ...
    async def record(
        self, snapshot: AutonomousOpsSnapshot, plan: AutonomousOpsPlan
    ) -> AutonomousOpsTask: ...


class AutonomousOpsControlClient:
    def __init__(self, *, url: str, token: str, transport=None):
        self.url = url
        self.token = token
        self.transport = transport

    async def _post(self, body: Mapping[str, object]) -> object:
        encoded = json.dumps(
            dict(body), ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.post(
                    self.url,
                    headers={
                        "x-coineasy-autonomous-ops-key": self.token,
                        "content-type": "application/json",
                    },
                    content=encoded,
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise AutonomousOpsError(
                "autonomous_ops_control_unavailable"
            ) from None
        if response.status_code != 200:
            raise AutonomousOpsError("autonomous_ops_control_unavailable")
        try:
            return response.json()
        except ValueError as exc:
            raise AutonomousOpsError(
                "autonomous_ops_control_invalid_response"
            ) from exc

    async def observe(self) -> AutonomousOpsSnapshot:
        raw = await self._post({
            "action": "observe",
            "protocol_version": AUTONOMOUS_OPS_PROTOCOL_VERSION,
        })
        if not isinstance(raw, Mapping):
            raise AutonomousOpsError("autonomous_ops_control_invalid_response")
        try:
            return AutonomousOpsSnapshot.from_mapping(raw)
        except ValueError as exc:
            raise AutonomousOpsError(
                "autonomous_ops_control_invalid_response"
            ) from exc

    async def record(
        self, snapshot: AutonomousOpsSnapshot, plan: AutonomousOpsPlan
    ) -> AutonomousOpsTask:
        raw = await self._post({
            "action": "record_plan",
            "protocol_version": AUTONOMOUS_OPS_PROTOCOL_VERSION,
            "snapshot_sha256": snapshot.snapshot_sha256,
            **plan.as_dict(),
        })
        if not isinstance(raw, Mapping) or set(raw) != {
            "workspace_id", "task_id", "incident_key", "category",
            "severity", "title_ko", "summary_ko", "steps_ko", "status",
            "reused", "automatic_execution",
        }:
            raise AutonomousOpsError("autonomous_ops_control_invalid_response")
        try:
            workspace_id = str(uuid.UUID(str(raw["workspace_id"])))
            task_id = str(uuid.UUID(str(raw["task_id"])))
        except (ValueError, AttributeError) as exc:
            raise AutonomousOpsError(
                "autonomous_ops_control_invalid_response"
            ) from exc
        steps = raw["steps_ko"]
        if (
            raw["incident_key"] != plan.incident_key
            or not isinstance(raw["incident_key"], str)
            or not _HASH.fullmatch(raw["incident_key"])
            or raw["category"] != plan.category
            or raw["category"] not in _CATEGORIES
            or raw["severity"] != plan.severity
            or raw["severity"] not in _SEVERITIES
            or raw["title_ko"] != plan.title_ko
            or raw["summary_ko"] != plan.summary_ko
            or not isinstance(steps, list)
            or tuple(steps) != plan.steps_ko
            or raw["status"] != "proposed"
            or not isinstance(raw["reused"], bool)
            or raw["automatic_execution"] is not False
        ):
            raise AutonomousOpsError("autonomous_ops_control_invalid_response")
        return AutonomousOpsTask(
            workspace_id=workspace_id,
            task_id=task_id,
            incident_key=plan.incident_key,
            category=plan.category,
            severity=plan.severity,
            title_ko=plan.title_ko,
            summary_ko=plan.summary_ko,
            steps_ko=plan.steps_ko,
            status="proposed",
            reused=bool(raw["reused"]),
            automatic_execution=False,
        )


class OriginTrailAutonomousOpsWorker:
    def __init__(self, control: AutonomousOpsControl):
        self.control = control

    async def run_once(self) -> AutonomousOpsRunResult:
        try:
            snapshot = await self.control.observe()
        except AutonomousOpsError as exc:
            return AutonomousOpsRunResult(
                ok=False, status="failed", error=exc.code
            )
        plan = plan_snapshot(snapshot)
        if plan is None:
            return AutonomousOpsRunResult(ok=True, status="healthy")
        try:
            task = await self.control.record(snapshot, plan)
        except AutonomousOpsError as exc:
            return AutonomousOpsRunResult(
                ok=False, status="failed", category=plan.category,
                severity=plan.severity, error=exc.code,
            )
        return AutonomousOpsRunResult(
            ok=True, status="proposed", category=task.category,
            severity=task.severity, task_id=task.task_id, reused=task.reused,
        )


__all__ = [
    "AutonomousOpsControlClient",
    "AutonomousOpsError",
    "OriginTrailAutonomousOpsWorker",
]
