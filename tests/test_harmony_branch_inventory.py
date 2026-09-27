from __future__ import annotations

import ast
from copy import deepcopy
import json
import os
import shutil
import sys
import tempfile

import pytest

from test_harmony_preview_proof_runner import (
    CHILD_REF, MANAGEMENT_TOKEN, PARENT_REF, RUNNER, FakeRunner, _args,
    _clock, _fake_exact_checkout, _valid_preview_branch_row,
)


def parent_row(**overrides):
    # Secret-free identity projection observed on 2026-09-07 with CLI 2.116.
    # This historical fixture does not claim a fresh scoped-PAT observation.
    row = {
        "id": "58fe9e3e-f943-40b1-bd7a-5072ff14e087",
        "name": "main",
        "project_ref": PARENT_REF,
        "parent_project_ref": PARENT_REF,
        "is_default": True,
        "persistent": False,
        "status": "FUNCTIONS_DEPLOYED",
    }
    return dict(row, **overrides)


def envelope(*rows):
    return {"branches": list(rows), "message": ""}


@pytest.fixture(autouse=True)
def fake_token(monkeypatch):
    monkeypatch.setenv(RUNNER.MANAGEMENT_TOKEN_SOURCE_ENV, MANAGEMENT_TOKEN)


@pytest.fixture(scope="module")
def watchdog_parser(tmp_path_factory):
    # Compile the actual generated watchdog's two pure functions; never run its
    # top-level CLI, control thread or deadline loop in a parser unit test.
    fake = FakeRunner()
    proof = RUNNER.HarmonyPreviewProof(
        _args(tmp_path_factory.mktemp("inventory-parser")), runner=fake,
    )
    proof.management_token = MANAGEMENT_TOKEN
    proof.management_home = tempfile.mkdtemp(prefix="harmony-supabase-home-")
    proof.management_home_cleanup_confirmed = False
    proof._arm_watchdog("hc-proof-" + "a" * 12 + "-20260828000000-" + "d" * 12)
    try:
        command = next(cmd for cmd in fake.commands if "-c" in cmd)
        tree = ast.parse(command[command.index("-c") + 1])
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"valid_project_ref", "parse_preview_branch_list"}]
        assert len(nodes) == 2
        namespace = {"WatchdogFenceError": ValueError}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "watchdog-parser", "exec"), namespace)
        yield namespace["parse_preview_branch_list"]
    finally:
        proof._cancel_watchdog()
        proof._clear_management_home()
        proof.management_token = ""


VALID = [
    (envelope(), []),
    (envelope(parent_row()), []),
    (envelope(_valid_preview_branch_row()), [CHILD_REF]),
    (envelope(parent_row(), _valid_preview_branch_row()), [CHILD_REF]),
    (envelope(_valid_preview_branch_row(), parent_row()), [CHILD_REF]),
    (envelope(parent_row(), _valid_preview_branch_row(),
              _valid_preview_branch_row(id="branch-id-2", name="other", project_ref="z" * 20)),
     [CHILD_REF, "z" * 20]),
    # Nested metadata must never promote a second identity to a CLI target.
    (envelope(parent_row(metadata=_valid_preview_branch_row())), []),
    (envelope(_valid_preview_branch_row(metadata=parent_row())), [CHILD_REF]),
    (envelope(_valid_preview_branch_row(status=parent_row())), [CHILD_REF]),
]


@pytest.mark.parametrize(("payload", "expected_refs"), VALID)
def test_foreground_watchdog_inventory_projection_agree(watchdog_parser, payload, expected_refs):
    before = deepcopy(payload)
    foreground = RUNNER.extract_preview_branch_list(payload, PARENT_REF)
    watchdog = watchdog_parser(payload)
    assert [row.ref for row in foreground] == expected_refs
    assert [(row.branch_id, row.name, row.ref, row.parent_project_ref, row.is_default)
            for row in foreground] == [
        (row["id"], row["name"], row["project_ref"], row["parent_project_ref"], row["is_default"])
        for row in watchdog
    ]
    assert all(row.ref != PARENT_REF for row in foreground)
    assert payload == before


