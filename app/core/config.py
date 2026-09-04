from functools import lru_cache

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
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
