"""
Unit tests for the IOTA Client.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.iota_client import (
    AnchorMessage,
    BlockMetadata,
    ConfirmationError,
    ConnectionError,
    IOTAClient,
    IOTAClientError,
    MessageStatus,
    PostingError,
)


class TestAnchorMessage:
    """Tests for AnchorMessage."""

    def test_creation(self, sample_anchor_message: AnchorMessage) -> None:
        """Test message creation."""
        assert sample_anchor_message.digest is not None
        assert sample_anchor_message.digest_algorithm == "sha256"
        assert sample_anchor_message.anchor_type == "merkle_root"
        assert sample_anchor_message.event_count == 100

    def test_to_bytes(self, sample_anchor_message: AnchorMessage) -> None:
        """Test serialization to bytes."""
        data = sample_anchor_message.to_bytes()
        assert isinstance(data, bytes)
        
        # Should be valid JSON
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["digest"] == sample_anchor_message.digest
        assert parsed["type"] == "merkle_root"
        assert parsed["count"] == 100

    def test_compute_hash(self, sample_anchor_message: AnchorMessage) -> None:
        """Test hash computation."""
        hash1 = sample_anchor_message.compute_hash()
        hash2 = sample_anchor_message.compute_hash()
        
        assert len(hash1) == 64  # SHA-256 hex
        assert hash1 == hash2  # Deterministic

    def test_default_timestamp(self) -> None:
        """Test that timestamp defaults to now."""
        msg = AnchorMessage(
            digest="test",
            event_count=10,
            start_time=0,
            end_time=1,
        )
        assert msg.timestamp > 0


class TestBlockMetadata:
    """Tests for BlockMetadata."""

    def test_creation(self, sample_block_metadata: BlockMetadata) -> None:
        """Test metadata creation."""
        assert sample_block_metadata.block_id is not None
        assert sample_block_metadata.network == "testnet"
        assert sample_block_metadata.is_solid
        assert sample_block_metadata.referenced_by_milestone

    def test_default_timestamp(self) -> None:
        """Test default timestamp."""
        meta = BlockMetadata(
            block_id="0x123",
            network="testnet",
        )
        assert meta.timestamp is not None


class TestIOTAClient:
    """Tests for IOTAClient."""

    def test_initialization(self, iota_client: IOTAClient) -> None:
        """Test client initialization."""
        assert iota_client.node_url == "https://api.testnet.shimmer.network"
        assert iota_client.network == "testnet"
        assert not iota_client.is_connected

    @pytest.mark.asyncio
    async def test_connect_success(self, iota_client: IOTAClient) -> None:
        """Test successful connection."""
        with patch.object(iota_client, "_check_health", new_callable=AsyncMock) as mock_health:
            mock_health.return_value = True
            
            with patch.object(iota_client, "_get_node_info", new_callable=AsyncMock) as mock_info:
                mock_info.return_value = {
                    "version": "1.0.0",
                    "protocol": {"networkName": "shimmer-testnet"},
                }
                
                with patch("httpx.AsyncClient") as mock_client_class:
                    mock_client = AsyncMock()
                    mock_client_class.return_value = mock_client
                    
                    await iota_client.connect()
                    
                    assert iota_client.is_connected

    @pytest.mark.asyncio
    async def test_connect_health_fail(self, iota_client: IOTAClient) -> None:
        """Test connection failure on health check."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            with patch.object(iota_client, "_check_health", new_callable=AsyncMock) as mock_health:
                mock_health.return_value = False
                
                with pytest.raises(ConnectionError):
                    await iota_client.connect()

    @pytest.mark.asyncio
    async def test_disconnect(self, iota_client: IOTAClient) -> None:
        """Test disconnection."""
        iota_client._client = AsyncMock()
        iota_client._connected = True
        
        await iota_client.disconnect()
        
        assert not iota_client.is_connected
        assert iota_client._client is None

    @pytest.mark.asyncio
    async def test_get_block_metadata(self, iota_client: IOTAClient) -> None:
        """Test getting block metadata."""
        iota_client._client = AsyncMock()
        iota_client._connected = True
        
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "isSolid": True,
            "referencedByMilestoneIndex": 12345,
            "ledgerInclusionState": "included",
        }
        mock_response.raise_for_status = MagicMock()
        iota_client._client.get = AsyncMock(return_value=mock_response)
        
        metadata = await iota_client.get_block_metadata("0x123")
        
        assert metadata.is_solid
        assert metadata.referenced_by_milestone
        assert metadata.milestone_index == 12345
        assert metadata.ledger_inclusion_state == "included"

    @pytest.mark.asyncio
    async def test_verify_block_exists(self, iota_client: IOTAClient) -> None:
        """Test block existence verification."""
        with patch.object(
            iota_client,
            "get_block_metadata",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = BlockMetadata(
                block_id="0x123",
                network="testnet",
                is_solid=True,
            )
            
            exists = await iota_client.verify_block_exists("0x123")
            
            assert exists

    @pytest.mark.asyncio
    async def test_verify_block_not_exists(self, iota_client: IOTAClient) -> None:
        """Test block non-existence."""
        with patch.object(
            iota_client,
            "get_block_metadata",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = IOTAClientError("Not found")
            
            exists = await iota_client.verify_block_exists("0xnonexistent")
            
            assert not exists

    def test_get_explorer_url(self, iota_client: IOTAClient) -> None:
        """Test explorer URL generation."""
        url = iota_client.get_explorer_url("0x123abc")
        
        assert "0x123abc" in url
        assert "block" in url


class TestMessageStatus:
    """Tests for MessageStatus enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert MessageStatus.PENDING.value == "pending"
        assert MessageStatus.INCLUDED.value == "included"
        assert MessageStatus.CONFLICTING.value == "conflicting"
        assert MessageStatus.UNKNOWN.value == "unknown"


class TestExceptions:
    """Tests for exception classes."""

    def test_iota_client_error(self) -> None:
        """Test IOTAClientError."""
        error = IOTAClientError("Test error")
        assert str(error) == "Test error"

    def test_connection_error(self) -> None:
        """Test ConnectionError."""
        error = ConnectionError("Connection failed")
        assert isinstance(error, IOTAClientError)

    def test_posting_error(self) -> None:
        """Test PostingError."""
        error = PostingError("Posting failed")
        assert isinstance(error, IOTAClientError)

    def test_confirmation_error(self) -> None:
        """Test ConfirmationError."""
        error = ConfirmationError("Confirmation timeout")
        assert isinstance(error, IOTAClientError)
