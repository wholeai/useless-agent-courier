from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .schemas import AgentRunRecord, DeliveryEvent, OrderCreateRequest, OrderRecord, TimelineItem


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _deserialize(text: str | None) -> object:
    if not text:
        return {}
    return json.loads(text)


@dataclass
class RepositoryStats:
    active_orders: int
    total_orders: int
    total_runs: int


class CourierRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = threading.Lock()
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    pickup_address TEXT NOT NULL,
                    delivery_address TEXT NOT NULL,
                    package_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    battery_level INTEGER NOT NULL,
                    current_location TEXT NOT NULL,
                    customer_contact_attempts INTEGER NOT NULL,
                    last_event_type TEXT NOT NULL,
                    last_reason TEXT NOT NULL,
                    next_checkin_at TEXT,
                    updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id)
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT,
                    reason_summary TEXT,
                    matched_rules_json TEXT NOT NULL,
                    next_actions_json TEXT NOT NULL,
                    next_checkin_minutes INTEGER,
                    confidence REAL,
                    model_name TEXT NOT NULL,
                    input_summary TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id)
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    tool_call_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id),
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS heartbeats (
                    heartbeat_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    battery_level INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id)
                );

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    memory_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id)
                );

                CREATE TABLE IF NOT EXISTS global_memories (
                    memory_key TEXT PRIMARY KEY,
                    memory_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def create_order(self, request: OrderCreateRequest) -> OrderRecord:
        now = _utc_now()
        order = OrderRecord(
            order_id=str(uuid4()),
            customer_name=request.customer_name,
            pickup_address=request.pickup_address,
            delivery_address=request.delivery_address,
            package_label=request.package_label,
            status="assigned",
            battery_level=request.starting_battery_level,
            current_location="hub",
            customer_contact_attempts=0,
            last_event_type="start",
            last_reason="order created",
            updated_at=now,
            created_at=now,
            metadata={"source": "api"},
        )
        self.save_order(order)
        return order

    def save_order(self, order: OrderRecord) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (
                    order_id, customer_name, pickup_address, delivery_address, package_label,
                    status, battery_level, current_location, customer_contact_attempts,
                    last_event_type, last_reason, next_checkin_at, updated_at, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    customer_name=excluded.customer_name,
                    pickup_address=excluded.pickup_address,
                    delivery_address=excluded.delivery_address,
                    package_label=excluded.package_label,
                    status=excluded.status,
                    battery_level=excluded.battery_level,
                    current_location=excluded.current_location,
                    customer_contact_attempts=excluded.customer_contact_attempts,
                    last_event_type=excluded.last_event_type,
                    last_reason=excluded.last_reason,
                    next_checkin_at=excluded.next_checkin_at,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    order.order_id,
                    order.customer_name,
                    order.pickup_address,
                    order.delivery_address,
                    order.package_label,
                    order.status,
                    order.battery_level,
                    order.current_location,
                    order.customer_contact_attempts,
                    order.last_event_type,
                    order.last_reason,
                    order.next_checkin_at.isoformat() if order.next_checkin_at else None,
                    order.updated_at.isoformat(),
                    order.created_at.isoformat(),
                    _serialize(order.metadata),
                ),
            )

    def get_order(self, order_id: str) -> OrderRecord:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(f"Order not found: {order_id}")
        return self._row_to_order(row)

    def list_active_orders(self) -> list[OrderRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM orders WHERE status NOT IN ('delivered', 'escalated') ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_order(row) for row in rows]

    def append_event(self, order_id: str, event: DeliveryEvent) -> str:
        event_id = str(uuid4())
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events (event_id, order_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, order_id, event.event_type, event.message, _serialize(event.model_dump()), now.isoformat()),
            )
        return event_id

    def record_run(self, run: AgentRunRecord) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id, order_id, event_type, status, decision, reason_summary,
                    matched_rules_json, next_actions_json, next_checkin_minutes,
                    confidence, model_name, input_summary, output_json, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    decision=excluded.decision,
                    reason_summary=excluded.reason_summary,
                    matched_rules_json=excluded.matched_rules_json,
                    next_actions_json=excluded.next_actions_json,
                    next_checkin_minutes=excluded.next_checkin_minutes,
                    confidence=excluded.confidence,
                    output_json=excluded.output_json,
                    finished_at=excluded.finished_at
                """,
                (
                    run.run_id,
                    run.order_id,
                    run.event_type,
                    run.status,
                    run.decision,
                    run.reason_summary,
                    _serialize(run.matched_rules),
                    _serialize(run.next_actions),
                    run.next_checkin_minutes,
                    run.confidence,
                    run.model_name,
                    run.input_summary,
                    _serialize(run.output_json),
                    run.created_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                ),
            )

    def record_tool_call(self, *, run_id: str, order_id: str, tool_name: str, input_json: dict, output_json: dict, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls (tool_call_id, run_id, order_id, tool_name, input_json, output_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    run_id,
                    order_id,
                    tool_name,
                    _serialize(input_json),
                    _serialize(output_json),
                    status,
                    _utc_now().isoformat(),
                ),
            )

    def get_tool_calls_by_run(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT tool_name, input_json, output_json, status, created_at FROM tool_calls WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [
            {
                "tool_name": row["tool_name"],
                "input": _deserialize(row["input_json"]),
                "output": _deserialize(row["output_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def record_heartbeat(self, order_id: str, status: str, battery_level: int, note: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO heartbeats (heartbeat_id, order_id, status, battery_level, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), order_id, status, battery_level, note, _utc_now().isoformat()),
            )

    def record_memory(self, order_id: str, memory_key: str, memory_json: dict) -> None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT memory_id FROM memories WHERE order_id = ? AND memory_key = ?",
                (order_id, memory_key),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO memories (memory_id, order_id, memory_key, memory_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid4()), order_id, memory_key, _serialize(memory_json), now.isoformat(), now.isoformat()),
                )
            else:
                connection.execute(
                    """
                    UPDATE memories SET memory_json = ?, updated_at = ?
                    WHERE order_id = ? AND memory_key = ?
                    """,
                    (_serialize(memory_json), now.isoformat(), order_id, memory_key),
                )

    def record_global_memory(self, memory_key: str, memory_json: dict) -> None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT memory_key FROM global_memories WHERE memory_key = ?",
                (memory_key,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO global_memories (memory_key, memory_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (memory_key, _serialize(memory_json), now.isoformat(), now.isoformat()),
                )
            else:
                connection.execute(
                    "UPDATE global_memories SET memory_json = ?, updated_at = ? WHERE memory_key = ?",
                    (_serialize(memory_json), now.isoformat(), memory_key),
                )

    def get_global_memory(self, memory_key: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT memory_json FROM global_memories WHERE memory_key = ?",
                (memory_key,),
            ).fetchone()
        if row is None:
            return None
        value = _deserialize(row["memory_json"])
        return value if isinstance(value, dict) else None

    def list_global_memory_keys(self) -> list[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT memory_key FROM global_memories ORDER BY updated_at DESC"
            ).fetchall()
        return [row["memory_key"] for row in rows]

    def get_timeline(self, order_id: str) -> list[TimelineItem]:
        with self._lock, self._connect() as connection:
            event_rows = connection.execute(
                "SELECT event_type, message, payload_json, created_at FROM events WHERE order_id = ? ORDER BY created_at ASC",
                (order_id,),
            ).fetchall()
            run_rows = connection.execute(
                """
                SELECT run_id, decision, reason_summary, matched_rules_json, next_actions_json, next_checkin_minutes,
                       confidence, input_summary, output_json, created_at, finished_at
                FROM agent_runs WHERE order_id = ? ORDER BY created_at ASC
                """,
                (order_id,),
            ).fetchall()
            tool_rows = connection.execute(
                """
                SELECT tool_name, input_json, output_json, status, created_at
                FROM tool_calls WHERE order_id = ? ORDER BY created_at ASC
                """,
                (order_id,),
            ).fetchall()
            heartbeat_rows = connection.execute(
                "SELECT status, battery_level, note, created_at FROM heartbeats WHERE order_id = ? ORDER BY created_at ASC",
                (order_id,),
            ).fetchall()

        items: list[TimelineItem] = []
        for row in event_rows:
            items.append(
                TimelineItem(
                    item_type="event",
                    created_at=datetime.fromisoformat(row["created_at"]),
                    payload={
                        "event_type": row["event_type"],
                        "message": row["message"],
                        "payload": _deserialize(row["payload_json"]),
                    },
                )
            )
        for row in run_rows:
            items.append(
                TimelineItem(
                    item_type="run",
                    created_at=datetime.fromisoformat(row["created_at"]),
                    payload={
                        "run_id": row["run_id"],
                        "decision": row["decision"],
                        "reason_summary": row["reason_summary"],
                        "matched_rules": _deserialize(row["matched_rules_json"]),
                        "next_actions": _deserialize(row["next_actions_json"]),
                        "next_checkin_minutes": row["next_checkin_minutes"],
                        "confidence": row["confidence"],
                        "input_summary": row["input_summary"],
                        "output": _deserialize(row["output_json"]),
                        "finished_at": row["finished_at"],
                    },
                )
            )
        for row in tool_rows:
            items.append(
                TimelineItem(
                    item_type="tool_call",
                    created_at=datetime.fromisoformat(row["created_at"]),
                    payload={
                        "tool_name": row["tool_name"],
                        "input": _deserialize(row["input_json"]),
                        "output": _deserialize(row["output_json"]),
                        "status": row["status"],
                    },
                )
            )
        for row in heartbeat_rows:
            items.append(
                TimelineItem(
                    item_type="heartbeat",
                    created_at=datetime.fromisoformat(row["created_at"]),
                    payload={
                        "status": row["status"],
                        "battery_level": row["battery_level"],
                        "note": row["note"],
                    },
                )
            )
        return sorted(items, key=lambda item: item.created_at)

    def stats(self) -> RepositoryStats:
        with self._lock, self._connect() as connection:
            active_orders = connection.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE status NOT IN ('delivered', 'escalated')"
            ).fetchone()["count"]
            total_orders = connection.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"]
            total_runs = connection.execute("SELECT COUNT(*) AS count FROM agent_runs").fetchone()["count"]
        return RepositoryStats(active_orders=active_orders, total_orders=total_orders, total_runs=total_runs)

    def _row_to_order(self, row: sqlite3.Row) -> OrderRecord:
        return OrderRecord(
            order_id=row["order_id"],
            customer_name=row["customer_name"],
            pickup_address=row["pickup_address"],
            delivery_address=row["delivery_address"],
            package_label=row["package_label"],
            status=row["status"],
            battery_level=row["battery_level"],
            current_location=row["current_location"],
            customer_contact_attempts=row["customer_contact_attempts"],
            last_event_type=row["last_event_type"],
            last_reason=row["last_reason"],
            next_checkin_at=datetime.fromisoformat(row["next_checkin_at"]) if row["next_checkin_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=_deserialize(row["metadata_json"]),
        )
