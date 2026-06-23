from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .integrations import ToolBackends
from .manual import manual_overview, search_manual
from .schemas import CourierDecision, DeliveryEvent, OrderRecord


# Ponytail: a recognised stub provider so unit tests can build the agent
# without network or a real LLM. Real deployments use the openai-compatible
# provider; tests should never reach for the network.
_TEST_MODEL_SENTINEL = "test"


@dataclass
class CourierDeps:
    repository: object
    order: OrderRecord
    event: DeliveryEvent
    run_id: str
    model_name: str
    low_battery_threshold: int
    backends: ToolBackends


COURIER_SYSTEM_PROMPT = """
You are CourierAgent, an autonomous professional delivery worker.

Mission:
- Deliver the package safely and on time.
- Follow the operating manual and business rules.
- Choose tools when needed; do not wait for a rigid workflow.

Explainability rules:
- Never reveal raw chain-of-thought.
- Return a concise, auditable reasoning summary.
- Mention the rules you used.
- Prefer concrete next actions.

Operational rules:
- Do not guess a gate code more than twice.
- If the battery is low, prioritize charging and dispatch notification.
- If the customer is unavailable after repeated attempts, escalate.
- If weather or route risk is high, replan and report the risk.
""".strip()


def build_agent_model(*, model_name: str, provider: str, api_key: str | None, base_url: str | None) -> Any:
    if provider == "openai-compatible":
        model_id = model_name.split(":", maxsplit=1)[1] if model_name.startswith("openai:") else model_name
        provider_config = OpenAIProvider(base_url=base_url, api_key=api_key) if base_url else OpenAIProvider(api_key=api_key)
        return OpenAIChatModel(model_id, provider=provider_config)

    if provider == "test":
        from pydantic_ai.models.test import TestModel

        return TestModel()

    return model_name


def build_courier_agent(
    model_name: str,
    *,
    provider: str = "openai-compatible",
    api_key: str | None = None,
    base_url: str | None = None,
) -> Agent[CourierDeps, CourierDecision]:
    model = build_agent_model(model_name=model_name, provider=provider, api_key=api_key, base_url=base_url)
    agent = Agent(
        model,
        deps_type=CourierDeps,
        output_type=CourierDecision,
        instructions=COURIER_SYSTEM_PROMPT,
    )

    @agent.instructions
    async def add_operating_context(ctx: RunContext[CourierDeps]) -> str:
        return "\n".join(
            [
                f"Current time: {datetime.utcnow().isoformat()}Z",
                f"Order status: {ctx.deps.order.status}",
                f"Battery level: {ctx.deps.order.battery_level}",
                f"Current location: {ctx.deps.order.current_location}",
                f"Customer contact attempts: {ctx.deps.order.customer_contact_attempts}",
                f"Current event: {ctx.deps.event.model_dump()}",
                "Manual summary:",
                manual_overview(),
            ]
        )

    @agent.tool
    async def search_delivery_manual(ctx: RunContext[CourierDeps], query: str) -> str:
        matches = search_manual(query)
        payload = [
            {"rule_id": rule.rule_id, "title": rule.title, "description": rule.description}
            for rule in matches
        ]
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="search_delivery_manual",
            input_json={"query": query},
            output_json={"matches": payload},
            status="success",
        )
        return "\n".join(f"{item['title']}: {item['description']}" for item in payload) or "No matching rule found."

    @agent.tool
    async def call_customer(ctx: RunContext[CourierDeps], message: str) -> str:
        result = ctx.deps.backends.customer.contact(message)
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="call_customer",
            input_json={"message": message},
            output_json=result.to_record(),
            status="success" if result.status == "ok" else "error",
        )
        return result.as_tool_string()

    @agent.tool
    async def notify_dispatch(ctx: RunContext[CourierDeps], reason: str) -> str:
        result = ctx.deps.backends.dispatch.notify(reason)
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="notify_dispatch",
            input_json={"reason": reason},
            output_json=result.to_record(),
            status="success" if result.status == "ok" else "error",
        )
        return result.as_tool_string()

    @agent.tool
    async def plan_route(ctx: RunContext[CourierDeps], destination: str) -> str:
        result = ctx.deps.backends.routing.plan(ctx.deps.order.current_location, destination)
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="plan_route",
            input_json={"origin": ctx.deps.order.current_location, "destination": destination},
            output_json=result.to_record(),
            status="success" if result.status == "ok" else "error",
        )
        return result.as_tool_string()

    @agent.tool
    async def update_memory(ctx: RunContext[CourierDeps], key: str, value: str, scope: str = "order") -> str:
        payload = {"value": value, "event": ctx.deps.event.model_dump(), "run_id": ctx.deps.run_id}
        if scope == "global":
            ctx.deps.repository.record_global_memory(key, payload)
        else:
            ctx.deps.repository.record_memory(ctx.deps.order.order_id, key, payload)
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="update_memory",
            input_json={"key": key, "value": value, "scope": scope},
            output_json={"stored": True, "scope": scope},
            status="success",
        )
        return f"Stored {scope} memory for {key}."

    @agent.tool
    async def recall_global_memory(ctx: RunContext[CourierDeps], key: str) -> str:
        memory = ctx.deps.repository.get_global_memory(key)
        if memory is None:
            output = {"found": False, "key": key}
            detail = f"no global memory for {key}"
        else:
            output = {"found": True, "key": key, "value": memory.get("value")}
            detail = f"global memory {key} = {memory.get('value')}"
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="recall_global_memory",
            input_json={"key": key},
            output_json=output,
            status="success",
        )
        return detail

    return agent
