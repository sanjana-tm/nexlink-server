"""
NexLink Server — Automation Execution Engine (Phase 8)
========================================================
Distributed remote test automation across all device platforms.

Public API:
    from server.automation.engine import automation_engine

Components:
    ExecutionQueue    — priority FIFO queue with DB persistence
    DeviceAllocator   — capability-based device locking
    ExecutionRunner   — command dispatch + result collection
    RecoveryEngine    — retry with device reallocation
    AutomationEngine  — top-level service wiring everything
"""
from __future__ import annotations
