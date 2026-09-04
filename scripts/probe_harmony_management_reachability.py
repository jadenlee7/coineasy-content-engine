#!/usr/bin/env python3
"""Run a credential-free Management API reachability gate.

This probe must run before a scoped PAT is created or a paid one-shot invocation
is claimed. It sends one fixed, unauthenticated GET and emits only allow-listed
JSON. It never reads an HTTP response body or exception message.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import socket
import ssl
import sys
from typing import Callable, Mapping
from urllib import error, parse, request


SCHEMA_VERSION = "harmony-management-reachability@1"
MANAGEMENT_API_BASE_URL = "https://api.supabase.com/v1"
PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")
EXPECTED_UNAUTHENTICATED_STATUS = 401
TRANSPORT_CATEGORIES = frozenset(
    {
        "dns",
        "tls",
        "timeout",
        "connect",
        "response_io",
        "client_value",
        "unknown",
    }
)
_TIMEOUT_ERRNOS = frozenset(
    value
    for value in (getattr(errno, "ETIMEDOUT", None),)
    if isinstance(value, int)
)
_CONNECT_ERRNOS = frozenset(
    value
    for value in (
        getattr(errno, "EACCES", None),
        getattr(errno, "EPERM", None),
        getattr(errno, "ECONNABORTED", None),
        getattr(errno, "ECONNREFUSED", None),
        getattr(errno, "ECONNRESET", None),
        getattr(errno, "EHOSTDOWN", None),
        getattr(errno, "EHOSTUNREACH", None),
        getattr(errno, "ENETDOWN", None),
        getattr(errno, "ENETUNREACH", None),
    )
    if isinstance(value, int)
)
SECRET_ENVIRONMENT_NAMES = frozenset(
    {
        "HARMONY_SUPABASE_MANAGEMENT_TOKEN",
        "SUPABASE_ACCESS_TOKEN",
    }
)


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    """Return a bounded exception chain without inspecting messages."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    result: list[BaseException] = []
    while pending and len(result) < 8:
        current = pending.pop(0)
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(current)
        if isinstance(current, error.URLError) and isinstance(
            current.reason, BaseException
        ):
            pending.append(current.reason)
        if isinstance(current.__cause__, BaseException):
            pending.append(current.__cause__)
        if isinstance(current.__context__, BaseException):
            pending.append(current.__context__)
    return tuple(result)


def classify_transport_exception(exc: BaseException) -> str:
    """Classify with exception types and errno, never provider-controlled text."""

    chain = _exception_chain(exc)
    if any(isinstance(item, socket.gaierror) for item in chain):
        return "dns"
    if any(
        isinstance(item, (ssl.CertificateError, ssl.SSLError)) for item in chain
    ):
        return "tls"
    if any(isinstance(item, (TimeoutError, socket.timeout)) for item in chain):
        return "timeout"
    if any(
        isinstance(item, OSError)
        and not isinstance(item, error.URLError)
        and item.errno in _TIMEOUT_ERRNOS
        for item in chain
    ):
        return "timeout"
    if any(isinstance(item, ConnectionError) for item in chain):
        return "connect"
    if any(
        isinstance(item, OSError)
        and not isinstance(item, error.URLError)
        and item.errno in _CONNECT_ERRNOS
        for item in chain
    ):
        return "connect"
    if any(isinstance(item, ValueError) for item in chain):
        return "client_value"
    if any(
        isinstance(item, OSError) and not isinstance(item, error.URLError)
        for item in chain
    ):
        return "response_io"
    return "unknown"


class RejectRedirectHandler(request.HTTPRedirectHandler):
    """Return redirects to urllib as HTTP errors instead of following them."""

    def redirect_request(  # type: ignore[override]
        self,
        req: object,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _result(
    parent_project_ref: str,
    *,
    category: str,
    http_status: int | None,
    ok: bool,
) -> dict[str, object]:
    safe_parent_project_ref = (
        parent_project_ref
        if isinstance(parent_project_ref, str)
        and PROJECT_REF_PATTERN.fullmatch(parent_project_ref) is not None
        else None
    )
    return {
        "authorization_header_sent": False,
        "category": category,
        "credential_used": False,
        "exception_message_recorded": False,
        "expected_unauthenticated_status": EXPECTED_UNAUTHENTICATED_STATUS,
        "http_status": http_status,
        "mutation_free": True,
        "ok": ok,
        "parent_project_ref": safe_parent_project_ref,
        "response_body_read": False,
        "schema_version": SCHEMA_VERSION,
    }


def _close_without_observation(resource: object) -> None:
    """Close a response without inspecting or reporting close failures."""

    try:
        close = getattr(resource, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def probe_reachability(
    parent_project_ref: str,
    *,
    timeout_seconds: float,
    opener: Callable[..., object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return one bounded, secret-free reachability result."""

    environment = os.environ if environ is None else environ
    if any(name in environment for name in SECRET_ENVIRONMENT_NAMES):
        return _result(
            parent_project_ref,
            category="credential_environment_present",
            http_status=None,
            ok=False,
        )
    if (
        not isinstance(parent_project_ref, str)
        or PROJECT_REF_PATTERN.fullmatch(parent_project_ref) is None
    ):
        return _result(
            parent_project_ref,
            category="project_ref_invalid",
            http_status=None,
            ok=False,
        )
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds, (int, float)
    ) or not (
        0 < float(timeout_seconds) <= 30
    ):
        return _result(
            parent_project_ref,
            category="timeout_value_invalid",
            http_status=None,
            ok=False,
        )

    path = f"/projects/{parent_project_ref}/billing/addons"
    url = MANAGEMENT_API_BASE_URL + path
    parsed = parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.supabase.com"
        or parsed.path != "/v1" + path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        return _result(
            parent_project_ref,
            category="url_fence_invalid",
            http_status=None,
            ok=False,
        )

    req = request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "coineasy-harmony-management-reachability/1",
        },
    )
    if opener is None:
        client = request.build_opener(
            request.ProxyHandler({}),
            RejectRedirectHandler(),
        )
        opener = client.open

    response: object | None = None
    try:
        response = opener(req, timeout=float(timeout_seconds))
        status = getattr(response, "status", None)
        if not isinstance(status, int):
            status = None
        return _result(
            parent_project_ref,
            category="http_status",
            http_status=status,
            ok=status == EXPECTED_UNAUTHENTICATED_STATUS,
        )
    except error.HTTPError as exc:
        status = exc.code if isinstance(exc.code, int) else None
        _close_without_observation(exc)
        return _result(
            parent_project_ref,
            category="http_status",
            http_status=status,
            ok=status == EXPECTED_UNAUTHENTICATED_STATUS,
        )
    except (error.URLError, TimeoutError, OSError, ValueError) as exc:
        return _result(
            parent_project_ref,
            category=classify_transport_exception(exc),
            http_status=None,
            ok=False,
        )
    except Exception as exc:
        return _result(
            parent_project_ref,
            category=classify_transport_exception(exc),
            http_status=None,
            ok=False,
        )
    finally:
        if response is not None:
            _close_without_observation(response)


def _timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be numeric") from exc
    if not 0 < parsed <= 30:
        raise argparse.ArgumentTypeError("timeout must be between 0 and 30")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-project-ref", required=True)
    parser.add_argument("--timeout-seconds", type=_timeout, default=10.0)
    args = parser.parse_args(argv)
    result = probe_reachability(
        args.parent_project_ref,
        timeout_seconds=args.timeout_seconds,
    )
    sys.stdout.write(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0 if result["ok"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
