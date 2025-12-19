"""
ARED Edge IOTA Anchor Service - Main Entry Point

Provides APIs for anchoring Merkle roots to IOTA Tangle and verifying proofs.
"""

import signal
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from prometheus_client import Gauge, make_asgi_app
from starlette.responses import Response

from app.api.v1 import router as api_v1_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.db import async_session_factory, close_db, init_db
from app.metrics import get_anchor_metrics
from app.services.anchor_service import AnchorService
from app.services.anchor_workflow import AnchorWorkflow
from app.services.reconciliation import ReconciliationService, ensure_retry_log_table

setup_logging()
logger = structlog.get_logger(__name__)

# Prometheus metrics
IOTA_NODE_CONNECTED = Gauge(
    "iota_node_connected",
    "IOTA node connection status (1=connected, 0=disconnected)",
)
SCHEDULER_LAST_RUN = Gauge(
    "anchor_scheduler_last_run_timestamp",
    "Timestamp of last scheduled anchor run",
)
RECONCILIATION_LAST_RUN = Gauge(
    "anchor_reconciliation_last_run_timestamp",
    "Timestamp of last reconciliation run",
)

# Scheduler for periodic anchoring
scheduler = AsyncIOScheduler()

# Global service instances
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
    if settings.IOTA_ENABLED:
        try:
            await anchor_service.initialize()
            IOTA_NODE_CONNECTED.set(1)
        except Exception as e:
            logger.warning(
                "Failed to connect to IOTA node on startup - service will operate in degraded mode",
                error=str(e),
            )
            IOTA_NODE_CONNECTED.set(0)
    else:
        logger.info("IOTA anchoring disabled via configuration")
        IOTA_NODE_CONNECTED.set(0)

    # Store service in app state for access in routes
    app.state.anchor_service = anchor_service

    # P6.1.4: Initialize anchor metrics
    anchor_metrics = get_anchor_metrics()
    anchor_metrics.set_service_info(
        version=settings.VERSION,
        environment=settings.ENV,
        schedule="daily",
    )
    anchor_metrics.set_iota_node_info(
        node_url=settings.IOTA_NODE_URL,
        network=settings.IOTA_NETWORK,
    )
    logger.info("Anchor metrics initialized")

    # Ensure retry log table exists
    async with async_session_factory() as session:
        await ensure_retry_log_table(session)

    # Start scheduler for periodic anchoring and reconciliation
    if settings.SCHEDULER_ENABLED:
        # Daily anchor job
        scheduler.add_job(
            run_anchor_job,
            "cron",
            hour=settings.ANCHOR_SCHEDULE_HOUR,
            minute=settings.ANCHOR_SCHEDULE_MINUTE,
            id="daily_anchor",
        )

        # Reconciliation job (every 15 minutes)
        scheduler.add_job(
            run_reconciliation_job,
            "interval",
            minutes=15,
            id="reconciliation",
        )

        scheduler.start()
        logger.info(
            "Scheduler started",
            anchor_hour=settings.ANCHOR_SCHEDULE_HOUR,
            anchor_minute=settings.ANCHOR_SCHEDULE_MINUTE,
            reconciliation_interval_minutes=15,
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
    """Execute daily anchor job using workflow."""
    global anchor_service
    import time

    logger.info("Running scheduled anchor job")
    SCHEDULER_LAST_RUN.set(time.time())

    if not anchor_service:
        logger.error("Anchor service not initialized")
        return

    try:
        async with async_session_factory() as session:
            workflow = AnchorWorkflow(session, anchor_service)
            result = await workflow.run_daily_anchor()

            if result.success:
                logger.info(
                    "Daily anchor job completed",
                    anchor_id=str(result.anchor_id) if result.anchor_id else None,
                    event_count=result.event_count,
                    duration=result.duration_seconds,
                )
            else:
                logger.error(
                    "Daily anchor job failed",
                    error=result.error,
                )

    except Exception as e:
        logger.error("Daily anchor job failed", error=str(e))


async def run_reconciliation_job() -> None:
    """Execute reconciliation job."""
    global anchor_service
    import time

    logger.debug("Running reconciliation job")
    RECONCILIATION_LAST_RUN.set(time.time())

    if not anchor_service:
        logger.error("Anchor service not initialized")
        return

    try:
        async with async_session_factory() as session:
            reconciliation = ReconciliationService(session, anchor_service)
            result = await reconciliation.run_reconciliation()

            if result.processed > 0:
                logger.info(
                    "Reconciliation completed",
                    processed=result.processed,
                    retried=result.retried,
                    confirmed=result.confirmed,
                )

    except Exception as e:
        logger.error("Reconciliation job failed", error=str(e))


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
            "iota_enabled": settings.IOTA_ENABLED,
            "iota_node": iota_status,
            "iota_network": settings.IOTA_NETWORK,
        }

    @app.get("/ready")
    async def ready() -> Response:
        """
        Readiness probe for Kubernetes.
        
        Checks database connectivity as the critical dependency.
        IOTA connectivity is optional for graceful degradation.
        """
        try:
            async with async_session_factory() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            return Response(status_code=200, content="ready")
        except Exception as e:
            logger.error("Readiness check failed - database unreachable", error=str(e))
            return Response(status_code=503, content="not ready - database unavailable")

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
