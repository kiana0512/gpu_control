"""Keep high-frequency Asset Worker heartbeats HOT-updatable.

Revision ID: 20260810_0013
Revises: 20260803_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0013"
down_revision = "20260803_0012"
branch_labels = None
depends_on = None

TABLE = "asset_workers"
HEARTBEAT_INDEX = "ix_asset_workers_last_heartbeat_at"


def _index_names() -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(TABLE)
        if index.get("name")
    }


def upgrade() -> None:
    # The fleet has a bounded, single-digit Worker population. Sequentially
    # checking heartbeat freshness is cheaper than maintaining an index for
    # every heartbeat, and lets unchanged status/node indexes use HOT updates.
    if HEARTBEAT_INDEX in _index_names():
        op.drop_index(HEARTBEAT_INDEX, table_name=TABLE)
    op.execute(
        "ALTER TABLE asset_workers SET ("
        "fillfactor = 70, "
        "autovacuum_vacuum_scale_factor = 0, "
        "autovacuum_vacuum_threshold = 1000, "
        "autovacuum_analyze_scale_factor = 0, "
        "autovacuum_analyze_threshold = 1000"
        ")"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE asset_workers RESET ("
        "fillfactor, "
        "autovacuum_vacuum_scale_factor, "
        "autovacuum_vacuum_threshold, "
        "autovacuum_analyze_scale_factor, "
        "autovacuum_analyze_threshold"
        ")"
    )
    if HEARTBEAT_INDEX not in _index_names():
        op.create_index(HEARTBEAT_INDEX, TABLE, ["last_heartbeat_at"])
