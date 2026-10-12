"""Two independent Git builds; pure contracts and fake transactions only."""
from dataclasses import replace
import hashlib
from unittest.mock import Mock

import pytest

from core.content_ops.release_pair import release_pair_sha256, valid_release_pair
from core.content_ops.final_publication_confirmation import FinalConfirmationError, FinalConfirmationSigner
from core.content_ops.final_decision_owner import FinalDecisionOwnerError, PostgresFinalDecisionOwner
from core.content_ops.final_publication_owner import PostgresFinalPublicationOwner
from tests.test_content_ops_final_publication_confirmation import snapshot, ROOM, MESSAGE, BOT, HUMAN, NOW
from tests import test_content_ops_final_publication_owner as publication_fixture

E, B = "a" * 40, "b" * 40


def test_digest_has_stable_versioned_role_order_and_no_authority():
    expected = hashlib.sha256(("content-ops-release-pair@1\nengine=" + E
                              + "\ncallback=" + B + "\n").encode("ascii")).hexdigest()
    assert release_pair_sha256(E, B) == expected
    assert valid_release_pair(E, B, expected)
    assert not valid_release_pair(B, E, expected)
    assert release_pair_sha256(E, B) != release_pair_sha256(B, E)


@pytest.mark.parametrize("bad", [None, True, "", "A" * 40, "a" * 39, "a" * 41, E + "\n"])
def test_malformed_or_normalized_away_release_is_rejected(bad):
    for e, b in ((bad, B), (E, bad)):
        with pytest.raises(ValueError, match="content_ops_release_pair_invalid"):
            release_pair_sha256(e, b)
        assert not valid_release_pair(e, b, "c" * 64)


@pytest.mark.parametrize("owner_class", [PostgresFinalDecisionOwner, PostgresFinalPublicationOwner])
@pytest.mark.parametrize("change", ["engine", "callback", "swapped", "digest", "missing"])
def test_owner_rejects_unbound_pair_before_factory_or_reader(owner_class, change):
    s = snapshot()
    args = dict(release_sha=s.release_sha, callback_release_sha=s.callback_release_sha,
                release_pair_sha256=s.release_pair_sha256, enabled=True)
    if change == "engine": args["release_sha"] = E
    if change == "callback": args["callback_release_sha"] = B
    if change == "swapped":
        args["release_sha"], args["callback_release_sha"] = args["callback_release_sha"], args["release_sha"]
    if change == "digest": args["release_pair_sha256"] = "0" * 64
    if change == "missing": args["release_pair_sha256"] = None
    factory, reader = Mock(), Mock()
    with pytest.raises(FinalDecisionOwnerError, match="configuration_invalid"):
        owner_class(factory, reader, **args)
    factory.assert_not_called()
    reader.assert_not_called()


@pytest.mark.parametrize("role", ["engine", "callback"])
def test_current_owner_rejects_other_well_formed_pair_before_mutation(role):
    s = snapshot()
    changed = replace(s, **({"release_sha": E} if role == "engine" else {"callback_release_sha": B}))
    changed = replace(changed, release_pair_sha256=release_pair_sha256(
        changed.release_sha, changed.callback_release_sha))
    factory = Mock()
    owner = PostgresFinalDecisionOwner(factory, lambda *_: changed, release_sha=s.release_sha,
        callback_release_sha=s.callback_release_sha, release_pair_sha256=s.release_pair_sha256, enabled=True)
    with pytest.raises(FinalDecisionOwnerError, match="read_unknown"):
        owner.read_confirmation(ROOM, MESSAGE, BOT, HUMAN)
    factory.assert_not_called()


@pytest.mark.parametrize("role", ["engine", "callback"])
def test_hmac_binds_both_independent_releases_even_with_new_valid_digest(role):
    s = snapshot()
    signer = FinalConfirmationSigner(b"f" * 32)
    token = signer.issue(s, "p", ROOM, now=NOW, expires_at=NOW + 900)
    changed = replace(s, **({"release_sha": E} if role == "engine" else {"callback_release_sha": B}))
    changed = replace(changed, release_pair_sha256=release_pair_sha256(
        changed.release_sha, changed.callback_release_sha))
    assert changed.digest() != s.digest()
    with pytest.raises(FinalConfirmationError, match="stale_or_invalid"):
        signer.verify(token, changed, ROOM, now=NOW)


def test_real_owner_rpc_passes_engine_and_callback_in_different_role_slots():
    fixture = publication_fixture.FinalPublicationOwnerTest()
    fixture.setUp()
    s = fixture.s
    assert s.release_sha != s.callback_release_sha
    fixture.owner().apply_final_decision(**fixture.request())
    query, args = fixture.connections[0].cursor_value.queries[1]
    assert query.count("%s") == len(args) == 14
    assert args[9] == s.release_sha
    assert args[12:] == (s.callback_release_sha, s.release_pair_sha256)
    assert fixture.connections[0].committed


def test_engine_sha_cannot_impersonate_callback_runtime():
    fixture = publication_fixture.FinalPublicationOwnerTest()
    fixture.setUp()
    with pytest.raises(FinalDecisionOwnerError, match="arguments_invalid"):
        fixture.owner().apply_final_decision(**{**fixture.request(), "runtime_release_sha": fixture.s.release_sha})
    assert fixture.connections == []
