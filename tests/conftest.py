"""
Pytest configuration and shared fixtures for IOTA anchor tests.
"""

import asyncio
from collections.abc import Generator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.iota_client import AnchorMessage, BlockMetadata, IOTAClient
from app.services.anchor_service import AnchorRecord, AnchorService, AnchorStatus


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    """Create a mock httpx client."""
    client = AsyncMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def sample_anchor_message() -> AnchorMessage:
    """Create a sample anchor message."""
    return AnchorMessage(
        digest="abc123def456789012345678901234567890123456789012345678901234",
        digest_algorithm="sha256",
        anchor_type="merkle_root",
        timestamp=int(datetime.utcnow().timestamp()),
        event_count=100,
        start_time=int(datetime(2025, 12, 1).timestamp()),
        end_time=int(datetime(2025, 12, 2).timestamp()),
    )


@pytest.fixture
def sample_block_metadata() -> BlockMetadata:
    """Create sample block metadata."""
    return BlockMetadata(
        block_id="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        network="testnet",
        is_solid=True,
        referenced_by_milestone=True,
        milestone_index=12345,
        ledger_inclusion_state="included",
    )


@pytest.fixture
def sample_anchor_record() -> AnchorRecord:
    """Create a sample anchor record."""
    return AnchorRecord(
        id=uuid4(),
        digest="abc123def456789012345678901234567890123456789012345678901234",
        method="merkle_sha256",
        start_time=datetime(2025, 12, 1),
        end_time=datetime(2025, 12, 2),
        item_count=100,
        status=AnchorStatus.PENDING,
    )


@pytest.fixture
def iota_client() -> IOTAClient:
    """Create an IOTA client for testing."""
    return IOTAClient(
        node_url="https://api.testnet.shimmer.network",
        network="testnet",
        tag="ARED_ANCHOR_v1",
    )


@pytest.fixture
def anchor_service() -> AnchorService:
    """Create an anchor service for testing."""
    return AnchorService()
