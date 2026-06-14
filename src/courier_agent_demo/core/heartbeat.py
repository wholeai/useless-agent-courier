from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..schemas import DeliveryEvent


@dataclass
class HeartbeatService:
    orchestrator: object
    repository: object
    interval_seconds: int
    running: bool = False

    async def run_once(self) -> None:
        active_orders = self.repository.list_active_orders()
        for order in active_orders:
            event = DeliveryEvent(
                event_type="heartbeat",
                message="scheduled heartbeat",
                location=order.current_location,
                battery_level=order.battery_level,
                metadata={"source": "scheduler"},
            )
            self.repository.record_heartbeat(order.order_id, order.status, order.battery_level, "scheduled heartbeat")
            await self.orchestrator.process_event_async(order.order_id, event)

    async def run_forever(self) -> None:
        self.running = True
        try:
            while self.running:
                await self.run_once()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            self.running = False
            raise

    def stop(self) -> None:
        self.running = False
