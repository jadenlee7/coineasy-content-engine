"""Regression coverage for Railway client-key isolation."""

import pytest
from fastapi.testclient import TestClient


KEYS = {
    "admin": "scope-admin-key-123",
    "yellow": "scope-yellow-key-123",
    "origintrail": "scope-origintrail-key-123",
    "squid": "scope-squid-key-123",
    "babylon": "scope-babylon-key-123",
}


@pytest.fixture
def client(monkeypatch):
    from api import security, server

    monkeypatch.setattr(security, "API_KEYS", KEYS)
    return TestClient(server.app)


@pytest.mark.parametrize(
    "path",
    [
        "/clients/squid/generate/edu-carousel",
        "/clients/squid/generate/news-card",
        "/clients/squid/generate/article",
        "/clients/squid/generate/daily-news",
        "/clients/squid/publish/daily-news",
    ],
)
def test_every_client_route_rejects_another_clients_key(client, path):
    response = client.post(
        path,
        headers={"x-api-key": KEYS["yellow"]},
        json={"source_content": "x" * 300},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "client_scope_violation"


@pytest.mark.parametrize("client_id", ["yellow", "origintrail", "squid", "babylon"])
def test_client_key_lists_only_its_own_configuration(client, client_id):
    response = client.get(
        "/clients",
        headers={"x-api-key": KEYS[client_id]},
    )

    assert response.status_code == 200
    assert [item["client_id"] for item in response.json()] == [client_id]


def test_admin_key_can_list_all_registered_clients(client):
    response = client.get("/clients", headers={"x-api-key": KEYS["admin"]})

    assert response.status_code == 200
    assert {item["client_id"] for item in response.json()} == {
        "yellow",
        "origintrail",
        "squid",
        "babylon",
    }
    assert all(item["features"]["article"] is True for item in response.json())


def test_duplicate_or_weak_configured_keys_fail_closed(client, monkeypatch):
    from api import security

    shared = "shared-key-at-least-16"
    monkeypatch.setattr(security, "API_KEYS", {"admin": shared, "yellow": shared})
    duplicate = client.get("/clients", headers={"x-api-key": shared})
    assert duplicate.status_code == 503
    assert duplicate.json()["detail"] == "api_key_configuration_error"

    monkeypatch.setattr(security, "API_KEYS", {"admin": "too-short"})
    weak = client.get("/clients", headers={"x-api-key": "too-short"})
    assert weak.status_code == 503
    assert weak.json()["detail"] == "api_key_configuration_error"
