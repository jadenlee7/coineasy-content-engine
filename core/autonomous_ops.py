from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Mapping


AUTONOMOUS_OPS_PROTOCOL_VERSION = "origintrail-autonomous-ops@1"
_HASH = re.compile(r"^[a-f0-9]{64}$")
_DATE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")


@dataclass(frozen=True)
class AutonomousOpsSnapshot:
    workspace_id: str
    observed_at_epoch: int
    observation_date_kst: str
    batch_failed_count: int
    batch_stale_count: int
    cost_overage_count: int
    buzz_delivery_failed_count: int
    buzz_delivery_unknown_count: int
    review_ack_unknown_count: int
    operations_response_unknown_count: int
    unexpected_publication_count: int
    nonterminal_batch_count: int
    actual_cost_microusd: int
    snapshot_sha256: str
    protocol_version: str = AUTONOMOUS_OPS_PROTOCOL_VERSION

    def metrics(self) -> dict[str, int]:
        return {
            "actual_cost_microusd": self.actual_cost_microusd,
            "batch_failed_count": self.batch_failed_count,
            "batch_stale_count": self.batch_stale_count,
            "buzz_delivery_failed_count": self.buzz_delivery_failed_count,
            "buzz_delivery_unknown_count": self.buzz_delivery_unknown_count,
            "cost_overage_count": self.cost_overage_count,
            "nonterminal_batch_count": self.nonterminal_batch_count,
            "operations_response_unknown_count": (
                self.operations_response_unknown_count
            ),
            "review_ack_unknown_count": self.review_ack_unknown_count,
            "unexpected_publication_count": (
                self.unexpected_publication_count
            ),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "AutonomousOpsSnapshot":
        expected = {
            "workspace_id", "protocol_version", "observed_at_epoch",
            "observation_date_kst", "snapshot_sha256",
            "batch_failed_count", "batch_stale_count", "cost_overage_count",
            "buzz_delivery_failed_count", "buzz_delivery_unknown_count",
            "review_ack_unknown_count", "operations_response_unknown_count",
            "unexpected_publication_count", "nonterminal_batch_count",
            "actual_cost_microusd",
        }
        if set(raw) != expected:
            raise ValueError("autonomous_ops_snapshot_invalid")
        try:
            workspace_id = str(uuid.UUID(str(raw["workspace_id"])))
        except (ValueError, AttributeError) as exc:
            raise ValueError("autonomous_ops_snapshot_invalid") from exc
        if raw["protocol_version"] != AUTONOMOUS_OPS_PROTOCOL_VERSION:
            raise ValueError("autonomous_ops_snapshot_invalid")
        observed = raw["observed_at_epoch"]
        date_kst = raw["observation_date_kst"]
        snapshot_sha = raw["snapshot_sha256"]
        if (
            not isinstance(observed, int)
            or isinstance(observed, bool)
            or not 1_700_000_000 <= observed <= 4_294_967_295
            or not isinstance(date_kst, str)
            or not _DATE.fullmatch(date_kst)
            or not isinstance(snapshot_sha, str)
            or not _HASH.fullmatch(snapshot_sha)
        ):
            raise ValueError("autonomous_ops_snapshot_invalid")
        values: dict[str, int] = {}
        for key in expected - {
            "workspace_id", "protocol_version", "observed_at_epoch",
            "observation_date_kst", "snapshot_sha256",
        }:
            value = raw[key]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > 1_000_000_000_000
            ):
                raise ValueError("autonomous_ops_snapshot_invalid")
            values[key] = value
        snapshot = cls(
            workspace_id=workspace_id,
            observed_at_epoch=observed,
            observation_date_kst=date_kst,
            snapshot_sha256=snapshot_sha,
            **values,
        )
        if snapshot.snapshot_sha256 != snapshot_fingerprint(snapshot):
            raise ValueError("autonomous_ops_snapshot_invalid")
        return snapshot


@dataclass(frozen=True)
class AutonomousOpsPlan:
    incident_key: str
    category: str
    severity: str
    title_ko: str
    summary_ko: str
    steps_ko: tuple[str, ...]
    execution_mode: str = "propose_only"
    automatic_publication: bool = False
    external_writes: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "incident_key": self.incident_key,
            "category": self.category,
            "severity": self.severity,
            "title_ko": self.title_ko,
            "summary_ko": self.summary_ko,
            "steps_ko": list(self.steps_ko),
            "execution_mode": self.execution_mode,
            "automatic_publication": self.automatic_publication,
            "external_writes": self.external_writes,
        }


@dataclass(frozen=True)
class AutonomousOpsTask:
    workspace_id: str
    task_id: str
    incident_key: str
    category: str
    severity: str
    title_ko: str
    summary_ko: str
    steps_ko: tuple[str, ...]
    status: str
    reused: bool
    automatic_execution: bool


