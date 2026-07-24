"""Add parent batches for distributed sequence-frame matting.

Revision ID: 20260724_0004
Revises: 20260723_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260724_0004"
down_revision = "20260723_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_batches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("api_clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_batch_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_key", sa.String(length=128), nullable=False),
        sa.Column("workflow_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("failure_policy", sa.String(length=32), nullable=False),
        sa.Column("output_naming", sa.String(length=32), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("batch_dir", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("archive_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("pending_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("running_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancelled_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "external_batch_id", name="uq_batch_external_id"),
    )
    op.create_index("ix_job_batches_tenant_id", "job_batches", ["tenant_id"])
    op.create_index(
        "ix_job_batches_tenant_status", "job_batches", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_job_batches_status_materialized",
        "job_batches",
        ["status", "last_materialized_at"],
    )
    op.create_index("ix_job_batches_request_id", "job_batches", ["request_id"])
    op.create_index("ix_job_batches_trace_id", "job_batches", ["trace_id"])

    op.create_table(
        "job_batch_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("job_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_relative_path", sa.Text(), nullable=False),
        sa.Column("output_relative_path", sa.Text(), nullable=False),
        sa.Column("input_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("image_format", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_batch_item_ordinal"),
        sa.UniqueConstraint(
            "batch_id", "input_relative_path", name="uq_batch_item_input_path"
        ),
        sa.UniqueConstraint(
            "batch_id", "output_relative_path", name="uq_batch_item_output_path"
        ),
    )
    op.create_index("ix_job_batch_items_batch_id", "job_batch_items", ["batch_id"])
    op.create_index(
        "ix_batch_items_batch_status",
        "job_batch_items",
        ["batch_id", "status", "ordinal"],
    )

    op.add_column(
        "jobs",
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("job_batches.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "batch_item_id",
            sa.String(length=36),
            sa.ForeignKey("job_batch_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_jobs_batch_status", "jobs", ["batch_id", "status"])
    op.create_unique_constraint("uq_jobs_batch_item_id", "jobs", ["batch_item_id"])

    op.create_table(
        "batch_artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("job_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batch_artifacts_batch_id", "batch_artifacts", ["batch_id"])

    op.create_table(
        "batch_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("job_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("previous_status", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "sequence", name="uq_batch_event_sequence"),
    )
    op.create_index("ix_batch_events_batch_id", "batch_events", ["batch_id"])

    op.create_table(
        "batch_idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "client_id",
            sa.String(length=64),
            sa.ForeignKey("api_clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("job_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "key", name="uq_client_batch_idempotency"),
    )
    op.create_index(
        "ix_batch_idempotency_keys_client_id", "batch_idempotency_keys", ["client_id"]
    )
    op.create_index(
        "ix_batch_idempotency_keys_batch_id", "batch_idempotency_keys", ["batch_id"]
    )


def downgrade() -> None:
    op.drop_table("batch_idempotency_keys")
    op.drop_table("batch_events")
    op.drop_table("batch_artifacts")
    op.drop_constraint("uq_jobs_batch_item_id", "jobs", type_="unique")
    op.drop_index("ix_jobs_batch_status", table_name="jobs")
    op.drop_column("jobs", "batch_item_id")
    op.drop_column("jobs", "batch_id")
    op.drop_table("job_batch_items")
    op.drop_table("job_batches")
