"""Settings loaded once from the environment (12-factor). Every other module
takes a ``Settings`` instance explicitly rather than importing this module
and reaching for globals — same discipline the retrieval library already
uses for ``Config`` (see src/clinical_retrieval/common/config.py), so the
service and the library are consistent about where configuration lives.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Core ---
    env: str = Field(default="development", alias="APP_ENV")
    debug: bool = False

    # --- Database ---
    # Two roles, deliberately: `clinical_app` owns the schema and runs
    # migrations; `clinical_runtime` is what the API and worker connect as.
    # Table owners bypass Postgres RLS unless FORCE ROW LEVEL SECURITY is
    # set, so keeping the runtime role distinct from the owner means RLS is
    # enforced even if a migration ever forgets the FORCE clause — see
    # docs/adr/0001-postgres-rls-over-application-authz.md.
    database_url: str = Field(
        default="postgresql+asyncpg://clinical_runtime:runtimepassword@localhost:5432/clinical_evidence",
        alias="DATABASE_URL",
    )
    database_url_migrator: str = Field(
        default="postgresql+asyncpg://clinical_app:devpassword@localhost:5432/clinical_evidence",
        alias="DATABASE_URL_MIGRATOR",
    )
    db_pool_size: int = 10
    db_pool_timeout_seconds: float = 5.0
    db_statement_timeout_ms: int = 5000

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_timeout_seconds: float = 2.0

    # --- Auth ---
    jwt_secret: str = Field(default="dev-secret-change-me-in-every-real-deployment", alias="JWT_SECRET")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 7 * 24 * 60 * 60

    # --- Idempotency ---
    idempotency_key_ttl_seconds: int = 24 * 60 * 60

    # --- Ingestion / queue ---
    ingest_max_retries: int = 3
    ingest_backoff_base_seconds: float = 1.0
    ingest_backoff_multiplier: float = 4.0  # 1s, 4s, 16s
    ingest_backoff_jitter_ratio: float = 0.25

    # --- Rate limiting ---
    rate_limit_requests_per_minute: int = 50
    rate_limit_burst: int = 50

    # --- Retrieval / RAG ---
    embedding_dim: int = 256
    retrieval_top_k: int = 5

    # --- LLM serving ---
    llm_model: str = Field(default="not-configured", alias="LLM_MODEL")
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_call_timeout_seconds: float = 10.0
    llm_max_tokens_per_request: int = 512

    # --- Circuit breaker ---
    breaker_failure_threshold: int = 5
    breaker_open_seconds: float = 30.0

    # --- Observability ---
    request_id_header: str = "X-Request-ID"

    # --- Data corpus (reused from the retrieval library) ---
    raw_dir: str = "data/raw"


@lru_cache
def get_settings() -> Settings:
    return Settings()
