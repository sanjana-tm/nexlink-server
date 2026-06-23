"""Add orchestration tables (Phase 5)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04

Tables added:
  - device_pairs: directional device relationships
  - orchestration_sessions: cross-device workflow sessions
  - orchestration_session_devices: session-device junction
  - notifications: persisted notification records
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── device_pairs ──────────────────────────────────────────────────────────
    op.create_table(
        "device_pairs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pair_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=False, server_default="controls"),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pair_id"),
        sa.UniqueConstraint("source_device_id", "target_device_id", "relationship_type", name="uq_device_pair_src_tgt_type"),
        sa.ForeignKeyConstraint(["source_device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_device_id"], ["devices.device_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_device_pairs_source", "device_pairs", ["source_device_id"])
    op.create_index("ix_device_pairs_target", "device_pairs", ["target_device_id"])
    op.create_index("ix_device_pairs_active", "device_pairs", ["is_active"])
    op.create_index("ix_device_pairs_created_at", "device_pairs", ["created_at"])

    # ── orchestration_sessions ────────────────────────────────────────────────
    op.create_table(
        "orchestration_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("session_type", sa.String(50), nullable=False, server_default="test"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("initiated_by", sa.String(255), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("ix_orch_sessions_status", "orchestration_sessions", ["status"])
    op.create_index("ix_orch_sessions_type", "orchestration_sessions", ["session_type"])
    op.create_index("ix_orch_sessions_started", "orchestration_sessions", ["started_at"])
    op.create_index("ix_orch_sessions_created_at", "orchestration_sessions", ["created_at"])

    # ── orchestration_session_devices ─────────────────────────────────────────
    op.create_table(
        "orchestration_session_devices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="participant"),
        sa.Column("device_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "device_id", name="uq_orch_session_device"),
        sa.ForeignKeyConstraint(["session_id"], ["orchestration_sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_orch_session_devices_device", "orchestration_session_devices", ["device_id"])

    # ── notifications ─────────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="info"),
        sa.Column("category", sa.String(50), nullable=False, server_default="system"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(30), nullable=False, server_default="websocket"),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id"),
        sa.ForeignKeyConstraint(["source_device_id"], ["devices.device_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_notifications_level", "notifications", ["level"])
    op.create_index("ix_notifications_category", "notifications", ["category"])
    op.create_index("ix_notifications_delivered", "notifications", ["delivered"])
    op.create_index("ix_notifications_source_device", "notifications", ["source_device_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("orchestration_session_devices")
    op.drop_table("orchestration_sessions")
    op.drop_table("device_pairs")
