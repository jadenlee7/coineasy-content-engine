from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

from pydantic import ValidationError

from core.grok_qa.models import (
    GrokQaDeliveryResult,
    GrokQaModelResult,
    GrokQaWorkClaim,
    verdict_payload_sha256,
)
from core.grok_qa.xai_client import PROMPT_VERSION, XaiQaClient, XaiQaError


_SAFE_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{2,79}$")


class GrokQaBrokerError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class GrokQaBroker(Protocol):
    async def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_clients: tuple[str, ...],
        canary_content_version_id: Optional[str],
        max_source_age_seconds: int,
    ) -> Optional[GrokQaWorkClaim]: ...

    async def mark_provider_attempt(
        self,
        *,
        claim: GrokQaWorkClaim,
        worker_id: str,
        input_sha256: str,
        banner_sha256: str,
        model: str,
        prompt_version: str,
    ) -> bool: ...

    async def deliver(
        self,
        *,
        action: str,
        content_item_id: str,
        content_version_id: str,
        worker_id: str,
        verdict: dict[str, object],
        verdict_sha256: str,
        model: str,
        prompt_version: str,
        provider_response_id: str,
        input_sha256: str,
        banner_sha256: str,
        cost_in_usd_ticks: int,
        x_search_citations: list[str],
        x_search_calls: int,
    ) -> GrokQaDeliveryResult: ...

    async def fail(
        self,
        *,
        claim: GrokQaWorkClaim,
        worker_id: str,
        error_code: str,
        retryable: bool,
    ) -> None: ...


class GrokQaProvider(Protocol):
    async def review(self, claim: GrokQaWorkClaim) -> GrokQaModelResult: ...


@dataclass(frozen=True)
class GrokQaRunResult:
    ok: bool
    claimed: bool
    status: str
    content_version_id: Optional[str] = None
    error: Optional[str] = None
    cost_in_usd_ticks: int = 0

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": self.ok,
            "claimed": self.claimed,
            "status": self.status,
            "cost_in_usd_ticks": self.cost_in_usd_ticks,
        }
        if self.content_version_id is not None:
            result["content_version_id"] = self.content_version_id
        if self.error is not None:
            result["error"] = self.error
        return result


def verdict_sha256(result: GrokQaModelResult) -> str:
    return verdict_payload_sha256(result.verdict)


