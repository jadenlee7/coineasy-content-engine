from concurrent.futures import ThreadPoolExecutor
from itertools import count
from types import SimpleNamespace
import threading

import pytest
from fastapi.testclient import TestClient


ADMIN_KEY = "test_admin_key_tutorial"


@pytest.fixture(autouse=True)
def patch_security(monkeypatch, tmp_path):
    from api import security, server

    monkeypatch.setattr(security, "API_KEYS", {"admin": ADMIN_KEY})
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path.resolve())


def test_concurrent_tutorial_requests_use_distinct_output_directories(monkeypatch):
    from api import server

    sequence = count(2_087_200_000_000_000_000)
    sequence_lock = threading.Lock()
    output_dirs = []
    output_lock = threading.Lock()

    def next_timestamp():
        with sequence_lock:
            return next(sequence)

    async def fake_generate_edu_carousel(*, output_dir, **_kwargs):
        with output_lock:
            output_dirs.append(output_dir)
        return SimpleNamespace(
            client_id="yellow",
            content_type="edu_carousel",
            series_meta={},
            png_paths=[str(output_dir / "lesson_01.png")],
            manifest_path=str(output_dir / "manifest.json"),
            duration_ms=1,
        )

    monkeypatch.setattr(server.time, "time_ns", next_timestamp)
    monkeypatch.setattr(server, "generate_edu_carousel", fake_generate_edu_carousel)

    def request_once():
        with TestClient(server.app) as client:
            return client.post(
                "/clients/yellow/generate/edu-carousel",
                json={"source_content": "source", "source_type": "article"},
                headers={"x-api-key": ADMIN_KEY},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: request_once(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert len(output_dirs) == 2
    assert output_dirs[0] != output_dirs[1]
    assert all(path.name.startswith("edu_") for path in output_dirs)
