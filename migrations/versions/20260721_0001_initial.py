"""Create the complete GPU Control schema.

Revision ID: 20260721_0001
Revises: None
"""

from alembic import op

from packages.gpu_control_core.models import Base

revision = "20260721_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    nodes = Base.metadata.tables["nodes"]
    op.bulk_insert(
        nodes,
        [
            {
                "id": "worker-3090-a",
                "display_name": "3090-A",
                "base_url": "http://192.168.10.11:8188",
                "agent_url": "http://192.168.10.11:9201",
                "pool": "PRIMARY",
                "mode": "ACTIVE",
                "health": "OFFLINE",
                "labels": {"gpu": "rtx3090"},
                "max_concurrency": 1,
                "current_jobs": 0,
                "manual_reserved": False,
                "external_busy": False,
                "foreign_queue_detected": False,
                "gpu_util_percent": 0,
                "free_vram_mb": 0,
                "total_vram_mb": 24576,
                "created_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            },
            {
                "id": "worker-3090-b",
                "display_name": "3090-B",
                "base_url": "http://192.168.10.12:8188",
                "agent_url": "http://192.168.10.12:9201",
                "pool": "PRIMARY",
                "mode": "ACTIVE",
                "health": "OFFLINE",
                "labels": {"gpu": "rtx3090"},
                "max_concurrency": 1,
                "current_jobs": 0,
                "manual_reserved": False,
                "external_busy": False,
                "foreign_queue_detected": False,
                "gpu_util_percent": 0,
                "free_vram_mb": 0,
                "total_vram_mb": 24576,
                "created_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            },
            {
                "id": "control-4090",
                "display_name": "4090 控制中心",
                "base_url": "http://comfyui-4090:8188",
                "agent_url": "http://127.0.0.1:9201",
                "pool": "OVERFLOW",
                "mode": "RESERVED",
                "health": "OFFLINE",
                "labels": {"gpu": "rtx4090"},
                "max_concurrency": 1,
                "current_jobs": 0,
                "manual_reserved": True,
                "external_busy": False,
                "foreign_queue_detected": False,
                "gpu_util_percent": 0,
                "free_vram_mb": 0,
                "total_vram_mb": 24576,
                "created_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            },
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
