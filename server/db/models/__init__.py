"""
ORM model registry — serial-number-keyed schema.

Importing this module ensures all models are registered with Base.metadata
so Alembic can see them for autogeneration.

Identity model:
  SERIAL_NUMBER (e.g. "TMX2405A12345") is the permanent, immutable device
  identifier. It replaces the old UUID-based device_id.
"""
from server.db.models.device import ApiKey, Device
from server.db.models.heartbeat import Heartbeat
from server.db.models.screenshot import Screenshot
from server.db.models.xml_snapshot import XmlSnapshot
from server.db.models.command import CommandHistory
from server.db.models.automation import AutomationRun
from server.db.models.audit import AuditLog
from server.db.models.event import DeviceEvent
from server.db.models.alert import Alert

__all__ = [
    "Device", "ApiKey", "Heartbeat", "Screenshot", "XmlSnapshot",
    "CommandHistory", "AutomationRun", "AuditLog", "DeviceEvent", "Alert",
]
