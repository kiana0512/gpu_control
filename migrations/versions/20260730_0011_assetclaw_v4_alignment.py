"""Persist AssetClaw V4.1 identity, timing, cancellation, and attempt evidence."""

import sqlalchemy as sa
from alembic import op

revision = "20260730_0011"
down_revision = "20260729_0010"
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
        "job_batches",
        (
            sa.Column("pipeline_commit", sa.String(length=64), nullable=True),
            sa.Column("pipeline_sha256", sa.String(length=64), nullable=True),
            sa.Column("output_node", sa.String(length=128), nullable=True),
            sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("execution_finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("assembling_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("artifact_ready_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    _add_missing_columns(
        "jobs",
        (
            sa.Column("submission_client_id", sa.String(length=128), nullable=True),
            sa.Column("submission_intent_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    _add_missing_columns(
        "job_attempts",
        (
            sa.Column("prompt_client_id", sa.String(length=128), nullable=True),
            sa.Column(
                "upload_attempts", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "prompt_attempts", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("gpu_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("gpu_finished_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )

    inspector = sa.inspect(op.get_bind())
    if "batch_cancel_operations" not in set(inspector.get_table_names()):
        op.create_table(
            "batch_cancel_operations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "batch_id",
                sa.String(length=36),
                sa.ForeignKey("job_batches.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "tenant_id",
                sa.String(length=64),
                sa.ForeignKey("api_clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("idempotency_key", sa.String(length=192), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("requested_by", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("source_ip", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column(
                "status", sa.String(length=24), nullable=False, server_default="REQUESTED"
            ),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_items", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cancelled_items", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("not_started_items", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("batch_id", name="uq_batch_cancel_operation_batch"),
            sa.UniqueConstraint(
                "tenant_id",
                "idempotency_key",
                name="uq_tenant_batch_cancel_idempotency",
            ),
        )
        op.create_index(
            "ix_batch_cancel_operations_batch_id", "batch_cancel_operations", ["batch_id"]
        )
        op.create_index(
            "ix_batch_cancel_operations_tenant_id", "batch_cancel_operations", ["tenant_id"]
        )
        op.create_index(
            "ix_batch_cancel_operations_request_id", "batch_cancel_operations", ["request_id"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "batch_cancel_operations" in set(inspector.get_table_names()):
        op.drop_table("batch_cancel_operations")

    removals = {
        "job_attempts": (
            "gpu_finished_at",
            "gpu_started_at",
            "prompt_attempts",
            "upload_attempts",
            "prompt_client_id",
        ),
        "jobs": ("submission_intent_at", "submission_client_id"),
        "job_batches": (
            "artifact_ready_at",
            "assembling_at",
            "execution_finished_at",
            "last_progress_at",
            "queued_at",
            "validated_at",
            "output_node",
            "pipeline_sha256",
            "pipeline_commit",
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
