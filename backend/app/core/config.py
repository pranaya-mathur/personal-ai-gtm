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

    # LLM: OpenAI is the default provider. Mock mode (default) is offline and free; a live call
    # needs mock mode OFF, paid calls enabled, an API key, an explicit model and explicit prices.
    llm_provider: str = "openai"
    llm_mock_mode: bool = True
    llm_paid_calls_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    # Optional per-capability overrides; empty means "use OPENAI_MODEL". Never silently
    # substituted: a task whose model is unavailable fails visibly.
    openai_discovery_model: str = ""
    openai_research_model: str = ""
    openai_qualification_model: str = ""
    openai_outreach_model: str = ""
    openai_analytics_model: str = ""
    # Prices are supplied by you (USD per 1M tokens); none are assumed. Cost tracking and spend
    # limits refuse live calls while these are unset.
    openai_input_usd_per_mtok: float | None = None
    openai_output_usd_per_mtok: float | None = None
    openai_cached_input_usd_per_mtok: float | None = None
    llm_max_output_tokens: int = 1500
    llm_max_retries: int = 3
    llm_retry_base_seconds: float = 0.5
    llm_spend_limit_per_run_usd: float = 0.50
    ai_max_opportunities_per_run: int = 10
    # Qualification requires the evidence to mention at least one of these (whole words). Keeps
    # generic IT/software procurement from qualifying as an AI project.
    topic_terms: str = (
        "ai,artificial intelligence,machine learning,generative,llm,large language,rag,"
        "retrieval,chatbot,neural,nlp,agentic,computer vision,deep learning"
    )

    # Controlled-test lifetime caps per provider family (None = no test cap). Enforced before each
    # call against the shared spend ledger, including estimated in-flight cost of the next call.
    openai_test_budget_usd: float | None = None
    apify_test_budget_usd: float | None = None
    total_test_budget_usd: float | None = None
    run_timeout_seconds: int = 900

    # Apify: nothing paid runs unless the token is set AND paid execution is approved.
    apify_token: SecretStr | None = None
    apify_paid_execution_approved: bool = False
    apify_api_base: str = "https://api.apify.com"

    # Source verification fetches the cited page; off by default (mock URLs are fictional).
    source_verification_enabled: bool = False

    worker_poll_interval_seconds: float = 2.0
    worker_max_attempts: int = 3

    @property
    def topic_term_list(self) -> list[str]:
        return [t.strip().lower() for t in self.topic_terms.split(",") if t.strip()]

    def model_for(self, task: str) -> str:
        """Model for an AI task; falls back to OPENAI_MODEL when no override is configured."""
        by_stage = {
            "opportunity_analysis": self.openai_discovery_model,
            "requirement_extraction": self.openai_discovery_model,
            "evidence_evaluation": self.openai_research_model,
            "research_brief": self.openai_research_model,
            "qualification": self.openai_qualification_model,
            "outreach_draft": self.openai_outreach_model,
            "analytics": self.openai_analytics_model,
        }
        return by_stage.get(task, "") or self.openai_model

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_connector_names(self) -> list[str]:
        return [c.strip() for c in self.enabled_connectors.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
