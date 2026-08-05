from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.buzz.models import BuzzRelayReceipt, BuzzShadowEvent


_EVENT_ID = re.compile(r"^[a-f0-9]{64}$")
_MAX_PROCESS_OUTPUT = 65_536
_MESSAGE_TEMPLATE_VERSION = "origintrail-batch-review-ready@2"
_PROCESS_TIMEOUT_SECONDS = 30.0
BUZZ_CLI_RELEASE = "desktop-v0.5.4"
_MAX_MESSAGE_BYTES = 1_024


class BuzzCliError(RuntimeError):
    def __init__(self, code: str, *, retryable_before_attempt: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable_before_attempt = retryable_before_attempt


@dataclass(frozen=True)
class BuzzCliConfig:
    cli_path: Path
    relay_url: str
    private_key: str
    auth_tag: str | None
    channel_id: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    async def __call__(
        self, argv: tuple[str, ...], *, stdin: bytes, env: dict[str, str]
    ) -> CommandResult: ...


async def _run_command(
    argv: tuple[str, ...], *, stdin: bytes, env: dict[str, str]
) -> CommandResult:
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError:
        raise BuzzCliError(
            "buzz_cli_preflight_failed", retryable_before_attempt=True
        ) from None
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin), timeout=_PROCESS_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        # An orphaned `messages send` could still reach the relay after the
        # worker records delivery_unknown; the process must die with the wait.
        try:
            process.kill()
            await process.wait()
        except (OSError, ProcessLookupError):
            pass
        raise BuzzCliError(
            "buzz_cli_preflight_failed", retryable_before_attempt=True
        ) from None
    if len(stdout) > _MAX_PROCESS_OUTPUT or len(stderr) > _MAX_PROCESS_OUTPUT:
        raise BuzzCliError("buzz_cli_response_invalid")
    return CommandResult(process.returncode or 0, stdout, stderr)


def _utf8_prefix(value: str, maximum_bytes: int) -> str:
    if maximum_bytes < 0:
        raise BuzzCliError("buzz_delivery_request_invalid")
    if len(value.encode("utf-8")) <= maximum_bytes:
        return value
    suffix = "…"
    available = maximum_bytes - len(suffix.encode("utf-8"))
    if available < 1:
        raise BuzzCliError("buzz_delivery_request_invalid")
    result: list[str] = []
    used = 0
    for character in value:
        size = len(character.encode("utf-8"))
        if used + size > available:
            break
        result.append(character)
        used += size
    return "".join(result).rstrip() + suffix


def format_origintrail_message(event: BuzzShadowEvent, *, studio_origin: str) -> str:
    if any("@" in value for value in (
        event.source_url,
        event.studio_review_path,
        event.headline_ko,
        event.summary_ko,
    )):
        raise BuzzCliError("buzz_delivery_request_invalid")
    cost_usd = event.actual_cost_microusd / 1_000_000
    prefix = (
        "OriginTrail Batch 실제 검토 결과\n\n"
        f"{event.headline_ko}\n\n"
    )
    suffix = (
        f"\n\n상태: {event.result_code} · 모델: {event.model_tier}\n"
        f"실측 비용: ${cost_usd:.6f}\n"
        f"완료: {event.finished_at}\n"
        f"원문: {event.source_url}\n"
        f"검토: {studio_origin.rstrip('/')}{event.studio_review_path}\n"
        "자동 발행: OFF"
    )
    fixed_bytes = len((prefix + suffix).encode("utf-8"))
    summary = _utf8_prefix(event.summary_ko, _MAX_MESSAGE_BYTES - fixed_bytes)
    message = prefix + summary + suffix
    if "@" in message or not 1 <= len(message.encode("utf-8")) <= _MAX_MESSAGE_BYTES:
        raise BuzzCliError("buzz_delivery_request_invalid")
    return message


def buzz_message_fingerprints(
    *, relay_url: str, channel_id: str, message: str
) -> tuple[str, str]:
    message_sha = hashlib.sha256(message.encode("utf-8")).hexdigest()
    request_sha = hashlib.sha256(
        (
            f"coineasy-buzz-delivery\0{BUZZ_CLI_RELEASE}\0"
            f"{relay_url.rstrip('/')}\0{channel_id}\0{message_sha}"
        ).encode("utf-8")
    ).hexdigest()
    return message_sha, request_sha


class BuzzCliPublisher:
    def __init__(self, config: BuzzCliConfig, *, runner: CommandRunner = _run_command):
        self.config = config
        self.runner = runner

    def _env(self) -> dict[str, str]:
        env = {
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "BUZZ_RELAY_URL": self.config.relay_url,
            "BUZZ_PRIVATE_KEY": self.config.private_key,
        }
        if self.config.auth_tag is not None:
            env["BUZZ_AUTH_TAG"] = self.config.auth_tag
        return env

    async def preflight(self) -> None:
        result = await self.runner(
            (
                str(self.config.cli_path),
                "channels",
                "get",
                "--channel",
                self.config.channel_id,
            ),
            stdin=b"",
            env=self._env(),
        )
        if result.returncode != 0:
            raise BuzzCliError(
                "buzz_cli_preflight_failed",
                retryable_before_attempt=result.returncode in {2, 4},
            )
        try:
            parsed = json.loads(result.stdout)
        except ValueError as exc:
            raise BuzzCliError("buzz_cli_preflight_failed") from exc
        if not isinstance(parsed, (dict, list)):
            raise BuzzCliError("buzz_cli_preflight_failed")

    async def send_once(self, message: str) -> BuzzRelayReceipt:
        result = await self.runner(
            (
                str(self.config.cli_path),
                "messages",
                "send",
                "--channel",
                self.config.channel_id,
                "--content",
                "-",
            ),
            stdin=message.encode("utf-8"),
            env=self._env(),
        )
        if result.returncode != 0:
            raise BuzzCliError("buzz_delivery_unknown")
        try:
            parsed = json.loads(result.stdout)
        except ValueError as exc:
            raise BuzzCliError("buzz_delivery_unknown") from exc
        event_id = parsed.get("event_id") if isinstance(parsed, dict) else None
        if (
            not isinstance(parsed, dict)
            or parsed.get("accepted") is not True
            or not isinstance(event_id, str)
            or not _EVENT_ID.fullmatch(event_id)
            or parsed.get("mention_pubkeys") != []
            or not isinstance(parsed.get("message"), str)
        ):
            raise BuzzCliError("buzz_delivery_unknown")
        return BuzzRelayReceipt(event_id=event_id)


__all__ = [
    "BuzzCliConfig",
    "BuzzCliError",
    "BuzzCliPublisher",
    "BUZZ_CLI_RELEASE",
    "CommandResult",
    "buzz_message_fingerprints",
    "format_origintrail_message",
]
