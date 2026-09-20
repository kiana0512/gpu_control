"""Persist AutoDL provider inventory and restart-safe operations.

Revision ID: 20260918_0014
Revises: 20260810_0013
"""

import sqlalchemy as sa
from alembic import op


revision = "20260918_0014"
down_revision = "20260810_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "provider_instances" not in tables:
        op.create_table(
            "provider_instances",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("product", sa.String(length=16), nullable=False),
            sa.Column("instance_id", sa.String(length=128), nullable=False),
            sa.Column("display_name", sa.String(length=256), nullable=False, server_default=""),
            sa.Column(
                "node_id",
                sa.String(length=64),
                sa.ForeignKey("nodes.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "scheduling_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("desired_state", sa.String(length=24), nullable=True),
            sa.Column(
                "observed_state", sa.String(length=24), nullable=False, server_default="unknown"
            ),
            sa.Column(
                "provider_status", sa.String(length=64), nullable=False, server_default="unknown"
            ),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "provider", "product", "instance_id", name="uq_provider_instance_ref"
            ),
        )
        op.create_index(
            "ix_provider_instances_provider_state",
            "provider_instances",
            ["provider", "observed_state"],
        )
    if "provider_operations" not in tables:
        op.create_table(
            "provider_operations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("product", sa.String(length=16), nullable=False),
            sa.Column("instance_id", sa.String(length=128), nullable=False),
            sa.Column("desired_state", sa.String(length=24), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("idempotency_key", sa.String(length=192), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("requested_by", sa.String(length=64), nullable=False),
            sa.Column("source_ip", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("provider_request_id", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("provider_status", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "provider", "idempotency_key", name="uq_provider_operation_key"
            ),
        )
        op.create_index(
            "ix_provider_operations_request_id", "provider_operations", ["request_id"]
        )
        op.create_index(
            "ix_provider_operations_reconcile",
            "provider_operations",
            ["status", "next_attempt_at"],
        )
        op.create_index(
            "ix_provider_operations_instance",
            "provider_operations",
            ["provider", "product", "instance_id", "created_at"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "provider_operations" in tables:
        op.drop_table("provider_operations")
    if "provider_instances" in tables:
        op.drop_table("provider_instances")
