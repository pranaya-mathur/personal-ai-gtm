from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://gtm:gtm@localhost:5432/gtm"
    cors_origins: str = "http://localhost:3000"

    enabled_connectors: str = "mock_opportunities,mock_agencies"

    spend_limit_per_run_usd: float = 1.00
    spend_limit_daily_usd: float = 5.00
    spend_limit_monthly_usd: float = 50.00

    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    apify_token: SecretStr | None = None

    worker_poll_interval_seconds: float = 2.0
    worker_max_attempts: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_connector_names(self) -> list[str]:
        return [c.strip() for c in self.enabled_connectors.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
