from __future__ import annotations

import errno
import io
import json
import socket
import ssl
from urllib import error

import pytest

from scripts import probe_harmony_management_reachability as PROBE


PARENT_REF = "isuqcqwxpojgzevxfdwr"
SECRET_TEXT = "transport-secret-must-not-escape"


class UnreadBody(io.BytesIO):
    def __init__(self, body: bytes, *, fail_close: bool = False) -> None:
        super().__init__(body)
        self.read_calls = 0
        self.close_calls = 0
        self.fail_close = fail_close

    def read(self, *args: object, **kwargs: object) -> bytes:
        self.read_calls += 1
        return super().read(*args, **kwargs)

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError(errno.EIO, SECRET_TEXT)
        super().close()


class FakeResponse:
    def __init__(self, status: int, *, fail_close: bool = False) -> None:
        self.status = status
        self.read_calls = 0
        self.close_calls = 0
        self.fail_close = fail_close

    def read(self, *_args: object, **_kwargs: object) -> bytes:
        self.read_calls += 1
        raise AssertionError("reachability probe must not read response bodies")

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError(errno.EIO, SECRET_TEXT)


def test_expected_http_401_is_secret_free_success_without_body_read() -> None:
    captured: list[object] = []
    body = UnreadBody(b'{"message":"provider-controlled-secret"}')

    def opener(req: object, *, timeout: float) -> object:
        assert timeout == 7
        captured.append(req)
        raise error.HTTPError(
            str(getattr(req, "full_url")),
            401,
            SECRET_TEXT,
            {},
            body,
        )

    result = PROBE.probe_reachability(
        PARENT_REF,
        timeout_seconds=7,
        opener=opener,
        environ={},
    )

    assert result == {
        "authorization_header_sent": False,
        "category": "http_status",
        "credential_used": False,
        "exception_message_recorded": False,
        "expected_unauthenticated_status": 401,
        "http_status": 401,
        "mutation_free": True,
        "ok": True,
        "parent_project_ref": PARENT_REF,
        "response_body_read": False,
        "schema_version": "harmony-management-reachability@1",
    }
    req = captured[0]
    assert getattr(req, "get_method")() == "GET"
    assert getattr(req, "get_header")("Authorization") is None
    assert getattr(req, "full_url") == (
        "https://api.supabase.com/v1/projects/"
        f"{PARENT_REF}/billing/addons"
    )
    assert body.read_calls == 0
    assert body.close_calls == 1
    assert body.closed is True
    assert SECRET_TEXT not in json.dumps(result, sort_keys=True)


@pytest.mark.parametrize("status", (200, 302, 403, 404, 429, 503))
def test_any_status_other_than_401_fails_without_reading_body(status: int) -> None:
    response = FakeResponse(status)

    result = PROBE.probe_reachability(
        PARENT_REF,
        timeout_seconds=5,
        opener=lambda _req, *, timeout: response,
        environ={},
    )

    assert result["ok"] is False
    assert result["category"] == "http_status"
    assert result["http_status"] == status
    assert response.read_calls == 0
    assert response.close_calls == 1


@pytest.mark.parametrize(
    ("transport_error", "expected_category"),
    (
        (
            error.URLError(socket.gaierror(socket.EAI_AGAIN, SECRET_TEXT)),
            "dns",
        ),
        (error.URLError(ssl.SSLError(SECRET_TEXT)), "tls"),
        (error.URLError(TimeoutError(SECRET_TEXT)), "timeout"),
        (
            error.URLError(
                ConnectionRefusedError(errno.ECONNREFUSED, SECRET_TEXT)
            ),
            "connect",
        ),
        (
            error.URLError(OSError(errno.EIO, SECRET_TEXT)),
            "response_io",
        ),
        (ValueError(SECRET_TEXT), "client_value"),
        (RuntimeError(SECRET_TEXT), "unknown"),
    ),
)
def test_transport_failures_are_typed_without_exception_text(
    transport_error: BaseException,
    expected_category: str,
) -> None:
    def opener(_req: object, *, timeout: float) -> object:
        raise transport_error

    result = PROBE.probe_reachability(
        PARENT_REF,
        timeout_seconds=5,
        opener=opener,
        environ={},
    )

    assert result["ok"] is False
    assert result["category"] == expected_category
    assert result["http_status"] is None
    assert expected_category in PROBE.TRANSPORT_CATEGORIES
    assert SECRET_TEXT not in json.dumps(result, sort_keys=True)


