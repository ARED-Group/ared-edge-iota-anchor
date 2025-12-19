"""
ARED Edge IOTA Anchor API - Anchor Endpoints

Implements anchoring APIs as defined in ared-edge-iota-anchor README:
- POST /anchors: Trigger immediate anchor job
- GET /anchors: List anchors with filtering and pagination
- GET /anchors/{id}: Get anchor details with items and proofs
- POST /verify: Verify inclusion proof
"""

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.crypto.merkle import MerkleProof, ProofElement, verify_proof
from app.db import async_session_factory
from app.db.repository import AnchorRepository
from app.services.anchor_service import AnchorRecord
from app.services.anchor_workflow import AnchorWorkflow

logger = structlog.get_logger(__name__)
router = APIRouter()


# Request/Response Models
class AnchorCreateRequest(BaseModel):
    """Request to trigger an immediate anchor."""

    start_time: datetime | None = Field(
        default=None,
        description="Start of anchor window (defaults to last anchor end time)",
    )
    end_time: datetime | None = Field(
        default=None,
        description="End of anchor window (defaults to now)",
    )
    wait_for_completion: bool = Field(
        default=False,
        description="If true, wait for job completion before responding",
    )


class AnchorItemResponse(BaseModel):
    """Response for a single anchor item."""

    event_hash: str
    position: int
    merkle_proof: list[str] | None = None


class AnchorResponse(BaseModel):
    """Anchor details response."""

    id: UUID
    digest: str
    method: str = "merkle_sha256"
    start_time: datetime
    end_time: datetime
    iota_message_id: str | None = None
    iota_network: str | None = None
    explorer_url: str | None = None
    status: str
    item_count: int
    created_at: datetime
    posted_at: datetime | None = None
    confirmed_at: datetime | None = None
    error_message: str | None = None

    class Config:
        from_attributes = True


class AnchorDetailResponse(AnchorResponse):
    """Detailed anchor response with items."""

    items: list[AnchorItemResponse] = Field(default_factory=list)


class AnchorJobResponse(BaseModel):
    """Response when triggering an anchor job."""

    job_id: str
    status: str
    message: str
    anchor_id: UUID | None = None
    digest: str | None = None
    event_count: int = 0
    iota_block_id: str | None = None


class AnchorListResponse(BaseModel):
    """Paginated anchor list response."""

    items: list[AnchorResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class VerifyRequest(BaseModel):
    """Request to verify inclusion proof."""

    event_hash: str = Field(
        ...,
        description="The event hash to verify",
        min_length=64,
        max_length=64,
    )
    anchor_id: UUID | None = Field(
        default=None,
        description="Optional anchor ID (if known)",
    )
    merkle_proof: list[str] | None = Field(
        default=None,
        description="Optional Merkle proof path (L:hash or R:hash format)",
    )
    verify_on_tangle: bool = Field(
        default=False,
        description="Also verify anchor exists on IOTA Tangle",
    )


class VerifyResponse(BaseModel):
    """Verification result."""

    verified: bool
    event_hash: str
    anchor_id: UUID | None = None
    anchor_digest: str | None = None
    iota_message_id: str | None = None
    explorer_url: str | None = None
    tangle_verified: bool | None = None
    message: str
    proof_path: list[str] | None = None


def _record_to_response(record: AnchorRecord) -> AnchorResponse:
    """Convert AnchorRecord to AnchorResponse."""
    return AnchorResponse(
        id=record.id,
        digest=record.digest,
        method=record.method,
        start_time=record.start_time,
        end_time=record.end_time,
        iota_message_id=record.iota_block_id,
        iota_network=record.iota_network,
        explorer_url=record.explorer_url,
        status=record.status.value,
        item_count=record.item_count,
        created_at=record.created_at,
        posted_at=record.posted_at,
        confirmed_at=record.confirmed_at,
        error_message=record.error_message,
    )


# Endpoints
@router.post(
    "",
    response_model=AnchorJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger anchor job",
    description="Trigger an immediate anchoring job for the specified time window.",
    responses={
        202: {"description": "Anchor job queued"},
        200: {"description": "Anchor job completed (when wait_for_completion=true)"},
        500: {"description": "Anchor job failed"},
    },
)
async def create_anchor(
    request: AnchorCreateRequest,
    req: Request,
) -> AnchorJobResponse:
    """
    Trigger an immediate anchor job.

    Creates a new anchor by:
    1. Fetching indexed events for the time window
    2. Building a Merkle tree from event hashes
    3. Posting the root digest to IOTA Tangle
    4. Storing anchor record and items
    """
    job_id = str(uuid4())

    logger.info(
        "Anchor job requested",
        job_id=job_id,
        start_time=request.start_time,
        end_time=request.end_time,
        wait=request.wait_for_completion,
    )

    # Get anchor service from app state
    anchor_service = getattr(req.app.state, "anchor_service", None)
    if not anchor_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Anchor service not initialized",
        )

    try:
        async with async_session_factory() as session:
            workflow = AnchorWorkflow(session, anchor_service)

            if request.wait_for_completion:
                # Synchronous execution
                result = await workflow.run_anchor_job(
                    start_time=request.start_time,
                    end_time=request.end_time,
                    wait_for_confirmation=True,
                )

                if result.success:
                    return AnchorJobResponse(
                        job_id=job_id,
                        status="completed",
                        message="Anchor created successfully",
                        anchor_id=result.anchor_id,
                        digest=result.digest,
                        event_count=result.event_count,
                        iota_block_id=result.iota_block_id,
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=result.error or "Anchor job failed",
                    )
            else:
                # Queue for background processing
                asyncio.create_task(
                    _run_background_anchor(
                        anchor_service,
                        request.start_time,
                        request.end_time,
                    )
                )

                return AnchorJobResponse(
                    job_id=job_id,
                    status="pending",
                    message="Anchor job queued for background processing",
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create anchor", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create anchor: {e}",
        )