BAD_ROWS = [
    None, {},
    parent_row(is_default=False), parent_row(is_default="true"),
    parent_row(is_default=1), parent_row(is_default=None),
    parent_row(name="MAIN"), parent_row(name="preview"), parent_row(name=" main"),
    parent_row(id=""), parent_row(parent_project_ref="z" * 20),
    parent_row(project_ref="z" * 20),
    _valid_preview_branch_row(is_default=True),
    _valid_preview_branch_row(is_default="false"),
    _valid_preview_branch_row(name="main"),
    _valid_preview_branch_row(project_ref=PARENT_REF),
    _valid_preview_branch_row(project_ref="Z" * 20),
    _valid_preview_branch_row(parent_project_ref="z" * 20),
    *[{key: value for key, value in parent_row().items() if key != missing}
      for missing in ("id", "name", "project_ref", "parent_project_ref", "is_default")],
]
BAD_ENVELOPES = [None, [], {}, {"branches": []},
                 {"branches": [], "message": "error"},
                 {"branches": [], "message": "", "extra": []},
                 {"branches": {}, "message": ""}]
COLLISIONS = [
    [parent_row(), parent_row()],
    [parent_row(), parent_row(id="other-parent-id")],
    [parent_row(), _valid_preview_branch_row(id=parent_row()["id"])],
    [_valid_preview_branch_row(), _valid_preview_branch_row(project_ref="z" * 20, name="other")],
    [_valid_preview_branch_row(), _valid_preview_branch_row(id="other-id", name="other")],
    [_valid_preview_branch_row(), _valid_preview_branch_row(id="other-id", project_ref="z" * 20)],
]
INVALID = BAD_ENVELOPES + [envelope(row) for row in BAD_ROWS]
INVALID += [envelope(parent_row(), _valid_preview_branch_row(), row) for row in BAD_ROWS]
INVALID += [envelope(*rows) for pair in COLLISIONS for rows in (pair, pair[::-1])]


@pytest.mark.parametrize("payload", INVALID)
def test_foreground_watchdog_reject_whole_ambiguous_inventory(watchdog_parser, payload):
    with pytest.raises(RUNNER.ProofError) as foreground:
        RUNNER.extract_preview_branch_list(payload, PARENT_REF)
    with pytest.raises(ValueError) as watchdog:
        watchdog_parser(payload)
    assert str(foreground.value) == "supabase_" + str(watchdog.value)


class InventoryRunner(FakeRunner):
    def __init__(self, bad_stage=None, *, create_ambiguous=False):
        super().__init__(create_ambiguous=create_ambiguous)
        self.bad_stage = bad_stage

    def run_json(self, command, **kwargs):
        result = super().run_json(command, **kwargs)
        if "branches" in command and "list" in command:
            result["branches"].insert(0, parent_row())
            if self.list_calls == self.bad_stage:
                # The invalid inventory cannot supply a new mutation target or
                # absence receipt. All later cleanup reads fail too.
                result["branches"].append(parent_row(is_default=False))
                self.bad_stage += 1
        return result


@pytest.mark.parametrize(("bad_stage", "create_ambiguous"),
                         [(None, False), (1, False), (2, False), (4, False), (2, True)])
def test_foreground_lifecycle_never_targets_parent(tmp_path, monkeypatch, bad_stage, create_ambiguous):
    monkeypatch.setattr(RUNNER, "verify_exact_checkout", _fake_exact_checkout)
    fake = InventoryRunner(bad_stage, create_ambiguous=create_ambiguous)
    proof = RUNNER.HarmonyPreviewProof(
        _args(tmp_path), runner=fake, opener=fake.open_endpoint,
        sleeper=lambda _: None, clock=_clock(),
    )
    try:
        receipt, code = proof.run()
        deletes = [cmd for cmd in fake.commands if "branches" in cmd and "delete" in cmd]
        assert all(cmd[cmd.index("delete") + 1] == "branch-id-1" for cmd in deletes)
        if bad_stage is None:
            assert code == 0 and receipt["ok"]
            assert fake.events.count("branch_create") == 1
            assert len(deletes) == 1
            assert receipt["cleanup"]["watchdog_cancelled"]
        else:
            assert code == 1 and not receipt["ok"]
            if bad_stage == 1:
                assert deletes == []
                assert "branch_create" not in fake.events
                assert "watchdog_armed" not in fake.events
            elif create_ambiguous:
                assert deletes == []
                assert receipt["cleanup"]["watchdog_cancelled"] is False
            else:
                # Existing cleanup authority for the identity bound by CREATE
                # is preserved. Invalid LIST must not assert later absence.
                assert len(deletes) == 1
                assert receipt["cleanup"]["absence_confirmations"] == 0
                assert receipt["cleanup"]["watchdog_cancelled"] is False
    finally:
        # Retained fake watchdogs need explicit local fixture teardown.
        if proof.watchdog is not None:
            proof._cancel_watchdog()
        proof.management_token = ""


