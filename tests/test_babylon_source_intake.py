"""Offline unit coverage for Babylon's validated reply/retweet exclusion policy."""

from copy import deepcopy
import os
import re
import socket
from types import MappingProxyType
import unittest
from unittest.mock import patch

from core.automation.babylon_source_intake import (
    BabylonIntakeError,
    select_babylon_intake_posts,
)


_MISSING = object()
_CODES = {
    "previous_cursor_invalid",
    "collection_size",
    "post_shape",
    "post_id_format",
    "collection_unique_ids",
    "post_after_cursor",
    "post_classification_boolean",
}


def _post(post_id="101", **values):
    return {
        "id": post_id,
        "is_retweet": False,
        "is_reply": False,
        "is_quote": False,
        "text": "synthetic-private-content-never-in-errors",
        "media": [{"media_key": "fixture-image", "width": 1200}],
        "metrics": {"like_count": 4},
        **values,
    }


class BabylonSourceIntakeTests(unittest.TestCase):
    def setUp(self):
        self.blockers = [
            patch.object(socket, name, side_effect=AssertionError("network_forbidden"))
            for name in ("socket", "create_connection", "getaddrinfo")
        ]
        for blocker in self.blockers:
            blocker.start()
            self.addCleanup(blocker.stop)

    def assertRejected(self, posts, previous_cursor="100", code=None):
        with self.assertRaises(BabylonIntakeError) as raised:
            select_babylon_intake_posts(posts, previous_cursor)
        error = raised.exception
        self.assertIsInstance(error, ValueError)
        self.assertIn(error.code, _CODES)
        self.assertIsNotNone(re.fullmatch(r"[a-z_]{1,64}", error.code))
        self.assertEqual(str(error), error.code)
        self.assertNotIn("synthetic-private-content", str(error))
        if code is not None:
            self.assertEqual(error.code, code)

    def test_empty_collections_return_tuple_without_advancing_any_cursor(self):
        for posts in ([], ()):
            for cursor in (None, "0", "100", "9999999999999999999"):
                with self.subTest(posts=type(posts), cursor=cursor):
                    self.assertEqual(select_babylon_intake_posts(posts, cursor), ())

    def test_mixed_batch_preserves_accepted_objects_and_original_order(self):
        quote = _post("106", is_quote=True)
        ordinary = _post("102")
        reply = _post("110", is_reply=True)
        retweet = _post("109", is_retweet=True)
        both = _post("108", is_reply=True, is_retweet=True)
        quoted_reply = _post("107", is_reply=True, is_quote=True)
        posts = [quote, reply, ordinary, retweet, both, quoted_reply]
        original = deepcopy(posts)
        result = select_babylon_intake_posts(posts, "100")
        self.assertIs(type(result), tuple)
        self.assertEqual(result, (quote, ordinary))
        self.assertIs(result[0], quote)
        self.assertIs(result[1], ordinary)
        self.assertEqual(posts, original)

    def test_reply_only_retweet_only_and_combined_batches_are_empty(self):
        for flags in ({"is_reply": True}, {"is_retweet": True}, {"is_reply": True, "is_retweet": True}):
            with self.subTest(flags=flags):
                posts = [_post("101", **flags), _post("102", **flags)]
                original = deepcopy(posts)
                self.assertEqual(select_babylon_intake_posts(posts, "100"), ())
                self.assertEqual(posts, original)

    def test_quote_and_nested_media_are_kept_without_copy_or_mutation(self):
        media = [{"nested": {"variants": [{"bitrate": 1000}]}}]
        post = _post("101", is_quote=True, media=media, article={"fixture": [1, 2]})
        original = deepcopy(post)
        result = select_babylon_intake_posts((post,), "100")
        self.assertIs(result[0], post)
        self.assertIs(result[0]["media"], media)
        self.assertIs(result[0]["media"][0]["nested"], media[0]["nested"])
        self.assertEqual(post, original)

    def test_mapping_objects_are_supported_without_coercion(self):
        post = MappingProxyType(_post())
        self.assertIs(select_babylon_intake_posts([post], "100")[0], post)

    def test_all_three_flags_must_be_present_exact_booleans(self):
        for flag in ("is_reply", "is_retweet", "is_quote"):
            for invalid in (None, "false", "true", 0, 1, [], {}, _MISSING):
                with self.subTest(flag=flag, invalid=invalid):
                    post = _post()
                    if invalid is _MISSING:
                        del post[flag]
                    else:
                        post[flag] = invalid
                    self.assertRejected([post], code="post_classification_boolean")

    def test_malformed_excluded_rows_are_validated_before_filtering(self):
        for exclusion in ("is_reply", "is_retweet"):
            other = "is_retweet" if exclusion == "is_reply" else "is_reply"
            for invalid in (None, "false", 0, 1, _MISSING):
                with self.subTest(exclusion=exclusion, invalid=invalid):
                    post = _post("102", **{exclusion: True})
                    if invalid is _MISSING:
                        del post[other]
                    else:
                        post[other] = invalid
                    posts = [_post("101"), post]
                    original = deepcopy(posts)
                    self.assertRejected(posts, code="post_classification_boolean")
                    self.assertEqual(posts, original)
            for post, code in (
                (_post("invalid", **{exclusion: True}), "post_id_format"),
                (_post("100", **{exclusion: True}), "post_after_cursor"),
                (_post("102", **{exclusion: True, "is_quote": None}), "post_classification_boolean"),
            ):
                with self.subTest(exclusion=exclusion, code=code):
                    self.assertRejected([_post("101"), post], code=code)

    def test_duplicates_are_rejected_even_when_one_or_both_are_excluded(self):
        for first, second in (
            (_post(), _post()),
            (_post(is_reply=True), _post()),
            (_post(), _post(is_retweet=True)),
            (_post(is_reply=True), _post(is_retweet=True)),
        ):
            with self.subTest(first=first["is_reply"], second=second["is_retweet"]):
                self.assertRejected([first, second], code="collection_unique_ids")

    def test_collection_limit_is_applied_before_exclusion(self):
        for container in (list, tuple):
            with self.subTest(container=container):
                rows = container(_post(str(101 + index)) for index in range(100))
                result = select_babylon_intake_posts(rows, "100")
                self.assertEqual(len(result), 100)
                self.assertTrue(all(selected is original for selected, original in zip(result, rows)))
                excess = container(_post(str(101 + index), is_reply=True) for index in range(101))
                self.assertRejected(excess, code="collection_size")

    def test_only_list_or_tuple_collections_are_accepted(self):
        for value in (None, {}, set(), "[]", b"[]", 1, True, range(1), iter([_post()])):
            with self.subTest(value=type(value)):
                self.assertRejected(value, code="collection_size")

    def test_non_mapping_rows_fail_closed(self):
        for value in (None, [], (), "synthetic-private-content", 1, True):
            with self.subTest(value=type(value)):
                self.assertRejected([value], code="post_shape")

    def test_previous_cursor_must_be_none_or_ascii_digit_string_one_to_nineteen(self):
        for cursor in ("", "1" * 20, " 100", "100 ", "+100", "-100", "1.0", "١٠٠", "１００", 100, 0, True, False, [], {}):
            with self.subTest(cursor=cursor):
                self.assertRejected([], cursor, "previous_cursor_invalid")
        self.assertEqual(select_babylon_intake_posts([_post("0")], None)[0]["id"], "0")
        self.assertEqual(select_babylon_intake_posts([_post("9999999999999999999")], "1")[0]["id"], "9999999999999999999")

    def test_post_id_must_be_ascii_digit_string_one_to_nineteen(self):
        for value in (None, "", "1" * 20, " 101", "101 ", "+101", "-101", "1.01", "١٠١", "１０１", 101, True, False, [], {}):
            with self.subTest(value=value):
                self.assertRejected([_post(value)], code="post_id_format")
        missing = _post()
        del missing["id"]
        self.assertRejected([missing], code="post_id_format")

    def test_cursor_boundary_is_numeric_strict_and_not_sort_dependent(self):
        for value in ("0", "99", "100", "000100"):
            with self.subTest(value=value):
                self.assertRejected([_post(value)], code="post_after_cursor")
        posts = [_post("1000"), _post("101"), _post("999")]
        self.assertEqual(select_babylon_intake_posts(posts, "100"), tuple(posts))
        self.assertEqual(select_babylon_intake_posts([_post("10")], "9")[0]["id"], "10")
        self.assertRejected([_post("9")], "10", "post_after_cursor")

    def test_selection_has_no_file_network_or_environment_side_effects(self):
        before = dict(os.environ)
        posts = [_post("101"), _post("102", is_reply=True)]
        with patch("builtins.open", side_effect=AssertionError("file_io_forbidden")):
            selected = select_babylon_intake_posts(posts, "100")
        self.assertEqual(selected, (posts[0],))
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
