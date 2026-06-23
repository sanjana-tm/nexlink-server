"""
NexLink Server — Orchestration Layer (Phase 5)
=================================================
Cross-device orchestration, pairing, session management,
dependency tracking, reconnect coordination, and notifications.

Public API:
    from server.orchestration import orchestration_engine

    # Started/stopped in lifecycle.py
    await orchestration_engine.start()
    await orchestration_engine.stop()
"""
from __future__ import annotations
