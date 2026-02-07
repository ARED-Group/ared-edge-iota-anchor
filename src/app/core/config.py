"""
ARED Edge IOTA Anchor Service - Configuration

Centralized configuration for IOTA Tangle anchoring service.
Supports environment variable overrides for all settings.
"""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
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
    DB_POOL_SIZE: int = 5
    DB_POOL_MAX_OVERFLOW: int = 10

    @property
    def DATABASE_URL(self) -> str:
        """Async database URL for SQLAlchemy."""
        return (
            f"postgresql+asyncpg://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # IOTA Node Configuration
    IOTA_NODE_URL: str = "https://api.testnet.shimmer.network"
    IOTA_FAUCET_URL: str = "https://faucet.testnet.shimmer.network/api/enqueue"
    IOTA_EXPLORER_URL: str = "https://explorer.shimmer.network/testnet"

    # IOTA Network Configuration
    IOTA_NETWORK: str = Field(
        default="testnet",
        description="IOTA network: 'mainnet', 'shimmer', or 'testnet'",
    )
    IOTA_COIN_TYPE: int = Field(
        default=4219,
        description="Coin type for key derivation (4219=Shimmer, 4218=IOTA)",
    )

    # IOTA Credentials (stored via K8s secrets in production)
    IOTA_MNEMONIC: str | None = Field(
        default=None,
        description="24-word mnemonic for wallet (use K8s secret in production)",
    )
    IOTA_STRONGHOLD_PASSWORD: str | None = Field(
        default=None,
        description="Password for Stronghold wallet file",
    )
    IOTA_STRONGHOLD_PATH: str = Field(
        default="/data/wallet.stronghold",
        description="Path to Stronghold wallet file",
    )

    # IOTA Client Configuration
    IOTA_REQUEST_TIMEOUT: int = Field(
        default=30,
        description="Request timeout in seconds",
    )
    IOTA_API_TIMEOUT: int = Field(
        default=60,
        description="API call timeout in seconds",
    )
    IOTA_RETRY_COUNT: int = Field(
        default=3,
        description="Number of retries for failed requests",
    )
    IOTA_RETRY_DELAY: float = Field(
        default=1.0,
        description="Base delay between retries in seconds",
    )
    IOTA_RETRY_MAX_DELAY: float = Field(
        default=30.0,
        description="Maximum delay between retries in seconds",
    )

    # Anchor Tag Configuration
    IOTA_TAG_PREFIX: str = Field(
        default="ARED_ANCHOR",
        description="Tag prefix for anchor messages on Tangle",
    )
    IOTA_TAG_VERSION: str = Field(
        default="v1",
        description="Version tag for anchor message format",
    )

    # Confirmation Monitoring
    IOTA_CONFIRMATION_TIMEOUT: int = Field(
        default=300,
        description="Timeout for confirmation monitoring in seconds",
    )
    IOTA_CONFIRMATION_POLL_INTERVAL: int = Field(
        default=5,
        description="Poll interval for confirmation status in seconds",
    )

    # IOTA Feature Toggle
    IOTA_ENABLED: bool = Field(
        default=True,
        description="Enable IOTA Tangle anchoring. Set to false for graceful degradation.",
    )

    # Scheduler
    SCHEDULER_ENABLED: bool = True
    ANCHOR_SCHEDULE_HOUR: int = 0
    ANCHOR_SCHEDULE_MINUTE: int = 0

    # Metrics
    METRICS_ENABLED: bool = True
    METRICS_PORT: int = 9084

    # Health Check
    HEALTH_PORT: int = 8082

    @field_validator("IOTA_NETWORK")
    @classmethod
    def validate_network(cls, v: str) -> str:
        """Validate IOTA network selection."""
        valid_networks = ("mainnet", "shimmer", "testnet", "devnet")
        if v.lower() not in valid_networks:
            raise ValueError(f"IOTA_NETWORK must be one of: {valid_networks}")
        return v.lower()

    @property
    def iota_tag(self) -> str:
        """Full IOTA anchor tag."""
        return f"{self.IOTA_TAG_PREFIX}_{self.IOTA_TAG_VERSION}"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
