from courier_agent_demo.repository import CourierRepository
from courier_agent_demo.schemas import DeliveryEvent, OrderCreateRequest


def test_repository_persists_order(tmp_path):
    repository = CourierRepository(str(tmp_path / "demo.db"))
    order = repository.create_order(
        OrderCreateRequest(
            customer_name="Alex",
            pickup_address="Warehouse A",
            delivery_address="Building B",
            package_label="PKG-001",
            starting_battery_level=88,
        )
    )

    loaded = repository.get_order(order.order_id)

    assert loaded.customer_name == "Alex"
    assert loaded.status == "assigned"
    assert loaded.battery_level == 88


def test_repository_records_events_and_timeline(tmp_path):
    repository = CourierRepository(str(tmp_path / "demo.db"))
    order = repository.create_order(
        OrderCreateRequest(
            customer_name="Alex",
            pickup_address="Warehouse A",
            delivery_address="Building B",
            package_label="PKG-001",
        )
    )
    repository.append_event(
        order.order_id,
        DeliveryEvent(event_type="heartbeat", message="tick", location="street 1", battery_level=80),
    )

    timeline = repository.get_timeline(order.order_id)

    assert len(timeline) == 1
    assert timeline[0].item_type == "event"
    assert timeline[0].payload["event_type"] == "heartbeat"
