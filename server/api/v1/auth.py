"""
NexLink Server — Auth API Endpoints
=====================================
POST /api/v1/auth/register   — device registration (serial_number based)
POST /api/v1/auth/token      — exchange API key for JWT
POST /api/v1/auth/refresh    — refresh access token

Identity:
  SERIAL_NUMBER (e.g. "TMX2405A12345") is the permanent device identifier.
  The JWT 'sub' claim carries the serial_number.
"""
from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_db
from server.core.exceptions import TokenExpiredError, TokenInvalidError
from server.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from server.db.models.device import Device
from server.schemas.auth import (
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    TokenRequest,
    TokenResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register device",
    description="""
Register a new device with the NexLink server.

Provide the device's hardware serial_number (mandatory) and optional
device info (model, manufacturer, android_version).

**CRITICAL**: The returned `api_key` is shown ONLY ONCE. The agent must
save it immediately — the server stores only a sha256 hash.
    """,
)
async def register_device(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    # Check if device already exists
    result = await db.execute(
        select(Device).where(Device.serial_number == req.serial_number)
    )
    existing = result.scalar_one_or_none()

    # Generate a fresh API key (new registration OR key rotation after reinstall/wipe)
    raw_key = secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    if existing:
        # Device re-registering (e.g. after wipe) — rotate key, update info
        existing.metadata_ = {
            **(existing.metadata_ or {}),
            "api_key_hash": key_hash,
            "api_key_prefix": raw_key[:8],
        }
        if req.model:
            existing.model = req.model
        if req.manufacturer:
            existing.manufacturer = req.manufacturer
        if req.android_version:
            existing.android_version = req.android_version
        await db.flush()
        logger.info("Device re-registered (key rotated): serial=%s prefix=%s", req.serial_number, raw_key[:8])
        return RegisterResponse(serial_number=req.serial_number, api_key=raw_key)

    # Create new device record
    device = Device(
        serial_number=req.serial_number,
        model=req.model,
        manufacturer=req.manufacturer,
        android_version=req.android_version,
        metadata_={
            "api_key_hash": key_hash,
            "api_key_prefix": raw_key[:8],
        },
    )
    db.add(device)
    await db.flush()

    logger.info(
        "Device registered: serial=%s prefix=%s",
        req.serial_number,
        raw_key[:8],
    )

    return RegisterResponse(serial_number=req.serial_number, api_key=raw_key)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Get access token",
    description="""
Exchange a device's serial_number + API key for JWT access and refresh tokens.

The API key is the raw key returned at registration time.
Access token lifetime: configurable (default 60 minutes).
Refresh token lifetime: configurable (default 30 days).
    """,
)
async def get_token(
    req: TokenRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Look up device
    result = await db.execute(
        select(Device).where(Device.serial_number == req.serial_number)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    # Verify API key against stored hash
    key_hash = hashlib.sha256(req.api_key.encode()).hexdigest()
    stored_hash = (device.metadata_ or {}).get("api_key_hash", "")
    if not stored_hash or key_hash != stored_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # Create JWT tokens — serial_number is used as both subject and agent_id
    access_token = create_access_token(
        device_id=device.serial_number,
        agent_id=device.serial_number,
    )
    refresh_token = create_refresh_token(
        device_id=device.serial_number,
        agent_id=device.serial_number,
    )

    from server.config.settings import get_settings
    settings = get_settings()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_access_expire_minutes * 60,
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Refresh access token",
    description="""
Issue a new access token using a valid refresh token.
The refresh token itself is NOT rotated — only a new access token is returned.
When the refresh token expires, the agent must re-authenticate with its API key.
    """,
)
async def refresh_token(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    try:
        payload = decode_token(req.refresh_token, expected_type="refresh")
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired. Re-authenticate with your API key.",
        )
    except TokenInvalidError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    serial_number = payload["sub"]

    # Verify device still exists
    result = await db.execute(
        select(Device).where(Device.serial_number == serial_number)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device no longer registered",
        )

    access_token = create_access_token(
        device_id=serial_number,
        agent_id=serial_number,
    )

    from server.config.settings import get_settings
    settings = get_settings()

    return RefreshResponse(
        access_token=access_token,
        expires_in=settings.jwt_access_expire_minutes * 60,
    )
