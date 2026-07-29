"""Reconcile legacy manual-review asset jobs into terminal QA outcomes.

Revision ID: 20260729_0009
Revises: 20260729_0008
"""

from alembic import op

revision = "20260729_0009"
down_revision = "20260729_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Before v3 automatic delivery, every completed retopology job entered
    # WAITING_REVIEW.  A null error_code meant its strict audit had passed;
    # RETOPOLOGY_AUDIT_FAILED meant it had failed.  Convert both cases to the
    # same immutable terminal truth now used by the API and Web UI.
    op.execute(
        """
        UPDATE asset_jobs
        SET status = CASE WHEN error_code IS NULL THEN 'SUCCEEDED' ELSE 'FAILED' END,
            stage = CASE WHEN error_code IS NULL THEN 'SUCCEEDED' ELSE 'FAILED' END,
            progress = 100,
            stage_message = CASE
                WHEN error_code IS NULL
                    THEN '历史任务严格 QA 已通过，已自动发布交付'
                ELSE '历史任务硬性 QA 未通过；诊断制品已保留，不可交付'
            END,
            estimated_remaining_seconds = 0,
            finished_at = COALESCE(finished_at, last_progress_at, updated_at),
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'WAITING_REVIEW'
        """
    )


def downgrade() -> None:
    # Terminal user-visible outcomes cannot be safely converted back into a
    # manual-review state because callers may already have consumed results.
    pass