async def _run_background_anchor(
    anchor_service: Any,
    start_time: datetime | None,
    end_time: datetime | None,
) -> None:
    """Run anchor job in background."""
    try:
        async with async_session_factory() as session:
            workflow = AnchorWorkflow(session, anchor_service)
            await workflow.run_anchor_job(
                start_time=start_time,
                end_time=end_time,
            )
    except Exception as e:
        logger.error("Background anchor job failed", error=str(e))


@router.get(
    "",
    response_model=AnchorListResponse,
    summary="List anchors",
    description="List anchors with optional status filtering and pagination.",
)
async def list_anchors(
    status_filter: str | None = Query(
        default=None,
        description="Filter by status (pending, posted, confirmed, failed)",
        alias="status",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of anchors to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of anchors to skip",
    ),
) -> AnchorListResponse:
    """List anchors with optional filtering and pagination."""
    logger.info(
        "Listing anchors",
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    try:
        async with async_session_factory() as session:
            repository = AnchorRepository(session)

            # Get anchors
            anchors = await repository.list_anchors(
                status=status_filter,
                limit=limit + 1,  # Fetch one extra to check has_more
                offset=offset,
            )

            # Check if there are more
            has_more = len(anchors) > limit
            if has_more:
                anchors = anchors[:limit]

            # Get total count
            total = await repository.count_anchors(status=status_filter)

            items = [_record_to_response(a) for a in anchors]

            return AnchorListResponse(
                items=items,
                total=total,
                limit=limit,
                offset=offset,
                has_more=has_more,
            )

    except Exception as e:
        logger.error("Failed to list anchors", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list anchors: {e}",
        )


@router.get(
    "/{anchor_id}",
    response_model=AnchorDetailResponse,
    summary="Get anchor details",
    description="Get detailed information about an anchor including items and proofs.",
    responses={
        200: {"description": "Anchor details"},
        404: {"description": "Anchor not found"},
    },
)
async def get_anchor(anchor_id: UUID) -> AnchorDetailResponse:
    """Get anchor details by ID including linked items."""
    logger.info("Getting anchor", anchor_id=str(anchor_id))

    try:
        async with async_session_factory() as session:
            repository = AnchorRepository(session)

            # Get anchor
            anchor = await repository.get_anchor(anchor_id)
            if not anchor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Anchor {anchor_id} not found",
                )

            # Get anchor items
            items = await repository.get_anchor_items(anchor_id)

            item_responses = [
                AnchorItemResponse(
                    event_hash=item["event_hash"],
                    position=item["position"],
                    merkle_proof=item.get("merkle_proof"),
                )
                for item in items
            ]

            return AnchorDetailResponse(
                id=anchor.id,
                digest=anchor.digest,
                method=anchor.method,
                start_time=anchor.start_time,
                end_time=anchor.end_time,
                iota_message_id=anchor.iota_block_id,
                iota_network=anchor.iota_network,
                explorer_url=anchor.explorer_url,
                status=anchor.status.value,
                item_count=anchor.item_count,
                created_at=anchor.created_at,
                posted_at=anchor.posted_at,
                confirmed_at=anchor.confirmed_at,
                error_message=anchor.error_message,
                items=item_responses,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get anchor", anchor_id=str(anchor_id), error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get anchor: {e}",
        )


class AnchorEventResponse(BaseModel):
    """Single event in anchor response."""

    event_hash: str
    position: int
    event_id: str | None = None
    merkle_proof: list[str] | None = None
    created_at: str | None = None


class AnchorEventsListResponse(BaseModel):
    """Paginated anchor events list response."""

    items: list[AnchorEventResponse]
    total: int
    limit: int
    offset: int
    has_more: bool
    anchor_id: UUID
    anchor_digest: str


