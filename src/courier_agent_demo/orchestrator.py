from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .agent import CourierDeps, build_courier_agent
from .core.logging import get_logger
from .schemas import AgentRunRecord, CourierDecision, DeliveryEvent, OrderRecord


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ProcessResult:
    order: OrderRecord
    decision: CourierDecision
    run: AgentRunRecord
    tool_calls: list[dict]


class CourierOrchestrator:
    def __init__(
        self,
        *,
        repository,
        model_name: str,
        low_battery_threshold: int,
        model_provider: str = "openai-compatible",
        openai_api_key: str | None = None,
        openai_base_url: str | None = None,
    ) -> None:
        self.repository = repository
        self.model_name = model_name
        self.low_battery_threshold = low_battery_threshold
        self.logger = get_logger(self.__class__.__name__)
        self._agent = build_courier_agent(
            model_name,
            provider=model_provider,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )

    def process_event(self, order_id: str, event: DeliveryEvent) -> ProcessResult:
        order = self.repository.get_order(order_id)
        updated_order = self._merge_event(order, event)
        self.repository.append_event(order_id, event)

        run_id = str(uuid4())
        run = AgentRunRecord(
            run_id=run_id,
            order_id=order_id,
            event_type=event.event_type,
            status="running",
            model_name=self.model_name,
            input_summary=self._summarize_event(event),
            created_at=_utc_now(),
        )
        self.repository.record_run(run)

        deps = CourierDeps(
            repository=self.repository,
            order=updated_order,
            event=event,
            run_id=run_id,
            model_name=self.model_name,
            low_battery_threshold=self.low_battery_threshold,
        )

        decision = self._run_agent(deps, event)
        finalized_order = self._apply_decision(updated_order, event, decision)
        self.repository.save_order(finalized_order)

        finished_run = AgentRunRecord(
            run_id=run_id,
            order_id=order_id,
            event_type=event.event_type,
            status="completed",
            decision=decision.decision,
            reason_summary=decision.reason_summary,
            matched_rules=decision.matched_rules,
            next_actions=decision.next_actions,
            next_checkin_minutes=decision.next_checkin_minutes,
            confidence=decision.confidence,
            model_name=self.model_name,
            input_summary=self._summarize_event(event),
            output_json=decision.model_dump(),
            created_at=run.created_at,
            finished_at=_utc_now(),
        )
        self.repository.record_run(finished_run)
        tool_calls = self.repository.get_tool_calls_by_run(run_id)

        self.logger.info(
            "agent decision completed",
            extra={
                "trace_id": run_id,
                "order_id": order_id,
                "run_id": run_id,
                "event_type": event.event_type,
                "decision": decision.decision,
            },
        )
        return ProcessResult(order=finalized_order, decision=decision, run=finished_run, tool_calls=tool_calls)

    async def process_event_async(self, order_id: str, event: DeliveryEvent) -> ProcessResult:
        return await asyncio.to_thread(self.process_event, order_id, event)

    def summarize_timeline(self, order_id: str) -> list[dict]:
        return [item.model_dump() for item in self.repository.get_timeline(order_id)]

    def _run_agent(self, deps: CourierDeps, event: DeliveryEvent) -> CourierDecision:
        prompt = self._build_prompt(deps.order, event)
        result = self._agent.run_sync(prompt, deps=deps)
        return result.output

    def _build_prompt(self, order: OrderRecord, event: DeliveryEvent) -> str:
        return (
            f"Order {order.order_id} for {order.customer_name}. "
            f"Package {order.package_label} from {order.pickup_address} to {order.delivery_address}. "
            f"Current status is {order.status}. Current event: {event.model_dump()}. "
            "Decide the best next action, use tools when needed, and return an auditable explanation."
        )

    def _merge_event(self, order: OrderRecord, event: DeliveryEvent) -> OrderRecord:
        battery_level = event.battery_level if event.battery_level is not None else order.battery_level
        current_location = event.location if event.location else order.current_location
        status = order.status
        if event.event_type == "heartbeat":
            status = "delayed" if battery_level < self.low_battery_threshold else order.status
        if event.event_type == "pickup_completed":
            status = "in_transit"
        if event.event_type == "delivery_completed":
            status = "delivered"
        return order.model_copy(
            update={
                "battery_level": battery_level,
                "current_location": current_location,
                "status": status,
                "last_event_type": event.event_type,
                "last_reason": event.message or event.event_type,
                "updated_at": _utc_now(),
            }
        )

    def _apply_decision(self, order: OrderRecord, event: DeliveryEvent, decision: CourierDecision) -> OrderRecord:
        next_checkin = _utc_now() + timedelta(minutes=decision.next_checkin_minutes)
        contact_attempts = order.customer_contact_attempts
        if decision.decision in {"contact_customer", "retry_contact"}:
            contact_attempts += 1
        return order.model_copy(
            update={
                "status": decision.status,
                "last_reason": decision.reason_summary,
                "customer_contact_attempts": contact_attempts,
                "next_checkin_at": next_checkin,
                "updated_at": _utc_now(),
            }
        )

    def _summarize_event(self, event: DeliveryEvent) -> str:
        return f"{event.event_type}: {event.message or 'no message'}"
