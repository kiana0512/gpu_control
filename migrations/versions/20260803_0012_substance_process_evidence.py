"""Persist fail-closed Substance host-process and Agent generation evidence."""

import sqlalchemy as sa
from alembic import op

revision = "20260803_0012"
down_revision = "20260730_0011"
branch_labels = None
depends_on = None


def _add_missing_columns(table: str, additions: tuple[sa.Column, ...]) -> None:
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)
    }
    for column in additions:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_missing_columns(
        "asset_workers",
        (
            sa.Column("agent_instance_id", sa.String(length=64), nullable=True),
            sa.Column(
                "agent_started_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column(
                "substance_process_probe_status",
                sa.String(length=24),
                nullable=False,
                server_default="NOT_RUN",
            ),
            sa.Column(
                "substance_process_probe_checked_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column("substance_active_processes", sa.Integer(), nullable=True),
        ),
    )
    _add_missing_columns(
        "asset_jobs",
        (sa.Column("worker_instance_id", sa.String(length=64), nullable=True),),
    )


def downgrade() -> None:
    removals = {
        "asset_jobs": ("worker_instance_id",),
        "asset_workers": (
            "substance_active_processes",
            "substance_process_probe_checked_at",
            "substance_process_probe_status",
            "agent_started_at",
            "agent_instance_id",
        ),
    }
    for table, columns in removals.items():
        existing = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns(table)
        }
        for name in columns:
            if name in existing:
                op.drop_column(table, name)