@pytest.mark.parametrize("mode", ["parent-only", "target", "id-alias", "late-invalid", "wrong-parent"])
def test_real_watchdog_inventory_cleanup(tmp_path, monkeypatch, mode):
    branch_name = "hc-proof-" + "a" * 12 + "-20260828000000-" + "d" * 12
    target = _valid_preview_branch_row(name=branch_name)
    rows = [parent_row()]
    if mode != "parent-only":
        rows.append(target)
    if mode == "id-alias":
        target["id"] = parent_row()["id"]
    elif mode == "late-invalid":
        rows.append(parent_row(is_default=False))
    elif mode == "wrong-parent":
        target["parent_project_ref"] = "z" * 20
    events = tmp_path / "events.jsonl"
    deleted = tmp_path / "deleted"
    cli = tmp_path / "fake-supabase"
    cli.write_text(f'''#!{sys.executable}
import json
from pathlib import Path
import sys
events = Path({str(events)!r})
deleted = Path({str(deleted)!r})
args = sys.argv[1:]
with events.open("a") as out:
    out.write(json.dumps(args) + "\\n")
if "list" in args:
    rows = {[parent_row()]!r} if deleted.exists() else {rows!r}
    print(json.dumps({{"branches": rows, "message": ""}}))
elif "delete" in args:
    if args[args.index("delete") + 1] != "branch-id-1":
        raise SystemExit(9)
    deleted.touch()
else:
    raise SystemExit(2)
''')
    cli.chmod(0o700)
    monkeypatch.setattr(RUNNER, "WATCHDOG_SECONDS", 0)
    # Leave headroom for four process-group-fenced CLI launches under suite load.
    monkeypatch.setattr(RUNNER, "WATCHDOG_RECONCILE_SECONDS", 2 if mode == "parent-only" else 10)
    monkeypatch.setattr(RUNNER, "WATCHDOG_POLL_INTERVAL_SECONDS", 0.01)
    args = _args(tmp_path)
    args.supabase = str(cli)
    proof = RUNNER.HarmonyPreviewProof(args, runner=RUNNER.ProcessRunner())
    proof.management_token = MANAGEMENT_TOKEN
    proof.management_home = tempfile.mkdtemp(prefix="harmony-supabase-home-")
    proof.management_home_cleanup_confirmed = False
    proof._arm_watchdog(branch_name)
    root = proof.watchdog_control_dir
    watchdog = proof.watchdog
    proof._detach_watchdog()
    proof._clear_management_home()
    try:
        watchdog.wait(timeout=15)
        calls = [json.loads(line) for line in events.read_text().splitlines()]
        deletes = [call for call in calls if "delete" in call]
        if mode == "target":
            assert len(deletes) == 1 and deleted.exists()
            # Confirm absence only from three later parent-only LISTs.
            assert sum("list" in call for call in calls) == 4
            assert watchdog.returncode == 0
        else:
            assert deletes == [] and not deleted.exists()
            if mode != "parent-only":
                assert len(calls) == 1
        if mode in {"target", "parent-only"}:
            assert not os.path.lexists(root)
        else:
            # Existing fail-closed watchdog behavior does not claim release on
            # a fence error; the fixture removes only its own fake control root.
            assert watchdog.returncode != 0
            assert os.path.lexists(root)
    finally:
        if watchdog.poll() is None:
            proof.runner.terminate_process_group(watchdog, code="inventory_test_cleanup")
        proof.watchdog = None
        proof.management_token = ""
        proof._close_watchdog_control_socket()
        if os.path.lexists(root):
            shutil.rmtree(root)
