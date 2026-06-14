from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


OrderStatus = Literal[
    "idle",
    "assigned",
    "en_route_pickup",
    "at_pickup",
    "in_transit",
    "at_dropoff",
    "delivered",
    "delayed",
    "charging",
    "escalated",
]


EventType = Literal[
    "start",
    "heartbeat",
    "customer_reply",
    "location_update",
    "pickup_completed",
    "delivery_completed",
    "exception",
]


class OrderCreateRequest(BaseModel):
    customer_name: str = Field(min_length=1)
    pickup_address: str = Field(min_length=1)
    delivery_address: str = Field(min_length=1)
    package_label: str = Field(min_length=1)
    starting_battery_level: int = Field(default=100, ge=0, le=100)


class DeliveryEvent(BaseModel):
    event_type: EventType
    message: str = Field(default="")
    location: str | None = None
    battery_level: int | None = Field(default=None, ge=0, le=100)
    weather: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CourierDecision(BaseModel):
    status: OrderStatus
    decision: str
    reason_summary: str
    matched_rules: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    next_checkin_minutes: int = Field(default=5, ge=1, le=240)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    user_visible_note: str


class OrderRecord(BaseModel):
    order_id: str
    customer_name: str
    pickup_address: str
    delivery_address: str
    package_label: str
    status: OrderStatus = "idle"
    battery_level: int = 100
    current_location: str = "hub"
    customer_contact_attempts: int = 0
    last_event_type: str = "start"
    last_reason: str = ""
    next_checkin_at: datetime | None = None
    updated_at: datetime
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimelineItem(BaseModel):
    item_type: Literal["event", "run", "tool_call", "heartbeat"]
    created_at: datetime
    payload: dict[str, Any]


class AgentRunRecord(BaseModel):
    run_id: str
    order_id: str
    event_type: str
    status: str
    decision: str | None = None
    reason_summary: str | None = None
    matched_rules: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    next_checkin_minutes: int | None = None
    confidence: float | None = None
    model_name: str = ""
    input_summary: str = ""
    output_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    finished_at: datetime | None = None
