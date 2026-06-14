from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas import DeliveryEvent, OrderCreateRequest

router = APIRouter(prefix="/api/v1", tags=["courier"])


@router.post("/orders")
async def create_order(request: Request, payload: OrderCreateRequest) -> dict:
    orchestrator = request.app.state.orchestrator
    order = request.app.state.repository.create_order(payload)
    initial_event = DeliveryEvent(event_type="start", message="order created", metadata={"source": "api"})
    result = await orchestrator.process_event_async(order.order_id, initial_event)
    return {
        "data": {
            "order": result.order.model_dump(),
            "decision": result.decision.model_dump(),
            "run": result.run.model_dump(),
            "tool_calls": result.tool_calls,
        }
    }


@router.post("/orders/{order_id}/events")
async def append_event(request: Request, order_id: str, payload: DeliveryEvent) -> dict:
    orchestrator = request.app.state.orchestrator
    try:
        result = await orchestrator.process_event_async(order_id, payload)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "data": {
            "order": result.order.model_dump(),
            "decision": result.decision.model_dump(),
            "run": result.run.model_dump(),
            "tool_calls": result.tool_calls,
        }
    }


@router.get("/orders/{order_id}")
async def get_order(request: Request, order_id: str) -> dict:
    try:
        order = request.app.state.repository.get_order(order_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"data": order.model_dump()}


@router.get("/orders/{order_id}/timeline")
async def get_timeline(request: Request, order_id: str) -> dict:
    try:
        timeline = request.app.state.orchestrator.summarize_timeline(order_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"data": timeline}


@router.get("/health")
async def health(request: Request) -> dict:
    stats = request.app.state.repository.stats()
    return {
        "data": {
            "status": "ok",
            "database": request.app.state.settings.database_path,
            "active_orders": stats.active_orders,
            "total_orders": stats.total_orders,
            "total_runs": stats.total_runs,
        }
    }
