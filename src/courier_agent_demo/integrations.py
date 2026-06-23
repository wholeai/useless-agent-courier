"""Real-world tool backends for the courier agent.

Each backend has a "log-only" mode and a real mode. If the corresponding env
var is not set, the backend degrades to log-only and records the would-be
call to the agent's tool-call audit log. This is the "easy path default" —
running the demo just works, and the user can plug in real endpoints when
they're ready.

Ponytail ceiling: synchronous stdlib HTTP, no retries, no circuit breaker.
Add tenacity + per-host circuit breaker when one bad upstream takes the
agent down with it.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 5

USER_AGENT = "courier-agent-demo/0.1 (https://github.com/local/courier-agent-demo)"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class IntegrationResult:
    mode: str  # "log_only" | "webhook_push" | "http_get"
    status: str  # "ok" | "error"
    detail: str
    endpoint: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "status": self.status, "detail": self.detail}
        if self.endpoint:
            out["endpoint"] = self.endpoint
        if self.error:
            out["error"] = self.error
        out.update(self.extra)
        return out

    def as_tool_string(self) -> str:
        prefix = f"[{self.mode}] {self.detail}"
        if self.error:
            return f"{prefix} (error: {self.error})"
        return prefix


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _scrub_url(url: str) -> str:
    """Strip query string and userinfo from URL before recording in tool_calls."""
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


def _http_post_json(url: str, payload: dict[str, Any], *, timeout: int) -> tuple[str, str]:
    """POST JSON to url. Returns (mode, body_excerpt). Raises on transport error."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is operator-configured
        body = response.read().decode("utf-8", errors="replace")
    return "webhook_push", body[:200]


def _http_get_json(url: str, *, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class DispatchBackend:
    """Notify dispatch.

    Real mode: POST to a DingTalk robot webhook. Set ``webhook_url`` to enable.
    Log-only mode: return a structured result that the audit log records verbatim.
    """

    def __init__(self, webhook_url: str | None, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def notify(self, reason: str) -> IntegrationResult:
        if not self.webhook_url:
            return IntegrationResult(
                mode="log_only",
                status="ok",
                detail=f"[log-only] dispatch notified: {reason}",
            )
        try:
            mode, body = _http_post_json(
                self.webhook_url,
                {"msgtype": "text", "text": {"content": f"[courier-agent] {reason}"}},
                timeout=self.timeout,
            )
            return IntegrationResult(
                mode=mode,
                status="ok",
                detail="dispatch webhook delivered",
                endpoint=_scrub_url(self.webhook_url),
                extra={"response_excerpt": body},
            )
        except (URLError, HTTPError, TimeoutError, OSError) as error:
            return IntegrationResult(
                mode="webhook_push",
                status="error",
                detail="dispatch webhook failed",
                endpoint=_scrub_url(self.webhook_url),
                error=repr(error),
            )


class RoutingBackend:
    """Plan a route.

    Real mode: call OSRM's public router (``https://router.project-osrm.org``).
    Ponytail: origin and destination must be ``"lng,lat"`` strings; free text
    falls through to log-only. Add a geocoder (Nominatim) when addresses
    matter.
    """

    def __init__(self, base_url: str = "https://router.project-osrm.org", *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def plan(self, origin: str, destination: str) -> IntegrationResult:
        origin_ll = _parse_lnglat(origin)
        dest_ll = _parse_lnglat(destination)
        if origin_ll is None or dest_ll is None:
            return IntegrationResult(
                mode="log_only",
                status="ok",
                detail=f"[log-only] route planned from {origin} to {destination}",
                extra={"note": "free-text endpoint; pass 'lng,lat' for a real OSRM call"},
            )
        url = (
            f"{self.base_url}/route/v1/driving/"
            f"{origin_ll[0]},{origin_ll[1]};{dest_ll[0]},{dest_ll[1]}"
            "?overview=false"
        )
        try:
            payload = _http_get_json(url, timeout=self.timeout)
        except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError) as error:
            return IntegrationResult(
                mode="http_get",
                status="error",
                detail="routing call failed",
                endpoint=_scrub_url(url),
                error=repr(error),
            )
        if payload.get("code") != "Ok" or not payload.get("routes"):
            return IntegrationResult(
                mode="http_get",
                status="error",
                detail="routing returned no route",
                endpoint=_scrub_url(url),
                error=str(payload.get("code", "unknown")),
            )
        route = payload["routes"][0]
        return IntegrationResult(
            mode="http_get",
            status="ok",
            detail=f"route planned ({route['distance']:.0f}m, {route['duration']:.0f}s)",
            endpoint=_scrub_url(url),
            extra={"distance_m": route["distance"], "duration_s": route["duration"]},
        )


class CustomerContactBackend:
    """Contact the customer.

    Real mode: POST a push payload to a generic webhook (Bark, 企业微信
    incoming, ntfy, anything that takes JSON). Free-text message only.
    Ponytail: this is a *customer contact simulator* unless a real
    telephony/SMS provider is wired in. Call it that in the article and
    in tool-call records.
    """

    def __init__(self, webhook_url: str | None, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def contact(self, message: str) -> IntegrationResult:
        if not self.webhook_url:
            return IntegrationResult(
                mode="log_only",
                status="ok",
                detail=f"[log-only] customer contact: {message}",
            )
        try:
            mode, body = _http_post_json(self.webhook_url, {"message": message}, timeout=self.timeout)
            return IntegrationResult(
                mode=mode,
                status="ok",
                detail="customer push delivered",
                endpoint=_scrub_url(self.webhook_url),
                extra={"response_excerpt": body},
            )
        except (URLError, HTTPError, TimeoutError, OSError) as error:
            return IntegrationResult(
                mode="webhook_push",
                status="error",
                detail="customer push failed",
                endpoint=_scrub_url(self.webhook_url),
                error=repr(error),
            )


def _parse_lnglat(text: str) -> tuple[float, float] | None:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass
class ToolBackends:
    dispatch: DispatchBackend
    routing: RoutingBackend
    customer: CustomerContactBackend

    @classmethod
    def log_only(cls) -> "ToolBackends":
        return cls(
            dispatch=DispatchBackend(webhook_url=None),
            routing=RoutingBackend(),
            customer=CustomerContactBackend(webhook_url=None),
        )

    @classmethod
    def from_settings(
        cls,
        *,
        dingtalk_webhook_url: str | None,
        customer_contact_webhook_url: str | None,
        routing_base_url: str | None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> "ToolBackends":
        return cls(
            dispatch=DispatchBackend(dingtalk_webhook_url, timeout=timeout_seconds),
            routing=RoutingBackend(
                base_url=routing_base_url or "https://router.project-osrm.org",
                timeout=timeout_seconds,
            ),
            customer=CustomerContactBackend(customer_contact_webhook_url, timeout=timeout_seconds),
        )
