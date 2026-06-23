"""Tests for the integrations module. No real network calls."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from courier_agent_demo.integrations import (
    CustomerContactBackend,
    DispatchBackend,
    RoutingBackend,
    ToolBackends,
    _http_get_json,
    _http_post_json,
    _parse_lnglat,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen_with(payload: str):
    def fake_urlopen(request, timeout=0):
        return _FakeResponse(payload)

    return patch("urllib.request.urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# parse_lnglat
# ---------------------------------------------------------------------------


def test_parse_lnglat_valid() -> None:
    assert _parse_lnglat("116.4,39.9") == (116.4, 39.9)
    assert _parse_lnglat(" -73.98 , 40.75 ") == (-73.98, 40.75)


def test_parse_lnglat_invalid() -> None:
    assert _parse_lnglat("hello") is None
    assert _parse_lnglat("1,2,3") is None
    assert _parse_lnglat("abc,def") is None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_log_only_when_no_webhook() -> None:
    result = DispatchBackend(webhook_url=None).notify("battery low")
    assert result.mode == "log_only"
    assert result.status == "ok"
    assert "battery low" in result.detail
    assert result.endpoint is None


def test_dispatch_delivers_via_webhook() -> None:
    with _patch_urlopen_with(json.dumps({"errcode": 0})):
        result = DispatchBackend(webhook_url="https://oapi.dingtalk.com/robot/send?access_token=secret").notify(
            "low battery"
        )
    assert result.mode == "webhook_push"
    assert result.status == "ok"
    assert result.endpoint == "https://oapi.dingtalk.com/robot/send"  # query stripped
    assert "errcode" in result.extra["response_excerpt"]


def test_dispatch_swallows_transport_error() -> None:
    def broken_urlopen(*args, **kwargs):
        raise TimeoutError("upstream slow")

    with patch("urllib.request.urlopen", broken_urlopen):
        result = DispatchBackend(webhook_url="https://example.com/hook").notify("x")
    assert result.status == "error"
    assert "TimeoutError" in result.error
    assert result.endpoint == "https://example.com/hook"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_routing_log_only_for_free_text() -> None:
    result = RoutingBackend().plan("hub", "Building B")
    assert result.mode == "log_only"
    assert "Building B" in result.detail


def test_routing_calls_osrm_with_lnglat() -> None:
    payload = json.dumps(
        {
            "code": "Ok",
            "routes": [{"distance": 1234.5, "duration": 567.8}],
        }
    )
    with _patch_urlopen_with(payload):
        result = RoutingBackend().plan("116.4,39.9", "116.5,39.9")
    assert result.mode == "http_get"
    assert result.status == "ok"
    assert result.extra["distance_m"] == 1234.5
    assert result.extra["duration_s"] == 567.8
    assert result.endpoint is not None
    assert "router.project-osrm.org" in result.endpoint


def test_routing_reports_error_when_osrm_returns_no_route() -> None:
    payload = json.dumps({"code": "NoRoute", "routes": []})
    with _patch_urlopen_with(payload):
        result = RoutingBackend().plan("0,0", "0,0")
    assert result.status == "error"
    assert "NoRoute" in result.error


# ---------------------------------------------------------------------------
# Customer contact
# ---------------------------------------------------------------------------


def test_customer_log_only_when_no_webhook() -> None:
    result = CustomerContactBackend(webhook_url=None).contact("ETA 10 minutes")
    assert result.mode == "log_only"
    assert result.status == "ok"
    assert "ETA 10 minutes" in result.detail


def test_customer_push_success() -> None:
    with _patch_urlopen_with("ok"):
        result = CustomerContactBackend(webhook_url="https://ntfy.sh/secret-topic").contact("I'm outside")
    assert result.mode == "webhook_push"
    assert result.status == "ok"
    assert result.endpoint == "https://ntfy.sh/secret-topic"


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_tool_backends_log_only_has_no_webhooks() -> None:
    backends = ToolBackends.log_only()
    assert backends.dispatch.webhook_url is None
    assert backends.customer.webhook_url is None
    assert backends.routing.base_url == "https://router.project-osrm.org"


def test_tool_backends_from_settings_passes_through() -> None:
    backends = ToolBackends.from_settings(
        dingtalk_webhook_url="https://ding/hook",
        customer_contact_webhook_url="https://ntfy/topic",
        routing_base_url=None,
        timeout_seconds=7,
    )
    assert backends.dispatch.webhook_url == "https://ding/hook"
    assert backends.customer.webhook_url == "https://ntfy/topic"
    assert backends.routing.base_url == "https://router.project-osrm.org"
    assert backends.dispatch.timeout == 7


# ---------------------------------------------------------------------------
# Sanity: internal helpers are wired
# ---------------------------------------------------------------------------


def test_http_post_json_sends_expected_headers() -> None:
    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        return _FakeResponse("{}")

    with patch("urllib.request.urlopen", fake_urlopen):
        mode, body = _http_post_json("https://example.com/hook", {"k": "v"}, timeout=3)

    assert mode == "webhook_push"
    assert captured["url"] == "https://example.com/hook"
    assert captured["method"] == "POST"
    assert captured["headers"]["Content-type"] == "application/json"
    assert json.loads(captured["body"]) == {"k": "v"}


def test_http_get_json_parses_response() -> None:
    with _patch_urlopen_with(json.dumps({"ok": True})):
        payload = _http_get_json("https://example.com/api", timeout=2)
    assert payload == {"ok": True}
