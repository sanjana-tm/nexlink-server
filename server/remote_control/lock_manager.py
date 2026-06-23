"""
NexLink Server — Control Lock Manager (Phase 12)
===================================================
Exclusive control locking for remote device access.

Why locking matters:
  Two engineers controlling the same IFP simultaneously = chaos.
  Engineer A taps "Settings", Engineer B taps "Back" — neither
  accomplishes anything. The lock ensures one controller at a time.

Lock model:
  - Only ONE user can have control of a device at a time
  - Other users can VIEW (stream) but not INJECT inputs
  - Lock has a TTL (default 5 min) — auto-releases on inactivity
  - Lock holder can explicitly release
  - Admin can force-release any lock

Lock states:
  UNLOCKED  → anyone can acquire
  LOCKED    → one user has control, others view-only
  EXPIRED   → TTL exceeded, auto-released (next request acquires)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

from server.services.event_bus import event_bus

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TTL = 300.0  # 5 minutes


@dataclass
class ControlLock:
    """Represents an active control lock on a device."""
    device_id: str
    holder_id: str               # User/session who holds the lock
    holder_name: str             # Display name for UI
    acquired_at: float
    expires_at: float
    last_activity: float         # Refreshed on each input event

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def remaining_seconds(self) -> float:
        return max(0, self.expires_at - time.time())

    def refresh(self) -> None:
        """Extend the lock TTL on activity."""
        self.last_activity = time.time()
        self.expires_at = self.last_activity + DEFAULT_LOCK_TTL

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "holder_id": self.holder_id,
            "holder_name": self.holder_name,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "remaining_seconds": self.remaining_seconds,
            "is_expired": self.is_expired,
        }


class LockManager:
    """
    Manages exclusive control locks on devices.

    Thread safety: all operations are synchronous and run on the
    asyncio event loop — no lock contention issues.
    """

    def __init__(self, default_ttl: float = DEFAULT_LOCK_TTL) -> None:
        self._locks: Dict[str, ControlLock] = {}
        self._ttl = default_ttl

    async def acquire(
        self,
        device_id: str,
        holder_id: str,
        holder_name: str = "",
        force: bool = False,
    ) -> tuple[bool, Optional[ControlLock]]:
        """
        Attempt to acquire control of a device.

        Args:
            device_id:    Target device.
            holder_id:    Who wants control.
            holder_name:  Display name.
            force:        Force-acquire (admin override, steals lock).

        Returns:
            (success, lock) — the lock object if acquired, or the existing
            lock if denied (so the caller can show who has it).
        """
        existing = self._locks.get(device_id)

        # Check if already locked by someone else
        if existing and not existing.is_expired:
            if existing.holder_id == holder_id:
                # Same holder — refresh TTL
                existing.refresh()
                return True, existing

            if not force:
                logger.info(
                    "Lock denied: device %s held by %s (requested by %s)",
                    device_id[:8], existing.holder_id[:8], holder_id[:8],
                )
                return False, existing

            # Force acquire — steal the lock
            logger.warning(
                "Lock force-acquired: device %s stolen from %s by %s",
                device_id[:8], existing.holder_id[:8], holder_id[:8],
            )
            await self._notify_lock_stolen(existing, holder_id)

        # Create new lock
        now = time.time()
        lock = ControlLock(
            device_id=device_id,
            holder_id=holder_id,
            holder_name=holder_name or holder_id[:8],
            acquired_at=now,
            expires_at=now + self._ttl,
            last_activity=now,
        )
        self._locks[device_id] = lock

        await event_bus.publish(
            "remote_control.lock.acquired",
            payload=lock.to_dict(),
            source_device_id=device_id,
        )

        logger.info(
            "Lock acquired: device %s by %s (TTL=%.0fs)",
            device_id[:8], holder_id[:8], self._ttl,
        )
        return True, lock

    async def release(self, device_id: str, holder_id: str) -> bool:
        """
        Release a control lock.

        Only the lock holder (or an admin via force_release) can release.
        """
        lock = self._locks.get(device_id)
        if not lock:
            return True  # Already unlocked

        if lock.holder_id != holder_id:
            logger.warning(
                "Lock release denied: device %s held by %s, not %s",
                device_id[:8], lock.holder_id[:8], holder_id[:8],
            )
            return False

        del self._locks[device_id]

        await event_bus.publish(
            "remote_control.lock.released",
            payload={"device_id": device_id, "holder_id": holder_id},
            source_device_id=device_id,
        )

        logger.info("Lock released: device %s", device_id[:8])
        return True

    async def force_release(self, device_id: str) -> bool:
        """Admin force-release — bypasses holder check."""
        lock = self._locks.pop(device_id, None)
        if lock:
            await event_bus.publish(
                "remote_control.lock.force_released",
                payload={"device_id": device_id, "holder_id": lock.holder_id},
                source_device_id=device_id,
            )
            logger.warning("Lock force-released: device %s", device_id[:8])
        return True

    def check(self, device_id: str, holder_id: str) -> bool:
        """Check if holder_id has the lock on device_id."""
        lock = self._locks.get(device_id)
        if not lock:
            return True  # No lock = anyone can control
        if lock.is_expired:
            del self._locks[device_id]
            return True  # Expired = unlocked
        return lock.holder_id == holder_id

    def get_lock(self, device_id: str) -> Optional[ControlLock]:
        """Get the current lock on a device, or None."""
        lock = self._locks.get(device_id)
        if lock and lock.is_expired:
            del self._locks[device_id]
            return None
        return lock

    def refresh_on_activity(self, device_id: str) -> None:
        """Extend lock TTL when the holder sends an input event."""
        lock = self._locks.get(device_id)
        if lock and not lock.is_expired:
            lock.refresh()

    def cleanup_expired(self) -> int:
        """Remove all expired locks. Returns count removed."""
        expired = [did for did, lock in self._locks.items() if lock.is_expired]
        for did in expired:
            del self._locks[did]
        return len(expired)

    @property
    def active_locks(self) -> list[dict]:
        self.cleanup_expired()
        return [lock.to_dict() for lock in self._locks.values()]

    async def _notify_lock_stolen(self, old_lock: ControlLock, new_holder: str) -> None:
        """Notify the old holder that their lock was stolen."""
        await event_bus.publish(
            "remote_control.lock.stolen",
            payload={
                "device_id": old_lock.device_id,
                "old_holder": old_lock.holder_id,
                "new_holder": new_holder,
            },
            source_device_id=old_lock.device_id,
        )
