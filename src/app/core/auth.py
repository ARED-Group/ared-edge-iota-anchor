"""
ARED Edge IOTA Anchor Service - API Key Authentication Middleware

Protects write endpoints with API key validation.
Read-only and health endpoints remain publicly accessible.
"""

import secrets
from collections.abc import Callable

import structlog
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings

logger = structlog.get_logger(__name__)

PUBLIC_PATHS = frozenset({
    "/health",
    "/ready",
    "/live",
    "/status",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
})

PUBLIC_PREFIXES = (
    "/metrics/",
)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

API_KEY_HEADER = "X-API-Key"


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """
    API key authentication middleware for IOTA Anchor Service.

    Protects write endpoints (POST /api/v1/anchors, etc.) with API key validation.
    GET requests to anchor listing and health endpoints remain open.
    """

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        if self._is_public(path):
            return await call_next(request)

        if not settings.API_AUTH_ENABLED or not settings.API_KEY:
            return await call_next(request)

        if request.method not in WRITE_METHODS:
            return await call_next(request)

        provided_key = request.headers.get(API_KEY_HEADER)

        if not provided_key:
            logger.warning(
                "Missing API key on protected endpoint",
                path=path,
                method=request.method,
                client=request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Missing {API_KEY_HEADER} header"},
            )

        if not secrets.compare_digest(provided_key, settings.API_KEY):
            logger.warning(
                "Invalid API key",
                path=path,
                method=request.method,
                client=request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)

    @staticmethod
    def _is_public(path: str) -> bool:
        if path in PUBLIC_PATHS:
            return True
        return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)
