from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .core.config import get_settings
from .core.heartbeat import HeartbeatService
from .core.logging import configure_logging
from .integrations import ToolBackends
from .orchestrator import CourierOrchestrator
from .repository import CourierRepository


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repository = CourierRepository(settings.database_path)
        orchestrator = CourierOrchestrator(
            repository=repository,
            model_name=settings.agent_model,
            low_battery_threshold=settings.low_battery_threshold,
            backends=ToolBackends.from_settings(
                dingtalk_webhook_url=settings.dingtalk_webhook_url,
                customer_contact_webhook_url=settings.customer_contact_webhook_url,
                routing_base_url=settings.routing_base_url,
                timeout_seconds=settings.integration_timeout_seconds,
            ),
            model_provider=settings.agent_provider,
            openai_api_key=settings.openai_api_key,
            openai_base_url=settings.openai_base_url,
        )
        heartbeat = HeartbeatService(
            orchestrator=orchestrator,
            repository=repository,
            interval_seconds=settings.heartbeat_interval_seconds,
        )
        heartbeat_task = asyncio.create_task(heartbeat.run_forever())

        app.state.settings = settings
        app.state.repository = repository
        app.state.orchestrator = orchestrator
        app.state.heartbeat = heartbeat
        app.state.heartbeat_task = heartbeat_task
        try:
            yield
        finally:
            heartbeat.stop()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
