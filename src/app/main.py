"""
ARED Edge IOTA Anchor Service - Main Entry Point

Provides APIs for anchoring Merkle roots to IOTA Tangle and verifying proofs.
"""

import signal
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.api.v1 import router as api_v1_router
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging()
logger = structlog.get_logger(__name__)

# Scheduler for periodic anchoring
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    logger.info(
        "Starting IOTA Anchor Service",
        version=settings.VERSION,
        environment=settings.ENV,
    )

    # Start scheduler for periodic anchoring
    if settings.SCHEDULER_ENABLED:
        scheduler.add_job(
            run_anchor_job,
            "cron",
            hour=0,  # Daily at midnight UTC
            minute=0,
            id="daily_anchor",
        )
        scheduler.start()
        logger.info("Scheduler started for daily anchoring")

    yield

    # Shutdown
    logger.info("Shutting down IOTA Anchor Service")
    if settings.SCHEDULER_ENABLED:
        scheduler.shutdown()


async def run_anchor_job() -> None:
    """Execute daily anchor job."""
    logger.info("Running scheduled anchor job")
    # TODO: Implement actual anchoring logic
    # - Fetch events since last anchor
    # - Build Merkle tree
    # - Post to IOTA Tangle
    # - Store anchor record


def create_application() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="ARED IOTA Anchor API",
        description="Anchoring service for IOTA Tangle integration",
        version=settings.VERSION,
        docs_url="/docs" if settings.ENV != "production" else None,
        lifespan=lifespan,
    )

    # Include routers
    app.include_router(api_v1_router, prefix="/api/v1")

    # Metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Health endpoints
    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy", "service": "iota-anchor"}

    @app.get("/ready")
    async def ready() -> dict:
        # TODO: Check IOTA node connectivity
        return {"status": "ready"}

    return app


app = create_application()


def main() -> None:
    """Run the service."""
    signal.signal(signal.SIGTERM, lambda *_: exit(0))

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        workers=settings.WORKERS,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