@dataclass(frozen=True)
class AutonomousOpsRunResult:
    ok: bool
    status: str
    category: str | None = None
    severity: str | None = None
    task_id: str | None = None
    reused: bool | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"ok": self.ok, "status": self.status}
        for key in ("category", "severity", "task_id", "error"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        if self.reused is not None:
            result["reused"] = self.reused
        return result


def snapshot_fingerprint(snapshot: AutonomousOpsSnapshot) -> str:
    metrics = snapshot.metrics()
    payload = "\0".join((
        "coineasy-autonomous-ops-snapshot",
        snapshot.protocol_version,
        snapshot.workspace_id,
        snapshot.observation_date_kst,
        *(str(metrics[key]) for key in sorted(metrics)),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _incident_key(snapshot: AutonomousOpsSnapshot, category: str) -> str:
    payload = "\0".join((
        "coineasy-autonomous-ops-incident",
        AUTONOMOUS_OPS_PROTOCOL_VERSION,
        snapshot.workspace_id,
        snapshot.observation_date_kst,
        category,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_snapshot(snapshot: AutonomousOpsSnapshot) -> AutonomousOpsPlan | None:
    """Select at most one bounded proposal from an immutable observation.

    The order is deliberate: containment risks beat availability risks.  This
    planner cannot execute, publish, deploy, spend, or call another provider.
    """
    policies: tuple[
        tuple[str, str, str, str, tuple[str, ...], int], ...
    ] = (
        (
            "unexpected_publication", "critical", "예상하지 않은 발행 감지",
            "자동 발행 OFF 경계에서 publication 행이 관측되었습니다.",
            (
                "발행 및 생성 워커 플래그가 OFF인지 읽기 전용 확인",
                "publication 행의 생성 시각·요청 주체·대상 버전을 보존",
                "추가 외부 전송 없이 운영자 승인을 요청",
            ), snapshot.unexpected_publication_count,
        ),
        (
            "batch_cost_overage", "critical", "Batch 비용 초과 증거 감지",
            "미해결 Batch cost overage incident가 있습니다.",
            (
                "해당 job·provider batch·예약 상한을 읽기 전용 확인",
                "새 Batch 제출을 중지한 상태인지 확인",
                "비용 증거와 복구 선택지를 운영자에게 보고",
            ), snapshot.cost_overage_count,
        ),
        (
            "batch_failed", "high", "OriginTrail Batch 실패 감지",
            "종결 실패 Batch 작업이 관측되었습니다.",
            (
                "error_code와 마지막 provider 상태를 수집",
                "동일 release/config에서 재현 가능한 검증 명령을 제안",
                "재제출 없이 수정 후보와 검증 체크리스트를 작성",
            ), snapshot.batch_failed_count,
        ),
        (
            "batch_stale", "high", "OriginTrail Batch 정체 감지",
            "허용 시간보다 오래 비종결 상태인 Batch 작업이 있습니다.",
            (
                "job·lease·provider 상태의 시간축을 읽기 전용 확인",
                "lease 만료와 provider 진행 중 상태를 구분",
                "수동 poll·재제출 없이 안전한 복구안을 제안",
            ), snapshot.batch_stale_count,
        ),
        (
            "buzz_delivery_unknown", "high", "Buzz 전달 결과 불명 감지",
            "relay 시도 이후 결과가 불명인 Buzz 전달이 있습니다.",
            (
                "원 thread에서 exact event를 읽기 전용 검색",
                "발견 시 기존 receipt와 hash를 대조",
                "미발견이어도 중복 전송 없이 reconciliation만 제안",
            ), snapshot.buzz_delivery_unknown_count,
        ),
        (
            "buzz_delivery_failed", "medium", "Buzz 전달 실패 감지",
            "provider attempt 이전 또는 종결 실패한 Buzz 전달이 있습니다.",
            (
                "error_code와 attempt fence 상태를 확인",
                "재시도 가능 여부를 상태기계 기준으로 분류",
                "중복 전송 없는 복구 절차를 제안",
            ), snapshot.buzz_delivery_failed_count,
        ),
        (
            "review_ack_unknown", "medium", "Buzz 검토 접수 결과 불명 감지",
            "검토 결정 ACK가 delivery_unknown 상태입니다.",
            (
                "결정 event와 서비스 답글을 exact thread에서 대조",
                "일치하는 답글이 하나면 reconciliation을 제안",
                "0건 또는 중복이면 전송 없이 운영자 확인을 요청",
            ), snapshot.review_ack_unknown_count,
        ),
        (
            "operations_response_unknown", "medium",
            "Buzz 운영 응답 결과 불명 감지",
            "운영 명령 응답이 delivery_unknown 상태입니다.",
            (
                "명령 event에 대한 서비스 답글을 읽기 전용 확인",
                "exact 응답이 하나면 receipt 정합화만 제안",
                "새 답글 전송은 금지하고 불명 상태를 보고",
            ), snapshot.operations_response_unknown_count,
        ),
    )
    for category, severity, title, summary, steps, count in policies:
        if count > 0:
            return AutonomousOpsPlan(
                incident_key=_incident_key(snapshot, category),
                category=category,
                severity=severity,
                title_ko=title,
                summary_ko=summary,
                steps_ko=steps,
            )
    return None


__all__ = [
    "AUTONOMOUS_OPS_PROTOCOL_VERSION",
    "AutonomousOpsPlan",
    "AutonomousOpsRunResult",
    "AutonomousOpsSnapshot",
    "AutonomousOpsTask",
    "plan_snapshot",
    "snapshot_fingerprint",
]
