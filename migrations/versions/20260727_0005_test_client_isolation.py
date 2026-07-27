"""Separate production and synthetic load-test API clients.

Revision ID: 20260727_0005
Revises: 20260724_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260727_0005"
down_revision = "20260724_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_clients" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("api_clients")}
    if "client_kind" not in columns:
        op.add_column(
            "api_clients",
            sa.Column(
                "client_kind",
                sa.String(length=16),
                nullable=False,
                server_default="production",
            ),
        )
    indexes = {index["name"] for index in inspector.get_indexes("api_clients")}
    if "ix_api_clients_role_kind" not in indexes:
        op.create_index(
            "ix_api_clients_role_kind",
            "api_clients",
            ["role", "client_kind"],
        )
    # These ten clients were created by the 2026-07-23 three-node smoke test.
    # Preserve their history but move it out of production dashboards.
    op.execute(
        sa.text(
            "UPDATE api_clients SET client_kind = 'test' "
            "WHERE id LIKE 'smoke10-20260723t104513z-%'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_clients" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("api_clients")}
    if "ix_api_clients_role_kind" in indexes:
        op.drop_index("ix_api_clients_role_kind", table_name="api_clients")
    columns = {column["name"] for column in inspector.get_columns("api_clients")}
    if "client_kind" in columns:
        op.drop_column("api_clients", "client_kind")