class GrokQaWorker:
    def __init__(
        self,
        *,
        broker: GrokQaBroker,
        provider: GrokQaProvider,
        allowed_clients: tuple[str, ...] = ("squid",),
        lease_seconds: int = 300,
        max_source_age_seconds: int = 86_400,
        canary_content_version_id: Optional[str] = None,
        worker_id: Optional[str] = None,
    ):
        if (
            not allowed_clients
            or len(allowed_clients) != len(set(allowed_clients))
            or any(
                value not in {"yellow", "origintrail", "squid", "babylon"}
                for value in allowed_clients
            )
            or not 180 <= lease_seconds <= 600
            or not 300 <= max_source_age_seconds <= 604_800
        ):
            raise ValueError("invalid Grok QA worker configuration")
        if canary_content_version_id is not None:
            try:
                parsed_canary_id = uuid.UUID(canary_content_version_id)
            except (AttributeError, ValueError) as exc:
                raise ValueError("invalid Grok QA canary content version") from exc
            if (
                parsed_canary_id.int == 0
                or str(parsed_canary_id) != canary_content_version_id
            ):
                raise ValueError("invalid Grok QA canary content version")
        self.broker = broker
        self.provider = provider
        self.allowed_clients = allowed_clients
        self.lease_seconds = lease_seconds
        self.max_source_age_seconds = max_source_age_seconds
        self.canary_content_version_id = canary_content_version_id
        self.worker_id = worker_id or f"grok-qa:{uuid.uuid4()}"

    async def _fail(
        self,
        claim: GrokQaWorkClaim,
        *,
        code: str,
        retryable: bool,
        result_error: Optional[str] = None,
    ) -> GrokQaRunResult:
        safe_code = code if _SAFE_ERROR_RE.fullmatch(code) else "grok_qa_failed"
        safe_result_error = (
            result_error
            if result_error is not None and _SAFE_ERROR_RE.fullmatch(result_error)
            else safe_code
        )
        try:
            await self.broker.fail(
                claim=claim,
                worker_id=self.worker_id,
                error_code=safe_code,
                retryable=retryable,
            )
        except Exception:
            return GrokQaRunResult(
                ok=False,
                claimed=True,
                status="failure_unknown",
                content_version_id=claim.content_version_id,
                error="grok_qa_failure_record_unavailable",
            )
        return GrokQaRunResult(
            ok=False,
            claimed=True,
            status="retrying" if retryable else "failed",
            content_version_id=claim.content_version_id,
            error=safe_result_error,
        )

    async def run_once(self) -> GrokQaRunResult:
        try:
            claim = await self.broker.claim(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                allowed_clients=self.allowed_clients,
                canary_content_version_id=self.canary_content_version_id,
                max_source_age_seconds=self.max_source_age_seconds,
            )
        except Exception:
            return GrokQaRunResult(
                ok=False,
                claimed=False,
                status="claim_unavailable",
                error="grok_qa_claim_unavailable",
            )
        if claim is None:
            return GrokQaRunResult(ok=True, claimed=False, status="idle")
        if claim.client_id not in self.allowed_clients:
            return await self._fail(
                claim,
                code="grok_qa_client_not_allowed",
                retryable=False,
            )

        if claim.staged_result is not None:
            # The exact provider result survived an earlier delivery failure.
            # Replay it without crossing the provider-attempt fence again.
            return await self._deliver_result(claim, claim.staged_result)

        if claim.provider_call_required is not True:
            return await self._fail(
                claim,
                code="grok_qa_provider_unknown",
                retryable=False,
            )

        try:
            authorized_once = await self.broker.mark_provider_attempt(
                claim=claim,
                worker_id=self.worker_id,
                input_sha256=claim.input_sha256,
                banner_sha256=claim.image_sha256,
                model="grok-4.5",
                prompt_version=PROMPT_VERSION,
            )
        except Exception:
            # The provider fence may have committed. Never infer that it did not
            # from a lost response, and never spend a second nondeterministic call.
            return GrokQaRunResult(
                ok=False,
                claimed=True,
                status="provider_unknown",
                content_version_id=claim.content_version_id,
                error="grok_qa_provider_attempt_unknown",
            )
        if authorized_once is not True:
            return GrokQaRunResult(
                ok=False,
                claimed=True,
                status="provider_unknown",
                content_version_id=claim.content_version_id,
                error="grok_qa_provider_attempt_not_authorized",
            )

        try:
            result = await self.provider.review(claim)
        except XaiQaError as exc:
            return await self._fail(
                claim,
                code="grok_qa_provider_unknown",
                retryable=False,
                result_error=exc.code,
            )
        except (ValidationError, ValueError):
            return await self._fail(
                claim,
                code="grok_qa_provider_unknown",
                retryable=False,
            )
        except Exception:
            return await self._fail(
                claim,
                code="grok_qa_provider_unknown",
                retryable=False,
            )

        if result.input_sha256 != claim.input_sha256:
            return await self._fail(
                claim,
                code="grok_qa_provider_unknown",
                retryable=False,
            )
        return await self._deliver_result(claim, result)

    async def _deliver_result(
        self,
        claim: GrokQaWorkClaim,
        result: GrokQaModelResult,
    ) -> GrokQaRunResult:
        digest = verdict_sha256(result)
        try:
            delivered = await self.broker.deliver(
                action="deliver",
                content_item_id=claim.content_item_id,
                content_version_id=claim.content_version_id,
                worker_id=self.worker_id,
                verdict=result.verdict.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                verdict_sha256=digest,
                model=result.model,
                prompt_version=PROMPT_VERSION,
                provider_response_id=result.provider_response_id,
                input_sha256=result.input_sha256,
                banner_sha256=claim.image_sha256,
                cost_in_usd_ticks=result.cost_in_usd_ticks,
                x_search_citations=result.x_search_citations,
                x_search_calls=result.x_search_calls,
            )
        except Exception:
            # Delivery may have committed. The broker's exact verdict SHA is the
            # idempotency proof; never call the nondeterministic provider again.
            return GrokQaRunResult(
                ok=False,
                claimed=True,
                status="delivery_unknown",
                content_version_id=claim.content_version_id,
                error="grok_qa_delivery_unknown",
                cost_in_usd_ticks=result.cost_in_usd_ticks,
            )
        return GrokQaRunResult(
            ok=True,
            claimed=True,
            status=("duplicate" if delivered.duplicate else "delivered"),
            content_version_id=claim.content_version_id,
            cost_in_usd_ticks=result.cost_in_usd_ticks,
        )


def build_grok_qa_worker(
    *,
    broker: GrokQaBroker,
    api_key: str,
    model: str = "grok-4.5",
    allowed_clients: tuple[str, ...] = ("squid",),
    lease_seconds: int = 300,
    max_source_age_seconds: int = 86_400,
    timeout_seconds: float = 180.0,
    max_turns: int = 3,
    x_search_window_days: int = 1,
    max_output_tokens: int = 1_600,
    max_cost_in_usd_ticks: int = 500_000_000,
    canary_content_version_id: Optional[str] = None,
) -> GrokQaWorker:
    provider = XaiQaClient(
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        max_turns=max_turns,
        x_search_window_days=x_search_window_days,
        max_output_tokens=max_output_tokens,
        max_cost_in_usd_ticks=max_cost_in_usd_ticks,
    )
    return GrokQaWorker(
        broker=broker,
        provider=provider,
        allowed_clients=allowed_clients,
        lease_seconds=lease_seconds,
        max_source_age_seconds=max_source_age_seconds,
        canary_content_version_id=canary_content_version_id,
    )


__all__ = [
    "GrokQaBroker",
    "GrokQaBrokerError",
    "GrokQaProvider",
    "GrokQaRunResult",
    "GrokQaWorker",
    "build_grok_qa_worker",
    "verdict_sha256",
]
