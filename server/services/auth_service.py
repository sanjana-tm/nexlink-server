"""
NexLink Server — Auth Service
===============================
Handles device registration, API key verification, and token issuance.

Registration Flow:
  1. Agent POSTs device_id + platform info
  2. If device exists: rotate API key (old keys remain valid until revoked)
  3. If device new: create Device row + ApiKey row
  4. Return raw_api_key (ONE TIME ONLY) + initial JWT tokens

Token Flow:
  1. Agent POSTs device_id + raw_api_key
  2. sha256(raw_api_key) looked up in api_keys table
  3. Verify key is active, not expired, matches device_id
  4. Update api_keys.last_used_at
  5. Return access_token + refresh_token (both JWT)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.core.exceptions import (
    AuthenticationError,
    DeviceNotFoundError,
    TokenInvalidError,
)
from server.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_key,
    hash_api_key,
    verify_api_key_hash,
)
from server.db.models.device import ApiKey, Device, DeviceCapability
from server.schemas.auth import (
    DeviceRegistrationRequest,
    DeviceRegistrationResponse,
    RefreshResponse,
    TokenResponse,
)

logger = logging.getLogger(__name__)

# JWT lifetime constants (in seconds) — for response field
_ACCESS_EXPIRE_SECONDS = 60 * 60        # 60 minutes
_REFRESH_EXPIRE_SECONDS = 30 * 86400    # 30 days


class AuthService:
    """
    Auth operations. Instantiated per request via dependency injection.
    All methods accept an AsyncSession and perform DB operations.
    """

    async def register_device(
        self,
        req: DeviceRegistrationRequest,
        db: AsyncSession,
    ) -> DeviceRegistrationResponse:
        """
        Register a new device or update an existing one.

        If the device_id is already in the DB:
          - Update platform/hostname/agent info
          - Issue a NEW api_key (key rotation)
          - Previous keys remain valid until explicitly revoked

        If device_id is new:
          - Create Device row
          - Create ApiKey row
          - Return raw_key + JWT tokens
        """
        device_id_str = str(req.device_id)

        # Look up existing device
        result = await db.execute(
            select(Device).where(Device.device_id == req.device_id)
        )
        device = result.scalar_one_or_none()

        if device is None:
            # ── New device ─────────────────────────────────────────────────────
            device = Device(
                device_id=req.device_id,
                agent_id=req.agent_id,
                agent_name=req.agent_name,
                agent_version=req.agent_version,
                platform=req.platform,
                platform_version=req.platform_version,
                machine=req.machine,
                hostname=req.hostname,
                python_version=req.python_version,
                is_online=False,
                first_seen=datetime.now(timezone.utc),
            )
            db.add(device)
            await db.flush()  # get the device.id populated
            registered = True
            logger.info("New device registered: %s (%s)", device_id_str, req.hostname)
        else:
            # ── Existing device — update fields ────────────────────────────────
            device.agent_id = req.agent_id or device.agent_id
            device.agent_name = req.agent_name or device.agent_name
            device.agent_version = req.agent_version or device.agent_version
            device.platform = req.platform or device.platform
            device.platform_version = req.platform_version or device.platform_version
            device.machine = req.machine or device.machine
            device.hostname = req.hostname or device.hostname
            device.python_version = req.python_version or device.python_version
            device.updated_at = datetime.now(timezone.utc)
            registered = False
            logger.info("Existing device re-registered (key rotation): %s", device_id_str)

        # ── Sync capabilities ──────────────────────────────────────────────────
        if req.capabilities:
            # Delete old capabilities and re-insert (simple upsert)
            existing = await db.execute(
                select(DeviceCapability).where(DeviceCapability.device_id == req.device_id)
            )
            for cap in existing.scalars():
                await db.delete(cap)
            for cap_name in req.capabilities:
                db.add(DeviceCapability(device_id=req.device_id, capability=cap_name))

        # ── Generate API key ───────────────────────────────────────────────────
        raw_key, key_hash, key_prefix = generate_api_key()
        api_key = ApiKey(
            device_id=req.device_id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            label=req.key_label or f"key-{key_prefix}",
            is_active=True,
        )
        db.add(api_key)
        await db.flush()

        # ── Issue JWT tokens ───────────────────────────────────────────────────
        agent_id = req.agent_id or str(req.device_id)[:8]
        access_token = create_access_token(str(req.device_id), agent_id)
        refresh_token = create_refresh_token(str(req.device_id), agent_id)

        return DeviceRegistrationResponse(
            device_id=req.device_id,
            agent_id=agent_id,
            api_key=raw_key,            # raw key — SHOWN ONLY ONCE
            key_prefix=key_prefix,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            access_expires_in=_ACCESS_EXPIRE_SECONDS,
            refresh_expires_in=_REFRESH_EXPIRE_SECONDS,
            registered=registered,
        )

    async def get_token(
        self,
        device_id: uuid.UUID,
        raw_api_key: str,
        db: AsyncSession,
    ) -> TokenResponse:
        """
        Exchange API key for JWT access + refresh tokens.

        Verifies:
        1. Device exists and is active
        2. API key exists, is active, not expired, belongs to this device
        """
        # Verify device
        result = await db.execute(
            select(Device).where(Device.device_id == device_id, Device.is_active == True)
        )
        device = result.scalar_one_or_none()
        if device is None:
            raise DeviceNotFoundError(f"Device {device_id} not found or inactive")

        # Look up API key by hash
        key_hash = hash_api_key(raw_api_key)
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.device_id == device_id,
                ApiKey.is_active == True,
            )
        )
        api_key = result.scalar_one_or_none()

        if api_key is None or api_key.is_expired:
            raise AuthenticationError("Invalid or expired API key")

        # Update last_used_at
        api_key.last_used_at = datetime.now(timezone.utc)
        await db.flush()

        agent_id = device.agent_id or str(device_id)[:8]
        access_token = create_access_token(str(device_id), agent_id)
        refresh_token = create_refresh_token(str(device_id), agent_id)

        logger.debug("Token issued for device %s", device_id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            access_expires_in=_ACCESS_EXPIRE_SECONDS,
            refresh_expires_in=_REFRESH_EXPIRE_SECONDS,
            device_id=device_id,
            agent_id=device.agent_id,
        )

    async def refresh_access_token(
        self,
        refresh_token: str,
        db: AsyncSession,
    ) -> RefreshResponse:
        """
        Issue a new access token from a valid refresh token.
        Does NOT issue a new refresh token (prevents infinite refresh chains).
        """
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except Exception as e:
            raise TokenInvalidError(f"Refresh token invalid: {e}")

        device_id = payload["sub"]
        agent_id = payload.get("aid", device_id[:8])

        # Verify device still exists and is active
        result = await db.execute(
            select(Device).where(
                Device.device_id == uuid.UUID(device_id),
                Device.is_active == True,
            )
        )
        if result.scalar_one_or_none() is None:
            raise DeviceNotFoundError(f"Device {device_id} not found or deactivated")

        access_token = create_access_token(device_id, agent_id)
        return RefreshResponse(
            access_token=access_token,
            token_type="bearer",
            access_expires_in=_ACCESS_EXPIRE_SECONDS,
        )
