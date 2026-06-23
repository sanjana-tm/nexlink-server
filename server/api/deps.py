"""
NexLink Server — FastAPI Dependency Injection
=============================================
All FastAPI Depends() factories live here.

Dependency Types:
  1. get_db()               — yields AsyncSession per request
  2. get_current_serial()   — extracts serial_number from JWT (Authorization: Bearer <token>)
  3. require_admin()        — validates the master admin API key (X-Admin-Key header)

JWT Auth Flow in Dependencies:
  Client sends: Authorization: Bearer <access_token>
  1. HTTPBearer parses the Bearer prefix
  2. decode_token() verifies signature + expiry
  3. Returns serial_number string from 'sub' claim

  Raises HTTPException 401 on any auth failure.
"""
from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from server.config.settings import ServerSettings, get_settings
from server.core.exceptions import TokenExpiredError, TokenInvalidError
from server.core.security import decode_token
from server.db.session import get_db  # noqa: F401 — re-exported for convenience

logger = logging.getLogger(__name__)

# HTTPBearer reads the Authorization: Bearer <token> header automatically
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_serial(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """
    FastAPI dependency: extract and validate JWT, return serial_number.

    Usage:
        @router.get("/devices/me")
        async def get_my_device(serial: str = Depends(get_current_serial)):
            ...

    The JWT 'sub' claim contains the device's serial_number.

    Raises HTTP 401 if:
      - No Authorization header present
      - Token is expired
      - Token signature is invalid
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Include: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        return payload["sub"]  # serial_number as string
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired. Request a new token with your API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except TokenInvalidError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_serial_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str | None:
    """
    Like get_current_serial but returns None instead of raising 401.
    Use for endpoints that work with or without auth.
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        return payload["sub"]
    except (TokenExpiredError, TokenInvalidError):
        return None


# Backward-compatible alias used by older endpoint modules
get_current_device_id = get_current_serial


async def require_admin(
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    settings: ServerSettings = Depends(get_settings),
) -> None:
    """
    FastAPI dependency: require master admin API key.

    Usage:
        @router.delete("/devices/{serial}")
        async def delete_device(
            serial: str,
            _: None = Depends(require_admin),
        ):
            ...

    The admin key is sent as: X-Admin-Key: <key>
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin operations are disabled (NEXLINK_ADMIN_API_KEY not configured)",
        )

    if x_admin_key != settings.admin_api_key:
        logger.warning("Admin auth failed: invalid X-Admin-Key header")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin key",
        )
