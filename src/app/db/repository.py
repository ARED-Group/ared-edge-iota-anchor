"""
ARED Edge IOTA Anchor Service - Anchor Repository

Database operations for anchor records and anchor items.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.anchor_service import AnchorRecord, AnchorStatus

logger = structlog.get_logger(__name__)


class AnchorRepository:
    """Repository for anchor-related database operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session
        """
        self._session = session

    async def save_anchor(self, record: AnchorRecord) -> UUID:
        """
        Save an anchor record to the database.

        Args:
            record: AnchorRecord to persist

        Returns:
            Anchor UUID
        """
        query = text("""
            INSERT INTO anchors (
                id, digest, method, start_time, end_time, item_count,
                status, iota_block_id, iota_network, explorer_url,
                error_message, created_at, posted_at, confirmed_at
            ) VALUES (
                :id, :digest, :method, :start_time, :end_time, :item_count,
                :status, :iota_block_id, :iota_network, :explorer_url,
                :error_message, :created_at, :posted_at, :confirmed_at
            )
            ON CONFLICT (digest, start_time, end_time) DO UPDATE SET
                status = EXCLUDED.status,
                iota_block_id = EXCLUDED.iota_block_id,
                iota_network = EXCLUDED.iota_network,
                explorer_url = EXCLUDED.explorer_url,
                error_message = EXCLUDED.error_message,
                posted_at = EXCLUDED.posted_at,
                confirmed_at = EXCLUDED.confirmed_at
            RETURNING id
        """)

        result = await self._session.execute(
            query,
            {
                "id": record.id,
                "digest": record.digest,
                "method": record.method,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "item_count": record.item_count,
                "status": record.status.value,
                "iota_block_id": record.iota_block_id,
                "iota_network": record.iota_network,
                "explorer_url": record.explorer_url,
                "error_message": record.error_message,
                "created_at": record.created_at,
                "posted_at": record.posted_at,
                "confirmed_at": record.confirmed_at,
            },
        )
        await self._session.commit()

        row = result.fetchone()
        return row.id if row else record.id

    async def get_anchor(self, anchor_id: UUID) -> AnchorRecord | None:
        """
        Get an anchor by ID.

        Args:
            anchor_id: Anchor UUID

        Returns:
            AnchorRecord or None if not found
        """
        query = text("""
            SELECT id, digest, method, start_time, end_time, item_count,
                   status, iota_block_id, iota_network, explorer_url,
                   error_message, created_at, posted_at, confirmed_at
            FROM anchors
            WHERE id = :id
        """)

        result = await self._session.execute(query, {"id": anchor_id})
        row = result.fetchone()

        if not row:
            return None

        return AnchorRecord(
            id=row.id,
            digest=row.digest,
            method=row.method,
            start_time=row.start_time,
            end_time=row.end_time,
            item_count=row.item_count,
            status=AnchorStatus(row.status),
            iota_block_id=row.iota_block_id,
            iota_network=row.iota_network,
            explorer_url=row.explorer_url,
            error_message=row.error_message,
            created_at=row.created_at,
            posted_at=row.posted_at,
            confirmed_at=row.confirmed_at,
        )

    async def list_anchors(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AnchorRecord]:
        """
        List anchors with optional filtering.

        Args:
            status: Optional status filter
            limit: Maximum records to return
            offset: Pagination offset

        Returns:
            List of AnchorRecords
        """
        if status:
            query = text("""
                SELECT id, digest, method, start_time, end_time, item_count,
                       status, iota_block_id, iota_network, explorer_url,
                       error_message, created_at, posted_at, confirmed_at
                FROM anchors
                WHERE status = :status
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """)
            result = await self._session.execute(
                query,
                {"status": status, "limit": limit, "offset": offset},
            )
        else:
            query = text("""
                SELECT id, digest, method, start_time, end_time, item_count,
                       status, iota_block_id, iota_network, explorer_url,
                       error_message, created_at, posted_at, confirmed_at
                FROM anchors
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """)
            result = await self._session.execute(
                query,
                {"limit": limit, "offset": offset},
            )

        records = []
        for row in result.fetchall():
            records.append(
                AnchorRecord(
                    id=row.id,
                    digest=row.digest,
                    method=row.method,
                    start_time=row.start_time,
                    end_time=row.end_time,
                    item_count=row.item_count,
                    status=AnchorStatus(row.status),
                    iota_block_id=row.iota_block_id,
                    iota_network=row.iota_network,
                    explorer_url=row.explorer_url,
                    error_message=row.error_message,
                    created_at=row.created_at,
                    posted_at=row.posted_at,
                    confirmed_at=row.confirmed_at,
                )
            )
        return records

    async def update_anchor_status(
        self,
        anchor_id: UUID,
        status: AnchorStatus,
        iota_block_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """
        Update anchor status.

        Args:
            anchor_id: Anchor UUID
            status: New status
            iota_block_id: Optional IOTA block ID
            error_message: Optional error message

        Returns:
            True if updated successfully
        """
        updates = ["status = :status"]
        params: dict[str, Any] = {"id": anchor_id, "status": status.value}

        if status == AnchorStatus.POSTED and iota_block_id:
            updates.append("iota_block_id = :iota_block_id")
            updates.append("posted_at = :posted_at")
            params["iota_block_id"] = iota_block_id
            params["posted_at"] = datetime.utcnow()

        if status == AnchorStatus.CONFIRMED:
            updates.append("confirmed_at = :confirmed_at")
            params["confirmed_at"] = datetime.utcnow()

        if status == AnchorStatus.FAILED and error_message:
            updates.append("error_message = :error_message")
            params["error_message"] = error_message

        query = text(f"""
            UPDATE anchors
            SET {', '.join(updates)}
            WHERE id = :id
            RETURNING id
        """)

        result = await self._session.execute(query, params)
        await self._session.commit()

        return result.fetchone() is not None

    async def save_anchor_item(
        self,
        anchor_id: UUID,
        event_hash: str,
        position: int,
        event_id: UUID | None = None,
        merkle_proof: list[str] | None = None,
    ) -> UUID:
        """
        Save an anchor item (event reference).

        Args:
            anchor_id: Parent anchor UUID
            event_hash: Event hash
            position: Position in Merkle tree
            event_id: Optional event UUID
            merkle_proof: Optional Merkle proof path

        Returns:
            Anchor item UUID
        """
        import json

        query = text("""
            INSERT INTO anchor_items (
                anchor_id, event_id, event_hash, position_in_merkle, merkle_proof
            ) VALUES (
                :anchor_id, :event_id, :event_hash, :position, :merkle_proof::jsonb
            )
            RETURNING id
        """)

        result = await self._session.execute(
            query,
            {
                "anchor_id": anchor_id,
                "event_id": event_id,
                "event_hash": event_hash,
                "position": position,
                "merkle_proof": json.dumps(merkle_proof) if merkle_proof else None,
            },
        )
        await self._session.commit()

        row = result.fetchone()
        return row.id

    async def get_anchor_items(
        self,
        anchor_id: UUID,
    ) -> list[dict[str, Any]]:
        """
        Get anchor items for an anchor.

        Args:
            anchor_id: Anchor UUID

        Returns:
            List of anchor items
        """
        query = text("""
            SELECT id, event_id, event_hash, position_in_merkle, merkle_proof
            FROM anchor_items
            WHERE anchor_id = :anchor_id
            ORDER BY position_in_merkle
        """)

        result = await self._session.execute(query, {"anchor_id": anchor_id})

        items = []
        for row in result.fetchall():
            items.append({
                "id": str(row.id),
                "event_id": str(row.event_id) if row.event_id else None,
                "event_hash": row.event_hash,
                "position": row.position_in_merkle,
                "merkle_proof": row.merkle_proof,
            })
        return items

    async def find_anchor_by_event_hash(
        self,
        event_hash: str,
    ) -> AnchorRecord | None:
        """
        Find anchor containing a specific event hash.

        Args:
            event_hash: Event hash to search for

        Returns:
            AnchorRecord or None
        """
        query = text("""
            SELECT a.id, a.digest, a.method, a.start_time, a.end_time, a.item_count,
                   a.status, a.iota_block_id, a.iota_network, a.explorer_url,
                   a.error_message, a.created_at, a.posted_at, a.confirmed_at
            FROM anchors a
            INNER JOIN anchor_items ai ON a.id = ai.anchor_id
            WHERE ai.event_hash = :event_hash
            LIMIT 1
        """)

        result = await self._session.execute(query, {"event_hash": event_hash})
        row = result.fetchone()

        if not row:
            return None

        return AnchorRecord(
            id=row.id,
            digest=row.digest,
            method=row.method,
            start_time=row.start_time,
            end_time=row.end_time,
            item_count=row.item_count,
            status=AnchorStatus(row.status),
            iota_block_id=row.iota_block_id,
            iota_network=row.iota_network,
            explorer_url=row.explorer_url,
            error_message=row.error_message,
            created_at=row.created_at,
            posted_at=row.posted_at,
            confirmed_at=row.confirmed_at,
        )

    async def get_pending_anchors(self) -> list[AnchorRecord]:
        """
        Get anchors in pending status for retry processing.

        Returns:
            List of pending AnchorRecords
        """
        return await self.list_anchors(status=AnchorStatus.PENDING.value)

    async def get_failed_anchors(self) -> list[AnchorRecord]:
        """
        Get failed anchors for reconciliation.

        Returns:
            List of failed AnchorRecords
        """
        return await self.list_anchors(status=AnchorStatus.FAILED.value)

    async def count_anchors(self, status: str | None = None) -> int:
        """
        Count total anchors with optional status filter.

        Args:
            status: Optional status filter

        Returns:
            Total count
        """
        if status:
            query = text("""
                SELECT COUNT(*) as count
                FROM anchors
                WHERE status = :status
            """)
            result = await self._session.execute(query, {"status": status})
        else:
            query = text("SELECT COUNT(*) as count FROM anchors")
            result = await self._session.execute(query)

        row = result.fetchone()
        return row.count if row else 0

    async def get_anchor_item_by_hash(
        self,
        anchor_id: UUID,
        event_hash: str,
    ) -> dict[str, Any] | None:
        """
        Get a specific anchor item by anchor ID and event hash.

        Args:
            anchor_id: Anchor UUID
            event_hash: Event hash

        Returns:
            Anchor item dict or None
        """
        query = text("""
            SELECT id, anchor_id, event_id, event_hash, position_in_merkle, merkle_proof
            FROM anchor_items
            WHERE anchor_id = :anchor_id AND event_hash = :event_hash
        """)

        result = await self._session.execute(
            query,
            {"anchor_id": anchor_id, "event_hash": event_hash},
        )
        row = result.fetchone()

        if not row:
            return None

        return {
            "id": str(row.id),
            "anchor_id": row.anchor_id,
            "event_id": str(row.event_id) if row.event_id else None,
            "event_hash": row.event_hash,
            "position": row.position_in_merkle,
            "merkle_proof": row.merkle_proof,
        }

    async def find_anchor_item_by_hash(
        self,
        event_hash: str,
    ) -> dict[str, Any] | None:
        """
        Find anchor item by event hash across all anchors.

        Args:
            event_hash: Event hash to search

        Returns:
            Anchor item dict or None
        """
        query = text("""
            SELECT id, anchor_id, event_id, event_hash, position_in_merkle, merkle_proof
            FROM anchor_items
            WHERE event_hash = :event_hash
            LIMIT 1
        """)

        result = await self._session.execute(query, {"event_hash": event_hash})
        row = result.fetchone()

        if not row:
            return None

        return {
            "id": str(row.id),
            "anchor_id": row.anchor_id,
            "event_id": str(row.event_id) if row.event_id else None,
            "event_hash": row.event_hash,
            "position": row.position_in_merkle,
            "merkle_proof": row.merkle_proof,
        }

    async def get_anchor_items_paginated(
        self,
        anchor_id: UUID,
        limit: int = 100,
        offset: int = 0,
        device_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Get paginated anchor items for an anchor with optional device filter.

        Args:
            anchor_id: Anchor UUID
            limit: Maximum items to return
            offset: Pagination offset
            device_id: Optional device ID filter

        Returns:
            Tuple of (items list, total count)
        """
        if device_id:
            query = text("""
                SELECT ai.id, ai.event_id, ai.event_hash, ai.position_in_merkle, 
                       ai.merkle_proof, ai.created_at
                FROM anchor_items ai
                LEFT JOIN events e ON ai.event_id = e.id
                WHERE ai.anchor_id = :anchor_id AND e.device_id = :device_id
                ORDER BY ai.position_in_merkle
                LIMIT :limit OFFSET :offset
            """)
            count_query = text("""
                SELECT COUNT(*) as count
                FROM anchor_items ai
                LEFT JOIN events e ON ai.event_id = e.id
                WHERE ai.anchor_id = :anchor_id AND e.device_id = :device_id
            """)
            params = {"anchor_id": anchor_id, "device_id": device_id, "limit": limit, "offset": offset}
            count_params = {"anchor_id": anchor_id, "device_id": device_id}
        else:
            query = text("""
                SELECT id, event_id, event_hash, position_in_merkle, merkle_proof, created_at
                FROM anchor_items
                WHERE anchor_id = :anchor_id
                ORDER BY position_in_merkle
                LIMIT :limit OFFSET :offset
            """)
            count_query = text("""
                SELECT COUNT(*) as count
                FROM anchor_items
                WHERE anchor_id = :anchor_id
            """)
            params = {"anchor_id": anchor_id, "limit": limit, "offset": offset}
            count_params = {"anchor_id": anchor_id}

        result = await self._session.execute(query, params)
        count_result = await self._session.execute(count_query, count_params)

        items = []
        for row in result.fetchall():
            items.append({
                "id": str(row.id),
                "event_id": str(row.event_id) if row.event_id else None,
                "event_hash": row.event_hash,
                "position": row.position_in_merkle,
                "merkle_proof": row.merkle_proof,
                "created_at": row.created_at.isoformat() if hasattr(row, "created_at") and row.created_at else None,
            })

        count_row = count_result.fetchone()
        total = count_row.count if count_row else 0

        return items, total
