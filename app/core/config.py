from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AgentReview"
    app_version: str = "0.1.0"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    database_url: str = "mysql+pymysql://agent_review:agent_review@localhost:3307/agent_review"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    review_worker_max_retries: int = Field(default=3, ge=0, le=10)
    review_retry_base_seconds: int = Field(default=5, ge=1, le=300)
    review_retry_max_seconds: int = Field(default=300, ge=1, le=3_600)
    github_webhook_secret: SecretStr | None = None
    github_app_client_id: str | None = None
    github_private_key_path: Path | None = None
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2026-03-10"
    github_http_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    github_token_refresh_buffer_seconds: int = Field(default=300, ge=0, le=900)
    github_max_pull_request_files: int = Field(default=3_000, ge=1, le=3_000)
    review_max_file_changes: int = Field(default=2_000, ge=1)
    review_max_file_patch_characters: int = Field(default=100_000, ge=1)
    review_max_total_patch_characters: int = Field(default=300_000, ge=1)
    github_review_body_max_characters: int = Field(default=60_000, ge=1_000, le=65_000)
    llm_provider: Literal["mock", "openai", "deepseek"] = "mock"
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    # Kept for backward compatibility; prefer LLM_API_KEY for new configurations.
    openai_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    llm_max_output_tokens: int = Field(default=4_096, ge=1)
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)

    @model_validator(mode="after")
    def validate_retry_delays(self) -> Self:
        if self.review_retry_max_seconds < self.review_retry_base_seconds:
            raise ValueError(
                "REVIEW_RETRY_MAX_SECONDS must be greater than or equal to "
                "REVIEW_RETRY_BASE_SECONDS"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
