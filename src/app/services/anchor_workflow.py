"""
ARED Edge IOTA Anchor Service - Anchor Workflow

Orchestrates the complete anchoring workflow:
1. Collect events for time window
2. Build Merkle tree
3. Post to IOTA Tangle
4. Store anchor record and items
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crypto.merkle import MerkleTree, verify_proof
from app.db.repository import AnchorRepository
from app.services.anchor_service import AnchorRecord, AnchorService, AnchorStatus
from app.services.event_consumer import EventConsumer, EventWindow, IndexedEvent
from app.services.iota_client import AnchorMessage

logger = structlog.get_logger(__name__)

# Prometheus metrics
ANCHOR_JOBS_TOTAL = Counter(
    "anchor_workflow_jobs_total",
    "Total anchor workflow jobs",
    ["status"],
)
ANCHOR_EVENTS_TOTAL = Counter(
    "anchor_workflow_events_total",
    "Total events anchored",
)
ANCHOR_WORKFLOW_DURATION = Histogram(
    "anchor_workflow_duration_seconds",
    "Duration of anchor workflow",
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)


@dataclass
class AnchorResult:
    """Result of an anchoring workflow."""

    success: bool
    anchor_id: UUID | None
    digest: str | None
    event_count: int
    iota_block_id: str | None
    error: str | None
    start_time: datetime
    end_time: datetime
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "anchor_id": str(self.anchor_id) if self.anchor_id else None,
            "digest": self.digest,
            "event_count": self.event_count,
            "iota_block_id": self.iota_block_id,
            "error": self.error,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_seconds": round(self.duration_seconds, 2),
        }


class AnchorWorkflowError(Exception):
    """Base exception for anchor workflow errors."""

    pass


class AnchorWorkflow:
    """
    Orchestrates the complete anchoring workflow.

    Steps:
    1. Fetch events for time window
    2. Build Merkle tree from event hashes
    3. Create anchor record in 'pending' status
    4. Post digest to IOTA Tangle
    5. Update status to 'posted' with block ID
    6. Monitor for confirmation
    7. Update status to 'confirmed'
    8. Store anchor_items with proofs
    """

    def __init__(
        self,
        session: AsyncSession,
        anchor_service: AnchorService,
    ) -> None:
        """
        Initialize anchor workflow.

        Args:
            session: Database session
            anchor_service: IOTA anchor service
        """
        self._session = session
        self._anchor_service = anchor_service
        self._event_consumer = EventConsumer(session)
        self._repository = AnchorRepository(session)

    async def run_anchor_job(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        wait_for_confirmation: bool = True,
    ) -> AnchorResult:
        """
        Run a complete anchoring job.

        Args:
            start_time: Start of anchor window (defaults to last anchor end)
            end_time: End of anchor window (defaults to now)
            wait_for_confirmation: Whether to wait for IOTA confirmation

        Returns:
            AnchorResult with status and details
        """
        job_start = datetime.utcnow()

        # Determine time window
        if end_time is None:
            end_time = datetime.utcnow()

        if start_time is None:
            last_anchor_time = await self._event_consumer.get_last_anchor_time()
            if last_anchor_time:
                start_time = last_anchor_time
            else:
                # Default to 24 hours ago
                start_time = end_time - timedelta(days=1)

        logger.info(
            "Starting anchor job",
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

        try:
            # Step 1: Fetch events
            window = await self._event_consumer.fetch_events_for_window(
                start_time=start_time,
                end_time=end_time,
            )

            if window.is_empty:
                logger.info("No events to anchor")
                ANCHOR_JOBS_TOTAL.labels(status="empty").inc()
                return AnchorResult(
                    success=True,
                    anchor_id=None,
                    digest=None,
                    event_count=0,
                    iota_block_id=None,
                    error=None,
                    start_time=start_time,
                    end_time=end_time,
                    duration_seconds=(datetime.utcnow() - job_start).total_seconds(),
                )

            # Step 2: Build Merkle tree
            tree = MerkleTree.from_raw_hashes(window.event_hashes)
            digest = tree.root_hash

            logger.info(
                "Built Merkle tree",
                event_count=window.event_count,
                digest=digest[:16] + "...",
            )

            # Step 3: Check for duplicate anchor (idempotency)
            existing = await self._check_existing_anchor(
                digest=digest,
                start_time=start_time,
                end_time=end_time,
            )
            if existing:
                logger.info(
                    "Anchor already exists",
                    anchor_id=str(existing.id),
                )
                ANCHOR_JOBS_TOTAL.labels(status="duplicate").inc()
                return AnchorResult(
                    success=True,
                    anchor_id=existing.id,
                    digest=digest,
                    event_count=window.event_count,
                    iota_block_id=existing.iota_block_id,
                    error=None,
                    start_time=start_time,
                    end_time=end_time,
                    duration_seconds=(datetime.utcnow() - job_start).total_seconds(),
                )

            # Step 4: Create and post anchor
            anchor_record = await self._anchor_service.create_anchor(
                digest=digest,
                item_count=window.event_count,
                start_time=start_time,
                end_time=end_time,
                method="merkle_sha256",
                wait_for_confirmation=wait_for_confirmation,
            )

            # Step 5: Save anchor to database
            await self._repository.save_anchor(anchor_record)

            # Step 6: Store anchor items with proofs
            await self._store_anchor_items(
                anchor_id=anchor_record.id,
                tree=tree,
                events=window.events,
            )

            # Update metrics
            ANCHOR_JOBS_TOTAL.labels(status="success").inc()
            ANCHOR_EVENTS_TOTAL.inc(window.event_count)

            duration = (datetime.utcnow() - job_start).total_seconds()
            ANCHOR_WORKFLOW_DURATION.observe(duration)

            logger.info(
                "Anchor job completed",
                anchor_id=str(anchor_record.id),
                digest=digest[:16] + "...",
                event_count=window.event_count,
                iota_block_id=anchor_record.iota_block_id,
                duration_seconds=round(duration, 2),
            )

            return AnchorResult(
                success=True,
                anchor_id=anchor_record.id,
                digest=digest,
                event_count=window.event_count,
                iota_block_id=anchor_record.iota_block_id,
                error=None,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration,
            )

        except Exception as e:
            ANCHOR_JOBS_TOTAL.labels(status="error").inc()
            duration = (datetime.utcnow() - job_start).total_seconds()

            logger.error(
                "Anchor job failed",
                error=str(e),
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
            )

            return AnchorResult(
                success=False,
                anchor_id=None,
                digest=None,
                event_count=0,
                iota_block_id=None,
                error=str(e),
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration,
            )

    async def run_daily_anchor(self) -> AnchorResult:
        """
        Run daily anchor job for the previous 24 hours.

        Returns:
            AnchorResult with status
        """
        end_time = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_time = end_time - timedelta(days=1)

        return await self.run_anchor_job(
            start_time=start_time,
            end_time=end_time,
            wait_for_confirmation=True,
        )

    async def run_incremental_anchor(
        self,
        min_events: int = 100,
    ) -> AnchorResult | None:
        """
        Run incremental anchor if enough events have accumulated.

        Args:
            min_events: Minimum events required to trigger anchor

        Returns:
            AnchorResult if anchor was created, None otherwise
        """
        last_time = await self._event_consumer.get_last_anchor_time()
        if not last_time:
            last_time = datetime.utcnow() - timedelta(days=1)

        event_count = await self._event_consumer.get_event_count_since(last_time)

        if event_count < min_events:
            logger.debug(
                "Not enough events for incremental anchor",
                count=event_count,
                min_required=min_events,
            )
            return None

        return await self.run_anchor_job(
            start_time=last_time,
            end_time=datetime.utcnow(),
        )

    async def _check_existing_anchor(
        self,
        digest: str,
        start_time: datetime,
        end_time: datetime,
    ) -> AnchorRecord | None:
        """Check if an anchor already exists for this digest and window."""
        try:
            anchors = await self._repository.list_anchors(limit=1)
            for anchor in anchors:
                if (
                    anchor.digest == digest
                    and anchor.start_time == start_time
                    and anchor.end_time == end_time
                ):
                    return anchor
            return None
        except Exception:
            return None

    async def _store_anchor_items(
        self,
        anchor_id: UUID,
        tree: MerkleTree,
        events: list[IndexedEvent],
    ) -> None:
        """
        Store anchor items with Merkle proofs.

        Args:
            anchor_id: Parent anchor ID
            tree: Merkle tree used for anchoring
            events: List of events in tree order
        """
        logger.info(
            "Storing anchor items",
            anchor_id=str(anchor_id),
            count=len(events),
        )

        for i, event in enumerate(events):
            proof = tree.get_proof(i)

            await self._repository.save_anchor_item(
                anchor_id=anchor_id,
                event_hash=event.event_hash,
                position=i,
                event_id=event.id,
                merkle_proof=proof.to_compact(),
            )

        await self._session.commit()

        logger.info(
            "Stored anchor items",
            anchor_id=str(anchor_id),
            count=len(events),
        )
