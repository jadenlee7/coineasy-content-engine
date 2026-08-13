from __future__ import annotations

import time
import uuid
from typing import Callable, Protocol

from core.buzz.cli import (
    BuzzCliConfig,
    BuzzCliError,
    BuzzCliReader,
    buzz_operations_reply_fingerprints,
)
from core.buzz.errors import BuzzAdapterError
from core.buzz.models import (
    BuzzOperationsCommand,
    BuzzOperationsResponse,
    BuzzOperationsRunResult,
    BuzzRelayReceipt,
    BuzzThreadMessage,
)
from core.buzz.operations import eligible_operations_commands
from core.buzz.settings import BuzzOperationsSettings


class OperationsControl(Protocol):
    async def reconcile(self, *, limit: int) -> dict[str, int]: ...
    async def first_unknown(self) -> BuzzOperationsResponse | None: ...
    async def claim_response(self, **kwargs) -> BuzzOperationsResponse | None: ...
    async def record(
        self, command: BuzzOperationsCommand, *, channel_id: str
    ) -> BuzzOperationsResponse: ...
    async def mark_response_attempt(
        self, response: BuzzOperationsResponse, **kwargs
    ) -> bool: ...
    async def complete_response(
        self, response: BuzzOperationsResponse, **kwargs
    ) -> None: ...
    async def fail_response(
        self, response: BuzzOperationsResponse, **kwargs
    ) -> str: ...


class OperationsReader(Protocol):
    async def read_channel(
        self, *, since_epoch: int, limit: int = 100
    ) -> tuple[BuzzThreadMessage, ...]: ...
    async def read_thread(
        self, root_event_id: str
    ) -> tuple[BuzzThreadMessage, ...]: ...


class OperationsPublisher(Protocol):
    async def preflight(self) -> None: ...
    async def send_reply_once(
        self, message: str, reply_to: str
    ) -> BuzzRelayReceipt: ...


def _exact_channel(message: BuzzThreadMessage, channel_id: str) -> bool:
    return tuple(tag for tag in message.tags if tag and tag[0] == "h") == (
        ("h", channel_id),
    )


def _exact_response_reply(
    message: BuzzThreadMessage,
    response: BuzzOperationsResponse,
) -> bool:
    tags = tuple(tag for tag in message.tags if tag and tag[0] == "e")
    if response.thread_root_event_id == response.command_event_id:
        return tags == (("e", response.command_event_id, "", "reply"),)
    return tags == (
        ("e", response.thread_root_event_id, "", "root"),
        ("e", response.command_event_id, "", "reply"),
    )


