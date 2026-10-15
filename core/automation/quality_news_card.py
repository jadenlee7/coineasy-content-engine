"""Opt-in natural news generation: persisted copy -> max-quality PNG.

Only the authenticated gateway's DB-resolved natural context is accepted. This
module cannot create a job, source link, approval, review card or publication.
Its result returns through the existing catalog/complete_job/private-card owners.
All replicas must share ONE private persistent journal volume. Configuration is
not spending authority; an operator must approve the rollout and spend policy.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from PIL import Image

from core.automation.higgsfield_banner import (
    BannerReference, CLIENT_HANDLES, HiggsfieldQualityBanner, MAX_BYTES, POLICY,
    QualityBannerError, QualityBannerRequest, _public_url,
    _reference_bytes, _copy as _validate_copy, _PRIVATE,
)
from core.content_ops.higgsfield_banner_journal import HiggsfieldBannerJournal
from core.content_ops.image_budget import (ESTIMATE_POLICY, STRICT_POLICY, TOKEN_RATE_FORECAST_POLICY,
    ImageBudgetError, ProviderEstimateProof, TokenRateForecastProof,
    budget_snapshot, provision_image_budget, usd_to_micros)

CONTEXT_KEYS = frozenset({"job_id", "request_id", "workspace_id", "client_id",
    "source_item_id", "source_published_at", "source_sha256", "source_content",
    "source_url", "source_image_url"})
_PROMPT_PRIVATE = re.compile(
    r"bearer\s+[A-Za-z0-9._~+/-]{12,}=*|"
    r"(?:cookie|set-cookie|bot[_ -]?token|chat[_ -]?id)\s*[:=]|"
    r"[0-9]{6,}:[A-Za-z0-9_-]{20,}", re.I)


def _require(value, code="quality_news_invalid"):
    if not value:
        raise QualityBannerError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


ESTIMATE_SPEND_POLICY_SHA256 = _sha(_json({
    "contract": "daily-quality-estimate-budget@1", "budget_mode": ESTIMATE_POLICY,
    "currency": "USD", "daily_limit_micros": 10_000_000, "timezone": "Asia/Seoul",
    "clients": sorted(CLIENT_HANDLES), "maximum_initial_requests_per_client_per_day": 1,
    "actual_settlement_variance_accepted": True,
}).encode())


class AuthenticatedEstimateAuthority:
    """Ephemeral authenticated estimates; durable admission lives in the DB.

    This object is never populated from caller input or environment estimates.
    A restart can re-estimate unsubmitted work, but cannot reopen a paid intent.
    """
    policy_mode = ESTIMATE_POLICY

    def __init__(self, account_sha256):
        _require(type(account_sha256) is str and re.fullmatch(r"[a-f0-9]{64}", account_sha256),
                 "quality_news_configuration_invalid")
        self.account_sha256, self._proofs = account_sha256, {}

    def remember(self, operation_id, proof):
        _require(type(proof) is ProviderEstimateProof
                 and proof.provider_account_sha256 == self.account_sha256,
                 "quality_news_budget_evidence_required")
        self._proofs[operation_id] = proof

    def proof_for(self, operation_id, request_sha256):
        proof = self._proofs.get(operation_id)
        _require(type(proof) is ProviderEstimateProof and proof.request_sha256 == request_sha256,
                 "quality_news_budget_evidence_required")
        return proof


class TokenRateForecastAuthority:
    """Explicit local forecast policy; selected only by exact factory mode/hash.

    No automatic fallback from malformed provider USD quotes. The factory
    requires an already transitioned same-store ledger; it never migrates one.
    """
    policy_mode = TOKEN_RATE_FORECAST_POLICY

    def __init__(self, account_sha256):
        _require(type(account_sha256) is str and re.fullmatch(r"[a-f0-9]{64}", account_sha256),
                 "quality_news_configuration_invalid")
        self.account_sha256, self._proofs = account_sha256, {}

    def forecast(self, request, *, now):
        from core.automation.higgsfield_token_forecast import forecast_proof
        return forecast_proof(request, provider_account_sha256=self.account_sha256, now=now)

    def remember(self, operation_id, proof):
        from core.automation.higgsfield_token_forecast import validate_forecast_proof
        validate_forecast_proof(proof)
        _require(proof.provider_account_sha256 == self.account_sha256,
                 "quality_news_budget_evidence_required")
        self._proofs[operation_id] = proof

    def proof_for(self, operation_id, request_sha256):
        proof = self._proofs.get(operation_id)
        _require(type(proof) is TokenRateForecastProof and proof.request_sha256 == request_sha256,
                 "quality_news_budget_evidence_required")
        return proof


def _budget_call(call, **kwargs):
    try:
        return call(**kwargs)
    except ImageBudgetError as error:
        code = {"image_budget_daily_limit": "quality_news_daily_budget_limit",
                "image_budget_binding_conflict": "quality_news_binding_conflict"}.get(
                    str(error), "quality_news_budget_evidence_required")
        raise QualityBannerError(code) from None


def _validate_prompt_input(payload):
    """Reject, never redact, private markers before reserving paid copy.

    Source and nested style/guidance fields are untrusted prompt material.
    Inspect keys as well as values so structured credentials cannot bypass the
    text guard. Bounded traversal also rejects malformed non-JSON input safely.
    """
    pending = [(payload.get(field), 0) for field in (
        "source_content", "source_url", "style_references", "brand_review_guidance")]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        _require(depth <= 16 and visited <= 10_000)
        if type(value) is dict:
            for key, child in value.items():
                _require(type(key) is str)
                pending.extend(((key + "=", depth + 1), (child, depth + 1)))
        elif type(value) in (list, tuple):
            pending.extend((child, depth + 1) for child in value)
        else:
            _require(value is None or type(value) in (str, int, float, bool))
            text = str(value)
            _require(not _PRIVATE.search(text) and not _PROMPT_PRIVATE.search(text))


def validate_context(client_id, request_id, payload):
    context = payload.get("quality_context")
    _require(type(context) is dict and set(context) == CONTEXT_KEYS)
    _require(client_id in CLIENT_HANDLES and context["client_id"] == client_id
             and context["request_id"] == request_id)
    for field in ("job_id", "request_id", "workspace_id", "source_item_id"):
        value = context[field]
        try:
            _require(type(value) is str and str(UUID(value)) == value and UUID(value).int != 0)
        except (ValueError, TypeError, AttributeError):
            raise QualityBannerError("quality_news_invalid") from None
    _require(payload.get("mock_mode") is False and payload.get("source_type") == "tweet"
             and payload.get("template_style") == "remix")
    for field in ("source_content", "source_url", "source_image_url"):
        _require(type(context[field]) is str and payload.get(field) == context[field])
    _require(10 <= len(context["source_content"]) <= 20_000
             and context["source_sha256"] == _sha(context["source_content"].encode()))
    _require(re.fullmatch(r"https://x\.com/" + CLIENT_HANDLES[client_id]
                         + r"/status/[0-9]{1,19}", context["source_url"]) is not None)
    return context


class QualityNewsJournal(HiggsfieldBannerJournal):
    """The same private DB owns one immutable copy checkpoint per natural job.

    A crash in the paid copy stage remains unknown; it is not restarted. Once
    checkpointed, new processes may only reuse copy and resume the image's
    recorded request ID. Neither a changed UUID nor a new day can bypass source
    uniqueness. The image submission has a separate, committed one-shot barrier.
    """
    def __init__(self, *args, budget_authority=None, **kwargs):
        self.budget_authority = budget_authority
        super().__init__(*args, **kwargs)
        with self._db() as db:
            if kwargs.get("require_existing"):
                _require(db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='quality_news_copy'").fetchone() is not None,
                    "quality_news_store_unavailable")
                return
            db.execute("""CREATE TABLE IF NOT EXISTS quality_news_copy (
                operation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, client_id TEXT NOT NULL,
                source_item_id TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                kst_day TEXT NOT NULL,
                checkpoint TEXT, checkpoint_sha256 TEXT,
                UNIQUE(workspace_id,client_id,source_item_id))""")

    def _reserve_image_budget(self, db, operation_id, request_sha256, now):
        # Exact trusted authorities remain distinct: quote, local forecast, cap.
        _require(self.budget_authority is not None,
                 "quality_news_budget_evidence_required")
        from core.content_ops.image_budget import reserve_image_budget
        proof = self.budget_authority.proof_for(operation_id, request_sha256)
        if type(proof) is TokenRateForecastProof:
            evidence = {"forecast_proof": proof}
        elif type(proof) is ProviderEstimateProof:
            evidence = {"estimate_proof": proof}
        else:
            evidence = {"ceiling_proof": proof}
        return _budget_call(reserve_image_budget, db=db, operation_id=operation_id,
            request_sha256=request_sha256, workspace_id=self.workspace_id,
            client_id=self.client_id, source_item_id=self.source_item_id,
            provider_account_sha256=self.budget_authority.account_sha256,
            now=datetime.fromtimestamp(now, timezone.utc), **evidence)

    def _bind_image_budget_request(self, db, operation_id, request_sha256, request_id):
        _require(self.budget_authority is not None,
                 "quality_news_budget_evidence_required")
        from core.content_ops.image_budget import bind_image_budget_request
        _budget_call(bind_image_budget_request, db=db, operation_id=operation_id, request_sha256=request_sha256,
            workspace_id=self.workspace_id, client_id=self.client_id,
            source_item_id=self.source_item_id,
            provider_account_sha256=self.budget_authority.account_sha256,
            provider_request_id=request_id)

    def _mark_image_budget_completed(self, db, operation_id, request_sha256, request_id, png_sha256):
        _require(self.budget_authority is not None, "quality_news_budget_evidence_required")
        from core.content_ops.image_budget import mark_image_budget_completed
        _budget_call(mark_image_budget_completed, db=db, operation_id=operation_id,
            request_sha256=request_sha256, workspace_id=self.workspace_id,
            client_id=self.client_id, source_item_id=self.source_item_id,
            provider_account_sha256=self.budget_authority.account_sha256,
            provider_request_id=request_id, png_sha256=png_sha256,
            now=datetime.fromtimestamp(self._clock(), timezone.utc))

    def start_or_read(self, context, input_sha, *, admit_new):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM quality_news_copy WHERE operation_id=? OR job_id=? "
                "OR (workspace_id=? AND client_id=? AND source_item_id=?)",
                (context["request_id"], context["job_id"], self.workspace_id,
                 self.client_id, self.source_item_id)).fetchall()
            if row:
                _require(len(row) == 1, "quality_news_binding_conflict")
                row = row[0]
                _require(row["operation_id"] == context["request_id"]
                         and row["job_id"] == context["job_id"]
                         and row["input_sha256"] == input_sha
                         and row["workspace_id"] == self.workspace_id
                         and row["client_id"] == self.client_id
                         and row["source_item_id"] == self.source_item_id,
                         "quality_news_binding_conflict")
                _require(row["checkpoint"] is not None, "quality_news_copy_unknown")
                _require(_sha(row["checkpoint"].encode()) == row["checkpoint_sha256"],
                         "quality_news_binding_conflict")
                return json.loads(row["checkpoint"])
            # Existing checkpoints may be reconciled after source expiry; a
            # new paid-copy intent still requires fresh source admission.
            _require(callable(admit_new))
            admit_new()
            day = datetime.fromtimestamp(self._clock(), ZoneInfo("Asia/Seoul")).date().isoformat()
            counts = db.execute("SELECT count(*),coalesce(sum(client_id=?),0) "
                "FROM quality_news_copy WHERE workspace_id=? AND kst_day=?",
                (self.client_id, self.workspace_id, day)).fetchone()
            _require(counts[0] < self.daily_limit and counts[1] < 1, "quality_news_daily_limit")
            db.execute("INSERT INTO quality_news_copy VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                (context["request_id"], context["job_id"], self.workspace_id,
                 self.client_id, self.source_item_id, input_sha, day))
            return None

    def save_checkpoint(self, context, input_sha, checkpoint):
        encoded = _json(checkpoint)
        _require(len(encoded.encode()) <= 128_000)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("UPDATE quality_news_copy SET checkpoint=?,checkpoint_sha256=? "
                "WHERE operation_id=? AND job_id=? AND workspace_id=? AND client_id=? "
                "AND source_item_id=? AND input_sha256=? AND checkpoint IS NULL",
                (encoded, _sha(encoded.encode()), context["request_id"], context["job_id"],
                 self.workspace_id, self.client_id, self.source_item_id, input_sha)).rowcount
            _require(count == 1, "quality_news_binding_conflict")


async def _copy(payload, client_id, *, before_effect=None):
    _validate_prompt_input(payload)
    # Import lazily: OFF validation does not construct an LLM client.
    from core.llm.news_card_pipeline import generate_news_card_spec
    return await asyncio.to_thread(generate_news_card_spec, client_id=client_id,
        source_content=payload["source_content"], source_type="tweet",
        source_url=payload["source_url"], mock_mode=False,
        style_references=payload.get("style_references", ()),
        brand_review_guidance=payload.get("brand_review_guidance"), single_attempt=True,
        before_external_call=before_effect)


def provision_quality_news_store(root, *, workspace_id, store_id, provider_account_sha256=None):
    """Explicit offline provisioning only, never called by HTTP startup.

    An existing path is never reset, migrated or overwritten. Production
    provisioning and the exact store identity require separate authorization.
    """
    for value in (workspace_id, store_id):
        _require(type(value) is str and str(UUID(value)) == value and UUID(value).int != 0)
    path = Path(root) / "quality-news.sqlite3"
    _require(not path.exists(), "quality_news_store_already_exists")
    owner = QualityNewsJournal(path, workspace_id=workspace_id, client_id="yellow",
                              source_item_id=store_id, daily_limit=3)
    with owner._db() as db:
        db.execute("CREATE TABLE quality_news_store (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                   "contract TEXT NOT NULL,workspace_id TEXT NOT NULL,store_id TEXT NOT NULL)")
        db.execute("INSERT INTO quality_news_store VALUES (1,'quality-news-store@1',?,?)",
                   (workspace_id, store_id))
        if provider_account_sha256 is not None:
            provision_image_budget(db, workspace_id=workspace_id,
                provider_account_sha256=provider_account_sha256, policy_mode=ESTIMATE_POLICY)


def _existing_store(root, workspace_id, store_id):
    try:
        path = root / "quality-news.sqlite3"
        for candidate, directory in ((root, True), (path, False)):
            info = candidate.lstat()
            _require(candidate.is_absolute() and candidate.resolve() == candidate
                     and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
                     and (stat.S_ISDIR(info.st_mode) if directory else
                          stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                     "quality_news_store_unavailable")
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT contract,workspace_id,store_id FROM quality_news_store WHERE singleton=1").fetchone()
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        _require(row == ("quality-news-store@1", workspace_id, store_id)
                 and {"quality_news_copy", "higgsfield_initial_banners"} <= tables,
                 "quality_news_store_unavailable")
    except (OSError, sqlite3.Error):
        raise QualityBannerError("quality_news_store_unavailable") from None


def _safe_spec(value):
    # Keep public copy only. Model-provided source/brand/approval/render fields
    # must never become server attestations or visual localization instructions.
    _require(type(value) is dict)
    headline, lines = value.get("headline"), value.get("body_lines")
    _require(type(headline) is str and 1 <= len(headline) <= 100
             and re.search(r"[가-힣]", headline) is not None
             and type(lines) is list and 1 <= len(lines) <= 3
             and all(type(line) is str and 1 <= len(line) <= 300 for line in lines))
    # No silent truncation. Only a short first supporting line goes on-image;
    # the complete body remains in Telegram/X copy for staff review.
    _require(len(lines[0]) <= 180, "quality_news_copy_too_dense")
    for line in [headline, *lines]:
        _validate_copy(line, 300)
    label, theme = value.get("label", "공식 업데이트"), value.get("theme", "dark")
    _validate_copy(label, 50)
    _require(theme in {"light", "dark", "yellow"})
    return {"headline": headline, "body_lines": lines,
            "label": label, "theme": theme}


def _request(context, spec, source_sha, logo):
    return QualityBannerRequest(operation_id=context["request_id"],
        workspace_id=context["workspace_id"], client_id=context["client_id"],
        source_item_id=context["source_item_id"], source_url=context["source_url"],
        source_sha256=context["source_sha256"], source_published_at=context["source_published_at"],
        headline=spec["headline"], subtitle=spec["body_lines"][0], design_feedback="",
        references=((BannerReference("official_source", context["source_image_url"], source_sha), logo)
                    if context["source_image_url"] else (logo,)))


def _restore_request(data):
    values = dict(data)
    values["references"] = tuple(BannerReference(**item) for item in values["references"])
    return QualityBannerRequest(**values)


class QualityNewsService:
    def __init__(self, *, journal_root, key_id, key_secret, logo_references,
                 reference_hosts, output_hosts, daily_limit, release_sha,
                 spend_policy_sha256, store_id, transport=None, clock=None, copy_factory=_copy,
                 budget_authority=None):
        _require(re.fullmatch(r"[a-f0-9]{40}", release_sha or "") is not None
                 and re.fullmatch(r"[a-f0-9]{64}", spend_policy_sha256 or "") is not None
                 and type(daily_limit) is int and 1 <= daily_limit <= 3
                 and callable(copy_factory), "quality_news_configuration_invalid")
        self.root = Path(journal_root)
        self.key_id, self.key_secret = key_id, key_secret
        self.logos = logo_references
        self.references, self.outputs = reference_hosts, output_hosts
        self.limit, self.release, self.spend_policy = daily_limit, release_sha, spend_policy_sha256
        _require(type(store_id) is str and str(UUID(store_id)) == store_id and UUID(store_id).int != 0,
                 "quality_news_configuration_invalid")
        self.store_id = store_id
        self.transport, self.clock = transport, clock or (lambda: datetime.now(timezone.utc))
        self.copy_factory = copy_factory
        self.budget_authority = budget_authority
        if type(budget_authority) is TokenRateForecastAuthority:
            from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
            _require(spend_policy_sha256 == FORECAST_POLICY_SHA256,
                     "quality_news_budget_evidence_required")

    async def _source_bytes(self, url, *, before_effect=None):
        _public_url(url, self.references)
        async with httpx.AsyncClient(transport=self.transport, timeout=30,
                                     follow_redirects=False, trust_env=False) as client:
            if before_effect is not None:
                before_effect()
            async with client.stream("GET", url) as response:
                _require(response.status_code == 200, "quality_news_reference_invalid")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    _require(len(data) <= MAX_BYTES, "quality_news_reference_invalid")
        raw = bytes(data)
        _reference_bytes(raw, _sha(raw))
        return raw

    async def generate(self, *, client_id, request_id, payload, output_root,
                       before_effect=None, collect_only=False):
        # The hosted one-shot trial supplies a durable owner/deadline readback.
        # Ordinary quality callers remain governed by their existing owner.
        guard = before_effect or (lambda: None)
        _require(type(collect_only) is bool and (not collect_only or before_effect is not None),
                 "quality_news_configuration_invalid")
        _require(before_effect is None or self.copy_factory is _copy,
                 "quality_news_configuration_invalid")
        guard()
        context = validate_context(client_id, request_id, payload)
        logo_only = context["source_image_url"] == ""
        # Only the bounded hosted trial supplies a durable effect guard.
        # Ordinary quality/legacy generation must retain its original-image
        # requirement; empty media is never inferred from a failed download.
        _require(not logo_only or before_effect is not None, "quality_news_invalid")
        _validate_prompt_input(payload)
        logo = self.logos.get(client_id)
        _require(type(logo) is BannerReference and logo.role == "official_logo",
                 "quality_news_logo_required")
        # Validate structure before local reads. Only an already-bound paid
        # attempt may reconcile after expiry; new copy/image admission below
        # remains fresh-only. Placeholder hash is replaced by measured bytes.
        probe = _request(context, {"headline": "공식 업데이트", "body_lines": ["검수 대기"]}, "0" * 64, logo)
        probe.validate(now=self.clock(), reference_hosts=self.references, require_fresh=False)
        _require(self.budget_authority is not None,
                 "quality_news_budget_evidence_required")
        output_root = Path(output_root)
        _require(output_root.is_absolute() and output_root.resolve() == output_root
                 and not (self.root == output_root or self.root.is_relative_to(output_root)
                          or output_root.is_relative_to(self.root)), "quality_news_configuration_invalid")
        _existing_store(self.root, context["workspace_id"], self.store_id)
        owner = QualityNewsJournal(self.root / "quality-news.sqlite3",
            workspace_id=context["workspace_id"], client_id=client_id,
            source_item_id=context["source_item_id"], daily_limit=self.limit,
            clock=lambda: self.clock().timestamp(), require_existing=True,
            budget_authority=self.budget_authority)
        # Refuse missing/wrong policy before a paid copy call. Merely setting an
        # environment mode must not silently migrate an existing strict store.
        with owner._db() as db:
            summary = _budget_call(budget_snapshot, db=db, workspace_id=context["workspace_id"],
                provider_account_sha256=self.budget_authority.account_sha256, now=self.clock())
            _require(summary.policy_mode == getattr(self.budget_authority, "policy_mode", STRICT_POLICY),
                     "quality_news_budget_evidence_required")
        adapter = HiggsfieldQualityBanner(key_id=self.key_id, key_secret=self.key_secret,
            owner=owner, reference_hosts=self.references, output_hosts=self.outputs,
            transport=self.transport, clock=self.clock, before_effect=guard)
        input_sha = _sha(_json({"payload": payload, "logo": asdict(logo), "policy": POLICY,
                               "release": self.release, "spend_policy": self.spend_policy}).encode())
        def admit_new():
            _require(not collect_only, "quality_banner_submit_unknown")
            probe.validate(now=self.clock(), reference_hosts=self.references)
            if type(self.budget_authority) is TokenRateForecastAuthority:
                # Read-only early rejection before paid copy. Atomic admission
                # is still repeated against the actual request before image POST.
                forecast = self.budget_authority.forecast(probe, now=self.clock())
                _require(usd_to_micros(forecast.estimated_usd) <= summary.remaining_usd_micros,
                         "quality_news_daily_budget_limit")
        checkpoint = owner.start_or_read(context, input_sha, admit_new=admit_new)
        if checkpoint is None:
            # An uncertain copy response is terminal; never rerun the LLM on
            # retry. Persist the exact accepted copy before the image POST.
            guard()
            if logo_only:
                # Verify the fixed public logo before paid copy/checkpoint.
                # The adapter verifies it again immediately before submit.
                logo_bytes = await self._source_bytes(logo.url, before_effect=guard)
                _reference_bytes(logo_bytes, logo.sha256)
                source_sha = ""
            else:
                source_bytes = await self._source_bytes(context["source_image_url"], before_effect=guard)
                source_sha = _sha(source_bytes)
            probe.validate(now=self.clock(), reference_hosts=self.references)
            guard()
            if before_effect is not None:
                spec = _safe_spec(await self.copy_factory(payload, client_id, before_effect=guard))
            else:
                spec = _safe_spec(await self.copy_factory(payload, client_id))
            request = _request(context, spec, source_sha, logo)
            request.validate(now=self.clock(), reference_hosts=self.references)
            checkpoint = {"spec": spec, "request": asdict(request)}
            owner.save_checkpoint(context, input_sha, checkpoint)
        request = _restore_request(checkpoint["request"])
        request.validate(now=self.clock(), reference_hosts=self.references, require_fresh=False)
        state = owner.attempt_state(request_id, request.digest())
        if collect_only:
            _require(state in {"submitted", "result_ready"}, "quality_banner_submit_unknown")
            # Persisted provider ID and exact request digest must both exist.
            # No new estimate, copy or image POST can be opened by this mode.
            owner.read_request_id(request_id, request.digest())
        if state is None:
            request.validate(now=self.clock(), reference_hosts=self.references)
            if type(self.budget_authority) is AuthenticatedEstimateAuthority:
                guard()
                proof = await adapter.estimate(request,
                    provider_account_sha256=self.budget_authority.account_sha256)
                self.budget_authority.remember(request_id, proof)
            elif type(self.budget_authority) is TokenRateForecastAuthority:
                # Pure calculation under a separately provisioned policy.
                # Never run this as a fallback from failed quote/generation.
                proof = self.budget_authority.forecast(request, now=self.clock())
                self.budget_authority.remember(request_id, proof)
            guard()
            await adapter.submit_once(request)
        elif state == "submit_started":
            raise QualityBannerError("quality_banner_submit_unknown")
        try:
            guard()
            png = await adapter.collect(request, max_polls=1)
        except QualityBannerError:
            # Retain the stale admission signal even if the GET-only provider
            # observation remains pending. No estimate or POST is repeated.
            request.validate(now=self.clock(), reference_hosts=self.references)
            raise
        # Completion never admits an expired source. Catalog and daily-card
        # owners must independently recheck latest source/current version.
        request.validate(now=self.clock(), reference_hosts=self.references)
        guard()
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
        spec = {**checkpoint["spec"], "brand_profile_policy_version": "official-reference-quality@1",
                "render_strategy": "official_logo_announcement" if logo_only else "reference_first_quality",
                "channel_profile": "x_square", "template_version": POLICY,
                "asset_pack_version": "official-logo-only@1" if logo_only else "official-reference-source-logo@1",
                "quality_banner": {"policy": POLICY, "request_sha256": request.digest(),
                                   "png_sha256": _sha(png), "width": width, "height": height,
                                   **({"reference_mode": "official_logo_only",
                                       "official_logo_sha256": logo.sha256} if logo_only else {})}}
        directory = output_root / client_id / ("news_request_" + request_id)
        guard()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _require(directory.resolve() == directory, "quality_news_output_invalid")
        image_path, manifest_path = directory / "news_card_quality.png", directory / "manifest.json"
        result = {"client_id": client_id, "content_type": "news_card", "spec": spec,
            "png_path": str(image_path), "template_style": "remix", "requested_template_style": "remix",
            "source_image_used": not logo_only,
            "source_image_url": "" if logo_only else request.references[0].url,
            "source_image_sha256": "" if logo_only else request.references[0].sha256,
            "source_visual_path": None,
            "figma_template": None, "manifest_path": str(manifest_path), "duration_ms": 0}
        # Recreating lost local files from the same durable PNG is not another
        # generation. Atomic replaces prevent gateway reads of partial bytes.
        for path, data in ((image_path, png), (manifest_path, _json(result).encode())):
            temporary = directory / ("." + uuid4().hex + ".tmp")
            try:
                guard()
                with temporary.open("xb") as file:
                    os.chmod(temporary, 0o600)
                    file.write(data); file.flush(); os.fsync(file.fileno())
                guard()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        guard()
        return result


def service_from_env(env, *, trial_authority=None):
    """No client/DB construction when OFF, except an already gated trial.

    `trial_authority` is passed only after the API has read an exact reserved
    TrialOwner phase. It is not inferred from environment or request content.
    """
    from core.automation.trial_generation_gate import TrialGenerationGate
    trial_only = trial_authority is not None
    _require(not trial_only or type(trial_authority) is TrialGenerationGate,
             "quality_news_configuration_invalid")
    if not trial_only and env.get("QUALITY_BANNER_ENABLED", "false") == "false":
        return None
    _require(((env.get("QUALITY_BANNER_ENABLED", "false") == "false"
               and env.get("NEWS_CARD_IDEMPOTENCY_ENABLED", "false") == "false") if trial_only
              else (env.get("QUALITY_BANNER_ENABLED") == "true"
                    and env.get("NEWS_CARD_IDEMPOTENCY_ENABLED") == "true")),
             "quality_news_configuration_invalid")
    release = env.get("RAILWAY_GIT_COMMIT_SHA", "")
    _require(release == env.get("QUALITY_BANNER_RELEASE_SHA")
             and re.fullmatch(r"[a-f0-9]{40}", release) is not None,
             "quality_news_release_mismatch")
    # The mode and digest are an exact pair. Neither is inferred from a failed
    # quote, request JSON, or the persisted ledger. Strict-cap mode stays closed.
    mode, digest = env.get("QUALITY_BANNER_BUDGET_MODE"), env.get("QUALITY_BANNER_SPEND_POLICY_SHA256")
    if mode == ESTIMATE_POLICY:
        _require(digest == ESTIMATE_SPEND_POLICY_SHA256, "quality_news_budget_evidence_required")
        authority_type = AuthenticatedEstimateAuthority
    elif mode == TOKEN_RATE_FORECAST_POLICY:
        from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
        _require(digest == FORECAST_POLICY_SHA256, "quality_news_budget_evidence_required")
        authority_type = TokenRateForecastAuthority
    else:
        raise QualityBannerError("quality_news_budget_evidence_required")
    try:
        logos = json.loads(env["QUALITY_BANNER_LOGO_REFERENCES"])
        _require(type(logos) is dict and set(logos) == set(CLIENT_HANDLES))
        service = QualityNewsService(journal_root=env["NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT"],
            key_id=env["HIGGSFIELD_API_KEY_ID"], key_secret=env["HIGGSFIELD_API_KEY_SECRET"],
            logo_references={client: BannerReference("official_logo", **value) for client, value in logos.items()},
            reference_hosts=frozenset(env["QUALITY_BANNER_REFERENCE_HOSTS"].split(",")),
            output_hosts=frozenset(env["QUALITY_BANNER_OUTPUT_HOSTS"].split(",")),
            daily_limit=int(env["QUALITY_BANNER_DAILY_REQUEST_LIMIT"]), release_sha=release,
            spend_policy_sha256=digest, store_id=env["QUALITY_BANNER_STORE_ID"],
            budget_authority=authority_type(env["QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256"]))
    except (KeyError, ValueError, TypeError):
        raise QualityBannerError("quality_news_configuration_invalid") from None
    path = service.root / "quality-news.sqlite3"
    if mode == TOKEN_RATE_FORECAST_POLICY or path.exists():
        # Read-only startup verification for every existing store. The legacy
        # quote mode may still be constructed before offline provisioning; the
        # new forecast mode requires the transitioned store to exist already.
        # No path creation, journal constructor, migration or paid client.
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute("SELECT workspace_id FROM quality_news_store WHERE singleton=1").fetchone()
                _require(row is not None, "quality_news_store_unavailable")
                _existing_store(service.root, row[0], service.store_id)
                summary = _budget_call(budget_snapshot, db=db, workspace_id=row[0],
                    provider_account_sha256=service.budget_authority.account_sha256,
                    now=datetime.now(timezone.utc))
                _require(summary.policy_mode == mode,
                         "quality_news_budget_evidence_required")
        except (OSError, sqlite3.Error, ValueError) as error:
            if isinstance(error, QualityBannerError):
                raise
            raise QualityBannerError("quality_news_store_unavailable") from None
    return service
