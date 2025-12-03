"""
ARED Edge IOTA Anchor Service - Configuration
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )

    # Application
    APP_NAME: str = "ARED IOTA Anchor"
    VERSION: str = "1.0.0"
    ENV: str = "development"
    DEBUG: bool = False

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8082
    WORKERS: int = 2
    LOG_LEVEL: str = "INFO"

    # Database
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "ared_edge"
    DB_USER: str = "ared"
    DB_PASSWORD: str = ""

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # IOTA Configuration
    IOTA_NODE_URL: str = "https://api.testnet.shimmer.network"
    IOTA_NETWORK: str = "testnet"
    IOTA_SEED: Optional[str] = Field(default=None, min_length=64)

    # Scheduler
    SCHEDULER_ENABLED: bool = True
    ANCHOR_SCHEDULE_HOUR: int = 0
    ANCHOR_SCHEDULE_MINUTE: int = 0

    # Metrics
    METRICS_ENABLED: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
