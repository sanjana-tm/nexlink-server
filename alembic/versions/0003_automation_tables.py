"""Add automation execution tables (Phase 8)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-08

Tables added:
  - automation_executions: test run records with device allocation
  - execution_steps: individual test cases within an execution
  - execution_artifacts: screenshots, logs, videos from executions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_executions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("test_type", sa.String(50), nullable=False, server_default="appium"),
        sa.Column("test_config", postgresql.JSONB(), nullable=True),
        sa.Column("platform_filter", sa.String(50), nullable=True),
        sa.Column("device_filter", postgresql.JSONB(), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("initiated_by", sa.String(255), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("total_tests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_tests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_tests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_tests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("parent_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_auto_exec_status", "automation_executions", ["status"])
    op.create_index("ix_auto_exec_device", "automation_executions", ["device_id"])
    op.create_index("ix_auto_exec_session", "automation_executions", ["session_id"])
    op.create_index("ix_auto_exec_priority_queued", "automation_executions", ["priority", "queued_at"])
    op.create_index("ix_auto_exec_started", "automation_executions", ["started_at"])
    op.create_index("ix_auto_exec_created_at", "automation_executions", ["created_at"])

    op.create_table(
        "execution_steps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id"),
        sa.ForeignKeyConstraint(["execution_id"], ["automation_executions.execution_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_exec_steps_execution", "execution_steps", ["execution_id"])
    op.create_index("ix_exec_steps_status", "execution_steps", ["status"])

    op.create_table(
        "execution_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_type", sa.String(30), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id"),
        sa.ForeignKeyConstraint(["execution_id"], ["automation_executions.execution_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_exec_artifacts_execution", "execution_artifacts", ["execution_id"])
    op.create_index("ix_exec_artifacts_type", "execution_artifacts", ["artifact_type"])


def downgrade() -> None:
    op.drop_table("execution_artifacts")
    op.drop_table("execution_steps")
    op.drop_table("automation_executions")
