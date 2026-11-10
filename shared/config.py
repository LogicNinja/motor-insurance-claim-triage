"""Central settings, read only from environment variables (D4)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    redis_url: str
    database_url: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    llm_max_retries: int
    llm_backoff_base_seconds: float
    breaker_failure_threshold: int
    breaker_cooldown_seconds: float
    retrieval_url: str
    verifier_url: str
    max_agent_steps: int
    max_tool_calls: int
    run_timeout_seconds: float
    rate_limit_per_minute: int
    cors_origins: str
    schema_dir: str
    prompt_version: str


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        redis_url=e("REDIS_URL", ""),
        database_url=e("DATABASE_URL", ""),
        llm_base_url=e("LLM_BASE_URL", ""),
        llm_api_key=e("LLM_API_KEY", ""),
        llm_model=e("LLM_MODEL", ""),
        llm_timeout_seconds=float(e("LLM_TIMEOUT_SECONDS", "30")),
        llm_max_retries=int(e("LLM_MAX_RETRIES", "3")),
        llm_backoff_base_seconds=float(e("LLM_BACKOFF_BASE_SECONDS", "1.0")),
        breaker_failure_threshold=int(e("BREAKER_FAILURE_THRESHOLD", "5")),
        breaker_cooldown_seconds=float(e("BREAKER_COOLDOWN_SECONDS", "30")),
        retrieval_url=e("RETRIEVAL_URL", ""),
        verifier_url=e("VERIFIER_URL", ""),
        max_agent_steps=int(e("MAX_AGENT_STEPS", "8")),
        max_tool_calls=int(e("MAX_TOOL_CALLS", "12")),
        run_timeout_seconds=float(e("RUN_TIMEOUT_SECONDS", "90")),
        rate_limit_per_minute=int(e("RATE_LIMIT_PER_MINUTE", "30")),
        cors_origins=e("CORS_ORIGINS", ""),
        schema_dir=e("SCHEMA_DIR", "/schemas"),
        prompt_version=e("PROMPT_VERSION", "v1"),
    )
