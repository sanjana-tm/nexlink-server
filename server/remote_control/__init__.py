"""
NexLink Server — Remote Control Layer (Phase 12)
===================================================
Enterprise IFP remote control: coordinate mapping, gesture
composition, control locking, and audit logging.

Sits on top of Phase 11's streaming + input injection.
Adds the enterprise layer that raw input injection lacks.
"""
from __future__ import annotations
