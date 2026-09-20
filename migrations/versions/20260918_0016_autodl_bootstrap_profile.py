"""Persist an allowlisted AutoDL bootstrap profile.

Revision ID: 20260918_0016
Revises: 20260918_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20260918_0016"
down_revision = "20260918_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "provider_instances" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("provider_instances")}
    if "bootstrap_profile" not in columns:
        op.add_column(
            "provider_instances",
            sa.Column("bootstrap_profile", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "provider_instances" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("provider_instances")}
    if "bootstrap_profile" in columns:
        op.drop_column("provider_instances", "bootstrap_profile")
