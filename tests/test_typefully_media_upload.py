"""Synthetic Typefully media allocation/upload with no external network."""

import hashlib
import json
import unittest

import httpx

from core.publications.typefully_media_upload import (
    TypefullyMediaUploadError,
    upload_typefully_png_once,
)


KEY = "synthetic_typefully_write_key_00000001"
MEDIA_ID = "99999999-9999-4999-8999-999999999999"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01" * 2 + b"synthetic"
SHA = hashlib.sha256(PNG).hexdigest()
UPLOAD_URL = ("https://synthetic-bucket.s3.us-east-1.amazonaws.com/news-card.png"
              "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
              "&X-Amz-Credential=test%2Fscope&X-Amz-Signature=" + "a" * 64)


class TypefullyMediaUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_png_upload_never_leaks_typefully_key_to_s3(self):
        api_calls, put_calls = [], []

        def api(request):
            api_calls.append(request)
            return httpx.Response(201, json={"media_id": MEDIA_ID,
                                             "upload_url": UPLOAD_URL,
                                             "private_url": "do not echo"})

        def storage(request):
            put_calls.append(request)
            return httpx.Response(204)

        result = await upload_typefully_png_once(
            social_set_id=12345, api_key=KEY, png_bytes=PNG, expected_sha256=SHA,
            api_transport=httpx.MockTransport(api),
            upload_transport=httpx.MockTransport(storage),
        )
        self.assertEqual(result, {"source": "typefully_media_upload_v2",
                                  "social_set_id": 12345, "media_id": MEDIA_ID,
                                  "uploaded_bytes_sha256": SHA})
        self.assertEqual(len(api_calls), 1)
        self.assertEqual(api_calls[0].method, "POST")
        self.assertEqual(str(api_calls[0].url),
                         "https://api.typefully.com/v2/social-sets/12345/media/upload")
        self.assertEqual(api_calls[0].headers.get("authorization"), f"Bearer {KEY}")
        self.assertEqual(json.loads(api_calls[0].content),
                         {"file_name": "news-card.png"})
        self.assertEqual(len(put_calls), 1)
        self.assertEqual(put_calls[0].method, "PUT")
        self.assertEqual(put_calls[0].content, PNG)
        self.assertNotIn("authorization", put_calls[0].headers)
        self.assertNotIn("content-type", put_calls[0].headers)
        self.assertNotIn("upload_url", result)
        self.assertNotIn("private_url", str(result))

    async def test_bad_bytes_or_key_stop_before_provider_call(self):
        calls = []

        def api(request):
            calls.append(request)
            return httpx.Response(201, json={})

        for overrides in ({"png_bytes": b"not png"},
                          {"expected_sha256": "f" * 64},
                          {"social_set_id": True},
                          {"api_key": "short"}):
            inputs = dict(social_set_id=12345, api_key=KEY, png_bytes=PNG,
                          expected_sha256=SHA, api_transport=httpx.MockTransport(api))
            inputs.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(TypefullyMediaUploadError):
                await upload_typefully_png_once(**inputs)
        self.assertEqual(calls, [])

    async def test_untrusted_presigned_url_never_receives_png(self):
        for url in ("http://synthetic-bucket.s3.amazonaws.com/file?X-Amz-Signature=abc",
                    "https://s3.amazonaws.com.evil.example/file?X-Amz-Signature=abc",
                    "https://127.0.0.1/file?X-Amz-Signature=abc",
                    UPLOAD_URL + "#fragment",
                    UPLOAD_URL.replace("X-Amz-Signature=", "Signature=")):
            put_calls = []

            def api(_request):
                return httpx.Response(201, json={"media_id": MEDIA_ID,
                                                 "upload_url": url})

            def storage(request):
                put_calls.append(request)
                return httpx.Response(204)

            with self.subTest(url=url), self.assertRaisesRegex(
                TypefullyMediaUploadError, "typefully_upload_url_invalid",
            ):
                await upload_typefully_png_once(
                    social_set_id=12345, api_key=KEY, png_bytes=PNG,
                    expected_sha256=SHA, api_transport=httpx.MockTransport(api),
                    upload_transport=httpx.MockTransport(storage),
                )
            self.assertEqual(put_calls, [])

    async def test_allocation_or_put_uncertainty_has_no_retry(self):
        api_calls, put_calls = [], []

        def api(request):
            api_calls.append(request)
            return httpx.Response(201, json={"media_id": MEDIA_ID,
                                             "upload_url": UPLOAD_URL})

        def storage(request):
            put_calls.append(request)
            raise httpx.ReadTimeout("lost PUT response")

        with self.assertRaisesRegex(TypefullyMediaUploadError,
                                    "typefully_media_put_unknown") as caught:
            await upload_typefully_png_once(
                social_set_id=12345, api_key=KEY, png_bytes=PNG,
                expected_sha256=SHA, api_transport=httpx.MockTransport(api),
                upload_transport=httpx.MockTransport(storage),
            )
        self.assertEqual(len(api_calls), 1)
        self.assertEqual(len(put_calls), 1)
        self.assertEqual(caught.exception.media_id, MEDIA_ID)
        self.assertNotIn(UPLOAD_URL, str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

        denied = []

        def rate_limited(request):
            denied.append(request)
            return httpx.Response(429, json={"secret": "never echo"})

        with self.assertRaisesRegex(TypefullyMediaUploadError,
                                    "typefully_media_allocation_unavailable") as caught:
            await upload_typefully_png_once(
                social_set_id=12345, api_key=KEY, png_bytes=PNG,
                expected_sha256=SHA, api_transport=httpx.MockTransport(rate_limited),
                upload_transport=httpx.MockTransport(storage),
            )
        self.assertEqual(len(denied), 1)
        self.assertNotIn("never echo", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
