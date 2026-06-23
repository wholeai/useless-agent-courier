from courier_agent_demo.orchestrator import CourierOrchestrator
from courier_agent_demo.repository import CourierRepository
from courier_agent_demo.schemas import CourierDecision, DeliveryEvent, OrderCreateRequest


def test_orchestrator_applies_decision_without_real_model(tmp_path):
    repository = CourierRepository(str(tmp_path / "demo.db"))
    order = repository.create_order(
        OrderCreateRequest(
            customer_name="Alex",
            pickup_address="Warehouse A",
            delivery_address="Building B",
            package_label="PKG-001",
            starting_battery_level=90,
        )
    )

    orchestrator = CourierOrchestrator(
        repository=repository,
        model_name="test",
        low_battery_threshold=15,
        model_provider="test",
    )
    orchestrator._run_agent = lambda deps, event: CourierDecision(  # type: ignore[method-assign]
        status="delayed",
        decision="notify_dispatch",
        reason_summary="Battery low",
        matched_rules=["manual.low_battery"],
        next_actions=["charge", "notify dispatch"],
        next_checkin_minutes=10,
        confidence=0.95,
        user_visible_note="Battery too low to continue.",
    )

    result = orchestrator.process_event(
        order.order_id,
        DeliveryEvent(event_type="heartbeat", message="battery warning", battery_level=12),
    )

    assert result.order.status == "delayed"
    assert result.decision.decision == "notify_dispatch"
    assert result.run.status == "completed"
