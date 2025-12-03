"""
ARED Edge IOTA Anchor Service - Main Entry Point

Provides APIs for anchoring Merkle roots to IOTA Tangle and verifying proofs.
"""

import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from starlette.responses import Response

from app.api.v1 import router as api_v1_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.db import close_db, init_db
from app.services.anchor_service import AnchorService

setup_logging()
logger = structlog.get_logger(__name__)

# Prometheus metrics
ANCHORS_CREATED = Counter(
    "iota_anchors_created_total",
    "Total anchors created",
    ["status"],
)
ANCHORS_POSTED = Counter(
    "iota_anchors_posted_total",
    "Total anchors posted to Tangle",
)
ANCHORS_CONFIRMED = Counter(
    "iota_anchors_confirmed_total",
    "Total anchors confirmed on Tangle",
)
ANCHOR_POSTING_TIME = Histogram(
    "iota_anchor_posting_seconds",
    "Time to post anchor to Tangle",
    buckets=[1, 5, 10, 30, 60, 120, 300],
)
IOTA_NODE_CONNECTED = Gauge(
    "iota_node_connected",
    "IOTA node connection status (1=connected, 0=disconnected)",
)

# Scheduler for periodic anchoring
scheduler = AsyncIOScheduler()

# Global anchor service instance
anchor_service: AnchorService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    global anchor_service

    logger.info(
        "Starting IOTA Anchor Service",
        version=settings.VERSION,
        environment=settings.ENV,
        iota_node=settings.IOTA_NODE_URL,
        iota_network=settings.IOTA_NETWORK,
    )

    # Initialize database
    await init_db()

    # Initialize anchor service
    anchor_service = AnchorService()
    try:
        await anchor_service.initialize()
        IOTA_NODE_CONNECTED.set(1)
    except Exception as e:
        logger.warning("Failed to connect to IOTA node on startup", error=str(e))
        IOTA_NODE_CONNECTED.set(0)

    # Store service in app state for access in routes
    app.state.anchor_service = anchor_service

    # Start scheduler for periodic anchoring
    if settings.SCHEDULER_ENABLED:
        scheduler.add_job(
            run_anchor_job,
            "cron",
            hour=settings.ANCHOR_SCHEDULE_HOUR,
            minute=settings.ANCHOR_SCHEDULE_MINUTE,
            id="daily_anchor",
        )
        scheduler.start()
        logger.info(
            "Scheduler started for daily anchoring",
            hour=settings.ANCHOR_SCHEDULE_HOUR,
            minute=settings.ANCHOR_SCHEDULE_MINUTE,
        )

    yield

    # Shutdown
    logger.info("Shutting down IOTA Anchor Service")

    if settings.SCHEDULER_ENABLED:
        scheduler.shutdown()

    if anchor_service:
        await anchor_service.shutdown()

    await close_db()

    logger.info("IOTA Anchor Service shutdown complete")


async def run_anchor_job() -> None:
    """Execute daily anchor job."""
    global anchor_service

    logger.info("Running scheduled anchor job")

    if not anchor_service:
        logger.error("Anchor service not initialized")
        return

    try:
        result = await anchor_service.run_daily_anchor()
        if result:
            ANCHORS_CREATED.labels(status=result.status.value).inc()
            if result.iota_block_id:
                ANCHORS_POSTED.inc()
            logger.info(
                "Daily anchor job completed",
                anchor_id=str(result.id),
                status=result.status.value,
            )
        else:
            logger.info("Daily anchor job completed (no events to anchor)")

    except Exception as e:
        logger.error("Daily anchor job failed", error=str(e))
        ANCHORS_CREATED.labels(status="failed").inc()


def create_application() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="ARED IOTA Anchor API",
        description="Anchoring service for IOTA Tangle integration",
        version=settings.VERSION,
        docs_url="/docs" if settings.ENV != "production" else None,
        redoc_url="/redoc" if settings.ENV != "production" else None,
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
        """Overall service health check."""
        global anchor_service

        iota_status = "unknown"
        if anchor_service:
            try:
                node_status = await anchor_service.get_node_status()
                iota_status = "connected" if node_status.get("connected") else "disconnected"
                IOTA_NODE_CONNECTED.set(1 if node_status.get("connected") else 0)
            except Exception:
                iota_status = "error"
                IOTA_NODE_CONNECTED.set(0)

        return {
            "status": "healthy",
            "service": "iota-anchor",
            "version": settings.VERSION,
            "iota_node": iota_status,
            "iota_network": settings.IOTA_NETWORK,
        }

    @app.get("/ready")
    async def ready() -> Response:
        """Readiness probe for Kubernetes."""
        global anchor_service

        if anchor_service and anchor_service.iota_client.is_connected:
            return Response(status_code=200, content="ready")
        return Response(status_code=503, content="not ready")

    @app.get("/live")
    async def live() -> Response:
        """Liveness probe for Kubernetes."""
        return Response(status_code=200, content="alive")

    @app.get("/status")
    async def status() -> dict:
        """Detailed service status."""
        global anchor_service

        if anchor_service:
            node_status = await anchor_service.get_node_status()
            return {
                "service": "iota-anchor",
                "version": settings.VERSION,
                "environment": settings.ENV,
                "iota": node_status,
                "scheduler_enabled": settings.SCHEDULER_ENABLED,
            }
        return {
            "service": "iota-anchor",
            "version": settings.VERSION,
            "error": "Anchor service not initialized",
        }

    return app


app = create_application()


def handle_signal(signum: int, frame: object) -> None:
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, initiating shutdown")
    sys.exit(0)


def main() -> None:
    """Run the service."""
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info(
        "Starting IOTA Anchor service",
        host=settings.HOST,
        port=settings.PORT,
        iota_url=settings.IOTA_NODE_URL,
    )

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        workers=settings.WORKERS,
        log_level=settings.LOG_LEVEL.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
