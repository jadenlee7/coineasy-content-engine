import pytest
from fastapi.testclient import TestClient

ADMIN_KEY = "test_admin_key_xyz"
YELLOW_KEY = "test_yellow_key_xyz"
SQUID_KEY = "test_squid_key_xyz"


@pytest.fixture(autouse=True)
def patch_keys(monkeypatch, tmp_path):
    from api import security
    monkeypatch.setattr(security, "API_KEYS", {
        "admin": ADMIN_KEY,
        "yellow": YELLOW_KEY,
        "squid": SQUID_KEY,
    })
    monkeypatch.setattr(security, "OUTPUT_ROOT", tmp_path.resolve())
    (tmp_path / "yellow" / "edu_x").mkdir(parents=True)
    (tmp_path / "yellow" / "edu_x" / "lesson_01.png").write_bytes(b"yellow")
    (tmp_path / "squid" / "edu_y").mkdir(parents=True)
    (tmp_path / "squid" / "edu_y" / "lesson_01.png").write_bytes(b"squid")
    yield


@pytest.fixture
def client():
    from api.server import app
    return TestClient(app)


def test_admin_can_access_yellow(client):
    r = client.get("/files/yellow/edu_x/lesson_01.png", headers={"x-api-key": ADMIN_KEY})
    assert r.status_code == 200


def test_yellow_key_can_access_yellow(client):
    r = client.get("/files/yellow/edu_x/lesson_01.png", headers={"x-api-key": YELLOW_KEY})
    assert r.status_code == 200


def test_yellow_key_cannot_access_squid(client):
    r = client.get("/files/squid/edu_y/lesson_01.png", headers={"x-api-key": YELLOW_KEY})
    assert r.status_code == 403
    assert r.json()["detail"] == "client_scope_violation"


def test_traversal_blocked_via_url_encoding(client):
    """Real-world traversal: many payloads use %2e%2e to bypass naive filters.
    httpx TestClient normalizes literal ../ client-side, so we test the encoded form
    which actually reaches the server (and is the more dangerous payload anyway)."""
    r = client.get("/files/yellow/%2e%2e/%2e%2e/etc/passwd", headers={"x-api-key": ADMIN_KEY})
    assert r.status_code == 403
    assert r.json()["detail"] == "path_traversal_blocked"


def test_resolve_safe_path_blocks_literal_traversal():
    """Unit test: resolve_safe_path() must reject literal ../ regardless of HTTP transport."""
    from api.security import resolve_safe_path
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        resolve_safe_path("yellow/../../etc/passwd")
    assert exc.value.status_code == 403
    assert exc.value.detail == "path_traversal_blocked"


def test_double_slash_normalized(client):
    r = client.get("/files//yellow/edu_x/lesson_01.png", headers={"x-api-key": YELLOW_KEY})
    assert r.status_code == 200


def test_invalid_key_rejected(client):
    r = client.get("/files/yellow/edu_x/lesson_01.png", headers={"x-api-key": "wrong"})
    assert r.status_code == 401
