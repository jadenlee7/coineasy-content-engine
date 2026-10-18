"""Offline structural-minimum tests; these fixtures are not fact approval."""

import ast
import importlib.util
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from core.automation.source_announcement import meets_source_text_minimum


class SourceTextMinimumTests(unittest.TestCase):
    def setUp(self):
        for name in ("socket", "create_connection", "getaddrinfo"):
            blocker = patch.object(socket, name, side_effect=AssertionError("network_forbidden"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def test_ordinary_announcement_bodies_pass_without_keyword_requirements(self):
        for text in (
            "Babylon launches a mainnet upgrade for all network participants.",
            "Registration for the developer workshop opens next Monday morning.",
            "The documentation now explains how validators configure the new release.",
        ):
            with self.subTest(text=text):
                self.assertIs(meets_source_text_minimum(text), True)

    def test_decorative_bitcoin_stickfigure_is_rejected(self):
        for text in (
            "Bitcoin\n  o\n /|\\\n / \\",
            "BTC\n₿ ₿ ₿\n(╯°□°）╯︵ ┻━┻\nhttps://t.co/fixture",
            "Bitcoin " * 20,
            "Bitcoin BITCOIN bitcoin " * 20,
            "🚀✨🟧" * 100,
            "-_=|\\/()[]{}" * 100,
        ):
            with self.subTest(text=text):
                self.assertIs(meets_source_text_minimum(text), False)

    def test_links_and_mentions_cannot_supply_missing_body_words(self):
        for text in (
            "https://example.com/announcing/our/mainnet/upgrade/for/all/participants",
            "WWW.EXAMPLE.COM/announcing/our/mainnet/upgrade/for/all/participants",
            "@BabylonLabs @developers @validators @community @contributors @partners",
            "Bitcoin https://example.com/one/two/three/four/five @developers @validators",
            "https://a.example/one https://b.example/two https://c.example/three "
            "https://d.example/four https://e.example/five https://f.example/six",
            "＠BabylonLabs ＠developers ＠validators ＠community ＠contributors ＠partners",
        ):
            with self.subTest(text=text):
                self.assertIs(meets_source_text_minimum(text), False)

    def test_urls_mentions_and_decoration_do_not_change_a_sufficient_body(self):
        text = "Babylon launches a mainnet upgrade for all network participants."
        value = "🚀 @BabylonLabs " + text + "\nhttps://t.co/synthetic"
        self.assertIs(meets_source_text_minimum(value), True)
        self.assertEqual(value, "🚀 @BabylonLabs " + text + "\nhttps://t.co/synthetic")

    def test_exact_thresholds_and_casefolded_word_diversity(self):
        # Six words, four distinct words, exactly 32 alphabetic characters.
        self.assertTrue(meets_source_text_minimum("abcdef ghijkl mnopqr stuv abcdef stuv"))
        # Every threshold is independently necessary.
        self.assertFalse(meets_source_text_minimum("abcdef ghijkl mnopqr stuv abcdef stu"))
        self.assertFalse(meets_source_text_minimum("abcdefgh ijklmnop qrstuvwx yzabcdef ghijklmn"))
        self.assertFalse(meets_source_text_minimum("abcdefgh ABCDEFGH ijklmnop IJKLMNOP qrstuvwx QRSTUVWX"))

    def test_unicode_letters_and_compatibility_normalization(self):
        for text in (
            "Équipe présente une nouvelle documentation pour les validateurs participants.",
            "Ｅｑｕｉｐｅ presents the updated documentation for all network participants.",
            "바빌론 네트워크의 검증자들은 새로운 업그레이드의 기술문서를 공식웹사이트에서 확인하실수있습니다.",
        ):
            with self.subTest(text=text):
                self.assertTrue(meets_source_text_minimum(text))

    def test_numbers_and_emoji_never_satisfy_letter_count(self):
        self.assertFalse(meets_source_text_minimum("one two tri for fiv six " + "1234567890🚀" * 100))

    def test_invalid_values_and_string_subclasses_are_not_coerced(self):
        class StringSubclass(str):
            pass

        class NoCoercion:
            def __str__(self):
                raise AssertionError("must_not_coerce")

        for value in (None, False, True, 0, 1, [], {}, b"fixture", NoCoercion(),
                      StringSubclass("Babylon launches a mainnet upgrade for network participants.")):
            with self.subTest(kind=type(value).__name__):
                self.assertIs(meets_source_text_minimum(value), False)

    def test_short_factual_and_long_nonfactual_bodies_are_not_semantically_classified(self):
        self.assertFalse(meets_source_text_minimum("Mainnet is live."))
        # Deliberately passes: the function cannot attest that a body is factual.
        self.assertTrue(meets_source_text_minimum(
            "Imagine purple dragons dancing around imaginary mountains every morning."))

    def test_import_and_execution_have_only_pure_standard_library_dependencies(self):
        path = Path(__file__).resolve().parents[1] / "core/automation/source_announcement.py"
        tree = ast.parse(path.read_text())
        imports = [node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)]
        self.assertEqual(imports, ["re", "unicodedata"])
        self.assertFalse(any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)))
        spec = importlib.util.spec_from_file_location("_source_minimum_offline_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch("builtins.open", side_effect=AssertionError("file_io_forbidden")):
            spec.loader.exec_module(module)
            self.assertFalse(module.meets_source_text_minimum("Bitcoin"))


if __name__ == "__main__":
    unittest.main()
