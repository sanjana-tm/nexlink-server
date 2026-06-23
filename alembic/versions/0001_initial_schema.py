"""Initial NexLink schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-05-25

Tables created:
  devices              — registered agent devices
  device_capabilities  — per-device capability declarations
  api_keys             — hashed authentication keys
  agent_sessions       — WebSocket session lifecycle records
  heartbeats           — time-series heartbeat data
  events               — append-only event log
  audit_log            — immutable audit trail
  reconnect_attempts   — reconnect tracking
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enable pgcrypto for gen_random_uuid() ──────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── devices ───────────────────────────────────────────────────────────────
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True, comment="Hardware UUID v5 — permanent identity"),
        sa.Column("agent_id", sa.String(255), nullable=True),
        sa.Column("agent_name", sa.String(255), nullable=True),
        sa.Column("agent_version", sa.String(50), nullable=True),
        sa.Column("platform", sa.String(50), nullable=True, comment="linux | windows | macos | android"),
        sa.Column("platform_version", sa.String(100), nullable=True),
        sa.Column("machine", sa.String(100), nullable=True),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("python_version", sa.String(20), nullable=True),
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true", comment="Soft delete flag"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_devices_device_id", "devices", ["device_id"])
    op.create_index("idx_devices_agent_id", "devices", ["agent_id"])
    op.create_index("idx_devices_is_online", "devices", ["is_online"])
    op.create_index("idx_devices_created_at", "devices", ["created_at"])

    # ── device_capabilities ───────────────────────────────────────────────────
    op.create_table(
        "device_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("device_id", "capability", name="uq_device_capability"),
    )
    op.create_index("idx_device_capabilities_device_id", "device_capabilities", ["device_id"])

    # ── api_keys ──────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_hash", sa.String(256), nullable=False, unique=True, comment="sha256(raw_key)"),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_api_keys_device_id", "api_keys", ["device_id"])
    op.create_index("idx_api_keys_key_hash", "api_keys", ["key_hash"])

    # ── agent_sessions ────────────────────────────────────────────────────────
    op.create_table(
        "agent_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True, comment="Audit only — NOT identity"),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("disconnect_reason", sa.String(255), nullable=True),
        sa.Column("disconnect_code", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
    )
    op.create_index("idx_agent_sessions_session_id", "agent_sessions", ["session_id"])
    op.create_index("idx_agent_sessions_device_id", "agent_sessions", ["device_id"])
    op.create_index("idx_agent_sessions_is_active", "agent_sessions", ["is_active"])

    # ── heartbeats ────────────────────────────────────────────────────────────
    op.create_table(
        "heartbeats",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_sessions.session_id", ondelete="SET NULL"), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("agent_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sequence_number", sa.BigInteger(), nullable=True),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("memory_percent", sa.Float(), nullable=True),
        sa.Column("disk_percent", sa.Float(), nullable=True),
        sa.Column("is_healthy", sa.Boolean(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index("idx_heartbeats_device_time", "heartbeats", ["device_id", "received_at"])

    # ── events ────────────────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("source_device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_sessions.session_id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_events_type_created", "events", ["event_type", "created_at"])
    op.create_index("idx_events_source_device", "events", ["source_device_id", "created_at"])

    # ── audit_log ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("record_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("actor_device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_label", sa.String(255), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("old_values", postgresql.JSONB(), nullable=True),
        sa.Column("new_values", postgresql.JSONB(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_audit_table_record", "audit_log", ["table_name", "record_id"])
    op.create_index("idx_audit_actor", "audit_log", ["actor_device_id", "created_at"])

    # ── reconnect_attempts ────────────────────────────────────────────────────
    op.create_table(
        "reconnect_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_sessions.session_id", ondelete="SET NULL"), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("backoff_seconds", sa.Float(), nullable=True),
    )
    op.create_index("idx_reconnect_device_time", "reconnect_attempts", ["device_id", "attempted_at"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("reconnect_attempts")
    op.drop_table("audit_log")
    op.drop_table("events")
    op.drop_table("heartbeats")
    op.drop_table("agent_sessions")
    op.drop_table("api_keys")
    op.drop_table("device_capabilities")
    op.drop_table("devices")
