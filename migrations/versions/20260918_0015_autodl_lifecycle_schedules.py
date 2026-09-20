"""Fence AutoDL mutations and persist one-shot lifecycle schedules.

Revision ID: 20260918_0015
Revises: 20260918_0014
"""

import sqlalchemy as sa
from alembic import op

revision = "20260918_0015"
down_revision = "20260918_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "provider_instances" in tables:
        columns = {column["name"] for column in inspector.get_columns("provider_instances")}
        additions = {
            "scheduled_start_at": sa.DateTime(timezone=True),
            "scheduled_stop_at": sa.DateTime(timezone=True),
            "schedule_updated_by": sa.String(length=64),
            "schedule_reason": sa.Text(),
        }
        for name, column_type in additions.items():
            if name not in columns:
                op.add_column(
                    "provider_instances",
                    sa.Column(name, column_type, nullable=True),
                )
        indexes = {index["name"] for index in inspector.get_indexes("provider_instances")}
        if "ix_provider_instances_schedule" not in indexes:
            op.create_index(
                "ix_provider_instances_schedule",
                "provider_instances",
                ["provider", "scheduled_start_at", "scheduled_stop_at"],
            )
        if "uq_provider_instances_node_id" not in indexes:
            op.create_index(
                "uq_provider_instances_node_id",
                "provider_instances",
                ["node_id"],
                unique=True,
                postgresql_where=sa.text("node_id IS NOT NULL"),
            )

    if "provider_operations" in tables:
        columns = {column["name"] for column in inspector.get_columns("provider_operations")}
        for name in (
            "dispatch_attempted_at",
            "dispatch_ack_at",
            "confirmation_deadline_at",
        ):
            if name not in columns:
                op.add_column(
                    "provider_operations",
                    sa.Column(name, sa.DateTime(timezone=True), nullable=True),
                )
        indexes = {index["name"] for index in inspector.get_indexes("provider_operations")}
        if "uq_provider_operations_active_instance" not in indexes:
            op.create_index(
                "uq_provider_operations_active_instance",
                "provider_operations",
                ["provider", "product", "instance_id"],
                unique=True,
                postgresql_where=sa.text(
                    "status IN ('PENDING', 'IN_FLIGHT', 'WAITING', 'UNCERTAIN')"
                ),
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "provider_operations" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("provider_operations")}
        if "uq_provider_operations_active_instance" in indexes:
            op.drop_index(
                "uq_provider_operations_active_instance", table_name="provider_operations"
            )
        columns = {column["name"] for column in inspector.get_columns("provider_operations")}
        for name in (
            "confirmation_deadline_at",
            "dispatch_ack_at",
            "dispatch_attempted_at",
        ):
            if name in columns:
                op.drop_column("provider_operations", name)

    if "provider_instances" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("provider_instances")}
        if "uq_provider_instances_node_id" in indexes:
            op.drop_index("uq_provider_instances_node_id", table_name="provider_instances")
        if "ix_provider_instances_schedule" in indexes:
            op.drop_index("ix_provider_instances_schedule", table_name="provider_instances")
        columns = {column["name"] for column in inspector.get_columns("provider_instances")}
        for name in (
            "schedule_reason",
            "schedule_updated_by",
            "scheduled_stop_at",
            "scheduled_start_at",
        ):
            if name in columns:
                op.drop_column("provider_instances", name)
