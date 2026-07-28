"""Add durable Asset Processing stages, ETA and SSE events.

Revision ID: 20260728_0007
Revises: 20260727_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_0007"
down_revision = "20260727_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "asset_jobs" in tables:
        columns = {column["name"] for column in inspector.get_columns("asset_jobs")}
        if "stage" not in columns:
            op.add_column(
                "asset_jobs",
                sa.Column(
                    "stage", sa.String(32), nullable=False, server_default="QUEUED"
                ),
            )
        if "stage_message" not in columns:
            op.add_column(
                "asset_jobs",
                sa.Column(
                    "stage_message",
                    sa.String(500),
                    nullable=False,
                    server_default="任务已进入资产处理队列",
                ),
            )
        if "estimated_remaining_seconds" not in columns:
            op.add_column(
                "asset_jobs",
                sa.Column("estimated_remaining_seconds", sa.Integer(), nullable=True),
            )
        if "last_progress_at" not in columns:
            op.add_column(
                "asset_jobs",
                sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
            )
    if "asset_job_events" not in tables:
        op.create_table(
            "asset_job_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "job_id",
                sa.String(36),
                sa.ForeignKey("asset_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("stage", sa.String(32), nullable=False),
            sa.Column("progress", sa.Float(), nullable=False),
            sa.Column("message", sa.String(500), nullable=False),
            sa.Column("estimated_remaining_seconds", sa.Integer(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "job_id", "sequence", name="uq_asset_job_event_sequence"
            ),
        )
        op.create_index("ix_asset_job_events_job_id", "asset_job_events", ["job_id"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "asset_job_events" in tables:
        op.drop_table("asset_job_events")
    if "asset_jobs" in tables:
        columns = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns("asset_jobs")
        }
        for name in (
            "last_progress_at",
            "estimated_remaining_seconds",
            "stage_message",
            "stage",
        ):
            if name in columns:
                op.drop_column("asset_jobs", name)
