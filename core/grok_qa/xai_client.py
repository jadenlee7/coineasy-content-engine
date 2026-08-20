from __future__ import annotations

import base64
import copy
import json
import re
from datetime import timedelta
from typing import Mapping, Optional, Sequence

import httpx
from pydantic import ValidationError

from core.grok_qa.models import (
    GROK_QA_VERDICT_JSON_SCHEMA,
    OFFICIAL_X_HANDLES,
    GrokQaModelResult,
    GrokQaVerdict,
    GrokQaWorkClaim,
    provider_x_citation_matches,
)


XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
PROMPT_VERSION = "official-x-grok-qa@1"
_MAX_PROVIDER_RESPONSE_BYTES = 512 * 1024
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$")
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_X_SEARCH_TOOL_NAMES = frozenset({
    "x_user_search",
    "x_keyword_search",
    "x_semantic_search",
    "x_thread_fetch",
})


class XaiQaError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _instructions(claim: GrokQaWorkClaim) -> str:
    return (
        "You must use X Search to retrieve the exact official post before "
        "returning the required advisory QA JSON."
    )


def _prompt_text(claim: GrokQaWorkClaim) -> str:
    payload = json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "client_id": claim.client_id,
            "content_kind": claim.content_kind,
            "title": claim.title,
            "official_source_url": claim.source_url,
            "source_published_at": claim.source_published_at.isoformat(),
            "review_package": claim.review_text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"Use X Search now to retrieve and cite exactly {claim.source_url}. "
        "Only after that call completes, review the following QA_INPUT_JSON and "
        "return the required verdict. Fact-check the copy, assess the supplied "
        "banner's brand fidelity, legibility, spacing, and Korean GTM clarity, "
        "and never invent facts. If generated_content.banner_provenance.mode is "
        "verified_official_source_remix, treat the banner's existing typography, "
        "abbreviations, blur, and graphic effects as preserved official source "
        "creative, not generated claims or defects; review generated channel copy "
        "and localized overlays only. Review rendered/public fields only, and do "
        "not treat omitted non-rendered metadata as a public claim. For every other "
        "provenance mode, review the full generated banner normally. This is "
        "advisory only and never publishes.\n"
        f"QA_INPUT_JSON={payload}"
    )


def _verdict_schema(claim: GrokQaWorkClaim) -> dict[str, object]:
    schema = copy.deepcopy(GROK_QA_VERDICT_JSON_SCHEMA)
    properties = schema["properties"]
    fact_check = properties["fact_check"]
    fact_check["properties"]["source_urls"] = {
        "type": "array",
        "minItems": 1,
        "maxItems": 1,
        "items": {
            "type": "string",
            "const": claim.source_url,
        },
    }
    issues = properties["issues"]
    issues["items"]["properties"]["evidence_url"] = {
        "type": "string",
        "const": claim.source_url,
    }
    return schema


def _citation_urls(body: Mapping[str, object]) -> tuple[str, ...]:
    urls: list[str] = []
    raw = body.get("citations")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        urls.extend(value for value in raw if isinstance(value, str))
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                annotations = part.get("annotations")
                if not isinstance(annotations, list):
                    continue
                for annotation in annotations:
                    if (
                        isinstance(annotation, Mapping)
                        and annotation.get("type") == "url_citation"
                        and isinstance(annotation.get("url"), str)
                    ):
                        urls.append(annotation["url"])
    return tuple(dict.fromkeys(urls))


def _x_search_call_count(body: Mapping[str, object]) -> int:
    output = body.get("output")
    documented_calls = sum(
        1
        for item in output
        if isinstance(item, Mapping) and item.get("type") == "x_search_call"
    ) if isinstance(output, list) else 0
    completed_custom_calls = sum(
        1
        for item in output
        if isinstance(item, Mapping)
        and item.get("type") == "custom_tool_call"
        and item.get("name") in _X_SEARCH_TOOL_NAMES
        and item.get("status") == "completed"
    ) if isinstance(output, list) else 0
    # The Responses API documents ``x_search_call`` output items, but its
    # agentic runtime can currently return the same server-side execution as a
    # completed, provider-owned ``custom_tool_call`` (for example
    # ``x_thread_fetch``). Prefer the documented representation if both are
    # present so one provider call cannot be double-counted.
    output_calls = documented_calls or completed_custom_calls
    if output_calls < 1:
        return 0
    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return 0
    numeric_usage = usage.get("num_server_side_tools_used")
    numeric_proof = (
        isinstance(numeric_usage, int)
        and not isinstance(numeric_usage, bool)
        and numeric_usage >= output_calls
    )
    named_usage = usage.get("server_side_tool_usage")
    named_proof = False
    if isinstance(named_usage, Mapping):
        named_proof = sum(
            value
            for key, value in named_usage.items()
            if isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 1
            and "x_search" in str(key).lower()
        ) >= output_calls
    elif isinstance(named_usage, list):
        named_proof = sum(
            1 for value in named_usage if "x_search" in str(value).lower()
        ) >= output_calls
    return output_calls if numeric_proof or named_proof else 0


def _failed_x_search_call_count(body: Mapping[str, object]) -> int:
    output = body.get("output")
    if not isinstance(output, list):
        return 0
    return sum(
        1
        for item in output
        if isinstance(item, Mapping)
        and item.get("type") == "custom_tool_call"
        and item.get("name") in _X_SEARCH_TOOL_NAMES
        and item.get("status") == "failed"
    )


class XaiQaClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "grok-4.5",
        timeout_seconds: float = 180.0,
        max_turns: int = 3,
        x_search_window_days: int = 1,
        max_output_tokens: int = 1_600,
        max_cost_in_usd_ticks: int = 500_000_000,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        normalized_key = api_key.strip()
        if (
            not re.fullmatch(r"xai-[A-Za-z0-9_-]{16,508}", normalized_key)
            or model != "grok-4.5"
            or not 30 <= timeout_seconds <= 300
            or not 1 <= max_turns <= 3
            or not 0 <= x_search_window_days <= 3
            or not 256 <= max_output_tokens <= 4_000
            or not 1 <= max_cost_in_usd_ticks <= 5_000_000_000
        ):
            raise ValueError("invalid xAI QA client configuration")
        self.api_key = normalized_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns
        self.x_search_window_days = x_search_window_days
        self.max_output_tokens = max_output_tokens
        self.max_cost_in_usd_ticks = max_cost_in_usd_ticks
        self.transport = transport

    def request_body(self, claim: GrokQaWorkClaim) -> dict[str, object]:
        published_date = claim.source_published_at.date()
        from_date = published_date - timedelta(days=self.x_search_window_days)
        to_date = published_date + timedelta(days=self.x_search_window_days)
        assert claim.image_png is not None
        image_data_url = "data:image/png;base64," + base64.b64encode(
            claim.image_png
        ).decode("ascii")
        return {
            "model": self.model,
            "instructions": _instructions(claim),
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _prompt_text(claim),
                        },
                        {
                            "type": "input_image",
                            "image_url": image_data_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            "tools": [
                {
                    "type": "x_search",
                    "allowed_x_handles": [OFFICIAL_X_HANDLES[claim.client_id]],
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "enable_image_understanding": True,
                }
            ],
            "tool_choice": "required",
            "include": ["no_inline_citations"],
            "max_turns": self.max_turns,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "coineasy_grok_qa_verdict",
                    "schema": _verdict_schema(claim),
                    "strict": True,
                }
            },
        }

    async def review(self, claim: GrokQaWorkClaim) -> GrokQaModelResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    XAI_RESPONSES_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self.request_body(claim),
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise XaiQaError("xai_qa_unavailable", retryable=True) from exc

        if not 200 <= response.status_code < 300:
            raise XaiQaError(
                "xai_qa_request_failed",
                retryable=response.status_code in _RETRYABLE_STATUS_CODES,
            )
        if len(response.content) > _MAX_PROVIDER_RESPONSE_BYTES:
            raise XaiQaError("xai_qa_response_too_large", retryable=False)
        try:
            body = response.json()
        except ValueError as exc:
            raise XaiQaError("xai_qa_invalid_response", retryable=False) from exc
        if not isinstance(body, Mapping):
            raise XaiQaError("xai_qa_invalid_response", retryable=False)
        return self._result(body, claim)

    def _result(
        self,
        body: Mapping[str, object],
        claim: GrokQaWorkClaim,
    ) -> GrokQaModelResult:
        if body.get("status") != "completed":
            raise XaiQaError("xai_qa_response_incomplete", retryable=False)
        if body.get("model") != self.model:
            raise XaiQaError("xai_qa_model_mismatch", retryable=False)
        response_id = body.get("id")
        if (
            not isinstance(response_id, str)
            or not _PROVIDER_ID_RE.fullmatch(response_id)
        ):
            raise XaiQaError("xai_qa_response_id_invalid", retryable=False)
        x_search_calls = _x_search_call_count(body)
        if x_search_calls < 1:
            if _failed_x_search_call_count(body) > 0:
                raise XaiQaError("xai_qa_x_search_failed", retryable=False)
            raise XaiQaError("xai_qa_x_search_missing", retryable=False)
        if x_search_calls > self.max_turns:
            raise XaiQaError("xai_qa_x_search_limit_exceeded", retryable=False)
        citations = _citation_urls(body)
        if not citations:
            raise XaiQaError("xai_qa_exact_source_not_cited", retryable=False)
        if any(
            not provider_x_citation_matches(
                claim.client_id,
                claim.source_url,
                value,
            )
            for value in citations
        ):
            raise XaiQaError(
                "xai_qa_citation_outside_source_boundary",
                retryable=False,
            )

        usage = body.get("usage")
        cost = usage.get("cost_in_usd_ticks") if isinstance(usage, Mapping) else None
        if (
            not isinstance(cost, int)
            or isinstance(cost, bool)
            or cost < 0
        ):
            raise XaiQaError("xai_qa_cost_invalid", retryable=False)
        if cost > self.max_cost_in_usd_ticks:
            raise XaiQaError("xai_qa_cost_cap_exceeded", retryable=False)

        output = body.get("output")
        if not isinstance(output, list) or len(output) > 64:
            raise XaiQaError("xai_qa_invalid_output", retryable=False)
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
                    raise XaiQaError("xai_qa_response_refused", retryable=False)
                if part.get("type") == "output_text" and isinstance(
                    part.get("text"), str
                ):
                    texts.append(part["text"])
        if len(texts) != 1 or len(texts[0].encode("utf-8")) > 64 * 1024:
            raise XaiQaError("xai_qa_invalid_output", retryable=False)
        try:
            verdict = GrokQaVerdict.model_validate_json(texts[0])
            verdict.validate_source_boundary(claim.source_url)
            return GrokQaModelResult(
                provider_response_id=response_id,
                model=self.model,
                cost_in_usd_ticks=cost,
                input_sha256=claim.input_sha256,
                x_search_performed=True,
                x_search_citations=list(citations[:8]),
                x_search_calls=x_search_calls,
                verdict=verdict,
            )
        except (ValidationError, ValueError) as exc:
            raise XaiQaError("xai_qa_invalid_verdict", retryable=False) from exc


__all__ = [
    "PROMPT_VERSION",
    "XAI_RESPONSES_URL",
    "XaiQaClient",
    "XaiQaError",
]
