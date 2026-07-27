"""Add the isolated CPU Asset Processing control plane.

Revision ID: 20260727_0006
Revises: 20260727_0005
"""

import sqlalchemy as sa
from alembic import op

revision = "20260727_0006"
down_revision = "20260727_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "asset_workers" not in tables:
        op.create_table(
            "asset_workers",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("display_name", sa.String(128), nullable=False),
            sa.Column("node_id", sa.String(64), nullable=False),
            sa.Column("hostname", sa.String(128), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="OFFLINE"),
            sa.Column("blender_version", sa.String(32), nullable=False),
            sa.Column("skill_version", sa.String(64), nullable=False),
            sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("current_jobs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cpu_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_asset_workers_node_id", "asset_workers", ["node_id"])
        op.create_index("ix_asset_workers_status", "asset_workers", ["status"])
        op.create_index(
            "ix_asset_workers_last_heartbeat_at", "asset_workers", ["last_heartbeat_at"]
        )
    if "asset_jobs" not in tables:
        op.create_table(
            "asset_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "client_id",
                sa.String(64),
                sa.ForeignKey("api_clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("external_asset_id", sa.String(128), nullable=False),
            sa.Column("job_type", sa.String(32), nullable=False, server_default="UV_UNWRAP"),
            sa.Column("status", sa.String(24), nullable=False, server_default="QUEUED"),
            sa.Column("source_filename", sa.String(256), nullable=False),
            sa.Column("input_path", sa.Text(), nullable=False),
            sa.Column("input_sha256", sa.String(64), nullable=False),
            sa.Column("input_size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("options", sa.JSON(), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("request_id", sa.String(64), nullable=False),
            sa.Column(
                "worker_id",
                sa.String(64),
                sa.ForeignKey("asset_workers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("lease_token_hash", sa.String(64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("client_id", "external_asset_id", name="uq_asset_external_id"),
        )
        op.create_index("ix_asset_jobs_client_id", "asset_jobs", ["client_id"])
        op.create_index("ix_asset_jobs_worker_id", "asset_jobs", ["worker_id"])
        op.create_index("ix_asset_jobs_request_id", "asset_jobs", ["request_id"])
        op.create_index("ix_asset_jobs_lease_expires_at", "asset_jobs", ["lease_expires_at"])
        op.create_index("ix_asset_jobs_queue", "asset_jobs", ["status", "created_at"])
        op.create_index(
            "ix_asset_jobs_client_status", "asset_jobs", ["client_id", "status"]
        )
    if "asset_artifacts" not in tables:
        op.create_table(
            "asset_artifacts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "job_id",
                sa.String(36),
                sa.ForeignKey("asset_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("filename", sa.String(256), nullable=False),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column("content_type", sa.String(128), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("job_id", "kind", name="uq_asset_artifact_kind"),
        )
        op.create_index("ix_asset_artifacts_job_id", "asset_artifacts", ["job_id"])
    if "asset_idempotency_keys" not in tables:
        op.create_table(
            "asset_idempotency_keys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "client_id",
                sa.String(64),
                sa.ForeignKey("api_clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("key", sa.String(128), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column(
                "job_id",
                sa.String(36),
                sa.ForeignKey("asset_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("client_id", "key", name="uq_client_asset_idempotency"),
        )
        op.create_index(
            "ix_asset_idempotency_keys_client_id", "asset_idempotency_keys", ["client_id"]
        )
        op.create_index(
            "ix_asset_idempotency_keys_job_id", "asset_idempotency_keys", ["job_id"]
        )


def downgrade() -> None:
    op.drop_table("asset_idempotency_keys")
    op.drop_table("asset_artifacts")
    op.drop_table("asset_jobs")
    op.drop_table("asset_workers")
