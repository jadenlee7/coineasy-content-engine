"""Offline exact final-human attribution; no Auth elevation or provider calls."""
import copy
import hashlib
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.publications.handoff import PublicationHandoffError, build_publication_handoff
from core.publications.typefully_approval import (
    typefully_route_binding, validate_telegram_principal_approval,
)
from core.publications.typefully_draft_preparation import (
    TypefullyDraftPreparationError, prepare_typefully_draft,
)
from core.publications.typefully_draft_once import SupabaseTypefullyDraftOwner, TypefullyDraftOwnerError
from tests.test_publication_handoff import NOW, _id, _snapshot
from tests.test_typefully_draft_preparation import evidence


SHA = "f" * 40


def principal_snapshot(client="squid", now=NOW):
    data = _snapshot(client)
    proof = {"schema_version": "content-ops-final-approval@1", "decision_id": _id(8),
        "review_principal_id": _id(9), "release_sha": SHA,
        "version_fingerprint": "d" * 64, "snapshot_sha256": "e" * 64,
        "banner_sha256": data["asset"]["sha256"],
        "x_copy_sha256": hashlib.sha256(data["channel_copy"]["x"].encode()).hexdigest(),
        "telegram_copy_sha256": hashlib.sha256(data["channel_copy"]["telegram"].encode()).hexdigest(),
        "typefully_route_binding": typefully_route_binding(workspace_id=data["workspace_id"],
            client_id=client, release_sha=SHA, social_set_id=12345,
            x_username={"yellow": "yellow__korea", "babylon": "babylonkorean",
                        "squid": "squidkorea", "origintrail": "origin_trail_kr"}[client]),
        "telegram_route_binding": "a" * 64,
        "route_verified_at": now.isoformat()}
    data["latest_approval"].update(reviewer_source="telegram_principal",
        review_principal_id=_id(9), telegram_principal_approval=proof)
    return data


def coexist(data, status="queued"):
    proof = data["latest_approval"]["telegram_principal_approval"]
    data["existing_publication_count"] = 1
    data["coexisting_telegram_publication"] = {
        **{k: data[k] for k in ("workspace_id", "content_item_id", "content_version_id", "client_id")},
        "publication_id": _id(10), "approval_id": _id(5), "channel": "telegram",
        "workflow": "exact_telegram_publication_v1", "status": status,
        "decision_id": proof["decision_id"], "route_binding": proof["telegram_route_binding"],
        "release_sha": SHA, "banner_sha256": proof["banner_sha256"],
        "telegram_copy_sha256": proof["telegram_copy_sha256"]}
    if status == "published":
        data["content_status"] = "published"
    return data


