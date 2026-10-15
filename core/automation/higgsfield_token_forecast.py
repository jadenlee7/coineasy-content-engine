"""Offline, explicit token-allocation forecast; NOT a provider quote or cap.

The public model rates are evidence. Token allocations and the 50% margin are
our proposed budgeting assumptions, not measured usage or provider maxima.
Nothing here reads credentials, provisions a ledger, or performs network I/O.
The production environment factory deliberately does not mount this policy.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from core.automation.higgsfield_banner import MODEL, QualityBannerError, generation_payload
from core.content_ops.image_budget import TOKEN_RATE_FORECAST_POLICY, TokenRateForecastProof

RATE_SOURCE_URL = "https://open.higgsfield.ai/models/marketing-studio/image/sunburst/api-reference"
RATE_OBSERVED_AT = datetime(2026, 10, 15, 9, 15, 36, 269000, tzinfo=timezone.utc)
RATE_DOCUMENT_SHA256 = "3ce095632b8178cc4edeee0a4fc3fe76b90e3f741e5d305bb9e229a3bd271869"
RATE_REVIEW_DUE = RATE_OBSERVED_AT + timedelta(days=30)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _require(condition):
    if not condition:
        raise QualityBannerError("quality_banner_token_forecast_invalid")


def policy_record():
    # Integer USD per million tokens equals integer USD micros per token.
    # Never apply the documented cache discounts without actual usage proof.
    return {
        "contract": TOKEN_RATE_FORECAST_POLICY, "model": MODEL,
        "settings": {"quality": "max", "resolution": "4k", "aspect_ratio": "1:1",
                     "enhance_prompt": False, "moderation": "auto"},
        "rates_usd_per_million": {"text_input": 5, "image_input": 8,
                                  "text_output": 10, "image_output": 30},
        "rate_source_url": RATE_SOURCE_URL, "rate_document_sha256": RATE_DOCUMENT_SHA256,
        "rate_observed_at": RATE_OBSERVED_AT.isoformat(), "rate_review_due": RATE_REVIEW_DUE.isoformat(),
        "assumed_tokens": {"text_input": 16384, "image_input_per_reference": 16384,
                           "text_output": 8192, "image_output": 65536},
        "maximum_prompt_utf8_bytes": 16384, "maximum_references": 2,
        "margin_numerator": 3, "margin_denominator": 2,
        "cache_discount_assumed": False, "provider_enforced_maximum": False,
        "actual_settlement_variance_accepted": True,
        "daily_limit_usd_micros": 10_000_000, "timezone": "Asia/Seoul",
        "clients": ["babylon", "origintrail", "yellow"],
        "maximum_initial_requests_per_client_per_day": 1,
    }


FORECAST_POLICY_SHA256 = _sha(_json(policy_record()))


def _calculation(reference_count, prompt_utf8_bytes):
    _require(type(reference_count) is int and reference_count in (1, 2)
             and type(prompt_utf8_bytes) is int and 0 < prompt_utf8_bytes <= 16384)
    policy = policy_record()
    assumptions, rates = policy["assumed_tokens"], policy["rates_usd_per_million"]
    tokens = {"text_input": assumptions["text_input"],
              "image_input": assumptions["image_input_per_reference"] * reference_count,
              "text_output": assumptions["text_output"], "image_output": assumptions["image_output"]}
    components = {key: count * rates[key] for key, count in tokens.items()}
    # Exact integer arithmetic, rounding UP to the next micro-dollar.
    amount = (sum(components.values()) * policy["margin_numerator"]
              + policy["margin_denominator"] - 1) // policy["margin_denominator"]
    return {"reference_count": reference_count, "prompt_utf8_bytes": prompt_utf8_bytes,
            "allocated_tokens": tokens, "component_usd_micros": components,
            "estimated_usd_micros": amount}


def _usd(micros):
    return f"{micros // 1_000_000}.{micros % 1_000_000:06d}"


def forecast_proof(request, *, provider_account_sha256, now):
    """Create a trusted local forecast from an already validated exact request.

    The owner still validates source freshness, immutable reference bytes and
    permission before a paid POST. This calculator cannot authorize a submit.
    No default/fallback invocation from the authenticated USD estimate path.
    """
    import re
    _require(type(now) is datetime and now.utcoffset() is not None
             and RATE_OBSERVED_AT <= now < RATE_REVIEW_DUE
             and type(provider_account_sha256) is str
             and re.fullmatch(r"[a-f0-9]{64}", provider_account_sha256) is not None)
    # Run the normal structural/source validation without relaxing URL syntax.
    # Network allowlisting and byte hashes remain the adapter's responsibility.
    from urllib.parse import urlsplit
    request.validate(now=now, reference_hosts=frozenset(urlsplit(ref.url).hostname for ref in request.references))
    payload = generation_payload(request)
    policy = policy_record()
    _require(_sha(_json(policy)) == FORECAST_POLICY_SHA256
             and {key: payload.get(key) for key in policy["settings"]} == policy["settings"]
             and set(payload) == set(policy["settings"]) | {"prompt", "image_urls"})
    calculation = _calculation(len(request.references), len(payload["prompt"].encode("utf-8")))
    issued = now.astimezone(timezone.utc)
    expires = min(issued + timedelta(minutes=5), RATE_REVIEW_DUE)
    record = {"policy": policy, "policy_sha256": FORECAST_POLICY_SHA256,
              "request_sha256": request.digest(), "payload_sha256": _sha(_json(payload)),
              "provider_account_sha256": provider_account_sha256,
              "estimated_at": issued.isoformat(), "expires_at": expires.isoformat(),
              **calculation}
    encoded = _json(record)
    return TokenRateForecastProof(request_sha256=request.digest(),
        provider_account_sha256=provider_account_sha256,
        estimated_usd=_usd(calculation["estimated_usd_micros"]),
        estimated_at=issued, expires_at=expires, evidence_sha256=_sha(encoded),
        policy_sha256=FORECAST_POLICY_SHA256, forecast_json=encoded)


def validate_forecast_proof(proof):
    """Check internally consistent sanitized evidence, not provider authenticity.

    Proof objects are trusted adapter outputs, never caller JSON. Digests bind
    evidence but do not turn an operator forecast into a provider quote.
    """
    import re
    try:
        _require(type(proof) is TokenRateForecastProof and proof.kind == "local_token_rate_forecast"
                 and proof.policy_sha256 == FORECAST_POLICY_SHA256
                 and _sha(_json(policy_record())) == FORECAST_POLICY_SHA256
                 and type(proof.forecast_json) is str and len(proof.forecast_json) <= 8192
                 and _sha(proof.forecast_json) == proof.evidence_sha256)
        value = json.loads(proof.forecast_json)
        _require(type(value) is dict and _json(value) == proof.forecast_json)
        expected = {"policy": policy_record(), "policy_sha256": FORECAST_POLICY_SHA256,
                    "request_sha256": proof.request_sha256, "payload_sha256": value["payload_sha256"],
                    "provider_account_sha256": proof.provider_account_sha256,
                    "estimated_at": proof.estimated_at.isoformat(), "expires_at": proof.expires_at.isoformat(),
                    **_calculation(value["reference_count"], value["prompt_utf8_bytes"])}
        _require(_json(value) == _json(expected) and re.fullmatch(r"[a-f0-9]{64}", value["payload_sha256"]) is not None
                 and type(proof.estimated_usd) is str
                 and proof.estimated_usd == _usd(expected["estimated_usd_micros"])
                 and RATE_OBSERVED_AT <= proof.estimated_at < proof.expires_at <= RATE_REVIEW_DUE)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise QualityBannerError("quality_banner_token_forecast_invalid") from None
