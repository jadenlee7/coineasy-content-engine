"""Bounded trusted final-card projections; no real credentials or provider I/O."""
import copy
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

from core.content_ops.final_snapshot_reader import (
    FINAL_SNAPSHOT_QUERY, FinalSnapshotReaderError, PostgresFinalSnapshotReader,
)
from tests.test_content_ops_final_card_courier import prepared, RELEASE

BINDS = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)


def projection():
    snapshot = prepared().snapshot
    review = snapshot.review
    data = {key: getattr(snapshot, key) for key in ("review_id", "card_id", "parent_card_id",
        "reviewer_id", "source_check_actor_id", "claims_check_actor_id", "review_epoch",
        "source_check_epoch", "claims_check_epoch", "version_fingerprint", "approval_count",
        "publication_count", "release_sha", "telegram_route_binding", "typefully_route_binding",
        "decision_mode")}
    data.update({key: getattr(review, key) for key in ("workspace_id", "client_id", "content_item_id",
        "content_version_id", "source_url", "source_published_at", "telegram_copy", "x_copy",
        "banner_sha256")})
    return {**data, "read_only": True, "snapshot_sha256": snapshot.digest()}


class Factory:
    def __init__(self, rows=None, *, autocommit=False, read_only=True):
        self.rows = [(projection(),)] if rows is None else rows
        self.autocommit, self.read_only = autocommit, read_only
        self.calls, self.queries = 0, []
        self.closed = False
    def __call__(self): self.calls += 1; return self
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def cursor(self): return self.Cursor(self)
    class Cursor:
        def __init__(self, factory): self.factory = factory
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, args=None): self.factory.queries.append((query, args))
        def fetchone(self): return ("5000", "2000")
        def fetchall(self): return self.factory.rows


class FinalSnapshotReaderTests(unittest.TestCase):
    def reader(self, factory, enabled=True):
        return PostgresFinalSnapshotReader(factory, release_sha=RELEASE, enabled=enabled)

    def test_default_off_and_bad_bindings_have_zero_io(self):
        factory = Factory()
        with self.assertRaisesRegex(FinalSnapshotReaderError, "disabled"):
            self.reader(factory, enabled=False)(*BINDS)
        with self.assertRaisesRegex(FinalSnapshotReaderError, "arguments_invalid"):
            self.reader(factory)("';select private.queue();--", *BINDS[1:])
        self.assertEqual(factory.calls, 0)

    def test_exact_final_and_parent_ids_reconstruct_stored_snapshot(self):
        factory = Factory()
        snapshot = self.reader(factory)(*BINDS)
        expected = prepared().snapshot
        self.assertEqual(snapshot.card_id, expected.card_id)
        self.assertEqual(snapshot.parent_card_id, expected.parent_card_id)
        self.assertNotEqual(snapshot.card_id, snapshot.parent_card_id)
        self.assertEqual(snapshot.digest(), expected.digest())
        self.assertEqual(snapshot.typefully_x_destination_label, "@yellow__korea")
        self.assertEqual(factory.queries[-1], (FINAL_SNAPSHOT_QUERY, (*BINDS, RELEASE)))
        self.assertEqual(factory.calls, 1)
        self.assertTrue(factory.closed)

    def test_timezone_equivalence_and_label_case_are_canonical(self):
        data = projection()
        instant = datetime.fromisoformat(data["source_published_at"])
        from datetime import timezone
        data["source_published_at"] = instant.astimezone(timezone(timedelta(hours=9))).isoformat()
        snapshot = self.reader(Factory([(data,)]))(*BINDS)
        self.assertTrue(snapshot.review.source_published_at.endswith(".000000Z"))
        self.assertEqual(snapshot.digest(), prepared().snapshot.digest())

    def test_parent_card_swap_digest_and_microsecond_drift_reject(self):
        data = projection()
        changes = (("parent_card_id", data["card_id"]),
            ("parent_card_id", "99999999-9999-4999-8999-999999999999"),
            ("snapshot_sha256", "f" * 64), ("release_sha", "b" * 40),
            ("source_check_epoch", 1), ("claims_check_actor_id", "99999999-9999-4999-8999-999999999999"),
            ("approval_count", 1), ("publication_count", 1))
        for key, value in changes:
            modified = copy.deepcopy(data); modified[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(FinalSnapshotReaderError, "unknown"):
                self.reader(Factory([(modified,)]))(*BINDS)
        data["source_published_at"] = (datetime.fromisoformat(data["source_published_at"])
                                      + timedelta(microseconds=1)).isoformat()
        with self.assertRaisesRegex(FinalSnapshotReaderError, "unknown"):
            self.reader(Factory([(data,)]))(*BINDS)

    def test_missing_ambiguous_or_unexpected_projection_rejects(self):
        cases = ([], [(projection(),), (projection(),)], [(None,)],
            [({**projection(), "secret": "do not reflect"},)],
            [({**projection(), "read_only": False},)])
        for rows in cases:
            with self.subTest(rows=len(rows)), self.assertRaisesRegex(FinalSnapshotReaderError, "unknown"):
                self.reader(Factory(rows))(*BINDS)

    def test_mutation_connection_rejected_before_queries(self):
        for flags in ({"autocommit": True}, {"read_only": False}):
            factory = Factory(**flags)
            with self.assertRaisesRegex(FinalSnapshotReaderError, "unknown"):
                self.reader(factory)(*BINDS)
            self.assertEqual(factory.queries, [])

    def test_sql_preserves_bounded_select_and_registered_identity_boundary(self):
        self.assertTrue(FINAL_SNAPSHOT_QUERY.endswith("limit 2"))
        for required in ("identity.active", "reviewer.active", "parent.active",
                "source_check.actor_id=principal.id", "claims_check.actor_id=principal.id",
                "c.id<>c.parent_card_id", "p.part_index=3", "slot.kst_date=", "j.status='succeeded'"):
            self.assertIn(required, FINAL_SNAPSHOT_QUERY)
        self.assertNotIn("to_jsonb(", FINAL_SNAPSHOT_QUERY)
        self.assertNotIn("for update", FINAL_SNAPSHOT_QUERY.lower())
        self.assertNotIn("content_ops_review_candidate(", FINAL_SNAPSHOT_QUERY)
        self.assertEqual(FINAL_SNAPSHOT_QUERY.count("%s"), 5)

    def test_fixed_failure_does_not_echo_database_or_private_data(self):
        def forbidden(): raise RuntimeError("secret private URL or body")
        with self.assertRaises(FinalSnapshotReaderError) as error:
            self.reader(forbidden)(*BINDS)
        self.assertEqual(str(error.exception), "final_snapshot_reader_unknown")
        self.assertTrue(error.exception.__suppress_context__)
