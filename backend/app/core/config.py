from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://gtm:gtm@localhost:5432/gtm"
    cors_origins: str = "http://localhost:3000"

    # Auth: "name:role:token,..." roles viewer|operator|admin. Empty = API refuses everything
    # (except /health). AUTH_DISABLED is a local-development escape hatch only.
    api_tokens: str = ""
    auth_disabled: bool = False
    api_rate_limit_per_minute: int = 300
    api_mutation_limit_per_minute: int = 30

    # Free, keyless discovery. Keywords drive the official-API connectors (TED, World Bank).
    discovery_keywords: str = (
        "artificial intelligence,machine learning,generative AI,chatbot,large language model,"
        "retrieval-augmented generation,AI agent,document processing,intelligent automation"
    )
    enabled_discoverers: str = ""  # e.g. "wikidata,github": automatic candidate discovery (network)
    candidates_per_run: int = 15  # candidate domains read per run
    # Intent-focused search (OPTIONAL, billable: Brave ~$0.005/request). Edit freely; each query
    # must describe a REQUEST, not a topic. Nothing runs unless SEARCH_DISCOVERY_ENABLED=true.
    intent_queries_agency: str = (
        '"looking for" "AI development partner" agency subcontract|'
        '"seeking" "white-label" AI engineering partner agency|'
        '"we are looking for" subcontractors "machine learning" OR "LLM" agency partner|'
        '"looking for" "{facet}" subcontractors OR "delivery partners" agency'
    )
    intent_queries_buyer: str = (
        '"request for proposal" chatbot OR "AI agent" OR "RAG" development|'
        '"looking for" developer to build "AI agent" OR "RAG" OR "document processing" budget|'
        '"RFP" "generative AI" "proposals due"|'
        '"looking for" developer to build "{facet}" project budget -hiring -jobs'
    )
    search_freshness: str = "pm"  # Brave: pd | pw | pm (31 days) | py
    search_max_pages: int = 15  # pages read per run
    query_plan_max_per_channel: int = 8  # configured + expanded queries per channel per plan
    apify_search_engines: str = "google,bing"  # Apify SERP Actors (billable; gated)
    apify_search_bing_queries: int = 1  # queries per channel sent to Bing (billed per result)
    search_budget_usd: float | None = None  # lifetime cap for the "brave" spend family
    brave_api_key: SecretStr | None = None
    search_discovery_enabled: bool = False  # Brave queries only when explicitly turned on
    # SAM.gov public Opportunities API (free key from sam.gov Account Details). Off without key.
    samgov_api_key: SecretStr | None = None

    enabled_connectors: str = "mock_opportunities,mock_agencies"
    # Scheduled discovery. Free sources only: paid providers are never scheduled unless this flag
    # is ON *and* the operator enabled that paid schedule *and* the provider's own approval flag
    # and budget are set. Off by default.
    scheduler_enabled: bool = False
    scheduler_allow_paid: bool = False

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
    # Per-record / per-task bounds so one record or task cannot consume the run budget.
    ai_record_budget_usd: float = 0.05
    ai_max_calls_per_record: int = 3  # per run: 1 dossier + 1 repair + 1 draft
    ai_task_budget_usd: float = 0.25  # per task per run
    ai_max_repair_attempts: int = 1
    # Optional extra model call that judges ONLY claims the deterministic check found ambiguous.
    ai_semantic_check_enabled: bool = False
    ai_task_max_output_tokens: str = "dossier=2500,outreach_draft=900"
    llm_retry_base_seconds: float = 0.5
    llm_spend_limit_per_run_usd: float = 0.50
    ai_max_opportunities_per_run: int = 10
    # Qualification requires the evidence to mention at least one of these (whole words). Keeps
    # generic IT/software procurement from qualifying as an AI project.
    # Deal-size fit (compared numerically for EUR/USD/GBP only; other currencies stay unknown).
    deal_preferred_min: float = 3000.0
    deal_preferred_max: float = 10000.0
    deal_acceptable_max: float = 100000.0  # above this = not realistic for a small team
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

    # Agency partnership discovery (free, permitted public sources). Seeds are YOUR list of
    # company domains/pages and announcement feeds; nothing is guessed or crawled beyond them.
    agency_seed_urls: str = ""
    agency_feed_urls: str = ""
    agency_pages_max: int = 20

    worker_poll_interval_seconds: float = 2.0
    worker_max_attempts: int = 3
    run_lease_seconds: int = 120  # a worker owns a run only while its lease is renewed
    run_heartbeat_seconds: float = 30.0

    @property
    def intent_queries_agency_list(self) -> list[str]:
        return [q.strip() for q in self.intent_queries_agency.split("|") if q.strip()]

    @property
    def intent_queries_buyer_list(self) -> list[str]:
        return [q.strip() for q in self.intent_queries_buyer.split("|") if q.strip()]

    @property
    def discovery_keyword_list(self) -> list[str]:
        return [k.strip() for k in self.discovery_keywords.split(",") if k.strip()]

    @property
    def enabled_discoverer_list(self) -> list[str]:
        return [k.strip() for k in self.enabled_discoverers.split(",") if k.strip()]

    @property
    def agency_seed_list(self) -> list[str]:
        return [u.strip() for u in self.agency_seed_urls.split(",") if u.strip()]

    @property
    def agency_feed_list(self) -> list[str]:
        return [u.strip() for u in self.agency_feed_urls.split(",") if u.strip()]

    @property
    def topic_term_list(self) -> list[str]:
        return [t.strip().lower() for t in self.topic_terms.split(",") if t.strip()]

    def max_output_for(self, task: str) -> int:
        for part in self.ai_task_max_output_tokens.split(","):
            name, _, val = part.partition("=")
            if name.strip() == task and val.strip().isdigit():
                return int(val)
        return self.llm_max_output_tokens

    def model_for(self, task: str) -> str:
        """Model for an AI task; falls back to OPENAI_MODEL when no override is configured."""
        by_stage = {
            "dossier": self.openai_research_model,
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
