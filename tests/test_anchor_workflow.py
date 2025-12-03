"""
Unit tests for the Anchor Workflow.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.anchor_service import AnchorRecord, AnchorStatus
from app.services.anchor_workflow import AnchorResult, AnchorWorkflow
from app.services.event_consumer import EventWindow, IndexedEvent
from app.services.iota_client import BlockMetadata


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock database session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_anchor_service() -> AsyncMock:
    """Create a mock anchor service."""
    service = AsyncMock()
    service.create_anchor = AsyncMock()
    return service


@pytest.fixture
def sample_events() -> list[IndexedEvent]:
    """Create sample indexed events."""
    return [
        IndexedEvent(
            id=uuid4(),
            block_number=100,
            block_hash="0x" + "a" * 64,
            event_index=0,
            pallet="TelemetryProofs",
            event_name="ProofSubmitted",
            event_data={"device_id": "dev1"},
            event_hash="a" * 64,
            timestamp=datetime.utcnow(),
        ),
        IndexedEvent(
            id=uuid4(),
            block_number=101,
            block_hash="0x" + "b" * 64,
            event_index=0,
            pallet="TelemetryProofs",
            event_name="ProofSubmitted",
            event_data={"device_id": "dev2"},
            event_hash="b" * 64,
            timestamp=datetime.utcnow(),
        ),
    ]


@pytest.fixture
def sample_anchor_record() -> AnchorRecord:
    """Create a sample anchor record."""
    return AnchorRecord(
        id=uuid4(),
        digest="c" * 64,
        method="merkle_sha256",
        start_time=datetime.utcnow() - timedelta(days=1),
        end_time=datetime.utcnow(),
        item_count=2,
        status=AnchorStatus.POSTED,
        iota_block_id="0x" + "d" * 64,
    )


class TestAnchorResult:
    """Tests for AnchorResult."""

    def test_to_dict(self) -> None:
        """Test serialization."""
        result = AnchorResult(
            success=True,
            anchor_id=uuid4(),
            digest="a" * 64,
            event_count=10,
            iota_block_id="0x123",
            error=None,
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            duration_seconds=5.5,
        )

        data = result.to_dict()

        assert data["success"] is True
        assert data["event_count"] == 10
        assert data["duration_seconds"] == 5.5

    def test_to_dict_with_error(self) -> None:
        """Test serialization with error."""
        result = AnchorResult(
            success=False,
            anchor_id=None,
            digest=None,
            event_count=0,
            iota_block_id=None,
            error="Connection failed",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            duration_seconds=1.0,
        )

        data = result.to_dict()

        assert data["success"] is False
        assert data["error"] == "Connection failed"


class TestAnchorWorkflow:
    """Tests for AnchorWorkflow."""

    @pytest.mark.asyncio
    async def test_run_anchor_job_empty_window(
        self,
        mock_session: AsyncMock,
        mock_anchor_service: AsyncMock,
    ) -> None:
        """Test anchor job with no events."""
        workflow = AnchorWorkflow(mock_session, mock_anchor_service)

        with patch.object(
            workflow._event_consumer,
            "fetch_events_for_window",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = EventWindow(
                start_time=datetime.utcnow() - timedelta(days=1),
                end_time=datetime.utcnow(),
                events=[],
            )

            with patch.object(
                workflow._event_consumer,
                "get_last_anchor_time",
                new_callable=AsyncMock,
            ) as mock_last:
                mock_last.return_value = None

                result = await workflow.run_anchor_job()

                assert result.success
                assert result.event_count == 0
                assert result.anchor_id is None

    @pytest.mark.asyncio
    async def test_run_anchor_job_success(
        self,
        mock_session: AsyncMock,
        mock_anchor_service: AsyncMock,
        sample_events: list[IndexedEvent],
        sample_anchor_record: AnchorRecord,
    ) -> None:
        """Test successful anchor job."""
        workflow = AnchorWorkflow(mock_session, mock_anchor_service)

        mock_anchor_service.create_anchor.return_value = sample_anchor_record

        with patch.object(
            workflow._event_consumer,
            "fetch_events_for_window",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = EventWindow(
                start_time=datetime.utcnow() - timedelta(days=1),
                end_time=datetime.utcnow(),
                events=sample_events,
            )

            with patch.object(
                workflow._event_consumer,
                "get_last_anchor_time",
                new_callable=AsyncMock,
            ) as mock_last:
                mock_last.return_value = None

                with patch.object(
                    workflow._repository,
                    "save_anchor",
                    new_callable=AsyncMock,
                ) as mock_save:
                    mock_save.return_value = sample_anchor_record.id

                    with patch.object(
                        workflow._repository,
                        "save_anchor_item",
                        new_callable=AsyncMock,
                    ):
                        with patch.object(
                            workflow,
                            "_check_existing_anchor",
                            new_callable=AsyncMock,
                        ) as mock_check:
                            mock_check.return_value = None

                            result = await workflow.run_anchor_job()

                            assert result.success
                            assert result.event_count == 2
                            assert result.anchor_id is not None

    @pytest.mark.asyncio
    async def test_run_anchor_job_duplicate(
        self,
        mock_session: AsyncMock,
        mock_anchor_service: AsyncMock,
        sample_events: list[IndexedEvent],
        sample_anchor_record: AnchorRecord,
    ) -> None:
        """Test anchor job with existing anchor (idempotency)."""
        workflow = AnchorWorkflow(mock_session, mock_anchor_service)

        with patch.object(
            workflow._event_consumer,
            "fetch_events_for_window",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = EventWindow(
                start_time=datetime.utcnow() - timedelta(days=1),
                end_time=datetime.utcnow(),
                events=sample_events,
            )

            with patch.object(
                workflow._event_consumer,
                "get_last_anchor_time",
                new_callable=AsyncMock,
            ) as mock_last:
                mock_last.return_value = None

                with patch.object(
                    workflow,
                    "_check_existing_anchor",
                    new_callable=AsyncMock,
                ) as mock_check:
                    mock_check.return_value = sample_anchor_record

                    result = await workflow.run_anchor_job()

                    assert result.success
                    assert result.event_count == 2
                    # Should use existing anchor, not create new

    @pytest.mark.asyncio
    async def test_run_daily_anchor(
        self,
        mock_session: AsyncMock,
        mock_anchor_service: AsyncMock,
    ) -> None:
        """Test daily anchor job."""
        workflow = AnchorWorkflow(mock_session, mock_anchor_service)

        with patch.object(
            workflow,
            "run_anchor_job",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AnchorResult(
                success=True,
                anchor_id=uuid4(),
                digest="a" * 64,
                event_count=100,
                iota_block_id="0x123",
                error=None,
                start_time=datetime.utcnow() - timedelta(days=1),
                end_time=datetime.utcnow(),
                duration_seconds=10.0,
            )

            result = await workflow.run_daily_anchor()

            assert result.success
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_incremental_anchor_not_enough_events(
        self,
        mock_session: AsyncMock,
        mock_anchor_service: AsyncMock,
    ) -> None:
        """Test incremental anchor with insufficient events."""
        workflow = AnchorWorkflow(mock_session, mock_anchor_service)

        with patch.object(
            workflow._event_consumer,
            "get_last_anchor_time",
            new_callable=AsyncMock,
        ) as mock_last:
            mock_last.return_value = datetime.utcnow() - timedelta(hours=6)

            with patch.object(
                workflow._event_consumer,
                "get_event_count_since",
                new_callable=AsyncMock,
            ) as mock_count:
                mock_count.return_value = 50  # Less than min_events

                result = await workflow.run_incremental_anchor(min_events=100)

                assert result is None
