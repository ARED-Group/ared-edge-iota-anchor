"""
Unit tests for the Anchor Service.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest

from app.services.anchor_service import (
    AnchorRecord,
    AnchorService,
    AnchorServiceError,
    AnchorStatus,
)
from app.services.iota_client import (
    BlockMetadata,
    IOTAClientError,
    PostingError,
)


class TestAnchorStatus:
    """Tests for AnchorStatus enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert AnchorStatus.PENDING.value == "pending"
        assert AnchorStatus.BUILDING.value == "building"
        assert AnchorStatus.POSTING.value == "posting"
        assert AnchorStatus.POSTED.value == "posted"
        assert AnchorStatus.CONFIRMING.value == "confirming"
        assert AnchorStatus.CONFIRMED.value == "confirmed"
        assert AnchorStatus.FAILED.value == "failed"


class TestAnchorRecord:
    """Tests for AnchorRecord."""

    def test_creation(self, sample_anchor_record: AnchorRecord) -> None:
        """Test record creation."""
        assert sample_anchor_record.id is not None
        assert sample_anchor_record.digest is not None
        assert sample_anchor_record.status == AnchorStatus.PENDING
        assert sample_anchor_record.created_at is not None

    def test_to_dict(self, sample_anchor_record: AnchorRecord) -> None:
        """Test dictionary serialization."""
        data = sample_anchor_record.to_dict()
        
        assert "id" in data
        assert "digest" in data
        assert "status" in data
        assert data["status"] == "pending"
        assert "created_at" in data

    def test_default_created_at(self) -> None:
        """Test default created_at timestamp."""
        record = AnchorRecord(
            id=uuid4(),
            digest="test",
            method="sha256",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            item_count=10,
            status=AnchorStatus.PENDING,
        )
        
        assert record.created_at is not None


class TestAnchorService:
    """Tests for AnchorService."""

    def test_initialization(self, anchor_service: AnchorService) -> None:
        """Test service initialization."""
        assert anchor_service._iota_client is not None
        assert anchor_service._pending_anchors == {}

    @pytest.mark.asyncio
    async def test_initialize(self, anchor_service: AnchorService) -> None:
        """Test service initialization."""
        with patch.object(
            anchor_service._iota_client,
            "connect",
            new_callable=AsyncMock,
        ):
            await anchor_service.initialize()

    @pytest.mark.asyncio
    async def test_shutdown(self, anchor_service: AnchorService) -> None:
        """Test service shutdown."""
        with patch.object(
            anchor_service._iota_client,
            "disconnect",
            new_callable=AsyncMock,
        ):
            await anchor_service.shutdown()

    @pytest.mark.asyncio
    async def test_create_anchor_success(
        self,
        anchor_service: AnchorService,
        sample_block_metadata: BlockMetadata,
    ) -> None:
        """Test successful anchor creation."""
        with patch.object(
            type(anchor_service._iota_client),
            "is_connected",
            new_callable=PropertyMock,
            return_value=True,
        ):
            with patch.object(
                anchor_service._iota_client,
                "post_anchor",
                new_callable=AsyncMock,
            ) as mock_post:
                mock_post.return_value = sample_block_metadata
                
                with patch.object(
                    anchor_service._iota_client,
                    "get_explorer_url",
                    return_value="https://explorer.test/block/0x123",
                ):
                    record = await anchor_service.create_anchor(
                        digest="abc123" * 10,
                        item_count=100,
                        start_time=datetime(2025, 12, 1),
                        end_time=datetime(2025, 12, 2),
                    )
                    
                    assert record is not None
                    assert record.status in (AnchorStatus.POSTED, AnchorStatus.CONFIRMED)
                    assert record.iota_block_id == sample_block_metadata.block_id

    @pytest.mark.asyncio
    async def test_create_anchor_posting_failure(
        self,
        anchor_service: AnchorService,
    ) -> None:
        """Test anchor creation with posting failure."""
        with patch.object(
            anchor_service._iota_client,
            "post_anchor",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.side_effect = PostingError("Network error")
            
            with pytest.raises(AnchorServiceError):
                await anchor_service.create_anchor(
                    digest="abc123" * 10,
                    item_count=100,
                    start_time=datetime(2025, 12, 1),
                    end_time=datetime(2025, 12, 2),
                )

    @pytest.mark.asyncio
    async def test_check_confirmation(
        self,
        anchor_service: AnchorService,
        sample_block_metadata: BlockMetadata,
    ) -> None:
        """Test confirmation checking."""
        with patch.object(
            anchor_service._iota_client,
            "get_block_metadata",
            new_callable=AsyncMock,
        ) as mock_get:
            sample_block_metadata.referenced_by_milestone = True
            mock_get.return_value = sample_block_metadata
            
            record = await anchor_service.check_confirmation(
                anchor_id=uuid4(),
                block_id="0x123",
            )
            
            assert record.status == AnchorStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_verify_anchor_on_tangle(
        self,
        anchor_service: AnchorService,
    ) -> None:
        """Test anchor verification on Tangle."""
        with patch.object(
            anchor_service._iota_client,
            "verify_block_exists",
            new_callable=AsyncMock,
        ) as mock_verify:
            mock_verify.return_value = True
            
            exists = await anchor_service.verify_anchor_on_tangle("0x123")
            
            assert exists

    @pytest.mark.asyncio
    async def test_get_node_status_connected(
        self,
        anchor_service: AnchorService,
    ) -> None:
        """Test node status when connected."""
        with patch.object(
            type(anchor_service._iota_client),
            "is_connected",
            new_callable=PropertyMock,
            return_value=True,
        ):
            with patch.object(
                anchor_service._iota_client,
                "_get_node_info",
                new_callable=AsyncMock,
            ) as mock_info:
                mock_info.return_value = {
                    "version": "1.0.0",
                    "protocol": {"networkName": "testnet"},
                }
                
                with patch.object(
                    anchor_service._iota_client,
                    "_check_health",
                    new_callable=AsyncMock,
                ) as mock_health:
                    mock_health.return_value = True
                    
                    status = await anchor_service.get_node_status()
                    
                    assert status["connected"]
                    assert "version" in status

    @pytest.mark.asyncio
    async def test_get_node_status_disconnected(
        self,
        anchor_service: AnchorService,
    ) -> None:
        """Test node status when disconnected."""
        anchor_service._iota_client._connected = False
        
        status = await anchor_service.get_node_status()
        
        assert not status["connected"]

    @pytest.mark.asyncio
    async def test_run_daily_anchor_no_events(
        self,
        anchor_service: AnchorService,
    ) -> None:
        """Test daily anchor with no events."""
        result = await anchor_service.run_daily_anchor()
        
        # Current implementation returns None when no events
        assert result is None
