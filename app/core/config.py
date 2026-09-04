from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
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
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
