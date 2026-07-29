"""Add RetopoFlow runtime health to CPU asset workers."""

import sqlalchemy as sa
from alembic import op

revision = "20260729_0010"
down_revision = "20260729_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("asset_workers")
    }
    additions = (
        ("retopoflow_version", sa.String(32), None),
        ("retopoflow_revision", sa.String(64), None),
        ("retopoflow_probe_status", sa.String(24), "NOT_RUN"),
        ("retopoflow_probe_latency_ms", sa.Integer(), None),
        ("retopoflow_last_checked_at", sa.DateTime(timezone=True), None),
        ("retopoflow_error_code", sa.String(64), None),
    )
    for name, type_, default in additions:
        if name in existing_columns:
            continue
        kwargs = {"nullable": True}
        if default is not None:
            kwargs["server_default"] = default
            kwargs["nullable"] = False
        op.add_column("asset_workers", sa.Column(name, type_, **kwargs))


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("asset_workers")
    }
    for name in (
        "retopoflow_error_code",
        "retopoflow_last_checked_at",
        "retopoflow_probe_latency_ms",
        "retopoflow_probe_status",
        "retopoflow_revision",
        "retopoflow_version",
    ):
        if name in columns:
            op.drop_column("asset_workers", name)
