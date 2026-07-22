"""Add durable alert delivery state.

Revision ID: 20260722_0002
Revises: 20260721_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260722_0002"
down_revision = "20260721_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "alerts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("alerts")}
    additions = {
        "last_notified_status": sa.Column("last_notified_status", sa.String(24)),
        "notification_attempts": sa.Column(
            "notification_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        "next_notification_at": sa.Column("next_notification_at", sa.DateTime(timezone=True)),
        "notification_error": sa.Column("notification_error", sa.Text()),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("alerts", column)
    indexes = {index["name"] for index in inspector.get_indexes("alerts")}
    if "ix_alerts_next_notification_at" not in indexes:
        op.create_index(
            "ix_alerts_next_notification_at", "alerts", ["next_notification_at"], unique=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "alerts" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("alerts")}
    if "ix_alerts_next_notification_at" in indexes:
        op.drop_index("ix_alerts_next_notification_at", table_name="alerts")
    columns = {column["name"] for column in inspector.get_columns("alerts")}
    for name in (
        "notification_error",
        "next_notification_at",
        "notification_attempts",
        "last_notified_status",
    ):
        if name in columns:
            op.drop_column("alerts", name)
