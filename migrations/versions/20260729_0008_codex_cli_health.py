"""Add persisted Codex CLI health to asset workers.

Revision ID: 20260729_0008
Revises: 20260728_0007
"""

import sqlalchemy as sa
from alembic import op

revision = "20260729_0008"
down_revision = "20260728_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("asset_workers")
    }
    additions = (
        ("codex_cli_version", sa.String(64), None),
        ("codex_auth_status", sa.String(24), "UNKNOWN"),
        ("codex_probe_status", sa.String(24), "NOT_RUN"),
        ("codex_probe_latency_ms", sa.Integer(), None),
        ("codex_last_checked_at", sa.DateTime(timezone=True), None),
        ("codex_last_success_at", sa.DateTime(timezone=True), None),
        ("codex_error_code", sa.String(64), None),
    )
    for name, column_type, default in additions:
        if name in columns:
            continue
        kwargs: dict[str, object] = {"nullable": True}
        if default is not None:
            kwargs.update(nullable=False, server_default=default)
        op.add_column("asset_workers", sa.Column(name, column_type, **kwargs))


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("asset_workers")
    }
    for name in (
        "codex_error_code",
        "codex_last_success_at",
        "codex_last_checked_at",
        "codex_probe_latency_ms",
        "codex_probe_status",
        "codex_auth_status",
        "codex_cli_version",
    ):
        if name in columns:
            op.drop_column("asset_workers", name)
