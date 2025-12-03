"""
ARED Edge IOTA Anchor API v1

Endpoints:
- POST /anchors - Trigger anchor job
- GET /anchors - List anchors
- GET /anchors/{id} - Get anchor details
- POST /verify - Verify inclusion proof
"""

from fastapi import APIRouter

from app.api.v1.endpoints import anchors

router = APIRouter()
router.include_router(anchors.router, prefix="/anchors", tags=["Anchors"])
