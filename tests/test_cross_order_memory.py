"""Tests for the cross-order (global) memory table and orchestrator wiring."""

from __future__ import annotations

import pytest

from courier_agent_demo.integrations import ToolBackends
from courier_agent_demo.orchestrator import CourierOrchestrator
from courier_agent_demo.repository import CourierRepository
from courier_agent_demo.schemas import CourierDecision, DeliveryEvent, OrderCreateRequest


# ---------------------------------------------------------------------------
# Repository: global memory
# ---------------------------------------------------------------------------


def test_global_memory_round_trip(tmp_path) -> None:
    repository = CourierRepository(str(tmp_path / "demo.db"))
    repository.record_global_memory("preferred_drop_zone", {"value": "Building B lobby"})

    assert repository.get_global_memory("preferred_drop_zone") == {"value": "Building B lobby"}
    assert repository.list_global_memory_keys() == ["preferred_drop_zone"]


def test_global_memory_upsert_keeps_single_row(tmp_path) -> None:
    repository = CourierRepository(str(tmp_path / "demo.db"))
    repository.record_global_memory("key1", {"value": "v1"})
    repository.record_global_memory("key1", {"value": "v2"})

    assert repository.get_global_memory("key1") == {"value": "v2"}
    assert repository.list_global_memory_keys() == ["key1"]


def test_global_memory_unknown_key_returns_none(tmp_path) -> None:
    repository = CourierRepository(str(tmp_path / "demo.db"))
    assert repository.get_global_memory("nope") is None
    assert repository.list_global_memory_keys() == []


# ---------------------------------------------------------------------------
# Orchestrator: backends are injected and tools record audit entries
# ---------------------------------------------------------------------------


def test_orchestrator_uses_injected_backends(tmp_path) -> None:
    repository = CourierRepository(str(tmp_path / "demo.db"))
    backends = ToolBackends.log_only()
    orchestrator = CourierOrchestrator(
        repository=repository,
        model_name="test",
        low_battery_threshold=15,
        backends=backends,
        model_provider="test",
    )
    assert orchestrator.backends is backends


def test_orchestrator_defaults_to_log_only_when_backends_omitted(tmp_path) -> None:
    repository = CourierRepository(str(tmp_path / "demo.db"))
    orchestrator = CourierOrchestrator(
        repository=repository,
        model_name="test",
        low_battery_threshold=15,
        model_provider="test",
    )
    # Ponytail ceiling: default is the no-network backend; flip to real when
    # the operator opts in via env vars.
    assert orchestrator.backends.dispatch.webhook_url is None
    assert orchestrator.backends.customer.webhook_url is None


def test_orchestrator_passes_backends_to_agent_deps(tmp_path) -> None:
    repository = CourierRepository(str(tmp_path / "demo.db"))
    backends = ToolBackends.log_only()
    orchestrator = CourierOrchestrator(
        repository=repository,
        model_name="test",
        low_battery_threshold=15,
        backends=backends,
        model_provider="test",
    )
    order = repository.create_order(
        OrderCreateRequest(
            customer_name="Alex",
            pickup_address="Warehouse A",
            delivery_address="Building B",
            package_label="PKG-001",
        )
    )
    captured = {}

    def fake_run_agent(deps, event):
        captured["backends"] = deps.backends
        return CourierDecision(
            status="in_transit",
            decision="en_route_pickup",
            reason_summary="heading out",
            matched_rules=[],
            next_actions=["plan_route"],
            next_checkin_minutes=5,
            confidence=0.9,
            user_visible_note="On the way.",
        )

    orchestrator._run_agent = fake_run_agent  # type: ignore[method-assign]
    orchestrator.process_event(
        order.order_id,
        DeliveryEvent(event_type="heartbeat", message="go", battery_level=80),
    )

    assert captured["backends"] is backends
