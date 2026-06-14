from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .manual import manual_overview, search_manual
from .schemas import CourierDecision, DeliveryEvent, OrderRecord


@dataclass
class CourierDeps:
    repository: object
    order: OrderRecord
    event: DeliveryEvent
    run_id: str
    model_name: str
    low_battery_threshold: int


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
        attempts = ctx.deps.order.customer_contact_attempts + 1
        response = "No response yet." if attempts < 2 else "Customer replied: gate code is 1234."
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="call_customer",
            input_json={"message": message, "attempt": attempts},
            output_json={"response": response},
            status="success",
        )
        return response

    @agent.tool
    async def notify_dispatch(ctx: RunContext[CourierDeps], reason: str) -> str:
        message = f"Dispatch notified: {reason}"
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="notify_dispatch",
            input_json={"reason": reason},
            output_json={"message": message},
            status="success",
        )
        return message

    @agent.tool
    async def plan_route(ctx: RunContext[CourierDeps], destination: str) -> str:
        route = f"Route planned from {ctx.deps.order.current_location} to {destination}"
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="plan_route",
            input_json={"destination": destination},
            output_json={"route": route},
            status="success",
        )
        return route

    @agent.tool
    async def update_memory(ctx: RunContext[CourierDeps], key: str, value: str) -> str:
        ctx.deps.repository.record_memory(
            ctx.deps.order.order_id,
            key,
            {"value": value, "event": ctx.deps.event.model_dump(), "run_id": ctx.deps.run_id},
        )
        ctx.deps.repository.record_tool_call(
            run_id=ctx.deps.run_id,
            order_id=ctx.deps.order.order_id,
            tool_name="update_memory",
            input_json={"key": key, "value": value},
            output_json={"stored": True},
            status="success",
        )
        return f"Stored memory for {key}."

    return agent