class OriginTrailBuzzOperationsWorker:
    def __init__(
        self,
        *,
        control: OperationsControl,
        reader: OperationsReader,
        publisher: OperationsPublisher,
        relay_url: str,
        channel_id: str,
        reviewer_pubkeys: frozenset[str],
        service_pubkey: str,
        release_sha: str,
        protocol_start_epoch: int,
        response_lease_seconds: int,
        clock: Callable[[], float] = time.time,
        worker_id: str | None = None,
    ):
        self.control = control
        self.reader = reader
        self.publisher = publisher
        self.relay_url = relay_url
        self.channel_id = channel_id
        self.reviewer_pubkeys = reviewer_pubkeys
        self.service_pubkey = service_pubkey
        self.release_sha = release_sha
        self.protocol_start_epoch = protocol_start_epoch
        self.response_lease_seconds = response_lease_seconds
        self.clock = clock
        self.worker_id = worker_id or f"origintrail-operations:{uuid.uuid4()}"

    async def _reconcile_unknown(
        self, response: BuzzOperationsResponse
    ) -> BuzzOperationsRunResult:
        try:
            messages = await self.reader.read_thread(
                response.thread_root_event_id
            )
        except BuzzCliError as exc:
            return BuzzOperationsRunResult(
                ok=False, status="response_unknown",
                command=response.command,
                command_event_id=response.command_event_id,
                task_id=response.task_id, response_status="delivery_unknown",
                error=exc.code,
            )
        if len({message.event_id for message in messages}) != len(messages):
            return BuzzOperationsRunResult(
                ok=False, status="response_unknown",
                command=response.command,
                command_event_id=response.command_event_id,
                task_id=response.task_id, response_status="delivery_unknown",
                error="buzz_operations_thread_invalid",
            )
        now_epoch = int(self.clock())
        started = response.delivery_started_at_epoch or 0
        matches = [
            message for message in messages
            if message.kind == 9
            and message.pubkey == self.service_pubkey
            and message.content == response.message
            and _exact_channel(message, response.channel_id)
            and _exact_response_reply(message, response)
            and started - 300 <= message.created_at <= now_epoch + 300
        ]
        if len(matches) != 1:
            return BuzzOperationsRunResult(
                ok=False, status="response_unknown",
                command=response.command,
                command_event_id=response.command_event_id,
                task_id=response.task_id, response_status="delivery_unknown",
                error=(
                    "buzz_operations_response_not_observed"
                    if not matches else "buzz_operations_response_duplicate"
                ),
            )
        if response.request_sha256 is None:
            return BuzzOperationsRunResult(
                ok=False, status="response_unknown",
                command_event_id=response.command_event_id,
                error="buzz_operations_response_invalid",
            )
        try:
            await self.control.complete_response(
                response,
                worker_id=self.worker_id,
                request_sha256=response.request_sha256,
                relay_event_id=matches[0].event_id,
                reconciled=True,
            )
        except BuzzAdapterError as exc:
            return BuzzOperationsRunResult(
                ok=False, status="response_unknown",
                command_event_id=response.command_event_id,
                response_status="delivery_unknown", error=exc.code,
            )
        return BuzzOperationsRunResult(
            ok=True, status="response_reconciled",
            command=response.command,
            command_event_id=response.command_event_id,
            task_id=response.task_id,
            response_status="delivered",
            response_event_id=matches[0].event_id,
        )

    async def _fail_before_attempt(
        self, response: BuzzOperationsResponse, error_code: str
    ) -> BuzzOperationsRunResult:
        try:
            status = await self.control.fail_response(
                response, worker_id=self.worker_id, error_code=error_code,
                retryable_before_attempt=True,
            )
        except BuzzAdapterError:
            status = "unknown"
        return BuzzOperationsRunResult(
            ok=False, status="recorded", command=response.command,
            command_event_id=response.command_event_id, task_id=response.task_id,
            response_status=status, error=error_code,
        )

    async def _deliver(
        self, response: BuzzOperationsResponse
    ) -> BuzzOperationsRunResult:
        try:
            await self.publisher.preflight()
            message_sha, request_sha = buzz_operations_reply_fingerprints(
                relay_url=self.relay_url,
                channel_id=response.channel_id,
                service_pubkey=self.service_pubkey,
                release_sha=self.release_sha,
                reply_to=response.reply_to_event_id,
                message=response.message,
            )
        except BuzzCliError as exc:
            return await self._fail_before_attempt(response, exc.code)
        if message_sha != response.message_sha256:
            return await self._fail_before_attempt(
                response, "buzz_delivery_request_invalid"
            )
        try:
            authorized = await self.control.mark_response_attempt(
                response, worker_id=self.worker_id,
                request_sha256=request_sha,
            )
        except BuzzAdapterError as exc:
            # Commit status may be unknown. Never send without a fresh
            # authorized_once response from the durable provider fence.
            return BuzzOperationsRunResult(
                ok=False, status="recorded", command=response.command,
                command_event_id=response.command_event_id,
                task_id=response.task_id, response_status="unknown",
                error=exc.code,
            )
        if not authorized:
            return BuzzOperationsRunResult(
                ok=False, status="recorded", command=response.command,
                command_event_id=response.command_event_id,
                task_id=response.task_id, response_status="unknown",
                error="buzz_operations_response_not_authorized",
            )
        try:
            receipt = await self.publisher.send_reply_once(
                response.message, response.reply_to_event_id
            )
        except BuzzCliError as exc:
            try:
                await self.control.fail_response(
                    response, worker_id=self.worker_id,
                    error_code=exc.code, retryable_before_attempt=False,
                )
            except BuzzAdapterError:
                pass
            return BuzzOperationsRunResult(
                ok=False, status="recorded", command=response.command,
                command_event_id=response.command_event_id,
                task_id=response.task_id,
                response_status="delivery_unknown", error=exc.code,
            )
        try:
            await self.control.complete_response(
                response, worker_id=self.worker_id,
                request_sha256=request_sha,
                relay_event_id=receipt.event_id, reconciled=False,
            )
        except BuzzAdapterError as exc:
            return BuzzOperationsRunResult(
                ok=False, status="recorded", command=response.command,
                command_event_id=response.command_event_id,
                task_id=response.task_id, response_status="unknown",
                error=exc.code,
            )
        return BuzzOperationsRunResult(
            ok=True, status="responded", command=response.command,
            command_event_id=response.command_event_id, task_id=response.task_id,
            response_status="delivered", response_event_id=receipt.event_id,
        )

    async def run_once(self) -> BuzzOperationsRunResult:
        try:
            await self.control.reconcile(limit=100)
            unknown = await self.control.first_unknown()
        except BuzzAdapterError as exc:
            return BuzzOperationsRunResult(
                ok=False, status="failed", error=exc.code
            )
        if unknown is not None:
            return await self._reconcile_unknown(unknown)

        try:
            pending = await self.control.claim_response(
                command_event_id=None, worker_id=self.worker_id,
                lease_seconds=self.response_lease_seconds,
            )
        except BuzzAdapterError as exc:
            return BuzzOperationsRunResult(
                ok=False, status="failed", error=exc.code
            )
        if pending is not None:
            return await self._deliver(pending)

        try:
            messages = await self.reader.read_channel(
                since_epoch=self.protocol_start_epoch, limit=100
            )
        except BuzzCliError as exc:
            return BuzzOperationsRunResult(
                ok=False, status="failed", error=exc.code
            )
        candidates = eligible_operations_commands(
            messages,
            channel_id=self.channel_id,
            reviewer_pubkeys=self.reviewer_pubkeys,
            service_pubkey=self.service_pubkey,
            protocol_start_epoch=self.protocol_start_epoch,
            now_epoch=int(self.clock()),
        )
        for command in reversed(candidates[-10:]):
            try:
                recorded = await self.control.record(
                    command, channel_id=self.channel_id
                )
            except BuzzAdapterError as exc:
                return BuzzOperationsRunResult(
                    ok=False, status="failed",
                    command=command.command,
                    command_event_id=command.event_id,
                    error=exc.code,
                )
            if recorded.status == "delivered":
                continue
            if recorded.status == "delivery_unknown":
                return await self._reconcile_unknown(recorded)
            if recorded.status == "failed":
                return BuzzOperationsRunResult(
                    ok=False, status="recorded", command=recorded.command,
                    command_event_id=recorded.command_event_id,
                    task_id=recorded.task_id, response_status="failed",
                    error="buzz_operations_response_failed",
                )
            try:
                claimed = await self.control.claim_response(
                    command_event_id=command.event_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.response_lease_seconds,
                )
            except BuzzAdapterError as exc:
                return BuzzOperationsRunResult(
                    ok=False, status="recorded", command=command.command,
                    command_event_id=command.event_id,
                    task_id=recorded.task_id, response_status="unknown",
                    error=exc.code,
                )
            if claimed is None:
                return BuzzOperationsRunResult(
                    ok=True, status="recorded", command=command.command,
                    command_event_id=command.event_id,
                    task_id=recorded.task_id, reused=recorded.reused,
                    response_status=recorded.status,
                )
            result = await self._deliver(claimed)
            return BuzzOperationsRunResult(
                **{**result.__dict__, "reused": recorded.reused}
            )
        return BuzzOperationsRunResult(ok=True, status="idle")


def build_origintrail_buzz_operations_worker(
    settings: BuzzOperationsSettings,
) -> OriginTrailBuzzOperationsWorker:
    from core.buzz.clients import BuzzOperationsControlClient
    from core.buzz.cli import BuzzCliPublisher

    config = BuzzCliConfig(
        cli_path=settings.cli_path,
        relay_url=settings.relay_url,
        private_key=settings.private_key,
        auth_tag=settings.auth_tag,
        channel_id=settings.channel_id,
    )
    return OriginTrailBuzzOperationsWorker(
        control=BuzzOperationsControlClient(
            url=settings.operations_url, token=settings.operations_token
        ),
        reader=BuzzCliReader(config),
        publisher=BuzzCliPublisher(config),
        relay_url=settings.relay_url,
        channel_id=settings.channel_id,
        reviewer_pubkeys=settings.reviewer_pubkeys,
        service_pubkey=settings.service_pubkey,
        release_sha=settings.release_sha,
        protocol_start_epoch=settings.protocol_start_epoch,
        response_lease_seconds=settings.response_lease_seconds,
    )


__all__ = [
    "OriginTrailBuzzOperationsWorker",
    "build_origintrail_buzz_operations_worker",
]
