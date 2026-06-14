# Courier Agent Demo

一个可 Docker 部署的职业型 LLM Agent demo：由大模型自主判断、选择工具、遵循 SOP，并把每次决策写入 SQLite 和审计日志。

## Features
- LLM 驱动的 `CourierAgent`
- FastAPI API
- SQLite 持久化
- Heartbeat worker
- 结构化 JSON 日志
- 可追踪的 audit timeline

## Tech Stack
- Python 3.10+
- FastAPI
- PydanticAI
- SQLite
- Uvicorn

## Setup
```bash
python -m pip install -e ".[dev]"
```

Set environment variables before running:
- Copy `env.example` to `.env`, then set your model endpoint.
- `OPENAI_API_KEY` for OpenAI or any OpenAI-compatible gateway.
- `OPENAI_BASE_URL` for the OpenAI-compatible endpoint, such as `https://api.openai.com/v1`.
- `COURIER_AGENT_PROVIDER`, defaults to `openai-compatible`.
- `COURIER_AGENT_MODEL`, such as `gpt-4o-mini` or a model name from your compatible gateway.
- `DATABASE_PATH`
- `HEARTBEAT_INTERVAL_SECONDS`
- `LOW_BATTERY_THRESHOLD`
- `LOG_LEVEL`

## Run
```bash
uvicorn courier_agent_demo.app:app --reload
```

## Docker
```bash
docker compose up --build
```

## API
### `GET /api/v1/health`
Health and database status.

### `POST /api/v1/orders`
Create an order and trigger the first agent decision.

### `POST /api/v1/orders/{order_id}/events`
Push a new event such as heartbeat, customer reply, or exception.

### `GET /api/v1/orders/{order_id}`
Fetch current order state.

### `GET /api/v1/orders/{order_id}/timeline`
Inspect events, runs, and tool calls.

## Explainability Model
The service does **not** expose raw chain-of-thought. It returns a safe, auditable summary instead:
- `decision`
- `reason_summary`
- `matched_rules`
- `next_actions`
- `next_checkin_minutes`

## Notes
- The demo is event-driven, not a rigid state machine.
- Heartbeat is a scheduler input, not the business logic itself.
- SQLite is used for local-first persistence and replayable debugging.
