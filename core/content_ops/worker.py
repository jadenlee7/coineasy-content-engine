"""Internal review cards only; no database, generation, or publishing authority.

A begin receipt consumes the right to send. Neither an uncertain send nor an
uncertain finish acknowledgement is retried by this worker.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx


GATEWAY_PATH = "/.netlify/functions/content-ops-review"
DESTINATION_ROLE = "content_ops_private"
APP_ORIGIN = "https://coineasy-newscard.netlify.app"
BUILD_SHA_PATH = Path("/app/content-ops-build-sha")
MAX_CLAIMS = 4
CONFIRMATION_CAPTURE_TIMEOUT_SECONDS = 20
_KST = timezone(timedelta(hours=9))
_OFFICIAL_X = {
    "yellow": "yellow",
    "origintrail": "origin_trail",
    "squid": "squidrouter",
    "babylon": "babylonlabs_io",
}
_SHA40 = re.compile(r"[a-f0-9]{40}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_CANARY_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z")
_BOT_TOKEN = re.compile(r"([1-9][0-9]{4,15}):[A-Za-z0-9_-]{30,100}\Z")
_CHAT_ID = re.compile(r"-100[1-9][0-9]{6,12}\Z")
_RELAY_NAMES = frozenset({
    "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
    "TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID",
})
_FORBIDDEN_NAMES = frozenset({
    "API_SECRET", "STUDIO_ACCESS_TOKEN", "STUDIO_AUTOMATION_TOKEN",
    "PUBLICATION_WORKER_TOKEN", "CONTENT_STUDIO_WORKSPACE_ID", "DATABASE_URL",
    "DIRECT_URL", "DB_PASSWORD", "DB_TOKEN", "DB_ADMIN_KEY", "PGPASSWORD",
    "PGUSER", "X_BEARER_TOKEN", "X_API_KEY", "X_API_SECRET",
})
_PRIVATE_MARKERS = re.compile(
    r"(?:api\.telegram\.org/bot|t\.me/\+|t\.me/joinchat/|"
    r"/storage/v1/object/sign/|supabase_service_role_key|studio_access_token|"
    r"authorization\s*[:=]|[1-9][0-9]{4,15}:[A-Za-z0-9_-]{30,100}|"
    r"(?<!\d)-100[1-9][0-9]{6,12}(?!\d))", re.IGNORECASE,
)


class _PrivateHttpLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx logs request URLs at INFO; Telegram embeds its credential in
        # that URL. Do not emit either private transport's request log.
        message = record.getMessage()
        return "api.telegram.org/bot" not in message and GATEWAY_PATH not in message


logging.getLogger("httpx").addFilter(_PrivateHttpLogFilter())


class ReviewError(RuntimeError):
    """Only fixed, non-sensitive codes may cross the runner boundary."""


def review_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    value = env.get("CONTENT_OPS_REVIEW_ENABLED", "false")
    if value not in {"true", "false"}:
        raise ValueError("content_ops_review_flag_invalid")
    return value == "true"


def _origin(value: str) -> str:
    if value not in {APP_ORIGIN, APP_ORIGIN + "/"}:
        raise ValueError("content_ops_origin_invalid")
    return APP_ORIGIN


def _read_build_sha() -> str:
    try:
        return BUILD_SHA_PATH.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise ValueError("content_ops_build_stamp_invalid") from None


def _validate_secret_boundary(env: Mapping[str, str]) -> None:
    for name in env:
        upper = name.upper()
        if upper in _RELAY_NAMES or not env.get(name, ""):
            continue
        forbidden = (
            upper in _FORBIDDEN_NAMES
            or upper.startswith(("SUPABASE_", "POSTGRES_", "XAI_", "OPENAI_",
                                 "ANTHROPIC_", "TYPEFULLY_", "FIGMA_", "TWITTER_"))
            or (upper.startswith(("GROK_", "DB_", "DATABASE_"))
                and any(part in upper for part in ("KEY", "SECRET", "TOKEN", "PASSWORD", "URL")))
            or (upper.startswith("TELEGRAM_") and any(
                part in upper for part in ("TOKEN", "CHAT_ID", "CHANNEL", "ADMIN")
            ))
        )
        if forbidden:
            raise ValueError("content_ops_forbidden_credential")


@dataclass(frozen=True)
class ReviewSettings:
    gateway_origin: str
    gateway_token: str = field(repr=False)
    release_sha: str
    studio_origin: str
    relay_bot_token: str = field(repr=False)
    relay_chat_id: str = field(repr=False)
    mode: str = "daily"
    canary_version_id: str | None = None
    packet_mode: str = "link_card"

    def __post_init__(self) -> None:
        if self.packet_mode not in {"link_card", "squid_bundle_v1"} or (
            self.packet_mode == "squid_bundle_v1" and self.mode != "canary"
        ):
            raise ValueError("content_ops_packet_mode_invalid")
        if self.mode not in {"canary", "daily"}:
            raise ValueError("content_ops_review_mode_invalid")
        if self.mode == "canary":
            if not isinstance(self.canary_version_id, str) or _CANARY_UUID.fullmatch(self.canary_version_id) is None:
                raise ValueError("content_ops_canary_version_invalid")
        elif self.canary_version_id is not None:
            raise ValueError("content_ops_daily_version_forbidden")

    @property
    def scope(self) -> dict[str, str | None]:
        return {"mode": self.mode, "content_version_id": self.canary_version_id,
                **({"packet_mode": self.packet_mode} if self.packet_mode != "link_card" else {})}

    @property
    def max_claims(self) -> int:
        return 1 if self.mode == "canary" else MAX_CLAIMS

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None,
        *, stamp_reader: Callable[[], str] | None = None,
    ) -> "ReviewSettings":
        env = os.environ if environ is None else environ
        review_enabled(env)
        mode = env.get("CONTENT_OPS_REVIEW_MODE", "")
        version = env.get("CONTENT_OPS_REVIEW_CANARY_VERSION_ID", "")
        if mode not in {"canary", "daily"}:
            raise ValueError("content_ops_review_mode_invalid")
        if mode == "canary" and _CANARY_UUID.fullmatch(version) is None:
            raise ValueError("content_ops_canary_version_invalid")
        if mode == "daily" and version:
            raise ValueError("content_ops_daily_version_forbidden")
        _validate_secret_boundary(env)
        gateway_token = env.get("CONTENT_OPS_GATEWAY_TOKEN", "")
        bot_token = env.get("TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", "")
        chat_id = env.get("TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", "")
        release_sha = env.get("CONTENT_OPS_REVIEW_RELEASE_SHA", "")
        runtime_sha = env.get("RAILWAY_GIT_COMMIT_SHA", "")
        build_sha = (stamp_reader or _read_build_sha)()
        if (
            re.fullmatch(r"[A-Za-z0-9_-]{32,256}", gateway_token) is None
            or _BOT_TOKEN.fullmatch(bot_token) is None
            or _CHAT_ID.fullmatch(chat_id) is None
            or _SHA40.fullmatch(release_sha) is None
            or _SHA40.fullmatch(runtime_sha) is None
            or not isinstance(build_sha, str) or _SHA40.fullmatch(build_sha) is None
            or not secrets.compare_digest(release_sha, runtime_sha)
            or not secrets.compare_digest(release_sha, build_sha)
            or secrets.compare_digest(gateway_token.encode(), bot_token.encode())
        ):
            raise ValueError("content_ops_configuration_invalid")
        # Dedicated credentials must not alias any other credential variable.
        for name, value in env.items():
            if name in {"CONTENT_OPS_GATEWAY_TOKEN", *_RELAY_NAMES} or not value:
                continue
            if any(marker in name.upper() for marker in ("TOKEN", "SECRET", "KEY")):
                if any(secrets.compare_digest(value.encode(), token.encode())
                       for token in (gateway_token, bot_token)):
                    raise ValueError("content_ops_credential_reused")
            if "CHAT_ID" in name.upper() or "CHANNEL" in name.upper():
                if secrets.compare_digest(value.encode(), chat_id.encode()):
                    raise ValueError("content_ops_destination_reused")
        return cls(
            gateway_origin=_origin(env.get("CONTENT_OPS_GATEWAY_URL", "")),
            gateway_token=gateway_token,
            release_sha=release_sha,
            studio_origin=_origin(env.get("CONTENT_OPS_STUDIO_URL", "")),
            relay_bot_token=bot_token,
            relay_chat_id=chat_id,
            mode=mode,
            canary_version_id=version if mode == "canary" else None,
            packet_mode=env.get("CONTENT_OPS_REVIEW_PACKET_MODE", "link_card"),
        )


def _uuid(value: object) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError
    except (ValueError, AttributeError):
        raise ReviewError("content_ops_claim_invalid") from None
    return value


def _bounded_text(value: object, maximum: int, *, multiline: bool = False) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= maximum
        or not value.strip() or _PRIVATE_MARKERS.search(value)
        or any((ord(c) < 32 and not (multiline and c in "\n\t"))
               or ord(c) == 127 for c in value)
    ):
        raise ReviewError("content_ops_claim_invalid")
    return value


@dataclass(frozen=True, repr=False)
class ReviewClaim:
    outbox_id: str
    claim_token: str
    client_id: str
    kst_date: str
    content_item_id: str
    content_version_id: str
    source_item_id: str
    generate_job_id: str
    banner_sha256: str
    title: str
    telegram_copy: str
    x_copy: str
    source_url: str
    source_published_at: datetime

    @classmethod
    def parse(cls, raw: object, now: datetime) -> "ReviewClaim":
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise ReviewError("content_ops_claim_invalid")
        ids = {name: _uuid(raw[name]) for name in (
            "outbox_id", "claim_token", "content_item_id", "content_version_id",
            "source_item_id", "generate_job_id",
        )}
        client_id = raw["client_id"]
        if not isinstance(client_id, str) or client_id not in _OFFICIAL_X:
            raise ReviewError("content_ops_claim_invalid")
        kst_date = raw["kst_date"]
        if now.tzinfo is None or kst_date != now.astimezone(_KST).date().isoformat():
            raise ReviewError("content_ops_claim_invalid")
        source_url = _bounded_text(raw["source_url"], 200)
        if re.fullmatch(
            rf"https://x\.com/{_OFFICIAL_X[client_id]}/status/[1-9][0-9]{{0,18}}",
            source_url, re.IGNORECASE,
        ) is None:
            raise ReviewError("content_ops_claim_invalid")
        try:
            published_raw = _bounded_text(raw["source_published_at"], 40)
            published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            if published.tzinfo is None or not timedelta(0) <= now - published < timedelta(hours=24):
                raise ValueError
        except (ValueError, TypeError):
            raise ReviewError("content_ops_claim_invalid") from None
        banner_hash = raw["banner_sha256"]
        if not isinstance(banner_hash, str) or _SHA256.fullmatch(banner_hash) is None:
            raise ReviewError("content_ops_claim_invalid")
        return cls(
            **ids, client_id=client_id, kst_date=kst_date,
            banner_sha256=banner_hash,
            title=_bounded_text(raw["title"], 240),
            telegram_copy=_bounded_text(raw["telegram_copy"], 3400, multiline=True),
            x_copy=_bounded_text(raw["x_copy"], 1000, multiline=True),
            source_url=source_url, source_published_at=published,
        )


@dataclass(frozen=True, repr=False)
class ReviewPacket:
    text: str
    review_url: str
    packet_sha256: str


def build_packet(claim: ReviewClaim, studio_origin: str, now: datetime) -> ReviewPacket:
    review_url = _origin(studio_origin) + "/?" + urlencode({
        "view": "library", "content": claim.content_item_id,
        "version": claim.content_version_id,
    })
    age_minutes = int((now - claim.source_published_at).total_seconds() // 60)
    if not 0 <= age_minutes < 1440:
        raise ReviewError("content_ops_claim_expired")
    esc = html.escape
    text = "\n".join([
        "<b>내부 검토 카드 · 승인/게시 아님</b>",
        f"<b>{esc(claim.client_id)} · {esc(claim.title)}</b>",
        f"KST 작업일: {claim.kst_date} · 출처: {age_minutes}분 전",
        f'<a href="{esc(claim.source_url, quote=True)}">공식 X 원문</a>',
        "⚠️ 사람의 사실 확인·브랜드 검토가 필요합니다. 이 카드는 승인할 수 없습니다.",
        "Telegram/X 전문과 배너는 Studio의 정확한 버전에서 확인하세요.",
        f'<a href="{esc(review_url, quote=True)}">Studio에서 버전 확인</a>',
        f"content_item_id: <code>{claim.content_item_id}</code>",
        f"content_version_id: <code>{claim.content_version_id}</code>",
        f"source_item_id: <code>{claim.source_item_id}</code>",
        f"generate_job_id: <code>{claim.generate_job_id}</code>",
        f"outbox_id: <code>{claim.outbox_id}</code>",
        f"banner_sha256: <code>{claim.banner_sha256}</code>",
    ])
    if len(text.encode("utf-16-le")) // 2 > 4096:
        raise ReviewError("content_ops_packet_too_large")
    digest = hashlib.sha256(json.dumps({
        "text": text, "review_url": review_url, "destination_role": DESTINATION_ROLE,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return ReviewPacket(text=text, review_url=review_url, packet_sha256=digest)


def _json_response(response: httpx.Response) -> dict[str, object]:
    if len(response.content) > 128_000:
        raise ReviewError("content_ops_response_invalid")
    try:
        raw = response.json()
    except (ValueError, UnicodeError):
        raise ReviewError("content_ops_response_invalid") from None
    if not isinstance(raw, dict):
        raise ReviewError("content_ops_response_invalid")
    return raw


class ReviewGateway(Protocol):
    async def request(self, action: str, **fields: object) -> dict[str, object]: ...


class HttpReviewGateway:
    def __init__(self, settings: ReviewSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    async def request(self, action: str, **fields: object) -> dict[str, object]:
        if action not in {"reconcile", "claim", "begin", "finish", "bundle_prepare", "bundle_begin",
                          "bundle_part_begin", "bundle_part_finish", "bundle_status"}:
            raise ReviewError("content_ops_action_invalid")
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=False, trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._settings.gateway_origin + GATEWAY_PATH,
                    headers={
                        "Authorization": "Bearer " + self._settings.gateway_token,
                        "x-content-ops-expected-release-sha": self._settings.release_sha,
                        "x-content-ops-mode": self._settings.mode,
                        **({"x-content-ops-packet-mode": self._settings.packet_mode}
                           if self._settings.packet_mode != "link_card" else {}),
                        **({"x-content-ops-version-id": self._settings.canary_version_id}
                           if self._settings.mode == "canary" else {}),
                        "Accept": "application/json",
                    }, json={"action": action, **fields},
                )
        except (httpx.HTTPError, ValueError):
            raise ReviewError("content_ops_gateway_unavailable") from None
        if not 200 <= response.status_code < 300:
            raise ReviewError("content_ops_gateway_unavailable")
        raw = _json_response(response)
        _gateway_receipt(raw, self._settings)
        return raw

    async def load_bundle_image(self, claim, snapshot_sha256):
        if self._settings.packet_mode != "squid_bundle_v1" or claim.client_id != "squid":
            raise ReviewError("content_ops_bundle_scope_invalid")
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False, transport=self._transport) as client:
                async with client.stream("POST", self._settings.gateway_origin + GATEWAY_PATH,
                    headers={"Authorization": "Bearer " + self._settings.gateway_token,
                             "x-content-ops-expected-release-sha": self._settings.release_sha,
                             "x-content-ops-mode": "canary", "x-content-ops-version-id": self._settings.canary_version_id,
                             "x-content-ops-packet-mode": "squid_bundle_v1", "Accept-Encoding": "identity"},
                    json={"action": "bundle_image", "outbox_id": claim.outbox_id,
                          "claim_token": claim.claim_token, "snapshot_sha256": snapshot_sha256}) as response:
                    if (response.status_code != 200 or response.headers.get("content-type") != "image/png"
                        or response.headers.get("x-content-ops-release-sha") != self._settings.release_sha
                        or response.headers.get("x-content-ops-version-id") != claim.content_version_id
                        or response.headers.get("x-content-ops-snapshot-sha256") != snapshot_sha256
                        or "location" in response.headers or "content-range" in response.headers
                        or response.headers.get("content-encoding", "identity") != "identity"):
                        raise ReviewError("content_ops_bundle_image_invalid")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 10_000_000:
                            raise ReviewError("content_ops_bundle_image_invalid")
                        data.extend(chunk)
                    if response.headers.get("content-length", str(len(data))) != str(len(data)):
                        raise ReviewError("content_ops_bundle_image_invalid")
                    return bytes(data)
        except Exception:
            raise ReviewError("content_ops_bundle_image_unavailable") from None


def _gateway_receipt(raw: object, settings: ReviewSettings) -> dict[str, object]:
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        raise ReviewError("content_ops_gateway_receipt_invalid")
    sha = raw.get("release_sha")
    if not isinstance(sha, str) or not secrets.compare_digest(sha, settings.release_sha):
        raise ReviewError("content_ops_gateway_release_mismatch")
    scope = raw.get("scope")
    if not isinstance(scope, dict) or scope != settings.scope:
        raise ReviewError("content_ops_gateway_scope_mismatch")
    return raw


@dataclass(frozen=True)
class DeliveryReceipt:
    outcome: str
    message_id: int | None = None


class Relay(Protocol):
    async def preflight(self) -> None: ...
    async def send(self, packet: ReviewPacket) -> DeliveryReceipt: ...


class TelegramReviewRelay:
    def __init__(self, settings: ReviewSettings, *, transport: httpx.AsyncBaseTransport | None = None,
                 response_capture=None):
        # Explicit dependency only: build_worker never enables/installs it.
        from core.content_ops.confirmation_response_capture import ConfirmationResponseCapture, DispatchResponseCapture
        if response_capture is not None and type(response_capture) not in (ConfirmationResponseCapture, DispatchResponseCapture):
            raise ReviewError("content_ops_capture_invalid")
        self._settings = settings
        self._transport = transport
        self._response_capture = response_capture
        self._verified = False
        self._bundle_attempts: set[str] = set()

    async def _post(self, method: str, body: dict[str, object]) -> httpx.Response:
        capture = self._response_capture
        capturing = capture is not None and capture.enabled and method not in {
            'getMe', 'getChat', 'getChatMember',
        }
        if capturing:
            capture.begin(settings=self._settings, method=method, body=body, verified=self._verified)
        # Transport is one attempt only; redirects and environment proxies are
        # disabled, so credentials cannot move to another origin implicitly.
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=False, trust_env=False,
            transport=self._transport,
        ) as client:
            if capturing:
                from core.content_ops.confirmation_response_capture import ConfirmationCaptureError
                try:
                    # Bound the complete stream AND sink, not just each read.
                    async with asyncio.timeout(CONFIRMATION_CAPTURE_TIMEOUT_SECONDS):
                        async with client.stream('POST',
                            "https://api.telegram.org/bot" + self._settings.relay_bot_token + "/" + method,
                            json=body, headers={'Accept-Encoding':'identity'},
                        ) as response:
                            return await capture.collect(response)
                except Exception:
                    raise ConfirmationCaptureError('confirmation_capture_unconfirmed') from None
            return await client.post(
                "https://api.telegram.org/bot" + self._settings.relay_bot_token + "/" + method,
                json=body,
            )

    async def _read(self, method: str, body: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._post(method, body)
            raw = _json_response(response)
        except (httpx.HTTPError, ValueError, ReviewError):
            raise ReviewError("content_ops_relay_preflight_failed") from None
        if not 200 <= response.status_code < 300 or raw.get("ok") is not True:
            raise ReviewError("content_ops_relay_preflight_failed")
        result = raw.get("result")
        if not isinstance(result, dict):
            raise ReviewError("content_ops_relay_preflight_failed")
        return result

    async def preflight(self) -> None:
        self._verified = False
        expected_id = int(self._settings.relay_bot_token.split(":", 1)[0])
        bot = await self._read("getMe", {})
        if type(bot.get("id")) is not int or bot["id"] != expected_id or bot.get("is_bot") is not True:
            raise ReviewError("content_ops_relay_identity_mismatch")
        chat_id = self._settings.relay_chat_id
        chat = await self._read("getChat", {"chat_id": chat_id})
        if (
            type(chat.get("id")) is not int or chat["id"] != int(chat_id)
            or chat.get("type") != "supergroup" or chat.get("username")
            or chat.get("active_usernames") or chat.get("linked_chat_id")
        ):
            raise ReviewError("content_ops_relay_destination_mismatch")
        member = await self._read("getChatMember", {"chat_id": chat_id, "user_id": expected_id})
        user = member.get("user")
        if (
            member.get("status") != "member" or not isinstance(user, dict)
            or type(user.get("id")) is not int or user["id"] != expected_id
            or user.get("is_bot") is not True
        ):
            raise ReviewError("content_ops_relay_role_invalid")
        self._verified = True

    async def send(self, packet: ReviewPacket) -> DeliveryReceipt:
        if not self._verified:
            raise ReviewError("content_ops_relay_preflight_required")
        try:
            response = await self._post("sendMessage", {
                "chat_id": self._settings.relay_chat_id, "text": packet.text,
                "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
                "protect_content": True,
            })
            raw = _json_response(response)
        except (httpx.HTTPError, ValueError, ReviewError):
            return DeliveryReceipt("delivery_unknown")
        if 400 <= response.status_code < 500 and raw.get("ok") is False:
            return DeliveryReceipt("rejected")
        if not 200 <= response.status_code < 300 or raw.get("ok") is not True:
            return DeliveryReceipt("delivery_unknown")
        result = raw.get("result")
        if not isinstance(result, dict):
            return DeliveryReceipt("delivery_unknown")
        chat = result.get("chat")
        message_id = result.get("message_id")
        if (
            not isinstance(chat, dict) or type(chat.get("id")) is not int
            or chat["id"] != int(self._settings.relay_chat_id)
            or type(message_id) is not int or not 0 < message_id <= 2**53 - 1
        ):
            return DeliveryReceipt("delivery_unknown")
        return DeliveryReceipt("sent", message_id)

    async def send_bundle_part(self, part, image: bytes) -> DeliveryReceipt:
        from core.content_ops.squid_bundle import BundlePart
        # An exact confirmation capture scope cannot become a media/bundle send.
        if self._response_capture is not None and self._response_capture.enabled:
            raise ReviewError("content_ops_bundle_send_denied")
        if (self._settings.packet_mode != "squid_bundle_v1" or not self._verified
            or type(part) is not BundlePart or part.sha256 in self._bundle_attempts
            or type(part.index) is not int or part.index not in (0, 1, 2)
            or part.kind != ("image", "telegram", "x")[part.index]):
            raise ReviewError("content_ops_bundle_send_denied")
        self._bundle_attempts.add(part.sha256)  # Consume before possible I/O.
        try:
            if part.kind == "image":
                async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False, transport=self._transport) as client:
                    response = await client.post("https://api.telegram.org/bot" + self._settings.relay_bot_token + "/sendPhoto",
                        data={"chat_id": self._settings.relay_chat_id, "caption": part.text, "protect_content": "true"},
                        files={"photo": ("news-card.png", image, "image/png")})
            else:
                response = await self._post("sendMessage", {"chat_id": self._settings.relay_chat_id,
                    "text": part.text, "link_preview_options": {"is_disabled": True}, "protect_content": True})
            raw = _json_response(response)
            if 400 <= response.status_code < 500 and raw.get("ok") is False:
                return DeliveryReceipt("rejected")
            r = raw.get("result")
            if not 200 <= response.status_code < 300 or raw.get("ok") is not True or not isinstance(r, dict):
                return DeliveryReceipt("delivery_unknown")
            chat, sender, mid = r.get("chat", {}), r.get("from", {}), r.get("message_id")
            if (type(mid) is not int or not 0 < mid <= 2**53 - 1
                or not isinstance(chat, dict) or not isinstance(sender, dict)
                or type(chat.get("id")) is not int or chat.get("id") != int(self._settings.relay_chat_id)
                or chat.get("type") != "supergroup" or chat.get("username") or chat.get("active_usernames") or chat.get("linked_chat_id")
                or type(sender.get("id")) is not int or sender.get("id") != int(self._settings.relay_bot_token.split(":")[0])
                or sender.get("is_bot") is not True or r.get("caption" if part.kind == "image" else "text") != part.text
                or (part.kind == "image" and (not isinstance(r.get("photo"), list) or not r["photo"]))):
                return DeliveryReceipt("delivery_unknown")
            return DeliveryReceipt("sent", mid)
        except Exception:
            return DeliveryReceipt("delivery_unknown")


class ReviewWorker:
    def __init__(
        self, settings: ReviewSettings, gateway: ReviewGateway, relay: Relay,
        *, now: Callable[[], datetime] | None = None,
    ):
        self._settings = settings
        self._gateway = gateway
        self._relay = relay
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def _request(self, action: str, **fields: object) -> dict[str, object]:
        return _gateway_receipt(
            await self._gateway.request(action, **fields), self._settings,
        )

    async def run(self) -> dict[str, object]:
        counts = {key: 0 for key in (
            "queued", "claimed", "sent", "rejected", "delivery_unknown", "ack_unknown", "blocked",
        )}
        seen_clients: set[str] = set()
        try:
            await self._relay.preflight()
            reconciled = await self._request("reconcile")
            queued = reconciled.get("queued")
            if type(queued) is not int or not 0 <= queued <= self._settings.max_claims:
                raise ReviewError("content_ops_reconcile_invalid")
            counts["queued"] = queued
            for _ in range(self._settings.max_claims):
                claim_token = str(uuid4())
                raw = await self._request("claim", claim_token=claim_token)
                if "claim" not in raw:
                    raise ReviewError("content_ops_claim_invalid")
                if raw["claim"] is None:
                    break
                counts["claimed"] += 1
                claim = ReviewClaim.parse(raw["claim"], self._now())
                if self._settings.mode == "canary" and claim.content_version_id != self._settings.canary_version_id:
                    raise ReviewError("content_ops_canary_claim_version_mismatch")
                # Even a compromised claim must not reflect the credentials
                # known only to this courier into an internal message.
                if any(
                    secret in value
                    for value in (claim.title, claim.telegram_copy, claim.x_copy, claim.source_url)
                    for secret in (
                        self._settings.gateway_token, self._settings.relay_bot_token,
                        self._settings.relay_chat_id,
                    )
                ):
                    raise ReviewError("content_ops_claim_private_value")
                if not secrets.compare_digest(claim.claim_token, claim_token):
                    raise ReviewError("content_ops_claim_token_mismatch")
                if claim.client_id in seen_clients:
                    raise ReviewError("content_ops_duplicate_client")
                seen_clients.add(claim.client_id)
                if self._settings.packet_mode == "squid_bundle_v1":
                    return await self._run_squid_bundle(claim, counts)
                packet = build_packet(claim, self._settings.studio_origin, self._now())
                fields = {
                    "outbox_id": claim.outbox_id, "claim_token": claim.claim_token,
                    "packet_sha256": packet.packet_sha256,
                }
                begun = await self._request("begin", **fields)
                if begun.get("accepted") is not True:
                    raise ReviewError("content_ops_begin_not_accepted")
                try:
                    receipt = await self._relay.send(packet)
                except Exception:
                    # Once begun, even an unclassified transport failure is
                    # uncertain. Never authorize another send for this claim.
                    receipt = DeliveryReceipt("delivery_unknown")
                if (
                    not isinstance(receipt, DeliveryReceipt)
                    or receipt.outcome not in {"sent", "rejected", "delivery_unknown"}
                    or (receipt.outcome == "sent" and (
                        type(receipt.message_id) is not int or receipt.message_id <= 0
                    )) or (receipt.outcome != "sent" and receipt.message_id is not None)
                ):
                    receipt = DeliveryReceipt("delivery_unknown")
                counts[receipt.outcome] += 1
                try:
                    finished = await self._request(
                        "finish", outbox_id=claim.outbox_id, claim_token=claim.claim_token,
                        outcome=receipt.outcome, message_id=receipt.message_id,
                    )
                    if finished.get("accepted") is not True:
                        raise ReviewError("content_ops_finish_not_accepted")
                except Exception:
                    counts["ack_unknown"] += 1
                    break
                if receipt.outcome != "sent":
                    break
        except Exception:
            counts["blocked"] += 1
        failed = any(counts[name] for name in ("blocked", "rejected", "delivery_unknown", "ack_unknown"))
        return {"ok": not failed, "enabled": True, **counts}

    async def _run_squid_bundle(self, claim, counts):
        from core.content_ops.squid_bundle import build_squid_bundle, validate_bundle_image, bundle_delivery_label
        receipts = []
        recorded = 0
        fields = {"outbox_id": claim.outbox_id, "claim_token": claim.claim_token}
        prepared = await self._request("bundle_prepare", **fields)
        bundle = build_squid_bundle(claim, prepared.get("bundle"), self._settings.studio_origin, self._now())
        for part in bundle.parts:
            if any(secret in part.text for secret in (self._settings.gateway_token, self._settings.relay_bot_token, self._settings.relay_chat_id)):
                raise ReviewError("content_ops_claim_private_value")
        import asyncio
        image = validate_bundle_image(await asyncio.wait_for(
            self._gateway.load_bundle_image(claim, bundle.snapshot_sha256), timeout=20), bundle)
        pin = {**fields, "snapshot_sha256": bundle.snapshot_sha256}
        started = await self._request("bundle_begin", **pin, packet_sha256=bundle.packet_sha256, parts=bundle.manifest)
        if started.get("accepted") is not True:
            raise ReviewError("content_ops_begin_not_accepted")
        status = "sending"
        for part in bundle.parts:
            try:
                await self._relay.preflight()
                begun = await self._request("bundle_part_begin", **pin, part_index=part.index)
                if begun.get("accepted") is not True:
                    raise ReviewError("content_ops_begin_not_accepted")
            except Exception:
                counts["ack_unknown"] += 1
                status = "delivery_unknown"
                break
            try:
                receipt = await self._relay.send_bundle_part(part, image)
                if (type(receipt) is not DeliveryReceipt or receipt.outcome not in {"sent", "rejected", "delivery_unknown"}
                    or (receipt.outcome == "sent" and (type(receipt.message_id) is not int or not 0 < receipt.message_id <= 2**53 - 1))
                    or (receipt.outcome != "sent" and receipt.message_id is not None)):
                    raise ReviewError("content_ops_bundle_receipt_invalid")
            except Exception:
                receipt = DeliveryReceipt("delivery_unknown")
            evidence = {"index": part.index, "kind": part.kind, "outcome": receipt.outcome, "message_id": receipt.message_id}
            receipts.append(evidence)
            try:
                finished = await self._request("bundle_part_finish", **pin, part_index=part.index,
                    outcome=receipt.outcome, message_id=receipt.message_id)
                if finished.get("accepted") is not True or finished.get("receipts") != receipts:
                    raise ReviewError("content_ops_finish_not_accepted")
                status = finished.get("status")
                recorded = len(receipts)
                if status == "sent" and part.index != 2:
                    raise ReviewError("content_ops_finish_not_accepted")
            except Exception:
                counts["ack_unknown"] += 1
                status = "delivery_unknown"
                break
            if receipt.outcome != "sent" or status not in {"sending", "sent"}:
                break
        complete = status == "sent" and bundle_delivery_label(status, receipts) == "팀 전달 완료"
        counts["sent" if complete else "delivery_unknown"] += 1
        return {"ok": complete, "enabled": True, **counts, "packet_mode": "squid_bundle_v1",
                "team_delivery_complete": complete, "delivery_label": bundle_delivery_label(status, receipts),
                "parts_received": sum(r["outcome"] == "sent" for r in receipts),
                "parts_recorded": recorded, "parts_expected": 3}


def build_worker(
    settings: ReviewSettings, *, transport: httpx.AsyncBaseTransport | None = None,
) -> ReviewWorker:
    return ReviewWorker(
        settings, HttpReviewGateway(settings, transport=transport),
        TelegramReviewRelay(settings, transport=transport),
    )
