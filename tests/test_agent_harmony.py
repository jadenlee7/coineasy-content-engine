from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.agent_control import (
    EmptyHarmonyAttestationRegistry,
    FrozenHarmonyAttestationRegistry,
    HARMONY_CLIENT_IDS,
    HarmonyInput,
    HarmonyRoundStatus,
    HarmonySignalAttestation,
    HarmonySnapshot,
    bind_harmony_signal_attestation,
    bind_harmony_signal_payload,
    build_harmony_snapshot,
    harmony_participants,
    load_harmony_client_profiles,
    render_harmony_dashboard,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _base_signal(
    *,
    client_id: str,
    kind: str,
    number: int,
    topic_codes: tuple[str, ...] = ("staking_basics",),
    observed_at: datetime | None = None,
    expires_at: datetime | None = None,
    source_event_number: int | None = None,
    producer_number: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "agent-harmony-signal@1",
        "signal_id": _uuid(number),
        "workspace_id": WORKSPACE_ID,
        "client_id": client_id,
        "signal_kind": kind,
        "source_event_id": _uuid(source_event_number or 10_000 + number),
        "producer_principal_id": _uuid(producer_number or 20_000 + number),
        "producer_release_sha": "a" * 40,
        "config_sha256": "b" * 64,
        "upstream_receipt_sha256": "c" * 64,
        "observed_at": observed_at or OBSERVED_AT - timedelta(hours=1),
        "expires_at": expires_at or OBSERVED_AT + timedelta(days=1),
        "evidence_sha256": "d" * 64,
        "topic_codes": topic_codes,
        "raw_messages_included": False,
        "personal_data_included": False,
        "instructions_allowed": False,
        "advisory_only": True,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
        "automatic_publication": False,
    }
    if kind == "quiz_learning":
        payload.update({
            "lane": "quiz_bot",
            "data_classification": "aggregate_anonymous",
            "content_factual_authority": False,
            "attempts": 40,
            "participants": 10,
            "accuracy_basis_points": 4_000,
            "tutorial_priority_basis_points": 8_000,
        })
    elif kind == "community_demand":
        payload.update({
            "lane": "community_ops",
            "data_classification": "aggregate_anonymous",
            "content_factual_authority": False,
            "room_mapping_count": 1,
            "sample_size": 20,
            "demand_score_basis_points": 7_000,
        })
    elif kind == "official_source":
        payload.update({
            "lane": "content_source",
            "data_classification": "public_official",
            "content_factual_authority": True,
            "source_item_id": _uuid(30_000 + number),
            "source_body_sha256": "e" * 64,
            "source_kind": "x_post_text",
            "source_verified": True,
            "eligible_content_kinds": (
                "article",
                "daily_news",
                "tutorial",
            ),
        })
    elif kind == "recap_metric":
        period_end = OBSERVED_AT - timedelta(hours=1)
        payload.update({
            "lane": "recap",
            "data_classification": "aggregate_anonymous",
            "content_factual_authority": False,
            "period_start": period_end - timedelta(days=7),
            "period_end": period_end,
            "metrics": ({
                "metric_code": "content_clicks",
                "unit": "count",
                "observed": True,
                "value": 12,
            },),
        })
    else:  # pragma: no cover - test fixture guard
        raise AssertionError(kind)
    return bind_harmony_signal_payload(payload)


def _client_signals(
    client_id: str,
    *,
    start: int = 1,
    topic_codes: tuple[str, ...] = ("staking_basics",),
) -> list[dict[str, object]]:
    return [
        _base_signal(
            client_id=client_id,
            kind=kind,
            number=start + offset,
            topic_codes=topic_codes,
        )
        for offset, kind in enumerate((
            "quiz_learning",
            "community_demand",
            "official_source",
            "recap_metric",
        ))
    ]


def _input(signals: list[dict[str, object]]) -> HarmonyInput:
    return HarmonyInput.model_validate({
        "schema_version": "agent-harmony-input@1",
        "workspace_id": WORKSPACE_ID,
        "signals": signals,
    })


def _profiles():
    return load_harmony_client_profiles(ROOT / "clients")


def _attestation(
    signal: object,
    *,
    number: int,
    method: str = "database_receipt",
    overrides: dict[str, object] | None = None,
) -> HarmonySignalAttestation:
    verified_at = max(
        signal.observed_at,  # type: ignore[attr-defined]
        OBSERVED_AT - timedelta(minutes=30),
    )
    payload: dict[str, object] = {
        "schema_version": "agent-harmony-signal-attestation@1",
        "attestation_id": _uuid(800_000 + number),
        "workspace_id": signal.workspace_id,  # type: ignore[attr-defined]
        "client_id": signal.client_id,  # type: ignore[attr-defined]
        "signal_id": signal.signal_id,  # type: ignore[attr-defined]
        "source_event_id": signal.source_event_id,  # type: ignore[attr-defined]
        "signal_kind": signal.signal_kind,  # type: ignore[attr-defined]
        "lane": signal.lane,  # type: ignore[attr-defined]
        "producer_principal_id": signal.producer_principal_id,  # type: ignore[attr-defined]
        "producer_release_sha": signal.producer_release_sha,  # type: ignore[attr-defined]
        "config_sha256": signal.config_sha256,  # type: ignore[attr-defined]
        "upstream_receipt_sha256": signal.upstream_receipt_sha256,  # type: ignore[attr-defined]
        "evidence_sha256": signal.evidence_sha256,  # type: ignore[attr-defined]
        "payload_sha256": signal.payload_sha256,  # type: ignore[attr-defined]
        "verification_method": method,
        "verification_reference_sha256": "f" * 64,
        "verified_at": verified_at,
        "expires_at": max(
            OBSERVED_AT + timedelta(days=1),
            verified_at + timedelta(days=1),
        ),
    }
    payload.update(overrides or {})
    return HarmonySignalAttestation.model_validate(
        bind_harmony_signal_attestation(payload)
    )


def _registry(
    harmony_input: HarmonyInput,
    *,
    method: str = "database_receipt",
) -> FrozenHarmonyAttestationRegistry:
    attestations = []
    seen: set[str] = set()
    for index, signal in enumerate(harmony_input.signals, start=1):
        if signal.payload_sha256 in seen:
            continue
        seen.add(signal.payload_sha256)
        attestations.append(_attestation(
            signal,
            number=index,
            method=method,
        ))
    return FrozenHarmonyAttestationRegistry(attestations)


def _trusted_snapshot(
    signals: list[dict[str, object]],
) -> HarmonySnapshot:
    harmony_input = _input(signals)
    return build_harmony_snapshot(
        harmony_input,
        _profiles(),
        observed_at=OBSERVED_AT,
        attestation_registry=_registry(harmony_input),
    )


def test_current_registry_has_four_isolated_clients_and_no_live_adapter() -> None:
    profiles = _profiles()
    assert tuple(profile.client_id for profile in profiles) == HARMONY_CLIENT_IDS
    capabilities = {
        profile.client_id: profile.supported_content_kinds for profile in profiles
    }
    assert "tutorial" not in capabilities["babylon"]
    assert "tutorial" not in capabilities["origintrail"]
    assert "tutorial" in capabilities["squid"]
    assert "tutorial" in capabilities["yellow"]
    assert all(profile.live_harmony_adapter_connected is False for profile in profiles)

    participants = harmony_participants()
    quiz_clients = tuple(
        item.client_id
        for item in participants
        if item.participant_kind == "client_quiz_bot"
    )
    assert quiz_clients == HARMONY_CLIENT_IDS
    assert all(item.can_publish is False for item in participants)
    assert all(item.can_change_production is False for item in participants)
    assert all(item.can_access_raw_community is False for item in participants)
    assert [item.participant_id for item in participants if item.can_approve_scope] == [
        "human_operator"
    ]


def test_empty_snapshot_shows_four_waiting_rounds_without_fake_observations() -> None:
    snapshot = build_harmony_snapshot(
        _input([]),
        _profiles(),
        observed_at=OBSERVED_AT,
    )
    assert snapshot.counts.clients == 4
    assert snapshot.counts.accepted_signals == 0
    assert snapshot.counts.waiting_for_signals == 4
    assert snapshot.counts.ready_for_human_scope_review == 0
    assert snapshot.live_adapters_connected is False
    assert snapshot.external_calls is False
    assert snapshot.database_calls is False
    assert snapshot.provider_calls is False
    assert snapshot.publication_calls is False
    assert snapshot.automatic_publication is False
    assert all(round_.handoff is None for round_ in snapshot.rounds)
    assert all(len(round_.turns) == 6 for round_ in snapshot.rounds)


def test_complete_caller_claims_without_registry_never_create_handoff() -> None:
    harmony_input = _input(_client_signals("squid", start=50))
    implicit = build_harmony_snapshot(
        harmony_input,
        _profiles(),
        observed_at=OBSERVED_AT,
    )
    explicit = build_harmony_snapshot(
        harmony_input,
        _profiles(),
        observed_at=OBSERVED_AT,
        attestation_registry=EmptyHarmonyAttestationRegistry(),
    )
    assert implicit.as_payload() == explicit.as_payload()
    squid = next(item for item in implicit.rounds if item.client_id == "squid")
    assert squid.status == HarmonyRoundStatus.WAITING_FOR_ATTESTATION
    assert squid.handoff is None
    assert all(turn.signal_ids == () for turn in squid.turns)
    assert implicit.counts.input_signal_claims == 4
    assert implicit.counts.runtime_attested_signals == 0
    assert implicit.counts.unattested_signal_claims == 4
    assert implicit.counts.ready_for_human_scope_review == 0
    assert implicit.trust_mode.value == "empty"


def test_fixture_attestations_are_rehearsal_only_and_cannot_reach_human_gate() -> None:
    harmony_input = _input(_client_signals("yellow", start=60))
    snapshot = build_harmony_snapshot(
        harmony_input,
        _profiles(),
        observed_at=OBSERVED_AT,
        attestation_registry=_registry(
            harmony_input,
            method="test_fixture",
        ),
    )
    yellow = next(item for item in snapshot.rounds if item.client_id == "yellow")
    assert yellow.status == HarmonyRoundStatus.WAITING_FOR_ATTESTATION
    assert yellow.handoff is None
    assert snapshot.counts.test_fixture_signals == 4
    assert snapshot.counts.runtime_attested_signals == 0
    assert snapshot.counts.ready_for_human_scope_review == 0
    assert snapshot.trust_mode.value == "test_fixture"


@pytest.mark.parametrize(("field", "wrong_value"), (
    ("workspace_id", _uuid(999_001)),
    ("client_id", "origintrail"),
    ("signal_id", _uuid(999_002)),
    ("source_event_id", _uuid(999_003)),
    ("signal_kind", "recap_metric"),
    ("lane", "recap"),
    ("producer_principal_id", _uuid(999_004)),
    ("producer_release_sha", "b" * 40),
    ("config_sha256", "a" * 64),
    ("upstream_receipt_sha256", "a" * 64),
    ("evidence_sha256", "a" * 64),
    ("verified_at", OBSERVED_AT - timedelta(hours=2)),
))
def test_runtime_attestation_must_exactly_bind_every_signal_claim(
    field: str,
    wrong_value: object,
) -> None:
    harmony_input = _input([_base_signal(
        client_id="squid",
        kind="community_demand",
        number=70,
    )])
    attestation = _attestation(
        harmony_input.signals[0],
        number=70,
        overrides={field: wrong_value},
    )
    with pytest.raises(ValueError, match="attestation_binding_invalid"):
        build_harmony_snapshot(
            harmony_input,
            _profiles(),
            observed_at=OBSERVED_AT,
            attestation_registry=FrozenHarmonyAttestationRegistry((
                attestation,
            )),
        )


def test_signal_json_cannot_self_supply_attestation_or_verified_state() -> None:
    signal = _base_signal(
        client_id="yellow",
        kind="community_demand",
        number=80,
    )
    signal["verified"] = True
    signal = bind_harmony_signal_payload(signal)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _input([signal])


def test_one_client_round_builds_six_hash_bound_turns_and_human_handoff() -> None:
    snapshot = _trusted_snapshot(_client_signals("squid"))
    squid = next(item for item in snapshot.rounds if item.client_id == "squid")
    assert squid.status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
    assert tuple(turn.sequence for turn in squid.turns) == (1, 2, 3, 4, 5, 6)
    assert len({turn.turn_sha256 for turn in squid.turns}) == 6
    assert squid.turns[2].content_factual_authority is True
    assert all(
        turn.content_factual_authority is False
        for index, turn in enumerate(squid.turns)
        if index != 2
    )
    assert all(turn.instructions_accepted is False for turn in squid.turns)
    assert squid.handoff is not None
    assert squid.handoff.recommended_content_kind == "tutorial"
    assert squid.handoff.next_gate == "human_scope_review"
    assert squid.handoff.execution_authorized is False
    assert squid.handoff.provider_calls is False
    assert squid.handoff.publication_calls is False
    assert squid.handoff.environment == "preview"
    assert squid.handoff.dispatchable is False
    assert squid.handoff.portable_trust is False
    assert squid.handoff.attestation_reverification_required is True
    assert snapshot.render_only is True
    assert snapshot.portable_trust is False
    assert snapshot.serialized_snapshot_authoritative is False
    assert snapshot.counts.ready_for_human_scope_review == 1
    assert snapshot.counts.waiting_for_signals == 3


def test_unsupported_tutorial_falls_back_to_private_daily_news_proposal() -> None:
    snapshot = _trusted_snapshot(_client_signals("origintrail"))
    target = next(
        item for item in snapshot.rounds if item.client_id == "origintrail"
    )
    assert target.handoff is not None
    assert target.handoff.recommended_content_kind == "daily_news"


def test_no_topic_consensus_challenges_instead_of_creating_work() -> None:
    topics = (
        ("tutorial_demand",),
        ("community_faq",),
        ("official_update",),
        ("performance_gap",),
    )
    signals = [
        _base_signal(
            client_id="yellow",
            kind=kind,
            number=100 + index,
            topic_codes=topics[index],
        )
        for index, kind in enumerate((
            "quiz_learning",
            "community_demand",
            "official_source",
            "recap_metric",
        ))
    ]
    snapshot = _trusted_snapshot(signals)
    yellow = next(item for item in snapshot.rounds if item.client_id == "yellow")
    assert yellow.status == HarmonyRoundStatus.NEEDS_ALIGNMENT
    assert yellow.handoff is None
    assert [item.value for item in yellow.blockers] == [
        "topic_consensus_missing"
    ]
    assert yellow.turns[4].message_kind == "challenge"
    assert yellow.turns[5].message_kind == "evidence_request"


def test_stale_signal_blocks_handoff_and_is_not_counted_as_active() -> None:
    signals = _client_signals("babylon", start=200)
    signals[0] = _base_signal(
        client_id="babylon",
        kind="quiz_learning",
        number=200,
        observed_at=OBSERVED_AT - timedelta(days=3),
        expires_at=OBSERVED_AT - timedelta(days=1),
    )
    snapshot = _trusted_snapshot(signals)
    babylon = next(item for item in snapshot.rounds if item.client_id == "babylon")
    assert babylon.status == HarmonyRoundStatus.WAITING_FOR_SIGNALS
    assert babylon.handoff is None
    assert len(babylon.active_signal_ids) == 3
    assert len(babylon.stale_signal_ids) == 1
    assert {item.value for item in babylon.blockers} == {
        "missing_quiz_learning",
        "stale_signal",
    }


def test_exact_replay_is_deduplicated_but_conflicting_replay_is_rejected() -> None:
    signals = _client_signals("squid", start=300)
    harmony_input = _input([*signals, dict(signals[0])])
    snapshot = build_harmony_snapshot(
        harmony_input,
        _profiles(),
        observed_at=OBSERVED_AT,
        attestation_registry=_registry(harmony_input),
    )
    assert snapshot.counts.accepted_signals == 4
    assert snapshot.counts.replayed_signals == 1

    conflict = _base_signal(
        client_id="squid",
        kind="quiz_learning",
        number=399,
        topic_codes=("routing_basics",),
        source_event_number=10_300,
        producer_number=20_300,
    )
    with pytest.raises(ValueError, match="agent_harmony_signal_key_conflict"):
        conflicting_input = _input([*signals, conflict])
        build_harmony_snapshot(
            conflicting_input,
            _profiles(),
            observed_at=OBSERVED_AT,
            attestation_registry=_registry(conflicting_input),
        )


def test_official_source_body_conflict_is_rejected_before_round_synthesis() -> None:
    first = _base_signal(
        client_id="yellow",
        kind="official_source",
        number=400,
    )
    second = _base_signal(
        client_id="yellow",
        kind="official_source",
        number=401,
    )
    second["source_item_id"] = first["source_item_id"]
    second["source_body_sha256"] = "f" * 64
    second = bind_harmony_signal_payload(second)
    with pytest.raises(ValueError, match="agent_harmony_official_source_conflict"):
        harmony_input = _input([first, second])
        build_harmony_snapshot(
            harmony_input,
            _profiles(),
            observed_at=OBSERVED_AT,
            attestation_registry=_registry(harmony_input),
        )


def test_workspace_tenant_and_payload_hashes_are_fail_closed() -> None:
    signal = _base_signal(
        client_id="yellow",
        kind="community_demand",
        number=500,
    )
    signal["workspace_id"] = _uuid(999)
    signal = bind_harmony_signal_payload(signal)
    with pytest.raises(ValidationError, match="workspace_binding"):
        _input([signal])

    tampered = _base_signal(
        client_id="yellow",
        kind="community_demand",
        number=501,
    )
    tampered["sample_size"] = 99
    with pytest.raises(ValidationError, match="payload_digest"):
        _input([tampered])


def test_privacy_prompt_injection_and_quiz_thresholds_are_rejected() -> None:
    for index, topic_code in enumerate((
        "ignore_previous",
        "alice_wallet",
        "system_override",
    )):
        injected = _base_signal(
            client_id="squid",
            kind="community_demand",
            number=600 + index,
            topic_codes=(topic_code,),
        )
        with pytest.raises(ValidationError, match="topic_codes"):
            _input([injected])

    raw = _base_signal(
        client_id="squid",
        kind="community_demand",
        number=610,
    )
    raw["raw_message"] = "private community message"
    raw = bind_harmony_signal_payload(raw)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _input([raw])

    undersized = _base_signal(
        client_id="squid",
        kind="quiz_learning",
        number=611,
    )
    undersized["attempts"] = 19
    undersized = bind_harmony_signal_payload(undersized)
    with pytest.raises(ValidationError):
        _input([undersized])


def test_unobserved_recap_value_stays_none_instead_of_becoming_zero() -> None:
    recap = _base_signal(
        client_id="babylon",
        kind="recap_metric",
        number=700,
    )
    recap["metrics"] = ({
        "metric_code": "content_clicks",
        "unit": "count",
        "observed": False,
        "value": None,
    },)
    recap = bind_harmony_signal_payload(recap)
    parsed = _input([recap]).signals[0]
    assert parsed.metrics[0].observed is False  # type: ignore[union-attr]
    assert parsed.metrics[0].value is None  # type: ignore[union-attr]


def test_cross_client_pattern_shares_only_aggregate_practice() -> None:
    signals = [
        _base_signal(
            client_id="yellow",
            kind="quiz_learning",
            number=800,
            topic_codes=("wallet_safety",),
        ),
        _base_signal(
            client_id="squid",
            kind="community_demand",
            number=801,
            topic_codes=("wallet_safety",),
        ),
    ]
    snapshot = _trusted_snapshot(signals)
    assert len(snapshot.shared_patterns) == 1
    pattern = snapshot.shared_patterns[0]
    assert pattern.client_ids == ("squid", "yellow")
    assert pattern.reuse_scope == "planning_practice_only"
    assert pattern.factual_copy_reuse is False
    assert pattern.client_asset_reuse is False
    assert pattern.audience_rank_comparison is False


def test_unobserved_recap_cannot_create_cross_client_shared_pattern() -> None:
    signals: list[dict[str, object]] = []
    for number, client_id in ((820, "yellow"), (821, "squid")):
        recap = _base_signal(
            client_id=client_id,
            kind="recap_metric",
            number=number,
            topic_codes=("wallet_safety",),
        )
        recap["metrics"] = ({
            "metric_code": "content_clicks",
            "unit": "count",
            "observed": False,
            "value": None,
        },)
        signals.append(bind_harmony_signal_payload(recap))

    snapshot = _trusted_snapshot(signals)

    assert snapshot.shared_patterns == ()


def test_four_client_rounds_never_mix_private_handoff_signal_ids() -> None:
    signals: list[dict[str, object]] = []
    expected: dict[str, set[str]] = {}
    for index, client_id in enumerate(HARMONY_CLIENT_IDS, start=1):
        client_signals = _client_signals(client_id, start=index * 1_000)
        signals.extend(client_signals)
        expected[client_id] = {str(item["signal_id"]) for item in client_signals}

    snapshot = _trusted_snapshot(signals)
    assert snapshot.counts.ready_for_human_scope_review == 4
    for round_ in snapshot.rounds:
        assert {str(item) for item in round_.active_signal_ids} == expected[
            round_.client_id
        ]
        assert round_.handoff is not None
        handoff_ids = {
            str(item)
            for item in (
                *round_.handoff.source_signal_ids,
                *round_.handoff.context_signal_ids,
            )
        }
        assert handoff_ids == expected[round_.client_id]
        assert all(turn.client_id == round_.client_id for turn in round_.turns)


def test_unattested_spoof_with_same_signal_id_cannot_poison_trusted_round() -> None:
    trusted_claims = _client_signals("squid", start=4_500)
    trusted_input = _input(trusted_claims)
    registry = _registry(trusted_input)
    spoof = dict(trusted_claims[0])
    spoof["topic_codes"] = ("routing_basics",)
    spoof = bind_harmony_signal_payload(spoof)
    combined_input = _input([*trusted_claims, spoof])
    snapshot = build_harmony_snapshot(
        combined_input,
        _profiles(),
        observed_at=OBSERVED_AT,
        attestation_registry=registry,
    )
    squid = next(item for item in snapshot.rounds if item.client_id == "squid")
    assert squid.status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
    assert squid.handoff is not None
    assert snapshot.counts.runtime_attested_signals == 4
    assert snapshot.counts.unattested_signal_claims == 1
    assert len(squid.signal_manifest) == 4


def test_stale_and_future_history_do_not_veto_four_fresh_attested_lanes() -> None:
    signals = _client_signals("yellow", start=4_600)
    signals.append(_base_signal(
        client_id="yellow",
        kind="quiz_learning",
        number=4_604,
        observed_at=OBSERVED_AT - timedelta(days=3),
        expires_at=OBSERVED_AT - timedelta(days=1),
    ))
    signals.append(_base_signal(
        client_id="yellow",
        kind="community_demand",
        number=4_605,
        observed_at=OBSERVED_AT + timedelta(hours=1),
        expires_at=OBSERVED_AT + timedelta(days=1),
    ))
    snapshot = _trusted_snapshot(signals)
    yellow = next(item for item in snapshot.rounds if item.client_id == "yellow")
    assert yellow.status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
    assert yellow.handoff is not None
    assert len(yellow.active_signal_ids) == 4
    assert len(yellow.stale_signal_ids) == 1
    assert len(yellow.future_signal_ids) == 1
    assert yellow.blockers == ()


def test_sixty_four_parallel_replays_are_deterministic_and_deduplicated() -> None:
    signal = _base_signal(
        client_id="squid",
        kind="quiz_learning",
        number=850,
    )
    harmony_input = _input([dict(signal) for _ in range(64)])
    registry = _registry(harmony_input)

    def project(_: int) -> tuple[str, int, int]:
        snapshot = build_harmony_snapshot(
            harmony_input,
            _profiles(),
            observed_at=OBSERVED_AT,
            attestation_registry=registry,
        )
        return (
            snapshot.snapshot_sha256,
            snapshot.counts.accepted_signals,
            snapshot.counts.replayed_signals,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = tuple(pool.map(project, range(64)))
    assert len({item[0] for item in outcomes}) == 1
    assert {item[1:] for item in outcomes} == {(1, 63)}


def test_unobserved_recap_does_not_create_topic_consensus() -> None:
    signals = _client_signals("yellow", start=860)
    signals[1] = _base_signal(
        client_id="yellow",
        kind="community_demand",
        number=861,
        topic_codes=("community_faq",),
    )
    recap = _base_signal(
        client_id="yellow",
        kind="recap_metric",
        number=863,
    )
    recap["metrics"] = ({
        "metric_code": "content_clicks",
        "unit": "count",
        "observed": False,
        "value": None,
    },)
    signals[3] = bind_harmony_signal_payload(recap)
    snapshot = _trusted_snapshot(signals)
    yellow = next(item for item in snapshot.rounds if item.client_id == "yellow")
    assert yellow.status == HarmonyRoundStatus.NEEDS_ALIGNMENT
    assert yellow.handoff is None
    assert yellow.consensus_topic_codes == ()


def test_future_recap_and_nonzero_actions_are_fail_closed() -> None:
    future_recap = _base_signal(
        client_id="babylon",
        kind="recap_metric",
        number=870,
    )
    future_recap["period_end"] = OBSERVED_AT
    future_recap = bind_harmony_signal_payload(future_recap)
    with pytest.raises(ValidationError, match="recap_invalid"):
        _input([future_recap])

    publication = _base_signal(
        client_id="babylon",
        kind="community_demand",
        number=871,
    )
    publication["automatic_publication"] = True
    publication = bind_harmony_signal_payload(publication)
    with pytest.raises(ValidationError):
        _input([publication])

    paid = _base_signal(
        client_id="babylon",
        kind="community_demand",
        number=872,
    )
    paid["max_cost_microusd"] = 1
    paid = bind_harmony_signal_payload(paid)
    with pytest.raises(ValidationError):
        _input([paid])

    harmony_input = _input([_base_signal(
        client_id="babylon",
        kind="community_demand",
        number=873,
    )])
    for override in (
        {"environment": "production"},
        {"capability": "submit_official_source"},
        {"audience": "another_audience"},
    ):
        with pytest.raises(ValidationError):
            _attestation(
                harmony_input.signals[0],
                number=873,
                overrides=override,
            )


def test_round_rejects_a_hash_valid_speaker_role_swap() -> None:
    snapshot = _trusted_snapshot(_client_signals("squid", start=880))
    payload = snapshot.model_dump(mode="json")
    squid = next(item for item in payload["rounds"] if item["client_id"] == "squid")
    first_turn = squid["turns"][0]
    first_turn["speaker"] = "grok_bot"
    turn_body = {
        key: value for key, value in first_turn.items() if key != "turn_sha256"
    }
    first_turn["turn_sha256"] = hashlib.sha256(json.dumps(
        turn_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    with pytest.raises(ValidationError, match="turn_role_binding"):
        HarmonySnapshot.model_validate(payload)


def test_handoff_rejects_hash_valid_source_and_context_swap() -> None:
    snapshot = _trusted_snapshot(_client_signals("squid", start=890))
    payload = snapshot.model_dump(mode="json")
    squid = next(item for item in payload["rounds"] if item["client_id"] == "squid")
    handoff = squid["handoff"]
    source = handoff["source_signal_ids"][0]
    context = handoff["context_signal_ids"][0]
    handoff["source_signal_ids"][0] = context
    handoff["context_signal_ids"][0] = source
    handoff_body = {
        key: value for key, value in handoff.items() if key != "scope_sha256"
    }
    handoff["scope_sha256"] = hashlib.sha256(json.dumps(
        handoff_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    with pytest.raises(ValidationError, match="handoff_signal_binding"):
        HarmonySnapshot.model_validate(payload)


def test_dashboard_has_five_sections_and_discloses_contract_only_state() -> None:
    snapshot = build_harmony_snapshot(
        _input(_client_signals("yellow", start=900)),
        _profiles(),
        observed_at=OBSERVED_AT,
    )
    dashboard = render_harmony_dashboard(snapshot)
    assert sum(line.startswith("## ") for line in dashboard.splitlines()) == 5
    assert "## 3. 고객별 구조화 대화 · 기획" in dashboard
    assert "live Harmony adapter: `0`" in dashboard
    assert "실제 봇 대화가 아닙니다" in dashboard
    assert "caller JSON의 client/producer/release/config/receipt 자기진술" in dashboard
    assert "범위 승인 대기: 0건" in dashboard
    assert "Production/DB/provider/Buzz/publication 호출: 0" in dashboard
    assert snapshot.snapshot_sha256 in dashboard