class FinalApprovalPreparationTests(unittest.TestCase):
    def test_studio_and_four_real_human_projections_remain_draft_only(self):
        for client in ("yellow", "babylon", "squid", "origintrail"):
            for source in ("studio_session", "telegram_principal"):
                data = _snapshot(client) if source == "studio_session" else principal_snapshot(client)
                account, media = evidence(client)
                before = copy.deepcopy(data)
                prepared = prepare_typefully_draft(data, social_set=account, media=media, now=NOW)
                self.assertNotIn("publish_at", prepared["preparation"]["request_body"])
                self.assertNotIn("plan_at", prepared["preparation"]["request_body"])
                self.assertFalse(prepared["preparation"]["execution_authorized"])
                self.assertFalse(prepared["preparation"]["provider_attempted"])
                handoff = build_publication_handoff(data, now=NOW)["packet"]
                self.assertEqual(handoff["approval"]["reviewer_source"], source)
                self.assertEqual(data, before)

    def test_source_string_or_agent_verdict_is_not_human_attribution(self):
        for source in ("telegram_principal", "codex:content-qa", "PASS"):
            data = _snapshot()
            data["latest_approval"]["reviewer_source"] = source
            with self.assertRaises(PublicationHandoffError):
                build_publication_handoff(data, now=NOW)

    def test_tampered_human_projection_or_copy_fails_closed(self):
        for key, value in (("decision_id", "invalid"), ("review_principal_id", _id(99)),
                ("banner_sha256", "b" * 64), ("x_copy_sha256", "b" * 64),
                ("telegram_copy_sha256", "b" * 64), ("release_sha", "bad"),
                ("route_verified_at", (NOW-timedelta(minutes=16)).isoformat()),
                ("route_verified_at", (NOW+timedelta(seconds=1)).isoformat())):
            data = principal_snapshot()
            data["latest_approval"]["telegram_principal_approval"][key] = value
            with self.subTest(key=key), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(data, now=NOW)
        data = principal_snapshot()
        data["latest_approval"]["telegram_principal_approval"]["provider_response"] = "not accepted"
        with self.assertRaises(PublicationHandoffError):
            build_publication_handoff(data, now=NOW)

    def test_actual_social_set_and_runtime_release_are_bound(self):
        data = principal_snapshot()
        proof = data["latest_approval"]["telegram_principal_approval"]
        for change in ({"social_set_id": 67890}, {"release_sha": "a" * 40}):
            with self.assertRaisesRegex(ValueError, "final_approval_invalid"):
                validate_telegram_principal_approval(proof, workspace_id=data["workspace_id"],
                    client_id="squid", banner_sha256="c" * 64, x_copy=data["channel_copy"]["x"],
                    now=NOW, **change)
        account, media = evidence()
        account["social_set_id"] = media["social_set_id"] = 67890
        with self.assertRaisesRegex(TypefullyDraftPreparationError, "final_approval_invalid"):
            prepare_typefully_draft(data, social_set=account, media=media, now=NOW)

    def test_route_digest_matches_existing_verified_route_contract(self):
        from core.content_ops.publication_route_verification import _digest
        for client, handle in (("yellow", "yellow__korea"), ("babylon", "babylonkorean"),
                ("squid", "squidkorea"), ("origintrail", "origin_trail_kr")):
            payload = {"schema": "content-ops-publication-route@1", "workspace_id": _id(1),
                "client_id": client, "release_sha": SHA, "provider": "typefully_x",
                "action": "x_draft_only", "social_set_id": 12345, "username": handle}
            self.assertEqual(typefully_route_binding(workspace_id=_id(1), client_id=client,
                release_sha=SHA, social_set_id=12345, x_username=handle), _digest(payload))

    def test_only_same_version_exact_telegram_can_coexist(self):
        for status in ("queued", "publishing", "published"):
            data = coexist(principal_snapshot(), status)
            result = build_publication_handoff(data, now=NOW)
            self.assertEqual(result["packet"]["approval"]["reviewer_source"], "telegram_principal")
        for key, value in (("content_version_id", _id(99)), ("channel", "x"),
                ("status", "delivery_unknown"), ("status", "failed"),
                ("workflow", "legacy"), ("route_binding", "b" * 64),
                ("approval_id", _id(99)), ("release_sha", "b" * 40),
                ("banner_sha256", "b" * 64), ("decision_id", _id(99))):
            data = coexist(principal_snapshot())
            data["coexisting_telegram_publication"][key] = value
            with self.subTest(key=key), self.assertRaises(PublicationHandoffError):
                build_publication_handoff(data, now=NOW)
        for data in (coexist(principal_snapshot()), _snapshot()):
            data["existing_publication_count"] = 2
            with self.assertRaises(PublicationHandoffError):
                build_publication_handoff(data, now=NOW)

    def test_preparation_does_not_read_credentials_files_or_network(self):
        data = principal_snapshot()
        account, media = evidence()
        with patch("builtins.open", side_effect=AssertionError("file")), \
             patch("socket.socket.connect", side_effect=AssertionError("network")), \
             patch("os.getenv", side_effect=AssertionError("credential")):
            prepare_typefully_draft(data, social_set=account, media=media, now=NOW)

    def test_new_proposal_pins_historical_bodies_and_never_grants(self):
        root = Path(__file__).resolve().parents[1]
        sql = (root/"supabase/proposals/content_ops_typefully_final_approval.sql").read_text()
        expected = set(re.findall(r"'([a-f0-9]{64})'", sql))
        actual = set()
        names = {"private.reserve_typefully_media_allocation_once", "private.record_typefully_media_upload",
                 "private.reserve_typefully_draft_once", "public.get_typefully_draft_candidate",
                 "public.claim_typefully_daily_slot"}
        for file in ("20260923120000_typefully_draft_once.sql", "20260923130000_typefully_daily_slot.sql"):
            source = (root/"supabase/migrations"/file).read_text()
            for match in re.finditer(r"create function ([\w.]+)\([\s\S]*?as \$\$([\s\S]*?)\$\$;", source):
                if match[1] in names:
                    actual.add(hashlib.sha256(match[2].encode()).hexdigest())
        self.assertEqual(actual, expected)
        self.assertNotRegex(sql, r"(?im)^\s*grant\s")
        self.assertIn("decision_mode='telegram_and_typefully_draft'", sql)
        self.assertIn("SECURITY INVOKER", sql)
        self.assertNotIn("set_config('request.jwt.claim.role'", sql)


class CandidateAttributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_rejects_mismatch_before_provider(self):
        from tests.test_typefully_draft_once import settings, PNG, WORKSPACE, ITEM, VERSION, APPROVAL, ASSET
        config = settings()
        proof_data = principal_snapshot(now=datetime.now(timezone.utc))
        proof = proof_data["latest_approval"]["telegram_principal_approval"]
        # Match this worker's trusted synthetic IDs and its pinned runtime.
        proof["release_sha"] = config.authorized_sha
        proof["banner_sha256"] = hashlib.sha256(PNG).hexdigest()
        x_copy = "Exact synthetic X copy"
        proof["x_copy_sha256"] = hashlib.sha256(x_copy.encode()).hexdigest()
        proof["typefully_route_binding"] = typefully_route_binding(workspace_id=WORKSPACE,
            client_id=config.client_id, release_sha=config.authorized_sha,
            social_set_id=config.social_set_id, x_username="squidkorea")
        raw = {"workspace_id": WORKSPACE, "client_id": config.client_id, "content_item_id": ITEM,
            "content_version_id": VERSION, "approval_id": APPROVAL, "asset_id": ASSET,
            "storage_bucket": "content-studio", "storage_path": f"{WORKSPACE}/squid/{ASSET}/news-card.png",
            "asset_sha256": proof["banner_sha256"], "asset_byte_size": len(PNG),
            "asset_width": 1, "asset_height": 1, "x_copy": x_copy,
            "version_created_at": NOW.isoformat(), "reviewer_source": "telegram_principal",
            "telegram_principal_approval": proof}
        class FakeOwner(SupabaseTypefullyDraftOwner):
            calls = 0
            async def _rpc(self, name, payload):
                self.calls += 1
                return copy.deepcopy(raw)
        owner = FakeOwner(config)
        self.assertEqual((await owner.candidate())["reviewer_source"], "telegram_principal")
        proof["release_sha"] = "b" * 40
        with self.assertRaisesRegex(TypefullyDraftOwnerError, "final_approval_invalid"):
            await owner.candidate()
        self.assertEqual(owner.calls, 2)
