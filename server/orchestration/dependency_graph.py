"""
NexLink Server — Dependency Graph (Phase 5)
==============================================
In-memory graph tracking device relationships and enabling
impact analysis when a device goes offline.

Structure:
  Directed graph where each edge is a DevicePair.
  Node = device_id (UUID string)
  Edge = (source, target, relationship_type)

Impact analysis:
  When device X goes offline, the graph answers:
    1. Which devices depend on X? (forward edges FROM X)
    2. Which devices does X depend on? (reverse edges TO X)
    3. Which orchestration sessions are affected?

The graph is loaded from the database at startup and kept in
sync via EventBus events (pair.created, pair.dissolved).

Why in-memory?
  Device pair counts are small (hundreds, not millions).
  Graph traversal needs to be fast (sub-millisecond) for
  real-time disconnect response. DB queries would add latency.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


class DependencyGraph:
    """
    In-memory directed graph of device relationships.

    Thread safety: safe for concurrent reads from the asyncio event loop.
    Writes should be serialized (EventBus subscriber is single-threaded).
    """

    def __init__(self) -> None:
        # device_id → list of (target_device_id, relationship_type, pair_id)
        self._forward: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        # device_id → list of (source_device_id, relationship_type, pair_id)
        self._reverse: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        # pair_id → (source, target, type)
        self._pairs: dict[str, tuple[str, str, str]] = {}

    # ── Mutations ─────────────────────────────────────────────────────────────

    def add_edge(
        self,
        pair_id: str,
        source_device_id: str,
        target_device_id: str,
        relationship_type: str,
    ) -> None:
        """Add a relationship edge to the graph."""
        if pair_id in self._pairs:
            return  # Already exists

        edge = (target_device_id, relationship_type, pair_id)
        self._forward[source_device_id].append(edge)

        reverse_edge = (source_device_id, relationship_type, pair_id)
        self._reverse[target_device_id].append(reverse_edge)

        self._pairs[pair_id] = (source_device_id, target_device_id, relationship_type)

        logger.debug(
            "Graph edge added: %s → %s (%s) pair_id=%s",
            source_device_id[:8], target_device_id[:8],
            relationship_type, pair_id[:8],
        )

    def remove_edge(self, pair_id: str) -> None:
        """Remove a relationship edge from the graph."""
        pair = self._pairs.pop(pair_id, None)
        if not pair:
            return

        source, target, rel_type = pair

        self._forward[source] = [
            e for e in self._forward[source] if e[2] != pair_id
        ]
        self._reverse[target] = [
            e for e in self._reverse[target] if e[2] != pair_id
        ]

        if not self._forward[source]:
            del self._forward[source]
        if not self._reverse[target]:
            del self._reverse[target]

        logger.debug(
            "Graph edge removed: %s → %s (%s)",
            source[:8], target[:8], rel_type,
        )

    def clear(self) -> None:
        """Remove all edges."""
        self._forward.clear()
        self._reverse.clear()
        self._pairs.clear()

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_dependents(self, device_id: str) -> list[tuple[str, str]]:
        """
        Get all devices that depend on this device (forward edges).

        Returns:
            List of (target_device_id, relationship_type).
        """
        return [(e[0], e[1]) for e in self._forward.get(device_id, [])]

    def get_dependencies(self, device_id: str) -> list[tuple[str, str]]:
        """
        Get all devices this device depends on (reverse edges).

        Returns:
            List of (source_device_id, relationship_type).
        """
        return [(e[0], e[1]) for e in self._reverse.get(device_id, [])]

    def get_all_related(self, device_id: str) -> set[str]:
        """
        Get ALL devices connected to this device (both directions, transitive).

        BFS traversal — finds the entire connected component.
        Used for impact analysis: "if this device dies, what's affected?"
        """
        visited: set[str] = set()
        queue = [device_id]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            # Forward edges
            for target, _, _ in self._forward.get(current, []):
                if target not in visited:
                    queue.append(target)

            # Reverse edges
            for source, _, _ in self._reverse.get(current, []):
                if source not in visited:
                    queue.append(source)

        visited.discard(device_id)  # Don't include the device itself
        return visited

    def impact_of(self, device_id: str) -> dict:
        """
        Analyze the impact of a device going offline.

        Returns:
            {
                "device_id": "...",
                "direct_dependents": [{"device_id": "...", "relationship": "..."}],
                "transitive_affected": ["device_id_1", "device_id_2"],
                "affected_pairs": ["pair_id_1", "pair_id_2"],
                "severity": "high" | "medium" | "low" | "none"
            }
        """
        direct = self.get_dependents(device_id)
        all_related = self.get_all_related(device_id)

        affected_pairs = [
            pair_id for pair_id, (src, tgt, _) in self._pairs.items()
            if src == device_id or tgt == device_id
        ]

        if len(direct) >= 3:
            severity = "critical"
        elif len(direct) >= 1:
            severity = "high"
        elif all_related:
            severity = "medium"
        else:
            severity = "none"

        return {
            "device_id": device_id,
            "direct_dependents": [
                {"device_id": d, "relationship": r} for d, r in direct
            ],
            "transitive_affected": list(all_related),
            "affected_pairs": affected_pairs,
            "severity": severity,
        }

    def get_pairs_for_device(self, device_id: str) -> list[str]:
        """Get all pair_ids involving this device."""
        return [
            pair_id for pair_id, (src, tgt, _) in self._pairs.items()
            if src == device_id or tgt == device_id
        ]

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def edge_count(self) -> int:
        return len(self._pairs)

    @property
    def node_count(self) -> int:
        nodes = set()
        for src, tgt, _ in self._pairs.values():
            nodes.add(src)
            nodes.add(tgt)
        return len(nodes)

    def to_dict(self) -> dict:
        return {
            "edge_count": self.edge_count,
            "node_count": self.node_count,
            "pairs": [
                {"pair_id": pid, "source": src, "target": tgt, "type": rt}
                for pid, (src, tgt, rt) in self._pairs.items()
            ],
        }
