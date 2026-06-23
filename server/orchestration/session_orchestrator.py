"""
NexLink Server — Session Orchestrator (Phase 5)
==================================================
Manages cross-device orchestration sessions.

An orchestration session groups multiple devices under one workflow:
  "Test session TS-123: Phone-A controls IFP-B, Mac-C monitors"

Session lifecycle:
  PENDING   → devices being registered, not yet started
  ACTIVE    → all devices ready, session running
  PAUSED    → temporarily halted (e.g., device recovery in progress)
  COMPLETED → session finished successfully
  FAILED    → session failed (device lost, error occurred)
  ABORTED   → session manually cancelled

State tracking:
  Each device in a session has its own status:
    pending → ready → active → (disconnected → reconnected →) active → completed

  When a device disconnects:
    1. Device status → "disconnected"
    2. Session status → "paused" (if auto_pause enabled)
    3. ReconnectOrchestrator triggered
    4. On reconnect: device status → "active", session may resume
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.db.models.pairing import (
    OrchestrationSession,
    OrchestrationSessionDevice,
)
from server.services.event_bus import event_bus

logger = logging.getLogger(__name__)


class SessionOrchestrator:
    """Manages orchestration session lifecycle and device participation."""

    async def create_session(
        self,
        name: str,
        device_ids: list[uuid.UUID],
        db: AsyncSession,
        session_type: str = "test",
        description: Optional[str] = None,
        device_roles: Optional[dict[str, str]] = None,
        config: Optional[dict] = None,
        initiated_by: Optional[str] = None,
    ) -> OrchestrationSession:
        """
        Create a new orchestration session with participating devices.

        Args:
            name:         Session name.
            device_ids:   List of device UUIDs to include.
            db:           Database session.
            session_type: Type of session (test, monitor, automation).
            description:  Optional description.
            device_roles: Optional mapping of device_id → role.
            config:       Optional session configuration.
            initiated_by: Who created the session.

        Returns:
            The created OrchestrationSession.
        """
        session = OrchestrationSession(
            session_id=uuid.uuid4(),
            name=name,
            description=description,
            session_type=session_type,
            status="pending",
            config=config,
            initiated_by=initiated_by,
        )
        db.add(session)
        await db.flush()

        # Add devices to session
        roles = device_roles or {}
        for did in device_ids:
            role = roles.get(str(did), "participant")
            device_entry = OrchestrationSessionDevice(
                session_id=session.session_id,
                device_id=did,
                role=role,
                device_status="pending",
            )
            db.add(device_entry)

        await db.flush()

        await event_bus.publish(
            "orchestration.session.created",
            payload={
                "session_id": str(session.session_id),
                "name": name,
                "session_type": session_type,
                "device_ids": [str(d) for d in device_ids],
            },
        )

        logger.info(
            "Orchestration session created: %s (%s) with %d devices",
            name, str(session.session_id)[:8], len(device_ids),
        )

        return session

    async def activate_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> bool:
        """Start a session — mark all devices as active."""
        session = await self._get_session(session_id, db)
        if not session or session.status != "pending":
            return False

        session.activate()

        # Mark all device entries as active
        for device_entry in session.devices:
            device_entry.device_status = "active"

        await db.flush()

        await event_bus.publish(
            "orchestration.session.activated",
            payload={
                "session_id": str(session_id),
                "name": session.name,
            },
        )

        logger.info("Session activated: %s", str(session_id)[:8])
        return True

    async def pause_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        reason: str = "",
    ) -> bool:
        """Pause a session (e.g., device recovery in progress)."""
        session = await self._get_session(session_id, db)
        if not session or session.status != "active":
            return False

        session.pause()
        await db.flush()

        await event_bus.publish(
            "orchestration.session.paused",
            payload={"session_id": str(session_id), "reason": reason},
        )

        logger.info("Session paused: %s reason=%s", str(session_id)[:8], reason)
        return True

    async def complete_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        result: str = "",
    ) -> bool:
        """Mark a session as successfully completed."""
        session = await self._get_session(session_id, db)
        if not session or session.status not in ("active", "paused"):
            return False

        session.complete(result=result)

        for device_entry in session.devices:
            if device_entry.device_status in ("active", "ready"):
                device_entry.device_status = "completed"
                device_entry.left_at = datetime.now(timezone.utc)

        await db.flush()

        await event_bus.publish(
            "orchestration.session.completed",
            payload={"session_id": str(session_id), "result": result},
        )

        logger.info("Session completed: %s", str(session_id)[:8])
        return True

    async def fail_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        error: str = "",
    ) -> bool:
        """Mark a session as failed."""
        session = await self._get_session(session_id, db)
        if not session or session.status in ("completed", "aborted"):
            return False

        session.fail(error=error)
        await db.flush()

        await event_bus.publish(
            "orchestration.session.failed",
            payload={"session_id": str(session_id), "error": error},
        )

        logger.info("Session failed: %s error=%s", str(session_id)[:8], error[:100])
        return True

    async def abort_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        reason: str = "",
    ) -> bool:
        """Abort a session manually."""
        session = await self._get_session(session_id, db)
        if not session or session.status in ("completed", "aborted"):
            return False

        session.abort(reason=reason)
        await db.flush()

        await event_bus.publish(
            "orchestration.session.aborted",
            payload={"session_id": str(session_id), "reason": reason},
        )

        logger.info("Session aborted: %s", str(session_id)[:8])
        return True

    async def update_device_status(
        self,
        session_id: uuid.UUID,
        device_id: uuid.UUID,
        new_status: str,
        db: AsyncSession,
    ) -> bool:
        """Update a device's status within a session."""
        result = await db.execute(
            select(OrchestrationSessionDevice).where(
                OrchestrationSessionDevice.session_id == session_id,
                OrchestrationSessionDevice.device_id == device_id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            return False

        old_status = entry.device_status
        entry.device_status = new_status

        if new_status in ("completed", "failed"):
            entry.left_at = datetime.now(timezone.utc)

        await db.flush()

        await event_bus.publish(
            "orchestration.device_status.changed",
            payload={
                "session_id": str(session_id),
                "device_id": str(device_id),
                "old_status": old_status,
                "new_status": new_status,
            },
        )

        return True

    async def get_active_sessions_for_device(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
    ) -> list[OrchestrationSession]:
        """Get all active/paused sessions this device participates in."""
        result = await db.execute(
            select(OrchestrationSession)
            .join(OrchestrationSessionDevice)
            .where(
                OrchestrationSessionDevice.device_id == device_id,
                OrchestrationSession.status.in_(["active", "paused", "pending"]),
            )
            .options(selectinload(OrchestrationSession.devices))
        )
        return list(result.scalars().unique().all())

    async def get_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> Optional[OrchestrationSession]:
        return await self._get_session(session_id, db)

    async def list_sessions(
        self,
        db: AsyncSession,
        status: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[OrchestrationSession], int]:
        query = select(OrchestrationSession).options(
            selectinload(OrchestrationSession.devices)
        )
        count_query = select(func.count()).select_from(OrchestrationSession)

        if status:
            query = query.where(OrchestrationSession.status == status)
            count_query = count_query.where(OrchestrationSession.status == status)

        total = (await db.execute(count_query)).scalar() or 0
        query = query.order_by(OrchestrationSession.created_at.desc())
        query = query.offset((page - 1) * per_page).limit(per_page)

        result = await db.execute(query)
        sessions = list(result.scalars().unique().all())

        return sessions, total

    async def _get_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> Optional[OrchestrationSession]:
        result = await db.execute(
            select(OrchestrationSession)
            .where(OrchestrationSession.session_id == session_id)
            .options(selectinload(OrchestrationSession.devices))
        )
        return result.scalar_one_or_none()
