from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_JWT_SECRET = "replace-with-long-random-secret"
DataMode = Literal["live", "demo"]
AppEnv = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """进程配置。密钥只从环境读取，不写入响应。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "development"
    app_base_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:5173"
    database_url: str = "postgresql+psycopg://finscope:change-me@localhost:5432/finscope"
    jwt_secret: str = PLACEHOLDER_JWT_SECRET
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    fred_api_key: str = ""
    sec_user_agent: str = "FinScopeAI research-admin@example.com"
    tushare_token: str = ""
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    embedding_api_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dim: int = 1536
    ai_daily_token_budget: int = 200_000
    enable_news: bool = False
    data_mode: DataMode = "live"
    upload_max_mb: int = 30
    log_level: str = "INFO"
    worker_heartbeat_interval_seconds: int = 5
    worker_heartbeat_ttl_seconds: int = 30
    worker_service_name: str = "worker"
    login_max_failures: int = 5
    login_failure_window_seconds: int = 300

    @field_validator("frontend_origin")
    @classmethod
    def normalize_origins(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def check_runtime_policy(self) -> "Settings":
        if self.worker_heartbeat_ttl_seconds <= self.worker_heartbeat_interval_seconds:
            raise ValueError("WORKER_HEARTBEAT_TTL_SECONDS 必须大于心跳间隔")
        if self.login_max_failures < 1 or self.login_failure_window_seconds < 1:
            raise ValueError("登录失败限制必须为正数")
        if self.app_env == "production":
            if self.jwt_secret == PLACEHOLDER_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("生产环境 JWT_SECRET 不能使用示例值，且长度至少 32")
            if "change-me" in self.database_url:
                raise ValueError("生产环境 DATABASE_URL 不能使用示例口令")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.frontend_origin.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