@router.get(
    "/{anchor_id}/events",
    response_model=AnchorEventsListResponse,
    summary="List anchor events",
    description="List events included in an anchor with pagination and optional device filter.",
    responses={
        200: {"description": "Anchor events list"},
        404: {"description": "Anchor not found"},
    },
)
async def list_anchor_events(
    anchor_id: UUID,
    device_id: str | None = Query(
        default=None,
        description="Filter by device ID",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of events to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of events to skip",
    ),
) -> AnchorEventsListResponse:
    """
    List events included in an anchor.

    Returns paginated list of event hashes with their Merkle proofs.
    Optionally filter by device_id to find specific device events.
    """
    logger.info(
        "Listing anchor events",
        anchor_id=str(anchor_id),
        device_id=device_id,
        limit=limit,
        offset=offset,
    )

    try:
        async with async_session_factory() as session:
            repository = AnchorRepository(session)

            anchor = await repository.get_anchor(anchor_id)
            if not anchor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Anchor {anchor_id} not found",
                )

            items, total = await repository.get_anchor_items_paginated(
                anchor_id=anchor_id,
                limit=limit,
                offset=offset,
                device_id=device_id,
            )

            has_more = offset + len(items) < total

            event_responses = [
                AnchorEventResponse(
                    event_hash=item["event_hash"],
                    position=item["position"],
                    event_id=item.get("event_id"),
                    merkle_proof=item.get("merkle_proof"),
                    created_at=item.get("created_at"),
                )
                for item in items
            ]

            return AnchorEventsListResponse(
                items=event_responses,
                total=total,
                limit=limit,
                offset=offset,
                has_more=has_more,
                anchor_id=anchor.id,
                anchor_digest=anchor.digest,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list anchor events", anchor_id=str(anchor_id), error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list anchor events: {e}",
        )


@router.post(
    "/verify",
    response_model=VerifyResponse,
    summary="Verify inclusion proof",
    description="Verify that an event hash is included in a posted anchor.",
    responses={
        200: {"description": "Verification result"},
        404: {"description": "Event not found in any anchor"},
    },
)
async def verify_inclusion(
    request: VerifyRequest,
    req: Request,
) -> VerifyResponse:
    """
    Verify inclusion of an event in an anchor.

    Steps:
    1. Find anchor containing the event hash
    2. Verify Merkle proof reconstructs to anchor digest
    3. Optionally verify anchor exists on IOTA Tangle
    """
    logger.info(
        "Verifying inclusion",
        event_hash=request.event_hash[:16] + "...",
        anchor_id=str(request.anchor_id) if request.anchor_id else None,
        verify_tangle=request.verify_on_tangle,
    )

    try:
        async with async_session_factory() as session:
            repository = AnchorRepository(session)

            # Find anchor item
            if request.anchor_id:
                # Look in specific anchor
                anchor = await repository.get_anchor(request.anchor_id)
                if not anchor:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Anchor {request.anchor_id} not found",
                    )
                item = await repository.get_anchor_item_by_hash(
                    request.anchor_id,
                    request.event_hash,
                )
            else:
                # Search all anchors
                item = await repository.find_anchor_item_by_hash(request.event_hash)
                if item:
                    anchor = await repository.get_anchor(item["anchor_id"])
                else:
                    anchor = None

            if not item or not anchor:
                return VerifyResponse(
                    verified=False,
                    event_hash=request.event_hash,
                    message="Event hash not found in any anchor",
                )

            # Get proof path
            proof_path = request.merkle_proof or item.get("merkle_proof")
            if not proof_path:
                return VerifyResponse(
                    verified=False,
                    event_hash=request.event_hash,
                    anchor_id=anchor.id,
                    anchor_digest=anchor.digest,
                    iota_message_id=anchor.iota_block_id,
                    message="No Merkle proof available",
                )

            # Parse proof path
            proof_elements = []
            for path_item in proof_path:
                direction, hash_value = path_item.split(":", 1)
                proof_elements.append(
                    ProofElement(
                        hash=hash_value,
                        direction=direction,
                    )
                )

            # Create and verify proof
            proof = MerkleProof(
                leaf_hash=request.event_hash,
                leaf_index=item["position"],
                proof_path=proof_elements,
                root_hash=anchor.digest,
                tree_size=anchor.item_count,
            )

            verified = verify_proof(proof)

            # Optionally verify on Tangle
            tangle_verified = None
            if request.verify_on_tangle and anchor.iota_block_id:
                anchor_service = getattr(req.app.state, "anchor_service", None)
                if anchor_service:
                    try:
                        tangle_verified = await anchor_service.verify_anchor_on_tangle(
                            anchor.iota_block_id
                        )
                    except Exception as e:
                        logger.warning("Tangle verification failed", error=str(e))
                        tangle_verified = None

            return VerifyResponse(
                verified=verified,
                event_hash=request.event_hash,
                anchor_id=anchor.id,
                anchor_digest=anchor.digest,
                iota_message_id=anchor.iota_block_id,
                explorer_url=anchor.explorer_url,
                tangle_verified=tangle_verified,
                message="Verification successful" if verified else "Merkle proof verification failed",
                proof_path=proof_path,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Verification failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Verification failed: {e}",
        )
