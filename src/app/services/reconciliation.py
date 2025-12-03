"""
ARED Edge IOTA Anchor Service - Reconciliation

Handles retry and recovery for failed anchoring operations.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from prometheus_client import Counter, Gauge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repository import AnchorRepository
from app.services.anchor_service import AnchorRecord, AnchorService, AnchorStatus
from app.services.iota_client import IOTAClient

logger = structlog.get_logger(__name__)

# Prometheus metrics
RECONCILIATION_RUNS = Counter(
    "anchor_reconciliation_runs_total",
    "Total reconciliation runs",
)
RECONCILIATION_RETRIES = Counter(
    "anchor_reconciliation_retries_total",
    "Total retry attempts",
    ["status"],
)
PENDING_ANCHORS = Gauge(
    "anchor_pending_count",
    "Number of pending anchors",
)
FAILED_ANCHORS = Gauge(
    "anchor_failed_count",
    "Number of failed anchors",
)


@dataclass
class ReconciliationResult:
    """Result of reconciliation run."""

    processed: int
    retried: int
    confirmed: int
    failed: int
    marked_for_review: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "processed": self.processed,
            "retried": self.retried,
            "confirmed": self.confirmed,
            "failed": self.failed,
            "marked_for_review": self.marked_for_review,
        }


class ReconciliationService:
    """
    Handles reconciliation of anchor states.

    Responsibilities:
    - Detect failed or stuck anchors
    - Retry posting with exponential backoff
    - Check confirmation status for posted anchors
    - Mark persistent failures for manual review
    """

    def __init__(
        self,
        session: AsyncSession,
        anchor_service: AnchorService,
        max_retries: int = 3,
        retry_delay_base: float = 60.0,
        retry_delay_max: float = 3600.0,
    ) -> None:
        """
        Initialize reconciliation service.

        Args:
            session: Database session
            anchor_service: IOTA anchor service
            max_retries: Maximum retry attempts
            retry_delay_base: Base delay between retries (seconds)
            retry_delay_max: Maximum delay between retries (seconds)
        """
        self._session = session
        self._anchor_service = anchor_service
        self._repository = AnchorRepository(session)
        self._max_retries = max_retries
        self._retry_delay_base = retry_delay_base
        self._retry_delay_max = retry_delay_max

    async def run_reconciliation(self) -> ReconciliationResult:
        """
        Run a complete reconciliation cycle.

        Steps:
        1. Check pending anchors for retry
        2. Check posted anchors for confirmation
        3. Mark persistent failures for review

        Returns:
            ReconciliationResult with statistics
        """
        logger.info("Starting reconciliation run")
        RECONCILIATION_RUNS.inc()

        processed = 0
        retried = 0
        confirmed = 0
        failed = 0
        marked_for_review = 0

        try:
            # Process pending anchors (retry failures)
            pending = await self._get_anchors_by_status(AnchorStatus.PENDING)
            PENDING_ANCHORS.set(len(pending))

            for anchor in pending:
                processed += 1
                result = await self._process_pending_anchor(anchor)
                if result == "retried":
                    retried += 1
                elif result == "failed":
                    failed += 1
                elif result == "review":
                    marked_for_review += 1

            # Process posted anchors (check confirmation)
            posted = await self._get_anchors_by_status(AnchorStatus.POSTED)

            for anchor in posted:
                processed += 1
                result = await self._check_confirmation(anchor)
                if result == "confirmed":
                    confirmed += 1

            # Process failed anchors
            failed_anchors = await self._get_anchors_by_status(AnchorStatus.FAILED)
            FAILED_ANCHORS.set(len(failed_anchors))

            for anchor in failed_anchors:
                processed += 1
                result = await self._process_failed_anchor(anchor)
                if result == "retried":
                    retried += 1
                elif result == "review":
                    marked_for_review += 1

            logger.info(
                "Reconciliation completed",
                processed=processed,
                retried=retried,
                confirmed=confirmed,
                failed=failed,
                marked_for_review=marked_for_review,
            )

            return ReconciliationResult(
                processed=processed,
                retried=retried,
                confirmed=confirmed,
                failed=failed,
                marked_for_review=marked_for_review,
            )

        except Exception as e:
            logger.error("Reconciliation failed", error=str(e))
            return ReconciliationResult(
                processed=processed,
                retried=retried,
                confirmed=confirmed,
                failed=failed + 1,
                marked_for_review=marked_for_review,
            )

    async def _get_anchors_by_status(
        self,
        status: AnchorStatus,
    ) -> list[AnchorRecord]:
        """Get anchors with a specific status."""
        return await self._repository.list_anchors(
            status=status.value,
            limit=100,
        )

    async def _process_pending_anchor(self, anchor: AnchorRecord) -> str:
        """
        Process a pending anchor.

        Returns:
            "retried", "failed", or "review"
        """
        retry_count = await self._get_retry_count(anchor.id)

        if retry_count >= self._max_retries:
            # Mark for manual review
            await self._mark_for_review(anchor)
            RECONCILIATION_RETRIES.labels(status="exhausted").inc()
            return "review"

        # Calculate backoff delay
        delay = self._calculate_backoff(retry_count)
        last_attempt = await self._get_last_attempt_time(anchor.id)

        if last_attempt and (datetime.utcnow() - last_attempt).total_seconds() < delay:
            # Not time to retry yet
            return "pending"

        # Retry posting
        try:
            await self._retry_anchor(anchor)
            RECONCILIATION_RETRIES.labels(status="success").inc()
            return "retried"
        except Exception as e:
            logger.error(
                "Retry failed",
                anchor_id=str(anchor.id),
                error=str(e),
            )
            await self._record_failure(anchor.id, str(e))
            RECONCILIATION_RETRIES.labels(status="failed").inc()
            return "failed"

    async def _process_failed_anchor(self, anchor: AnchorRecord) -> str:
        """
        Process a failed anchor for potential retry.

        Returns:
            "retried" or "review"
        """
        retry_count = await self._get_retry_count(anchor.id)

        if retry_count >= self._max_retries:
            return "review"

        # Only retry if enough time has passed
        delay = self._calculate_backoff(retry_count)
        if anchor.created_at:
            age = (datetime.utcnow() - anchor.created_at).total_seconds()
            if age < delay:
                return "waiting"

        try:
            await self._retry_anchor(anchor)
            return "retried"
        except Exception:
            return "review"

    async def _check_confirmation(self, anchor: AnchorRecord) -> str:
        """
        Check confirmation status of a posted anchor.

        Returns:
            "confirmed", "pending", or "failed"
        """
        if not anchor.iota_block_id:
            return "failed"

        try:
            confirmed_record = await self._anchor_service.check_confirmation(
                anchor_id=anchor.id,
                block_id=anchor.iota_block_id,
            )

            if confirmed_record.status == AnchorStatus.CONFIRMED:
                await self._repository.update_anchor_status(
                    anchor_id=anchor.id,
                    status=AnchorStatus.CONFIRMED,
                )
                logger.info(
                    "Anchor confirmed",
                    anchor_id=str(anchor.id),
                    block_id=anchor.iota_block_id,
                )
                return "confirmed"

            return "pending"

        except Exception as e:
            logger.error(
                "Confirmation check failed",
                anchor_id=str(anchor.id),
                error=str(e),
            )
            return "failed"

    async def _retry_anchor(self, anchor: AnchorRecord) -> None:
        """
        Retry posting an anchor to IOTA.

        Args:
            anchor: Anchor to retry
        """
        logger.info(
            "Retrying anchor",
            anchor_id=str(anchor.id),
            digest=anchor.digest[:16] + "...",
        )

        # Re-post to IOTA
        new_record = await self._anchor_service.create_anchor(
            digest=anchor.digest,
            item_count=anchor.item_count,
            start_time=anchor.start_time,
            end_time=anchor.end_time,
            method=anchor.method,
            wait_for_confirmation=True,
        )

        # Update existing anchor record
        await self._repository.update_anchor_status(
            anchor_id=anchor.id,
            status=new_record.status,
            iota_block_id=new_record.iota_block_id,
        )

        # Record retry attempt
        await self._record_retry_attempt(anchor.id)

    async def _mark_for_review(self, anchor: AnchorRecord) -> None:
        """Mark an anchor for manual review."""
        await self._repository.update_anchor_status(
            anchor_id=anchor.id,
            status=AnchorStatus.FAILED,
            error_message="Max retries exceeded - requires manual review",
        )

        logger.warning(
            "Anchor marked for review",
            anchor_id=str(anchor.id),
            digest=anchor.digest[:16] + "...",
        )

    def _calculate_backoff(self, retry_count: int) -> float:
        """
        Calculate exponential backoff delay.

        Args:
            retry_count: Number of previous retries

        Returns:
            Delay in seconds
        """
        delay = self._retry_delay_base * (2 ** retry_count)
        return min(delay, self._retry_delay_max)

    async def _get_retry_count(self, anchor_id) -> int:
        """Get number of retry attempts for an anchor."""
        try:
            query = text("""
                SELECT COUNT(*) as count
                FROM anchor_retry_log
                WHERE anchor_id = :anchor_id
            """)
            result = await self._session.execute(query, {"anchor_id": anchor_id})
            row = result.fetchone()
            return row.count if row else 0
        except Exception:
            return 0

    async def _get_last_attempt_time(self, anchor_id) -> datetime | None:
        """Get time of last retry attempt."""
        try:
            query = text("""
                SELECT created_at
                FROM anchor_retry_log
                WHERE anchor_id = :anchor_id
                ORDER BY created_at DESC
                LIMIT 1
            """)
            result = await self._session.execute(query, {"anchor_id": anchor_id})
            row = result.fetchone()
            return row.created_at if row else None
        except Exception:
            return None

    async def _record_retry_attempt(self, anchor_id) -> None:
        """Record a retry attempt."""
        try:
            query = text("""
                INSERT INTO anchor_retry_log (anchor_id, created_at)
                VALUES (:anchor_id, :created_at)
            """)
            await self._session.execute(
                query,
                {"anchor_id": anchor_id, "created_at": datetime.utcnow()},
            )
            await self._session.commit()
        except Exception as e:
            logger.warning("Failed to record retry attempt", error=str(e))

    async def _record_failure(self, anchor_id, error: str) -> None:
        """Record a failure for an anchor."""
        try:
            await self._repository.update_anchor_status(
                anchor_id=anchor_id,
                status=AnchorStatus.FAILED,
                error_message=error,
            )
        except Exception as e:
            logger.warning("Failed to record failure", error=str(e))


async def ensure_retry_log_table(session: AsyncSession) -> None:
    """Ensure the retry log table exists."""
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS anchor_retry_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            anchor_id UUID NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            error_message TEXT
        )
    """))

    await session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_anchor_retry_log_anchor_id
        ON anchor_retry_log(anchor_id)
    """))

    await session.commit()
