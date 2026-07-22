#!/usr/bin/env python3
"""Apply the reviewed three-node inventory to PostgreSQL after migration."""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import Node
from packages.gpu_control_core.settings import get_settings

POOLS = {"PRIMARY", "OVERFLOW"}
MODES = {"ACTIVE", "RESERVED", "OVERFLOW", "DRAINING", "DISABLED"}
EXPECTED_IDS = {"worker-3090-a", "worker-3090-b", "control-4090"}


def load_inventory(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise ValueError("inventory must contain a nodes list")
    nodes: list[dict[str, Any]] = []
    for raw in payload["nodes"]:
        if not isinstance(raw, dict):
            raise ValueError("each node must be an object")
        item = {str(key): value for key, value in raw.items()}
        required = {"id", "host", "pool", "mode"}
        if not required.issubset(item):
            raise ValueError(f"node is missing fields: {sorted(required - item.keys())}")
        if item["pool"] not in POOLS or item["mode"] not in MODES:
            raise ValueError(f"invalid pool/mode for {item['id']}")
        if int(item.get("max_concurrency", 1)) != 1:
            raise ValueError("every GPU node must use max_concurrency=1")
        nodes.append(item)
    inventory_ids = {str(item["id"]) for item in nodes}
    if inventory_ids != EXPECTED_IDS:
        raise ValueError(f"inventory node ids must be exactly {sorted(EXPECTED_IDS)}")
    return nodes


async def apply(path: Path) -> None:
    inventory = load_inventory(path)
    db = Database(get_settings())
    async with db.session() as session:
        for item in inventory:
            node_id = str(item["id"])
            host = str(item["host"])
            node = await session.get(Node, node_id)
            if node is None:
                node = Node(
                    id=node_id, display_name=str(item.get("display_name", node_id)), base_url=""
                )
                session.add(node)
            node.display_name = str(item.get("display_name", node_id))
            node.base_url = str(item.get("base_url", f"http://{host}:8188"))
            node.agent_url = str(item.get("agent_url", f"http://{host}:9201"))
            node.pool = str(item["pool"])
            node.mode = str(item["mode"])
            node.manual_reserved = node.mode == "RESERVED"
            node.max_concurrency = 1
            node.labels = {"gpu": str(item.get("gpu", "unknown")), "host": host}
            node.approved_at = node.approved_at or datetime.now(UTC)
        await session.commit()
    await db.close()
    print(f"已应用 {len(inventory)} 个节点；运行任务和健康字段未被覆盖")


def main() -> None:
    parser = argparse.ArgumentParser(description="应用经过审核的三节点清单")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(apply(args.config.resolve()))


if __name__ == "__main__":
    main()
