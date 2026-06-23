"""
NexLink Server — Device Pydantic Schemas
==========================================
Request/response schemas for device registry endpoints.

Identity:
  SERIAL_NUMBER (string like "TMX2405A12345") is the permanent device
  identifier — it never changes across reboots, resets, or network changes.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

_INTERNAL_METADATA_KEYS = frozenset({"api_key_hash", "api_key_prefix"})


class DeviceSchema(BaseModel):
    """Full device representation returned by the API."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    serial_number: str
    device_name: str | None = None
    model: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None
    sdk_version: int | None = None
    agent_version: str | None = None
    is_online: bool = False
    status: str = "unknown"
    screen_status: str = "unknown"
    cpu_percent: float | None = None
    memory_percent: float | None = None
    storage_percent: float | None = None
    health_score: int | None = None
    ip_address: str | None = None
    wifi_ssid: str | None = None
    last_heartbeat_at: datetime | None = None
    first_seen_at: datetime | None = None
    uptime_seconds: int | None = None
    metadata_: dict | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def metadata(self) -> dict:
        raw = self.metadata_ or {}
        return {k: v for k, v in raw.items() if k not in _INTERNAL_METADATA_KEYS}


class DeviceListResponse(BaseModel):
    """Paginated device list."""

    devices: list[DeviceSchema]
    total: int


class DeviceUpdateRequest(BaseModel):
    """Fields that can be updated for an existing device."""

    device_name: str | None = None
    metadata: dict | None = None
