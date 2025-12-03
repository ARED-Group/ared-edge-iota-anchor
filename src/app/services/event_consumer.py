"""
ARED Edge IOTA Anchor Service - Event Consumer

Consumes indexed events from the Substrate indexer database
and collects event hashes for anchoring.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = structlog.get_logger(__name__)


@dataclass
class IndexedEvent:
    """Represents an indexed blockchain event."""

    id: UUID
    block_number: int
    block_hash: str
    event_index: int
    pallet: str
    event_name: str
    event_data: dict[str, Any]
    event_hash: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": str(self.id),
            "block_number": self.block_number,
            "block_hash": self.block_hash,
            "event_index": self.event_index,
            "pallet": self.pallet,
            "event_name": self.event_name,
            "event_data": self.event_data,
            "event_hash": self.event_hash,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class EventWindow:
    """Represents a time window of events for anchoring."""

    start_time: datetime
    end_time: datetime
    events: list[IndexedEvent] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        """Number of events in window."""
        return len(self.events)

    @property
    def event_hashes(self) -> list[str]:
        """List of event hashes in order."""
        return [e.event_hash for e in self.events]

    @property
    def is_empty(self) -> bool:
        """Check if window has no events."""
        return len(self.events) == 0


class EventConsumerError(Exception):
    """Base exception for event consumer errors."""

    pass


class EventConsumer:
    """
    Consumes indexed events from the database.

    Provides methods for:
    - Fetching events for a time window
    - Tracking last consumed position
    - Handling reconnection
    """

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize event consumer.

        Args:
            session: Async database session
        """
        self._session = session
        self._last_block: int | None = None
        self._last_timestamp: datetime | None = None

    async def fetch_events_for_window(
        self,
        start_time: datetime,
        end_time: datetime,
        pallets: list[str] | None = None,
    ) -> EventWindow:
        """
        Fetch indexed events for a time window.

        Args:
            start_time: Start of time window
            end_time: End of time window
            pallets: Optional filter by pallet names

        Returns:
            EventWindow containing events
        """
        logger.info(
            "Fetching events for window",
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            pallets=pallets,
        )

        try:
            # Build query
            if pallets:
                pallet_filter = "AND pallet = ANY(:pallets)"
                params = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "pallets": pallets,
                }
            else:
                pallet_filter = ""
                params = {
                    "start_time": start_time,
                    "end_time": end_time,
                }

            query = text(f"""
                SELECT id, block_number, block_hash, event_index,
                       pallet, event_name, event_data, event_hash, 
                       created_at as timestamp
                FROM indexed_events
                WHERE created_at >= :start_time
                  AND created_at < :end_time
                  {pallet_filter}
                ORDER BY block_number, event_index
            """)

            result = await self._session.execute(query, params)
            rows = result.fetchall()

            events = []
            for row in rows:
                events.append(
                    IndexedEvent(
                        id=row.id,
                        block_number=row.block_number,
                        block_hash=row.block_hash,
                        event_index=row.event_index,
                        pallet=row.pallet,
                        event_name=row.event_name,
                        event_data=row.event_data or {},
                        event_hash=row.event_hash,
                        timestamp=row.timestamp,
                    )
                )

            # Update last consumed position
            if events:
                self._last_block = events[-1].block_number
                self._last_timestamp = events[-1].timestamp

            logger.info(
                "Fetched events",
                count=len(events),
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
            )

            return EventWindow(
                start_time=start_time,
                end_time=end_time,
                events=events,
            )

        except Exception as e:
            logger.error("Failed to fetch events", error=str(e))
            raise EventConsumerError(f"Failed to fetch events: {e}") from e

    async def fetch_unanchored_events(
        self,
        since: datetime | None = None,
        limit: int = 10000,
    ) -> list[IndexedEvent]:
        """
        Fetch events that haven't been anchored yet.

        Args:
            since: Optional start time
            limit: Maximum events to return

        Returns:
            List of unanchored events
        """
        logger.info("Fetching unanchored events", since=since, limit=limit)

        try:
            if since:
                query = text("""
                    SELECT ie.id, ie.block_number, ie.block_hash, ie.event_index,
                           ie.pallet, ie.event_name, ie.event_data, ie.event_hash,
                           ie.created_at as timestamp
                    FROM indexed_events ie
                    LEFT JOIN anchor_items ai ON ie.event_hash = ai.event_hash
                    WHERE ai.id IS NULL
                      AND ie.created_at >= :since
                    ORDER BY ie.block_number, ie.event_index
                    LIMIT :limit
                """)
                result = await self._session.execute(
                    query,
                    {"since": since, "limit": limit},
                )
            else:
                query = text("""
                    SELECT ie.id, ie.block_number, ie.block_hash, ie.event_index,
                           ie.pallet, ie.event_name, ie.event_data, ie.event_hash,
                           ie.created_at as timestamp
                    FROM indexed_events ie
                    LEFT JOIN anchor_items ai ON ie.event_hash = ai.event_hash
                    WHERE ai.id IS NULL
                    ORDER BY ie.block_number, ie.event_index
                    LIMIT :limit
                """)
                result = await self._session.execute(query, {"limit": limit})

            rows = result.fetchall()

            events = []
            for row in rows:
                events.append(
                    IndexedEvent(
                        id=row.id,
                        block_number=row.block_number,
                        block_hash=row.block_hash,
                        event_index=row.event_index,
                        pallet=row.pallet,
                        event_name=row.event_name,
                        event_data=row.event_data or {},
                        event_hash=row.event_hash,
                        timestamp=row.timestamp,
                    )
                )

            logger.info("Fetched unanchored events", count=len(events))
            return events

        except Exception as e:
            logger.error("Failed to fetch unanchored events", error=str(e))
            raise EventConsumerError(f"Failed to fetch unanchored events: {e}") from e

    async def get_last_anchor_time(self) -> datetime | None:
        """
        Get the end time of the last successful anchor.

        Returns:
            End time of last anchor, or None if no anchors exist
        """
        try:
            query = text("""
                SELECT end_time
                FROM anchors
                WHERE status IN ('posted', 'confirmed')
                ORDER BY end_time DESC
                LIMIT 1
            """)
            result = await self._session.execute(query)
            row = result.fetchone()

            return row.end_time if row else None

        except Exception as e:
            logger.error("Failed to get last anchor time", error=str(e))
            return None

    async def get_event_count_since(self, since: datetime) -> int:
        """
        Get count of events since a given time.

        Args:
            since: Start time

        Returns:
            Number of events
        """
        try:
            query = text("""
                SELECT COUNT(*) as count
                FROM indexed_events
                WHERE created_at >= :since
            """)
            result = await self._session.execute(query, {"since": since})
            row = result.fetchone()
            return row.count if row else 0

        except Exception as e:
            logger.error("Failed to get event count", error=str(e))
            return 0

    @property
    def last_block(self) -> int | None:
        """Get last consumed block number."""
        return self._last_block

    @property
    def last_timestamp(self) -> datetime | None:
        """Get last consumed timestamp."""
        return self._last_timestamp