@pytest.mark.parametrize(
    "environment_name",
    tuple(sorted(PROBE.SECRET_ENVIRONMENT_NAMES)),
)
def test_credential_environment_fails_before_opener(
    environment_name: str,
) -> None:
    calls = 0

    def opener(_req: object, *, timeout: float) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("opener must not run")

    result = PROBE.probe_reachability(
        PARENT_REF,
        timeout_seconds=5,
        opener=opener,
        environ={environment_name: SECRET_TEXT},
    )

    assert calls == 0
    assert result["category"] == "credential_environment_present"
    assert result["ok"] is False
    assert SECRET_TEXT not in json.dumps(result, sort_keys=True)


@pytest.mark.parametrize(
    ("project_ref", "timeout_seconds", "expected_category"),
    (
        (SECRET_TEXT, 5, "project_ref_invalid"),
        (PARENT_REF, 0, "timeout_value_invalid"),
        (PARENT_REF, True, "timeout_value_invalid"),
        (PARENT_REF, 31, "timeout_value_invalid"),
        (PARENT_REF, "not-a-number", "timeout_value_invalid"),
    ),
)
def test_invalid_input_fails_before_opener_and_redacts_invalid_ref(
    project_ref: str,
    timeout_seconds: object,
    expected_category: str,
) -> None:
    calls = 0

    def opener(_req: object, *, timeout: float) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("opener must not run")

    result = PROBE.probe_reachability(
        project_ref,
        timeout_seconds=timeout_seconds,  # type: ignore[arg-type]
        opener=opener,
        environ={},
    )

    assert calls == 0
    assert result["category"] == expected_category
    assert result["ok"] is False
    if project_ref != PARENT_REF:
        assert result["parent_project_ref"] is None
    assert SECRET_TEXT not in json.dumps(result, sort_keys=True)


def test_success_response_close_failure_is_not_exposed() -> None:
    response = FakeResponse(401, fail_close=True)

    result = PROBE.probe_reachability(
        PARENT_REF,
        timeout_seconds=5,
        opener=lambda _req, *, timeout: response,
        environ={},
    )

    assert result["ok"] is True
    assert response.read_calls == 0
    assert response.close_calls == 1
    assert SECRET_TEXT not in json.dumps(result, sort_keys=True)


def test_http_error_close_failure_is_not_exposed() -> None:
    body = UnreadBody(b"provider-body", fail_close=True)

    def opener(req: object, *, timeout: float) -> object:
        raise error.HTTPError(
            str(getattr(req, "full_url")),
            401,
            SECRET_TEXT,
            {},
            body,
        )

    result = PROBE.probe_reachability(
        PARENT_REF,
        timeout_seconds=5,
        opener=opener,
        environ={},
    )

    assert result["ok"] is True
    assert body.read_calls == 0
    assert body.close_calls == 1
    assert SECRET_TEXT not in json.dumps(result, sort_keys=True)


def test_redirect_handler_never_follows_location() -> None:
    handler = PROBE.RejectRedirectHandler()

    assert (
        handler.redirect_request(
            object(),
            object(),
            302,
            "Found",
            {},
            "https://attacker.invalid/redirect",
        )
        is None
    )


def test_default_opener_disables_environment_proxy_and_rejects_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: list[object] = []
    response = FakeResponse(401)

    class FakeClient:
        def open(self, _req: object, *, timeout: float) -> FakeResponse:
            assert timeout == 5
            return response

    def build_opener(*items: object) -> FakeClient:
        handlers.extend(items)
        return FakeClient()

    monkeypatch.setattr(PROBE.request, "build_opener", build_opener)

    result = PROBE.probe_reachability(
        PARENT_REF,
        timeout_seconds=5,
        environ={},
    )

    assert result["ok"] is True
    assert len(handlers) == 2
    assert isinstance(handlers[0], PROBE.request.ProxyHandler)
    assert getattr(handlers[0], "proxies") == {}
    assert isinstance(handlers[1], PROBE.RejectRedirectHandler)
    assert response.read_calls == 0
    assert response.close_calls == 1
