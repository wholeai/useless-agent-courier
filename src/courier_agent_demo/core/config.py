from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "courier-agent-demo"
    environment: str = "development"
    database_path: str = Field(default="data/courier_agent.db", alias="DATABASE_PATH")
    agent_model: str = Field(default="gpt-4o-mini", alias="COURIER_AGENT_MODEL")
    agent_provider: str = Field(default="openai-compatible", alias="COURIER_AGENT_PROVIDER")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    heartbeat_interval_seconds: int = Field(default=10, alias="HEARTBEAT_INTERVAL_SECONDS")
    low_battery_threshold: int = Field(default=15, alias="LOW_BATTERY_THRESHOLD")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # Real-world integrations. Empty / unset = log-only mode (no network call).
    dingtalk_webhook_url: str | None = Field(default=None, alias="DINGTALK_WEBHOOK_URL")
    customer_contact_webhook_url: str | None = Field(default=None, alias="CUSTOMER_CONTACT_WEBHOOK_URL")
    routing_base_url: str | None = Field(default=None, alias="ROUTING_BASE_URL")
    integration_timeout_seconds: int = Field(default=5, alias="INTEGRATION_TIMEOUT_SECONDS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
