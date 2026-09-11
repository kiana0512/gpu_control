from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import Node
from packages.gpu_control_core.settings import Settings
from scripts.bootstrap_nodes import apply_inventory, load_inventory


def node_spec(**changes: object) -> dict[str, object]:
    return {
        "id": "worker-5070ti-01",
        "display_name": "5070 Ti",
        "host": "10.3.34.18",
        "pool": "PRIMARY",
        "hostname": "worker-5070ti-wsl",
        "gpu": "RTX5070Ti",
        "mac": "12:34:56:78:90:ab",
        "gpu_uuid": "GPU-12345678-1234-1234-1234-123456789abc",
        "wsl_runtime": True,
        "dcgm_exporter_enabled": False,
        "vram_class": "16gb",
        **changes,
    }


def inventory_file(tmp_path: Path, *nodes: dict[str, object]) -> Path:
    path = tmp_path / "new-node.yaml"
    path.write_text(yaml.safe_dump({"nodes": list(nodes)}), encoding="utf-8")
    return path


def test_additive_defaults_to_disabled_and_cannot_bootstrap_full_cluster(tmp_path: Path) -> None:
    path = inventory_file(tmp_path, node_spec())
    assert load_inventory(path, add_only=True)[0]["mode"] == "DISABLED"
    with pytest.raises(ValueError):
        load_inventory(path)
    path = inventory_file(tmp_path, node_spec(), node_spec(id="worker-other"))
    with pytest.raises(ValueError, match="exactly one"):
        load_inventory(path, add_only=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "ACTIVE"},
        {"mode": "RESERVED"},
        {"mode": "OVERFLOW"},
        {"host": "127.0.0.1"},
        {"host": "0.0.0.0"},  # noqa: S104 - rejected fixture
        {"host": "other.example"},
        {"mac": ""},
        {"gpu_uuid": "GPU-invalid"},
        {"hostname": ""},
        {"agent_url": "http://10.3.34.14:9201"},
        {"wsl_runtime": "true"},
    ],
)
def test_additive_rejects_unverified_or_schedulable_inventory(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        load_inventory(inventory_file(tmp_path, node_spec(**changes)), add_only=True)


async def test_additive_preserves_active_drained_reserved_nodes_and_jobs(tmp_path: Path) -> None:
    db = Database(
        Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'nodes.sqlite'}")
    )
    try:
        async with db.engine.begin() as connection:
            await connection.run_sync(Node.__table__.create)
        async with db.session() as session:
            for index, (name, mode) in enumerate(
                [
                    ("control-4090", "ACTIVE"),
                    ("worker-3090-a", "RESERVED"),
                    ("worker-3090-b", "DRAINING"),
                    ("worker-4070ti-animation-host-01", "ACTIVE"),
                ]
            ):
                session.add(
                    Node(
                        id=name,
                        display_name=name,
                        base_url=f"http://10.3.34.{40 + index}:8188",
                        mode=mode,
                        current_jobs=1,
                        manual_reserved=mode == "RESERVED",
                        health="ONLINE",
                        labels={
                            "host": f"10.3.34.{40 + index}",
                            "substance_bake_fence_job_ids": ["live-bake"],
                        },
                    )
                )
            await session.commit()
        async with db.session() as session:
            before = {
                n.id: {c.name: getattr(n, c.name) for c in Node.__table__.columns}
                for n in (await session.scalars(select(Node))).all()
            }
            inventory = load_inventory(inventory_file(tmp_path, node_spec()), add_only=True)
            await apply_inventory(session, inventory, add_only=True)
            await session.commit()
        async with db.session() as session:
            nodes = {n.id: n for n in (await session.scalars(select(Node))).all()}
            assert len(nodes) == 5
            for node_id, snapshot in before.items():
                assert {
                    c.name: getattr(nodes[node_id], c.name) for c in Node.__table__.columns
                } == snapshot
            assert nodes["worker-5070ti-01"].mode == "DISABLED"
            assert nodes["worker-5070ti-01"].health == "OFFLINE"
            assert nodes["worker-5070ti-01"].current_jobs == 0
            assert nodes["worker-5070ti-01"].labels["vram_class"] == "16gb"
            with pytest.raises(ValueError, match="already exists"):
                await apply_inventory(session, inventory, add_only=True)
    finally:
        await db.close()


@pytest.mark.parametrize("duplicate", ["host", "mac", "gpu_uuid"])
async def test_additive_rejects_existing_physical_identity(tmp_path: Path, duplicate: str) -> None:
    db = Database(
        Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'identity.sqlite'}")
    )
    try:
        async with db.engine.begin() as connection:
            await connection.run_sync(Node.__table__.create)
        spec = node_spec()
        async with db.session() as session:
            session.add(
                Node(
                    id="worker-existing",
                    display_name="existing",
                    base_url="http://10.3.34.99:8188",
                    labels={duplicate: spec[duplicate]},
                )
            )
            await session.commit()
        async with db.session() as session:
            inventory = load_inventory(inventory_file(tmp_path, spec), add_only=True)
            with pytest.raises(ValueError, match="already assigned"):
                await apply_inventory(session, inventory, add_only=True)
            assert len((await session.scalars(select(Node))).all()) == 1
    finally:
        await db.close()
