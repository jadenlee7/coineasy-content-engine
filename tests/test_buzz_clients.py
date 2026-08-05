from __future__ import annotations

import hashlib
import struct
import unittest

import httpx

from core.buzz.clients import BuzzShadowClient
from core.buzz.errors import BuzzAdapterError
from core.buzz.models import BuzzShadowEvent


JOB_ID = "22222222-2222-4222-8222-222222222222"
TOKEN = "shadow-token-that-is-dedicated-and-long-enough"


def _event() -> BuzzShadowEvent:
    return BuzzShadowEvent(
        event_id="a" * 64,
        event_type="origintrail.batch_review_ready.v1",
        job_id=JOB_ID,
        review_ref=f"batch:{JOB_ID}",
        client_id="origintrail",
        agent_id="origintrail_client_agent",
        workflow_kind="official_source_nonurgent_pack",
        result_code="needs_review",
        model_tier="S",
        actual_cost_microusd=2200,
        finished_at="2026-08-03T12:00:00.000Z",
        source_url="https://x.com/origin_trail/status/2082883998829752783",
        studio_review_path=f"/?batch={JOB_ID}",
        headline_ko="OriginTrail 업데이트",
        summary_ko="검토용 요약",
    )


def _png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", 1_200, 630)
    )


class BuzzShadowBannerClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_exact_authenticated_bounded_png(self):
        content = _png()
        digest = hashlib.sha256(content).hexdigest()

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url),
                "https://console.example/api/buzz-shadow/origintrail/batch/"
                f"{JOB_ID}/banner.png",
            )
            self.assertEqual(request.headers["x-coineasy-buzz-key"], TOKEN)
            return httpx.Response(200, headers={
                "content-type": "image/png",
                "content-length": str(len(content)),
                "content-disposition": (
                    f'inline; filename="origintrail-review-{JOB_ID}.png"'
                ),
                "x-coineasy-content-sha256": digest,
            }, content=content)

        client = BuzzShadowClient(
            url="https://console.example/api/buzz-shadow/origintrail/batch",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        attachment = await client.banner(_event())
        self.assertEqual(attachment.media_type, "image/png")
        self.assertEqual(attachment.content_sha256, digest)
        self.assertEqual(attachment.content, content)

    async def test_hash_or_dimensions_mismatch_fails_closed(self):
        content = _png()

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={
                "content-type": "image/png",
                "content-length": str(len(content)),
                "content-disposition": (
                    f'inline; filename="origintrail-review-{JOB_ID}.png"'
                ),
                "x-coineasy-content-sha256": "b" * 64,
            }, content=content)

        client = BuzzShadowClient(
            url="https://console.example/api/buzz-shadow/origintrail/batch",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(BuzzAdapterError, "buzz_banner_invalid_response"):
            await client.banner(_event())


if __name__ == "__main__":
    unittest.main()
