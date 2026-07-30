from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterable, Mapping
from urllib.parse import urlsplit

import httpx

from core.batch.models import BatchItemOutcome, BatchSnapshot, BatchWorkItem


_MAX_REQUESTS = 50_000
_MAX_FILE_BYTES = 200 * 1024 * 1024
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,199}$")
_SAFE_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_BATCH_STATUSES = frozenset({
    "validating",
    "failed",
    "in_progress",
    "finalizing",
    "completed",
    "expired",
    "cancelling",
    "cancelled",
})
_FILE_RETENTION_SECONDS = 7 * 24 * 60 * 60
_MAX_BATCH_LIST_PAGES = 100


class OpenAIBatchError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _validated_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("OPENAI_BASE_URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "api.openai.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OPENAI_BASE_URL is outside the Batch allowlist")
    return normalized


def _provider_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not _PROVIDER_ID_RE.fullmatch(value):
        raise OpenAIBatchError(f"openai_invalid_{name}", retryable=False)
    return value


def _safe_nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


class OpenAIBatchClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key.strip()
        if not 20 <= len(self.api_key) <= 512 or any(
            character.isspace() for character in self.api_key
        ):
            raise ValueError("OPENAI_API_KEY has an invalid format")
        self.base_url = _validated_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @staticmethod
    def build_jsonl(items: Iterable[BatchWorkItem]) -> bytes:
        materialized = tuple(items)
        if not 1 <= len(materialized) <= _MAX_REQUESTS:
            raise ValueError("Batch input must contain 1 to 50000 requests")
        custom_ids = [item.custom_id for item in materialized]
        if len(set(custom_ids)) != len(custom_ids):
            raise ValueError("Batch custom_id values must be unique")
        lines = [
            json.dumps(
                item.batch_request(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in materialized
        ]
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        if len(payload) > _MAX_FILE_BYTES:
            raise ValueError("Batch JSONL exceeds the 200 MB provider limit")
        return payload

    async def upload_input_file(
        self,
        *,
        payload: bytes,
        bundle_id: str,
    ) -> str:
        if not payload or len(payload) > _MAX_FILE_BYTES:
            raise ValueError("Batch JSONL payload size is invalid")
        if not _PROVIDER_ID_RE.fullmatch(bundle_id):
            raise ValueError("bundle_id is invalid")
        response = await self._request(
            "POST",
            "/v1/files",
            data={
                "purpose": "batch",
                "expires_after[anchor]": "created_at",
                "expires_after[seconds]": str(_FILE_RETENTION_SECONDS),
            },
            files={
                "file": (
                    f"coineasy-{bundle_id}.jsonl",
                    payload,
                    "application/jsonl",
                ),
            },
        )
        body = self._json_body(response, "file_upload")
        if body.get("purpose") != "batch":
            raise OpenAIBatchError("openai_invalid_file_upload", retryable=False)
        return _provider_id(body.get("id"), "file_id")

    async def create_batch(
        self,
        *,
        input_file_id: str,
        metadata: Mapping[str, str],
    ) -> BatchSnapshot:
        input_file_id = _provider_id(input_file_id, "file_id")
        normalized_metadata = self._metadata(metadata)
        response = await self._request(
            "POST",
            "/v1/batches",
            json={
                "input_file_id": input_file_id,
                "endpoint": "/v1/responses",
                "completion_window": "24h",
                "metadata": normalized_metadata,
                "output_expires_after": {
                    "anchor": "created_at",
                    "seconds": _FILE_RETENTION_SECONDS,
                },
            },
        )
        snapshot = self._snapshot(self._json_body(response, "batch_create"))
        if (
            snapshot.input_file_id != input_file_id
            or snapshot.metadata is None
            or any(
                snapshot.metadata.get(key) != value
                for key, value in normalized_metadata.items()
            )
        ):
            raise OpenAIBatchError(
                "openai_batch_identity_mismatch",
                retryable=False,
            )
        return snapshot

    async def retrieve_batch(self, provider_batch_id: str) -> BatchSnapshot:
        provider_batch_id = _provider_id(provider_batch_id, "batch_id")
        response = await self._request(
            "GET",
            f"/v1/batches/{provider_batch_id}",
        )
        return self._snapshot(self._json_body(response, "batch_retrieve"))

    async def find_recent_batch(
        self,
        *,
        dispatch_key: str,
        not_before: datetime,
    ) -> BatchSnapshot | None:
        if not re.fullmatch(r"[a-f0-9]{64}", dispatch_key):
            raise ValueError("dispatch_key is invalid")
        if not isinstance(not_before, datetime) or not_before.tzinfo is None:
            raise ValueError("not_before must be timezone-aware")
        cutoff = int(not_before.timestamp())
        after: str | None = None
        seen_cursors: set[str] = set()
        previous_created_at: int | None = None
        for _page in range(_MAX_BATCH_LIST_PAGES):
            params: dict[str, object] = {"limit": 100}
            if after is not None:
                params["after"] = after
            response = await self._request(
                "GET",
                "/v1/batches",
                params=params,
            )
            body = self._json_body(response, "batch_list")
            raw_batches = body.get("data")
            if not isinstance(raw_batches, list) or len(raw_batches) > 100:
                raise OpenAIBatchError("openai_invalid_batch_list", retryable=False)
            for raw in raw_batches:
                if not isinstance(raw, Mapping):
                    raise OpenAIBatchError(
                        "openai_invalid_batch_list",
                        retryable=False,
                    )
                created_at = raw.get("created_at")
                if (
                    not isinstance(created_at, int)
                    or created_at < 0
                    or (
                        previous_created_at is not None
                        and created_at > previous_created_at
                    )
                ):
                    raise OpenAIBatchError(
                        "openai_invalid_batch_list",
                        retryable=False,
                    )
                previous_created_at = created_at
                metadata = raw.get("metadata")
                if (
                    isinstance(metadata, Mapping)
                    and metadata.get("dispatch_key") == dispatch_key
                ):
                    return self._snapshot(raw)
                if created_at < cutoff:
                    return None
            has_more = body.get("has_more", False)
            if not isinstance(has_more, bool):
                raise OpenAIBatchError(
                    "openai_invalid_batch_list",
                    retryable=False,
                )
            if not has_more:
                return None
            raw_cursor = body.get("last_id")
            if raw_cursor is None and raw_batches:
                raw_cursor = raw_batches[-1].get("id")
            cursor = _provider_id(raw_cursor, "batch_list_cursor")
            if cursor in seen_cursors:
                raise OpenAIBatchError(
                    "openai_invalid_batch_list",
                    retryable=False,
                )
            seen_cursors.add(cursor)
            after = cursor
        raise OpenAIBatchError(
            "openai_batch_recovery_window_exceeded",
            retryable=True,
        )

    async def download_file(self, file_id: str) -> bytes:
        file_id = _provider_id(file_id, "file_id")
        response = await self._request("GET", f"/v1/files/{file_id}/content")
        if len(response.content) > _MAX_FILE_BYTES:
            raise OpenAIBatchError("openai_batch_output_too_large", retryable=False)
        return response.content

    @staticmethod
    def parse_result_file(payload: bytes) -> tuple[BatchItemOutcome, ...]:
        if not payload or len(payload) > _MAX_FILE_BYTES:
            raise OpenAIBatchError("openai_invalid_batch_output", retryable=False)
        outcomes: list[BatchItemOutcome] = []
        seen: set[str] = set()
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OpenAIBatchError(
                "openai_invalid_batch_output",
                retryable=False,
            ) from exc
        for raw_line in decoded.splitlines():
            if not raw_line.strip():
                continue
            try:
                line = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise OpenAIBatchError(
                    "openai_invalid_batch_output",
                    retryable=False,
                ) from exc
            if not isinstance(line, Mapping):
                raise OpenAIBatchError("openai_invalid_batch_output", retryable=False)
            custom_id = line.get("custom_id")
            if (
                not isinstance(custom_id, str)
                or custom_id in seen
                or not 10 <= len(custom_id) <= 255
            ):
                raise OpenAIBatchError("openai_invalid_batch_output", retryable=False)
            seen.add(custom_id)
            outcomes.append(OpenAIBatchClient._item_outcome(custom_id, line))
        if not outcomes or len(outcomes) > _MAX_REQUESTS:
            raise OpenAIBatchError("openai_invalid_batch_output", retryable=False)
        return tuple(outcomes)

    @staticmethod
    def _item_outcome(
        custom_id: str,
        line: Mapping[str, object],
    ) -> BatchItemOutcome:
        raw_response = line.get("response")
        if isinstance(raw_response, Mapping):
            status_code = raw_response.get("status_code")
            body = raw_response.get("body")
            if isinstance(status_code, int) and 200 <= status_code < 300:
                if not isinstance(body, Mapping):
                    return BatchItemOutcome(
                        custom_id=custom_id,
                        ok=False,
                        output=None,
                        error_code="openai_invalid_response_output",
                        cost_basis="reservation",
                    )
                billing = OpenAIBatchClient._billing(body)
                try:
                    output = OpenAIBatchClient._structured_output(body)
                except OpenAIBatchError as exc:
                    return BatchItemOutcome(
                        custom_id=custom_id,
                        ok=False,
                        output=None,
                        error_code=exc.code,
                        **billing,
                    )
                if billing["cost_basis"] != "usage":
                    model = body.get("model")
                    return BatchItemOutcome(
                        custom_id=custom_id,
                        ok=False,
                        output=None,
                        error_code=(
                            "openai_response_model_mismatch"
                            if model not in {"gpt-5.6-luna", "gpt-5.6-terra"}
                            else "openai_invalid_response_usage"
                        ),
                        **billing,
                    )
                return BatchItemOutcome(
                    custom_id=custom_id,
                    ok=True,
                    output=output,
                    error_code=None,
                    **billing,
                )
            if isinstance(status_code, int) and status_code in {408, 429, 500, 502, 503, 504}:
                code = "openai_batch_item_retryable"
            else:
                code = "openai_batch_item_rejected"
            return BatchItemOutcome(
                custom_id=custom_id,
                ok=False,
                output=None,
                error_code=code,
            )
        raw_error = line.get("error")
        provider_code = raw_error.get("code") if isinstance(raw_error, Mapping) else None
        safe_code = (
            provider_code
            if isinstance(provider_code, str) and _SAFE_ERROR_RE.fullmatch(provider_code)
            else "openai_batch_item_failed"
        )
        return BatchItemOutcome(
            custom_id=custom_id,
            ok=False,
            output=None,
            error_code=safe_code,
        )

    @staticmethod
    def _billing(body: Mapping[str, object]) -> dict[str, object]:
        model = body.get("model")
        usage = body.get("usage")
        input_details = (
            usage.get("input_tokens_details")
            if isinstance(usage, Mapping)
            else None
        )
        input_tokens = usage.get("input_tokens") if isinstance(usage, Mapping) else None
        output_tokens = (
            usage.get("output_tokens") if isinstance(usage, Mapping) else None
        )
        cached_tokens = (
            input_details.get("cached_tokens")
            if isinstance(input_details, Mapping)
            else None
        )
        if (
            model not in {"gpt-5.6-luna", "gpt-5.6-terra"}
            or not isinstance(input_tokens, int)
            or input_tokens <= 0
            or not isinstance(output_tokens, int)
            or output_tokens < 0
            or not isinstance(cached_tokens, int)
            or cached_tokens < 0
            or cached_tokens > input_tokens
        ):
            return {
                "model": None,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "cost_basis": "reservation",
            }
        return {
            "model": model,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "cost_basis": "usage",
        }

    @staticmethod
    def _structured_output(body: Mapping[str, object]) -> Mapping[str, object]:
        if body.get("status") != "completed":
            raise OpenAIBatchError("openai_response_incomplete", retryable=False)
        output = body.get("output")
        if not isinstance(output, list) or len(output) > 64:
            raise OpenAIBatchError("openai_invalid_response_output", retryable=False)
        texts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list) or len(content) > 64:
                continue
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") == "refusal":
                    raise OpenAIBatchError("openai_response_refused", retryable=False)
                text = part.get("text")
                if part.get("type") == "output_text" and isinstance(text, str):
                    texts.append(text)
        if len(texts) != 1:
            raise OpenAIBatchError("openai_invalid_response_output", retryable=False)
        try:
            parsed = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise OpenAIBatchError(
                "openai_invalid_structured_output",
                retryable=False,
            ) from exc
        if not isinstance(parsed, Mapping):
            raise OpenAIBatchError("openai_invalid_structured_output", retryable=False)
        return dict(parsed)

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    **kwargs,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise OpenAIBatchError("openai_batch_unavailable", retryable=True) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise OpenAIBatchError(
                "openai_batch_request_failed",
                retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
            )
        return response

    @staticmethod
    def _json_body(response: httpx.Response, operation: str) -> Mapping[str, object]:
        try:
            body = response.json()
        except ValueError as exc:
            raise OpenAIBatchError(
                f"openai_invalid_{operation}_response",
                retryable=False,
            ) from exc
        if not isinstance(body, Mapping):
            raise OpenAIBatchError(
                f"openai_invalid_{operation}_response",
                retryable=False,
            )
        return body

    @staticmethod
    def _metadata(value: Mapping[str, str]) -> dict[str, str]:
        if not 1 <= len(value) <= 16:
            raise ValueError("Batch metadata must contain 1 to 16 items")
        result: dict[str, str] = {}
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not isinstance(item, str)
                or not 1 <= len(key) <= 64
                or not 1 <= len(item) <= 512
            ):
                raise ValueError("Batch metadata is invalid")
            result[key] = item
        return result

    @staticmethod
    def _snapshot(body: Mapping[str, object]) -> BatchSnapshot:
        status = body.get("status")
        if status not in _BATCH_STATUSES:
            raise OpenAIBatchError("openai_invalid_batch_response", retryable=False)
        counts = body.get("request_counts")
        counts = counts if isinstance(counts, Mapping) else {}
        usage = body.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        metadata = body.get("metadata")
        if metadata is not None and (
            not isinstance(metadata, Mapping)
            or any(not isinstance(key, str) or not isinstance(value, str)
                    for key, value in metadata.items())
        ):
            raise OpenAIBatchError("openai_invalid_batch_response", retryable=False)
        try:
            return BatchSnapshot(
                provider_batch_id=_provider_id(body.get("id"), "batch_id"),
                input_file_id=_provider_id(body.get("input_file_id"), "file_id"),
                status=status,
                output_file_id=(
                    _provider_id(body.get("output_file_id"), "file_id")
                    if body.get("output_file_id") is not None
                    else None
                ),
                error_file_id=(
                    _provider_id(body.get("error_file_id"), "file_id")
                    if body.get("error_file_id") is not None
                    else None
                ),
                request_total=_safe_nonnegative_int(counts.get("total")),
                request_completed=_safe_nonnegative_int(counts.get("completed")),
                request_failed=_safe_nonnegative_int(counts.get("failed")),
                input_tokens=_safe_nonnegative_int(usage.get("input_tokens")),
                output_tokens=_safe_nonnegative_int(usage.get("output_tokens")),
                metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            )
        except ValueError as exc:
            raise OpenAIBatchError(
                "openai_invalid_batch_response",
                retryable=False,
            ) from exc
