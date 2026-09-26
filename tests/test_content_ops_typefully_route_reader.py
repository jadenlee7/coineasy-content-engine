"""Synthetic official GET-only detail read; no real Typefully credential."""

import asyncio
import unittest

import httpx

from core.content_ops.typefully_route_reader import (
    TypefullyRouteReaderError, TypefullySocialSetDetailReader,
)
from tests.test_content_ops_publication_route_verification import fixture


TOKEN = "synthetic_read_only_bearer"


class TypefullyRouteReaderTest(unittest.TestCase):
    def setUp(self):
        self.values = fixture()
        self.social_set_id = self.values["expected"].typefully_social_set_id
        self.calls = []

    def provider(self, request):
        self.calls.append(request)
        return httpx.Response(200, json=self.values["typefully_social_set"])

    def reader(self, *, token=TOKEN, enabled=True, provider=None):
        return TypefullySocialSetDetailReader(bearer_token=token,
            enabled=enabled,
            transport=httpx.MockTransport(provider or self.provider))

    def test_default_off_and_invalid_inputs_have_zero_io(self):
        with self.assertRaisesRegex(TypefullyRouteReaderError, "disabled"):
            asyncio.run(self.reader(enabled=False)(self.social_set_id))
        for token, social_set_id in (("bad token", self.social_set_id),
                                     (TOKEN, 0), (TOKEN, True)):
            with self.subTest(token=token, social_set_id=social_set_id), \
                    self.assertRaisesRegex(TypefullyRouteReaderError,
                                           "configuration_invalid"):
                asyncio.run(self.reader(token=token)(social_set_id))
        self.assertEqual(self.calls, [])

    def test_one_exact_read_only_detail_request(self):
        result = asyncio.run(self.reader()(self.social_set_id))
        self.assertEqual(result, self.values["typefully_social_set"])
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url),
            f"https://api.typefully.com/v2/social-sets/{self.social_set_id}/")
        self.assertEqual(request.headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(request.content, b"")

    def test_provider_failures_are_bounded_and_redacted(self):
        cases = (
            lambda _: httpx.Response(401, json={"detail": "private secret"}),
            lambda _: httpx.Response(302, headers={"Location": "https://example.com"}),
            lambda _: httpx.Response(200, content=b"<private secret>"),
            lambda _: httpx.Response(200, content=b"a" * 32769),
            lambda _: httpx.Response(200, json={"id": self.social_set_id + 1}),
        )
        for provider in cases:
            with self.subTest(provider=provider):
                self.calls.clear()
                def observed(request):
                    self.calls.append(request)
                    return provider(request)
                with self.assertRaises(TypefullyRouteReaderError) as caught:
                    asyncio.run(self.reader(provider=observed)(self.social_set_id))
                self.assertNotIn("private secret", str(caught.exception))
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0].method, "GET")


if __name__ == "__main__":
    unittest.main()
