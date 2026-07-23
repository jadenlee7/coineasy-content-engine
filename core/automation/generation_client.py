from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

import httpx


_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,119}$")
_X_STATUS_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$")
_ALLOWED_PRODUCTION_HOST = "coineasy-newscard.netlify.app"


class GenerationRequestError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class GeneratedCatalogResult:
    content_item_id: str
    content_version_id: str
    asset_ids: tuple[str, ...]
    reused: bool


def _validated_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("STUDIO_BASE_URL is invalid") from exc
    is_local = parsed.hostname in {"localhost", "127.0.0.1"}
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.hostname is None
        or parsed.hostname.lower() not in {_ALLOWED_PRODUCTION_HOST, "localhost", "127.0.0.1"}
        or (parsed.scheme != "https" and not (is_local and parsed.scheme == "http"))
    ):
        raise ValueError("STUDIO_BASE_URL is outside the automation allowlist")
    return normalized


def _validated_source_url(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("automation source_url is invalid") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "x.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not _X_STATUS_PATH_PATTERN.fullmatch(parsed.path)
    ):
        raise ValueError("automation source_url must be a canonical X status URL")
    return normalized


class StudioGenerationClient:
    def __init__(
        self,
        *,
        base_url: str,
        automation_token: str,
        timeout_seconds: float = 75.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = _validated_base_url(base_url)
        self.automation_token = automation_token.strip()
        if len(self.automation_token) < 32 or len(self.automation_token) > 512:
            raise ValueError("STUDIO_AUTOMATION_TOKEN must contain 32 to 512 characters")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def generate(
        self,
        *,
        client_id: str,
        content_kind: str,
        request_id: str,
        source_content: str,
        source_url: str,
        source_image_url: str = "",
        template_style: str = "classic",
    ) -> GeneratedCatalogResult:
        if not _UUID_PATTERN.fullmatch(request_id):
            raise ValueError("automation request_id must be a UUID")
        if client_id not in {"yellow", "origintrail", "squid", "babylon"}:
            raise ValueError("unsupported automation client")
        source_content = source_content.strip()
        source_url = _validated_source_url(source_url)

        if content_kind == "daily_news":
            if len(source_content) < 10 or len(source_content) > 20_000:
                raise ValueError("daily news source_content must be 10 to 20000 characters")
            route = f"/api/news-card/{client_id}"
            payload = {
                "source_content": source_content,
                "source_type": "tweet",
                "source_url": source_url,
                "source_image_url": source_image_url if template_style == "remix" else "",
                "template_style": template_style,
                "mock_mode": False,
            }
        elif content_kind == "article":
            if len(source_content) < 300 or len(source_content) > 60_000:
                raise ValueError("article source_content must be 300 to 60000 characters")
            route = f"/api/article/{client_id}"
            payload = {
                "source_content": source_content,
                "source_type": "article",
                "source_url": source_url,
            }
        elif content_kind == "tutorial":
            if client_id not in {"yellow", "squid"}:
                raise ValueError("tutorial automation is available only for Yellow and Squid")
            if len(source_content) < 10 or len(source_content) > 20_000:
                raise ValueError("tutorial source_content must be 10 to 20000 characters")
            route = f"/api/tutorial/{client_id}"
            payload = {
                "source_content": source_content,
                "source_type": "article",
                "source_url": source_url,
                "mock_llm": False,
            }
        else:
            raise ValueError("unsupported automation content kind")

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}{route}",
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": request_id,
                        "X-Studio-Automation-Key": self.automation_token,
                    },
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GenerationRequestError("studio_generation_unavailable", retryable=True) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise GenerationRequestError(
                "studio_generation_invalid_response",
                retryable=response.status_code >= 500,
            ) from exc
        if not isinstance(body, Mapping):
            raise GenerationRequestError("studio_generation_invalid_response", retryable=False)
        if response.status_code < 200 or response.status_code >= 300:
            code = body.get("error")
            raise GenerationRequestError(
                code if isinstance(code, str) and _ERROR_CODE_PATTERN.fullmatch(code)
                else "studio_generation_failed",
                retryable=response.status_code in {429, 502, 503, 504},
            )

        content_item_id = body.get("content_item_id")
        content_version_id = body.get("content_version_id")
        raw_asset_ids = body.get("asset_ids", [])
        if (
            content_item_id != request_id.lower()
            or not isinstance(content_version_id, str)
            or not _UUID_PATTERN.fullmatch(content_version_id)
            or not isinstance(raw_asset_ids, list)
            or any(not isinstance(item, str) or not _UUID_PATTERN.fullmatch(item) for item in raw_asset_ids)
            or body.get("storage_backend") != "supabase"
        ):
            raise GenerationRequestError("studio_generation_invalid_response", retryable=False)
        if content_kind == "daily_news" and len(raw_asset_ids) != 1:
            raise GenerationRequestError("studio_generation_invalid_response", retryable=False)
        if content_kind == "article" and raw_asset_ids:
            raise GenerationRequestError("studio_generation_invalid_response", retryable=False)
        if content_kind == "tutorial" and not 1 <= len(raw_asset_ids) <= 12:
            raise GenerationRequestError("studio_generation_invalid_response", retryable=False)

        return GeneratedCatalogResult(
            content_item_id=content_item_id,
            content_version_id=content_version_id.lower(),
            asset_ids=tuple(item.lower() for item in raw_asset_ids),
            reused=body.get("reused") is True,
        )
