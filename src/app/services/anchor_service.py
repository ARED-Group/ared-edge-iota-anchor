"""
ARED Edge IOTA Anchor Service - Anchor Management Service

Orchestrates anchor creation, posting, and persistence.
Provides methods for the API and scheduler to trigger anchoring.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import structlog

from app.core.config import settings
from app.services.iota_client import (
    AnchorMessage,
    BlockMetadata,
    IOTAClient,
    IOTAClientError,
    PostingError,
)

logger = structlog.get_logger(__name__)


class AnchorStatus(str, Enum):
    """Anchor lifecycle status."""

    PENDING = "pending"
    BUILDING = "building"
    POSTING = "posting"
    POSTED = "posted"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass
class AnchorRecord:
    """Anchor record for persistence."""

    id: UUID
    digest: str
    method: str
    start_time: datetime
    end_time: datetime
    item_count: int
    status: AnchorStatus
    iota_block_id: str | None = None
    iota_network: str | None = None
    explorer_url: str | None = None
    created_at: datetime = None
    posted_at: datetime | None = None
    confirmed_at: datetime | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "digest": self.digest,
            "method": self.method,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "item_count": self.item_count,
            "status": self.status.value,
            "iota_block_id": self.iota_block_id,
            "iota_network": self.iota_network,
            "explorer_url": self.explorer_url,
            "created_at": self.created_at.isoformat(),
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "error_message": self.error_message,
        }


class AnchorServiceError(Exception):
    """Base exception for anchor service errors."""

    pass


class AnchorService:
    """
    Anchor management service.

    Orchestrates:
    - Anchor record creation
    - IOTA Tangle posting
    - Confirmation monitoring
    - Status tracking and persistence
    """

    def __init__(self) -> None:
        """Initialize anchor service."""
        self._iota_client = IOTAClient()
        self._pending_anchors: dict[UUID, AnchorRecord] = {}
        self._lock = asyncio.Lock()

    @property
    def iota_client(self) -> IOTAClient:
        """Get the IOTA client instance."""
        return self._iota_client

    async def initialize(self) -> None:
        """Initialize service and connect to IOTA node."""
        logger.info("Initializing Anchor Service")
        await self._iota_client.connect()
        logger.info("Anchor Service initialized")

    async def shutdown(self) -> None:
        """Shutdown service and close connections."""
        logger.info("Shutting down Anchor Service")
        await self._iota_client.disconnect()

    async def create_anchor(
        self,
        digest: str,
        item_count: int,
        start_time: datetime,
        end_time: datetime,
        method: str = "merkle_sha256",
        metadata: dict[str, Any] | None = None,
        wait_for_confirmation: bool = True,
    ) -> AnchorRecord:
        """
        Create and post an anchor to the IOTA Tangle.

        Args:
            digest: Merkle root or aggregate hash
            item_count: Number of items in the anchor
            start_time: Start of the anchored time window
            end_time: End of the anchored time window
            method: Digest method (merkle_sha256, sha256, etc.)
            metadata: Optional metadata to include
            wait_for_confirmation: Whether to wait for Tangle confirmation

        Returns:
            AnchorRecord with status and Tangle references

        Raises:
            AnchorServiceError: If anchoring fails
        """
        anchor_id = uuid4()

        logger.info(
            "Creating anchor",
            anchor_id=str(anchor_id),
            digest=digest[:16] + "...",
            item_count=item_count,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

        # Create initial record
        record = AnchorRecord(
            id=anchor_id,
            digest=digest,
            method=method,
            start_time=start_time,
            end_time=end_time,
            item_count=item_count,
            status=AnchorStatus.PENDING,
        )

        async with self._lock:
            self._pending_anchors[anchor_id] = record

        try:
            # Update status to building
            record.status = AnchorStatus.BUILDING

            # Create anchor message
            message = AnchorMessage(
                digest=digest,
                digest_algorithm="sha256" if "sha256" in method else method,
                anchor_type="merkle_root" if "merkle" in method else "hash",
                event_count=item_count,
                start_time=int(start_time.timestamp()),
                end_time=int(end_time.timestamp()),
                metadata=metadata or {},
            )

            # Update status to posting
            record.status = AnchorStatus.POSTING

            # Post to Tangle
            block_metadata = await self._iota_client.post_anchor(
                message=message,
                wait_for_inclusion=wait_for_confirmation,
            )

            # Update record with Tangle references
            record.iota_block_id = block_metadata.block_id
            record.iota_network = block_metadata.network
            record.explorer_url = self._iota_client.get_explorer_url(
                block_metadata.block_id
            )
            record.posted_at = datetime.utcnow()

            if block_metadata.referenced_by_milestone:
                record.status = AnchorStatus.CONFIRMED
                record.confirmed_at = datetime.utcnow()
            else:
                record.status = AnchorStatus.POSTED

            logger.info(
                "Anchor created successfully",
                anchor_id=str(anchor_id),
                block_id=block_metadata.block_id,
                status=record.status.value,
            )

            return record

        except PostingError as e:
            record.status = AnchorStatus.FAILED
            record.error_message = str(e)
            logger.error(
                "Anchor posting failed",
                anchor_id=str(anchor_id),
                error=str(e),
            )
            raise AnchorServiceError(f"Failed to post anchor: {e}") from e

        except IOTAClientError as e:
            record.status = AnchorStatus.FAILED
            record.error_message = str(e)
            logger.error(
                "IOTA client error during anchoring",
                anchor_id=str(anchor_id),
                error=str(e),
            )
            raise AnchorServiceError(f"IOTA error: {e}") from e

        finally:
            async with self._lock:
                self._pending_anchors.pop(anchor_id, None)

    async def check_confirmation(self, anchor_id: UUID, block_id: str) -> AnchorRecord:
        """
        Check confirmation status of a posted anchor.

        Args:
            anchor_id: Anchor record ID
            block_id: IOTA block ID

        Returns:
            Updated AnchorRecord
        """
        logger.info(
            "Checking anchor confirmation",
            anchor_id=str(anchor_id),
            block_id=block_id,
        )

        try:
            metadata = await self._iota_client.get_block_metadata(block_id)

            if metadata.referenced_by_milestone:
                logger.info(
                    "Anchor confirmed",
                    anchor_id=str(anchor_id),
                    milestone_index=metadata.milestone_index,
                )
                return AnchorRecord(
                    id=anchor_id,
                    digest="",
                    method="",
                    start_time=datetime.utcnow(),
                    end_time=datetime.utcnow(),
                    item_count=0,
                    status=AnchorStatus.CONFIRMED,
                    iota_block_id=block_id,
                    confirmed_at=datetime.utcnow(),
                )

            return AnchorRecord(
                id=anchor_id,
                digest="",
                method="",
                start_time=datetime.utcnow(),
                end_time=datetime.utcnow(),
                item_count=0,
                status=AnchorStatus.POSTED,
                iota_block_id=block_id,
            )

        except IOTAClientError as e:
            logger.error(
                "Failed to check confirmation",
                anchor_id=str(anchor_id),
                error=str(e),
            )
            raise AnchorServiceError(f"Failed to check confirmation: {e}") from e

    async def verify_anchor_on_tangle(self, block_id: str) -> bool:
        """
        Verify an anchor exists on the Tangle.

        Args:
            block_id: IOTA block ID

        Returns:
            True if anchor exists and is valid
        """
        return await self._iota_client.verify_block_exists(block_id)

    async def get_node_status(self) -> dict[str, Any]:
        """
        Get IOTA node status information.

        Returns:
            Node status dictionary
        """
        if not self._iota_client.is_connected:
            return {
                "connected": False,
                "node_url": self._iota_client.node_url,
                "network": self._iota_client.network,
            }

        try:
            info = await self._iota_client._get_node_info()
            return {
                "connected": True,
                "node_url": self._iota_client.node_url,
                "network": self._iota_client.network,
                "protocol": info.get("protocol", {}).get("networkName"),
                "version": info.get("version"),
                "is_healthy": await self._iota_client._check_health(),
            }
        except Exception as e:
            return {
                "connected": False,
                "node_url": self._iota_client.node_url,
                "network": self._iota_client.network,
                "error": str(e),
            }

    async def run_daily_anchor(self) -> AnchorRecord | None:
        """
        Execute daily anchoring job.

        Collects events from the last 24 hours, builds digest,
        and posts to Tangle.

        Returns:
            AnchorRecord if successful, None if no events
        """
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=1)

        logger.info(
            "Running daily anchor job",
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

        # TODO: Integrate with database to fetch events
        # For now, return None indicating no events to anchor
        # This will be implemented in P4.3

        logger.info("Daily anchor job completed (no events to anchor)")
        return None
