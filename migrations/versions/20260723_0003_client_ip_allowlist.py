"""Add source IP allowlist to API clients.

Revision ID: 20260723_0003
Revises: 20260722_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20260723_0003"
down_revision = "20260722_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_clients" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("api_clients")}
    if "allowed_ips" not in columns:
        op.add_column(
            "api_clients",
            sa.Column("allowed_ips", sa.JSON(), nullable=False, server_default="[]"),
        )
    if "last_seen_ip" not in columns:
        op.add_column("api_clients", sa.Column("last_seen_ip", sa.String(64)))
    if "last_seen_at" not in columns:
        op.add_column(
            "api_clients", sa.Column("last_seen_at", sa.DateTime(timezone=True))
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_clients" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("api_clients")}
    for name in ("last_seen_at", "last_seen_ip", "allowed_ips"):
        if name in columns:
            op.drop_column("api_clients", name)
