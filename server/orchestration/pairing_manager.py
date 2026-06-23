"""
NexLink Server — Pairing Manager (Phase 5)
=============================================
CRUD operations for device pairs, with DependencyGraph integration.

Pairing rules:
  1. A device can be in multiple pairs (Phone-A → IFP-B, Phone-A → IFP-C)
  2. Self-pairing is not allowed (source != target)
  3. Duplicate pairs (same source, target, type) are rejected
  4. Both devices must exist and be active
  5. Dissolving a pair removes the graph edge and notifies affected sessions

Events published:
  "pair.created"    — new pair established
  "pair.dissolved"  — pair removed
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.models.pairing import DevicePair
from server.db.models.device import Device
from server.services.event_bus import event_bus

from .dependency_graph import DependencyGraph

logger = logging.getLogger(__name__)


class PairingManager:
    """Manages device pairing with DB persistence and in-memory graph sync."""

    def __init__(self, graph: DependencyGraph) -> None:
        self._graph = graph

    async def create_pair(
        self,
        source_device_id: uuid.UUID,
        target_device_id: uuid.UUID,
        relationship_type: str,
        db: AsyncSession,
        label: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> DevicePair:
        """
        Create a new device pair.

        Validates both devices exist, then creates the DB record
        and adds the edge to the in-memory graph.

        Raises:
            ValueError: If self-pair, duplicate, or device not found.
        """
        if source_device_id == target_device_id:
            raise ValueError("Cannot pair a device with itself")

        # Verify both devices exist
        for did in (source_device_id, target_device_id):
            result = await db.execute(
                select(Device).where(Device.device_id == did, Device.is_active == True)
            )
            if result.scalar_one_or_none() is None:
                raise ValueError(f"Device not found or inactive: {did}")

        # Check for duplicate
        existing = await db.execute(
            select(DevicePair).where(
                DevicePair.source_device_id == source_device_id,
                DevicePair.target_device_id == target_device_id,
                DevicePair.relationship_type == relationship_type,
                DevicePair.is_active == True,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(
                f"Pair already exists: {source_device_id} → {target_device_id} ({relationship_type})"
            )

        pair = DevicePair(
            pair_id=uuid.uuid4(),
            source_device_id=source_device_id,
            target_device_id=target_device_id,
            relationship_type=relationship_type,
            label=label,
            metadata_=metadata,
        )
        db.add(pair)
        await db.flush()

        # Sync to in-memory graph
        self._graph.add_edge(
            pair_id=str(pair.pair_id),
            source_device_id=str(source_device_id),
            target_device_id=str(target_device_id),
            relationship_type=relationship_type,
        )

        await event_bus.publish(
            "pair.created",
            payload={
                "pair_id": str(pair.pair_id),
                "source_device_id": str(source_device_id),
                "target_device_id": str(target_device_id),
                "relationship_type": relationship_type,
            },
            source_device_id=str(source_device_id),
        )

        logger.info(
            "Pair created: %s → %s (%s) pair_id=%s",
            str(source_device_id)[:8], str(target_device_id)[:8],
            relationship_type, str(pair.pair_id)[:8],
        )

        return pair

    async def dissolve_pair(
        self,
        pair_id: uuid.UUID,
        db: AsyncSession,
    ) -> bool:
        """
        Dissolve (soft-delete) a pair.

        Sets is_active=False and removes the graph edge.
        """
        result = await db.execute(
            select(DevicePair).where(
                DevicePair.pair_id == pair_id,
                DevicePair.is_active == True,
            )
        )
        pair = result.scalar_one_or_none()
        if not pair:
            return False

        pair.is_active = False
        await db.flush()

        self._graph.remove_edge(str(pair_id))

        await event_bus.publish(
            "pair.dissolved",
            payload={
                "pair_id": str(pair_id),
                "source_device_id": str(pair.source_device_id),
                "target_device_id": str(pair.target_device_id),
                "relationship_type": pair.relationship_type,
            },
        )

        logger.info("Pair dissolved: pair_id=%s", str(pair_id)[:8])
        return True

    async def get_pair(
        self, pair_id: uuid.UUID, db: AsyncSession,
    ) -> Optional[DevicePair]:
        result = await db.execute(
            select(DevicePair).where(DevicePair.pair_id == pair_id)
        )
        return result.scalar_one_or_none()

    async def list_pairs(
        self,
        db: AsyncSession,
        device_id: Optional[uuid.UUID] = None,
        active_only: bool = True,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[DevicePair], int]:
        """List pairs with optional filtering."""
        query = select(DevicePair)
        count_query = select(func.count()).select_from(DevicePair)

        if active_only:
            query = query.where(DevicePair.is_active == True)
            count_query = count_query.where(DevicePair.is_active == True)

        if device_id:
            condition = (
                (DevicePair.source_device_id == device_id) |
                (DevicePair.target_device_id == device_id)
            )
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = (await db.execute(count_query)).scalar() or 0
        query = query.order_by(DevicePair.created_at.desc())
        query = query.offset((page - 1) * per_page).limit(per_page)

        result = await db.execute(query)
        pairs = list(result.scalars().all())

        return pairs, total

    async def load_graph_from_db(self, db: AsyncSession) -> int:
        """
        Load all active pairs from DB into the in-memory graph.

        Called once at startup to hydrate the graph.
        Returns the number of edges loaded.
        """
        self._graph.clear()

        result = await db.execute(
            select(DevicePair).where(DevicePair.is_active == True)
        )
        pairs = result.scalars().all()

        for pair in pairs:
            self._graph.add_edge(
                pair_id=str(pair.pair_id),
                source_device_id=str(pair.source_device_id),
                target_device_id=str(pair.target_device_id),
                relationship_type=pair.relationship_type,
            )

        logger.info("Loaded %d active pairs into dependency graph", len(pairs))
        return len(pairs)
