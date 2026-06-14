from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManualRule:
    rule_id: str
    title: str
    description: str
    keywords: tuple[str, ...]


MANUAL_RULES: tuple[ManualRule, ...] = (
    ManualRule(
        rule_id="manual.low_battery",
        title="Low battery escalation",
        description="If battery drops below threshold, prioritize charging and notify dispatch.",
        keywords=("battery", "charge", "power", "low battery"),
    ),
    ManualRule(
        rule_id="manual.gate_code",
        title="Gate code handling",
        description="Do not guess gate codes more than twice; contact customer and record the result.",
        keywords=("gate", "code", "entrance", "door", "locked"),
    ),
    ManualRule(
        rule_id="manual.customer_unavailable",
        title="Customer unavailable",
        description="Retry contact twice, then escalate or hand back to dispatch.",
        keywords=("customer", "unavailable", "no answer", "phone"),
    ),
    ManualRule(
        rule_id="manual.weather_risk",
        title="Weather risk handling",
        description="Replan route or pause if weather makes delivery unsafe.",
        keywords=("weather", "rain", "storm", "snow", "wind"),
    ),
)


def search_manual(query: str) -> list[ManualRule]:
    normalized_query = query.lower()
    matches: list[ManualRule] = []
    for rule in MANUAL_RULES:
        if any(keyword in normalized_query for keyword in rule.keywords):
            matches.append(rule)
    return matches


def manual_overview() -> str:
    return "\n".join(f"- {rule.title}: {rule.description}" for rule in MANUAL_RULES)
