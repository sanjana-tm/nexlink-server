"""
NexLink v2 schema — serial-number-keyed, with api_keys table.

Drops all UUID-based tables from previous migrations and creates 10 new
tables with SERIAL_NUMBER (VARCHAR 50) as the permanent device identity.

Tables created:
  1. devices         — central device registry (PK: serial_number)
  2. api_keys        — per-device API key authentication
  3. heartbeats      — time-series health metrics from agents
  4. screenshots     — screenshot capture metadata
  5. xml_snapshots   — UI hierarchy XML dumps
  6. command_history — shell command audit trail
  7. automation_runs — distributed test execution records
  8. audit_logs      — system-wide action audit trail
  9. device_events   — device lifecycle event log
  10. alerts         — actionable device alerts

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Drop old UUID-based tables (reverse dependency order) ─────────────
    op.drop_table_if_exists("execution_artifacts")
    op.drop_table_if_exists("execution_steps")
    op.drop_table_if_exists("automation_executions")
    op.drop_table_if_exists("notifications")
    op.drop_table_if_exists("orchestration_session_devices")
    op.drop_table_if_exists("orchestration_sessions")
    op.drop_table_if_exists("device_pairs")
    op.drop_table_if_exists("reconnect_attempts")
    op.drop_table_if_exists("events")
    op.drop_table_if_exists("audit_log")
    op.drop_table_if_exists("heartbeats")
    op.drop_table_if_exists("agent_sessions")
    op.drop_table_if_exists("device_capabilities")
    op.drop_table_if_exists("api_keys")
    op.drop_table_if_exists("devices")

    # ── 1. devices ────────────────────────────────────────────────────────
    op.create_table(
        "devices",
        sa.Column(
            "serial_number",
            sa.String(50),
            primary_key=True,
            comment="Hardware serial number — permanent device identity",
        ),
        sa.Column("device_name", sa.String(255), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("manufacturer", sa.String(255), nullable=True),
        sa.Column("android_version", sa.String(20), nullable=True),
        sa.Column("sdk_version", sa.Integer, nullable=True),
        sa.Column("agent_version", sa.String(20), nullable=True),
        sa.Column("is_online", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'unknown'"),
            comment="unknown | healthy | warning | critical | offline",
        ),
        sa.Column(
            "screen_status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'unknown'"),
            comment="unknown | on | off | standby",
        ),
        sa.Column("cpu_percent", sa.Float, nullable=True),
        sa.Column("memory_percent", sa.Float, nullable=True),
        sa.Column("storage_percent", sa.Float, nullable=True),
        sa.Column(
            "health_score",
            sa.Integer,
            nullable=True,
            comment="Composite health score 0-100",
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("wifi_ssid", sa.String(100), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("uptime_seconds", sa.BigInteger, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 2. api_keys ──────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "key_hash",
            sa.String(128),
            nullable=False,
            comment="SHA-256 hash of the API key",
        ),
        sa.Column(
            "key_prefix",
            sa.String(8),
            nullable=False,
            comment="First 8 chars of the key for identification",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 3. heartbeats ─────────────────────────────────────────────────────
    op.create_table(
        "heartbeats",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("cpu_percent", sa.Float, nullable=True),
        sa.Column("memory_percent", sa.Float, nullable=True),
        sa.Column("storage_percent", sa.Float, nullable=True),
        sa.Column("screen_status", sa.String(20), nullable=True),
        sa.Column("uptime_seconds", sa.BigInteger, nullable=True),
        sa.Column("battery_level", sa.Integer, nullable=True),
        sa.Column("wifi_signal_dbm", sa.Integer, nullable=True),
        sa.Column("agent_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("payload", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "ix_heartbeats_serial_time",
        "heartbeats",
        ["serial_number", sa.text("received_at DESC")],
    )

    # ── 4. screenshots ────────────────────────────────────────────────────
    op.create_table(
        "screenshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("format", sa.String(10), nullable=False, server_default=sa.text("'png'")),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_screenshots_serial_time",
        "screenshots",
        ["serial_number", sa.text("captured_at DESC")],
    )

    # ── 5. xml_snapshots ──────────────────────────────────────────────────
    op.create_table(
        "xml_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("xml_content", sa.Text, nullable=False),
        sa.Column("app_package", sa.String(255), nullable=True),
        sa.Column("activity", sa.String(255), nullable=True),
        sa.Column("node_count", sa.Integer, nullable=True),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_xml_snapshots_serial_time",
        "xml_snapshots",
        ["serial_number", sa.text("captured_at DESC")],
    )

    # ── 6. command_history ────────────────────────────────────────────────
    op.create_table(
        "command_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("command", sa.Text, nullable=False),
        sa.Column("output", sa.Text, nullable=True),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("timeout_seconds", sa.Float, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_command_history_serial_time",
        "command_history",
        ["serial_number", sa.text("executed_at DESC")],
    )

    # ── 7. automation_runs ────────────────────────────────────────────────
    op.create_table(
        "automation_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "test_type",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'appium'"),
            comment="appium | pytest | shell",
        ),
        sa.Column("test_config", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'queued'"),
            comment="queued | running | passed | failed | error | timeout | cancelled",
        ),
        sa.Column(
            "priority",
            sa.Integer,
            nullable=False,
            server_default=sa.text("5"),
            comment="1=highest, 10=lowest",
        ),
        sa.Column("total_tests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("passed_tests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("failed_tests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_tests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("result_summary", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
    )
    op.create_index(
        "ix_automation_runs_serial_status_queued",
        "automation_runs",
        ["serial_number", "status", "queued_at"],
    )

    # ── 8. audit_logs ─────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(255), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "ip_address",
            sa.String(45),
            nullable=True,
            comment="Source IP — for audit only",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_audit_logs_resource_time",
        "audit_logs",
        ["resource_type", "resource_id", sa.text("created_at DESC")],
    )

    # ── 9. device_events ──────────────────────────────────────────────────
    op.create_table(
        "device_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "severity",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'info'"),
            comment="info | warning | error | critical",
        ),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_device_events_serial_time",
        "device_events",
        ["serial_number", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_device_events_type_time",
        "device_events",
        ["event_type", sa.text("created_at DESC")],
    )

    # ── 10. alerts ────────────────────────────────────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "serial_number",
            sa.String(50),
            sa.ForeignKey("devices.serial_number", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "severity",
            sa.String(20),
            nullable=False,
            comment="warning | error | critical",
        ),
        sa.Column(
            "category",
            sa.String(50),
            nullable=True,
            comment="health | connectivity | storage | agent",
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("is_resolved", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_alerts_resolved_severity_time",
        "alerts",
        ["is_resolved", "severity", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Drop all v2 tables.

    NOTE: This does NOT recreate the old UUID-based schema. To fully roll
    back, restore from a backup or re-run migrations 0001-0003.
    """
    op.drop_table("alerts")
    op.drop_table("device_events")
    op.drop_table("audit_logs")
    op.drop_table("automation_runs")
    op.drop_table("command_history")
    op.drop_table("xml_snapshots")
    op.drop_table("screenshots")
    op.drop_table("heartbeats")
    op.drop_table("api_keys")
    op.drop_table("devices")
