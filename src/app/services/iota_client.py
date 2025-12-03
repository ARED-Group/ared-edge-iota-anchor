"""
ARED Edge IOTA Anchor Service - IOTA Client

Enterprise-grade IOTA Tangle client for anchoring Merkle roots.
Provides connection management, message posting, and confirmation monitoring.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = structlog.get_logger(__name__)


class MessageStatus(str, Enum):
    """IOTA message confirmation status."""

    PENDING = "pending"
    INCLUDED = "included"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


@dataclass
class BlockMetadata:
    """Metadata for a posted IOTA block."""

    block_id: str
    network: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    is_solid: bool = False
    referenced_by_milestone: bool = False
    milestone_index: int | None = None
    ledger_inclusion_state: str | None = None


@dataclass
class AnchorMessage:
    """Anchor message structure for IOTA Tangle."""

    digest: str
    digest_algorithm: str = "sha256"
    anchor_type: str = "merkle_root"
    timestamp: int = 0
    event_count: int = 0
    start_time: int = 0
    end_time: int = 0
    version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp == 0:
            self.timestamp = int(datetime.utcnow().timestamp())

    def to_bytes(self) -> bytes:
        """Serialize message to bytes for Tangle posting."""
        data = {
            "digest": self.digest,
            "algorithm": self.digest_algorithm,
            "type": self.anchor_type,
            "ts": self.timestamp,
            "count": self.event_count,
            "start": self.start_time,
            "end": self.end_time,
            "v": self.version,
        }
        if self.metadata:
            data["meta"] = self.metadata
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    def compute_hash(self) -> str:
        """Compute hash of the anchor message."""
        return hashlib.sha256(self.to_bytes()).hexdigest()


class IOTAClientError(Exception):
    """Base exception for IOTA client errors."""

    pass


class ConnectionError(IOTAClientError):
    """Failed to connect to IOTA node."""

    pass


class PostingError(IOTAClientError):
    """Failed to post message to Tangle."""

    pass


class ConfirmationError(IOTAClientError):
    """Message confirmation failed or timed out."""

    pass


class IOTAClient:
    """
    IOTA Tangle client for anchoring operations.

    Provides:
    - Node connection and health checking
    - Tagged data block submission
    - Confirmation monitoring with polling
    - Retry logic with exponential backoff
    """

    def __init__(
        self,
        node_url: str = settings.IOTA_NODE_URL,
        network: str = settings.IOTA_NETWORK,
        tag: str = settings.iota_tag,
    ) -> None:
        """
        Initialize IOTA client.

        Args:
            node_url: IOTA node API URL
            network: Network identifier (mainnet/shimmer/testnet)
            tag: Tag prefix for anchor messages
        """
        self._node_url = node_url.rstrip("/")
        self._network = network
        self._tag = tag
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        self._node_info: dict[str, Any] | None = None

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to node."""
        return self._connected and self._client is not None

    @property
    def node_url(self) -> str:
        """Get configured node URL."""
        return self._node_url

    @property
    def network(self) -> str:
        """Get configured network."""
        return self._network

    async def connect(self) -> None:
        """
        Establish connection to IOTA node.

        Verifies node health and retrieves node info.
        """
        logger.info(
            "Connecting to IOTA node",
            url=self._node_url,
            network=self._network,
        )

        self._client = httpx.AsyncClient(
            base_url=self._node_url,
            timeout=httpx.Timeout(settings.IOTA_REQUEST_TIMEOUT),
            headers={"Content-Type": "application/json"},
        )

        try:
            # Verify node health
            health = await self._check_health()
            if not health:
                raise ConnectionError("IOTA node health check failed")

            # Get node info
            self._node_info = await self._get_node_info()
            self._connected = True

            logger.info(
                "Connected to IOTA node",
                protocol=self._node_info.get("protocol", {}).get("networkName"),
                version=self._node_info.get("version"),
                is_healthy=health,
            )

        except httpx.HTTPError as e:
            self._connected = False
            raise ConnectionError(f"Failed to connect to IOTA node: {e}") from e

    async def disconnect(self) -> None:
        """Close connection to IOTA node."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        logger.info("Disconnected from IOTA node")

    async def post_anchor(
        self,
        message: AnchorMessage,
        wait_for_inclusion: bool = True,
    ) -> BlockMetadata:
        """
        Post an anchor message to the IOTA Tangle.

        Args:
            message: Anchor message to post
            wait_for_inclusion: Whether to wait for confirmation

        Returns:
            BlockMetadata with block ID and status

        Raises:
            PostingError: If posting fails
            ConfirmationError: If confirmation times out
        """
        if not self.is_connected:
            await self.connect()

        logger.info(
            "Posting anchor to Tangle",
            digest=message.digest[:16] + "...",
            event_count=message.event_count,
            tag=self._tag,
        )

        try:
            # Build and submit block with retry
            block_id = await self._submit_block_with_retry(message)

            metadata = BlockMetadata(
                block_id=block_id,
                network=self._network,
            )

            logger.info(
                "Block submitted",
                block_id=block_id,
            )

            # Wait for confirmation if requested
            if wait_for_inclusion:
                metadata = await self._wait_for_confirmation(
                    block_id,
                    timeout=settings.IOTA_CONFIRMATION_TIMEOUT,
                )

            return metadata

        except RetryError as e:
            raise PostingError(f"Failed to post anchor after retries: {e}") from e
        except Exception as e:
            raise PostingError(f"Unexpected error posting anchor: {e}") from e

    async def get_block_metadata(self, block_id: str) -> BlockMetadata:
        """
        Get metadata for a block.

        Args:
            block_id: Block ID to query

        Returns:
            BlockMetadata with current status
        """
        if not self.is_connected:
            await self.connect()

        try:
            response = await self._client.get(f"/api/core/v2/blocks/{block_id}/metadata")
            response.raise_for_status()
            data = response.json()

            return BlockMetadata(
                block_id=block_id,
                network=self._network,
                is_solid=data.get("isSolid", False),
                referenced_by_milestone=data.get("referencedByMilestoneIndex") is not None,
                milestone_index=data.get("referencedByMilestoneIndex"),
                ledger_inclusion_state=data.get("ledgerInclusionState"),
            )

        except httpx.HTTPError as e:
            logger.error("Failed to get block metadata", block_id=block_id, error=str(e))
            raise IOTAClientError(f"Failed to get block metadata: {e}") from e

    async def verify_block_exists(self, block_id: str) -> bool:
        """
        Verify a block exists on the Tangle.

        Args:
            block_id: Block ID to verify

        Returns:
            True if block exists and is valid
        """
        try:
            metadata = await self.get_block_metadata(block_id)
            return metadata.is_solid
        except IOTAClientError:
            return False

    async def _check_health(self) -> bool:
        """Check node health status."""
        try:
            response = await self._client.get("/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _get_node_info(self) -> dict[str, Any]:
        """Get node information."""
        response = await self._client.get("/api/core/v2/info")
        response.raise_for_status()
        return response.json()

    async def _submit_block_with_retry(self, message: AnchorMessage) -> str:
        """Submit block with retry logic."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.IOTA_RETRY_COUNT),
            wait=wait_exponential(
                multiplier=settings.IOTA_RETRY_DELAY,
                max=settings.IOTA_RETRY_MAX_DELAY,
            ),
            retry=retry_if_exception_type((httpx.HTTPError, IOTAClientError)),
            reraise=True,
        ):
            with attempt:
                return await self._submit_tagged_data_block(message)

    async def _submit_tagged_data_block(self, message: AnchorMessage) -> str:
        """
        Submit a tagged data block to the Tangle.

        Uses the IOTA Core API v2 to submit a block containing
        tagged data payload with the anchor message.

        Args:
            message: Anchor message to include

        Returns:
            Block ID of the submitted block
        """
        # Encode tag as bytes (hex string)
        tag_bytes = self._tag.encode("utf-8").hex()
        data_bytes = message.to_bytes().hex()

        # Build tagged data payload
        payload = {
            "type": 5,  # Tagged Data payload type
            "tag": tag_bytes,
            "data": data_bytes,
        }

        # Build block
        block = {
            "protocolVersion": 2,
            "payload": payload,
        }

        try:
            response = await self._client.post(
                "/api/core/v2/blocks",
                json=block,
                timeout=settings.IOTA_API_TIMEOUT,
            )
            response.raise_for_status()
            result = response.json()
            return result.get("blockId", "")

        except httpx.HTTPStatusError as e:
            logger.error(
                "Block submission failed",
                status_code=e.response.status_code,
                response=e.response.text[:200],
            )
            raise PostingError(f"Block submission failed: {e.response.status_code}") from e

    async def _wait_for_confirmation(
        self,
        block_id: str,
        timeout: int,
    ) -> BlockMetadata:
        """
        Wait for block confirmation.

        Polls block metadata until included or timeout.

        Args:
            block_id: Block ID to monitor
            timeout: Maximum wait time in seconds

        Returns:
            Final BlockMetadata

        Raises:
            ConfirmationError: If timeout or conflicting state
        """
        start_time = asyncio.get_event_loop().time()
        poll_interval = settings.IOTA_CONFIRMATION_POLL_INTERVAL

        logger.info(
            "Waiting for block confirmation",
            block_id=block_id,
            timeout=timeout,
        )

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                raise ConfirmationError(
                    f"Block confirmation timeout after {timeout}s"
                )

            try:
                metadata = await self.get_block_metadata(block_id)

                if metadata.ledger_inclusion_state == "included":
                    logger.info(
                        "Block confirmed",
                        block_id=block_id,
                        milestone_index=metadata.milestone_index,
                        elapsed_seconds=round(elapsed, 2),
                    )
                    return metadata

                if metadata.ledger_inclusion_state == "conflicting":
                    raise ConfirmationError("Block has conflicting state")

                # Still pending, continue polling
                await asyncio.sleep(poll_interval)

            except IOTAClientError:
                # Node error, retry after delay
                await asyncio.sleep(poll_interval)

    async def get_tips(self) -> list[str]:
        """Get current tips from the node."""
        if not self.is_connected:
            await self.connect()

        try:
            response = await self._client.get("/api/core/v2/tips")
            response.raise_for_status()
            return response.json().get("tips", [])
        except httpx.HTTPError as e:
            raise IOTAClientError(f"Failed to get tips: {e}") from e

    def get_explorer_url(self, block_id: str) -> str:
        """
        Get explorer URL for a block.

        Args:
            block_id: Block ID

        Returns:
            URL to view block in explorer
        """
        return f"{settings.IOTA_EXPLORER_URL}/block/{block_id}"
