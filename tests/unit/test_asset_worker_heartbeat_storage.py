from pathlib import Path

from packages.gpu_control_core.models import AssetWorker

MIGRATION = Path("migrations/versions/20260810_0013_asset_worker_heartbeat_storage.py")


def test_asset_worker_heartbeat_is_not_indexed() -> None:
    assert not AssetWorker.__table__.c.last_heartbeat_at.index
    assert "ix_asset_workers_last_heartbeat_at" not in {
        index.name for index in AssetWorker.__table__.indexes
    }


def test_asset_worker_heartbeat_migration_preserves_hot_update_capacity() -> None:
    source = MIGRATION.read_text("utf-8")
    assert 'down_revision = "20260803_0012"' in source
    assert "op.drop_index(HEARTBEAT_INDEX" in source
    assert "fillfactor = 70" in source
    assert "autovacuum_vacuum_threshold = 1000" in source
