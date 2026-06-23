"""
NexLink Server — Custom Exceptions & HTTP Exception Handlers
=============================================================
Centralised exception hierarchy. All domain errors inherit from
NexLinkError so they can be caught at a single handler level.

FastAPI exception handlers are registered in core/lifecycle.py.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


# ── Base ──────────────────────────────────────────────────────────────────────

class NexLinkError(Exception):
    """Root exception for all NexLink server errors."""
    status_code: int = 500
    error_code: str = "NEXLINK_ERROR"

    def __init__(self, detail: str, *, error_code: str | None = None) -> None:
        self.detail = detail
        if error_code:
            self.error_code = error_code
        super().__init__(detail)


# ── Auth ──────────────────────────────────────────────────────────────────────

class AuthenticationError(NexLinkError):
    """Invalid credentials — wrong API key or malformed JWT."""
    status_code = 401
    error_code = "AUTHENTICATION_FAILED"


class AuthorizationError(NexLinkError):
    """Valid credentials but insufficient permissions."""
    status_code = 403
    error_code = "AUTHORIZATION_FAILED"


class TokenExpiredError(NexLinkError):
    """JWT token has expired."""
    status_code = 401
    error_code = "TOKEN_EXPIRED"


class TokenInvalidError(NexLinkError):
    """JWT token signature or format is invalid."""
    status_code = 401
    error_code = "TOKEN_INVALID"


# ── Device ────────────────────────────────────────────────────────────────────

class DeviceNotFoundError(NexLinkError):
    """Device with given DEVICE_ID does not exist."""
    status_code = 404
    error_code = "DEVICE_NOT_FOUND"


class DeviceAlreadyRegisteredError(NexLinkError):
    """Device is already registered (use update instead)."""
    status_code = 409
    error_code = "DEVICE_ALREADY_REGISTERED"


class DeviceOfflineError(NexLinkError):
    """Operation requires device to be online."""
    status_code = 503
    error_code = "DEVICE_OFFLINE"


# ── Session ───────────────────────────────────────────────────────────────────

class SessionNotFoundError(NexLinkError):
    """Session with given SESSION_ID does not exist."""
    status_code = 404
    error_code = "SESSION_NOT_FOUND"


class SessionExpiredError(NexLinkError):
    """WebSocket session has expired or disconnected."""
    status_code = 410
    error_code = "SESSION_EXPIRED"


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class HeartbeatError(NexLinkError):
    """Error processing a heartbeat."""
    status_code = 422
    error_code = "HEARTBEAT_ERROR"


# ── Event ─────────────────────────────────────────────────────────────────────

class EventBusFullError(NexLinkError):
    """Event bus queue is at capacity — backpressure."""
    status_code = 503
    error_code = "EVENT_BUS_FULL"


class EventNotFoundError(NexLinkError):
    """Event with given ID does not exist."""
    status_code = 404
    error_code = "EVENT_NOT_FOUND"


# ── WebSocket ─────────────────────────────────────────────────────────────────

class WebSocketAuthError(NexLinkError):
    """WebSocket connection rejected due to bad token."""
    status_code = 4001
    error_code = "WS_AUTH_FAILED"


class WebSocketMessageError(NexLinkError):
    """Malformed WebSocket message."""
    status_code = 4002
    error_code = "WS_MESSAGE_ERROR"


# ── Validation ────────────────────────────────────────────────────────────────

class ValidationError(NexLinkError):
    """Input validation failed."""
    status_code = 422
    error_code = "VALIDATION_ERROR"


# ── HTTP Exception Handlers ───────────────────────────────────────────────────

async def nexlink_error_handler(request: Request, exc: NexLinkError) -> JSONResponse:
    """
    Global handler for all NexLinkError subclasses.
    Returns a consistent JSON error envelope:

        {
          "error": "DEVICE_NOT_FOUND",
          "detail": "Device abc123 does not exist",
          "path": "/api/v1/devices/abc123"
        }
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.error_code,
            "detail": exc.detail,
            "path": str(request.url.path),
        },
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected errors (prevents stack trace leakage)."""
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "detail": "An unexpected error occurred. Check server logs.",
            "path": str(request.url.path),
        },
    )
