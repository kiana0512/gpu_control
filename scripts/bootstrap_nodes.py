#!/usr/bin/env python3
"""Apply the legacy inventory or safely add one isolated, approved node."""

import argparse
import asyncio
import ipaddress
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import Node
from packages.gpu_control_core.settings import get_settings

POOLS = {"PRIMARY", "OVERFLOW"}
MODES = {"ACTIVE", "RESERVED", "OVERFLOW", "DRAINING", "DISABLED"}
EXPECTED_IDS = {
    "worker-3090-a",
    "worker-3090-b",
    "worker-4070ti-animation-host-01",
    "control-4090",
}


def load_inventory(path: Path, *, add_only: bool = False) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise ValueError("inventory must contain a nodes list")
    nodes: list[dict[str, Any]] = []
    for raw in payload["nodes"]:
        if not isinstance(raw, dict):
            raise ValueError("each node must be an object")
        item = {str(key): value for key, value in raw.items()}
        if add_only:
            item.setdefault("mode", "DISABLED")
        required = {"id", "host", "pool", "mode"}
        if not required.issubset(item):
            raise ValueError(f"node is missing fields: {sorted(required - item.keys())}")
        if item["pool"] not in POOLS or item["mode"] not in MODES:
            raise ValueError(f"invalid pool/mode for {item['id']}")
        if int(item.get("max_concurrency", 1)) != 1:
            raise ValueError("every GPU node must use max_concurrency=1")
        if add_only:
            node_id = item["id"]
            if (
                not isinstance(node_id, str)
                or len(node_id) > 64
                or not re.fullmatch(r"(?:worker|control)-[a-z0-9-]+", node_id)
            ):
                raise ValueError("invalid new node ID")
            if item["mode"] not in {"DISABLED", "DRAINING"}:
                raise ValueError("new nodes must begin DISABLED or DRAINING")
            host = str(ipaddress.IPv4Address(str(item["host"])))
            if (
                ipaddress.IPv4Address(host).is_unspecified
                or ipaddress.IPv4Address(host).is_loopback
            ):
                raise ValueError("new node host must be a reachable physical host address")
            item["host"] = host
            for field in ("hostname", "gpu", "mac", "gpu_uuid"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError(f"new node requires its verified {field}")
            if not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", item["mac"]):
                raise ValueError("new node MAC address is invalid")
            gpu_uuid = item["gpu_uuid"]
            if not gpu_uuid.startswith("GPU-") or str(UUID(gpu_uuid[4:])) != gpu_uuid[4:].lower():
                raise ValueError("new node GPU UUID is invalid")
            # Heartbeat later uses these exact host ports. Do not register an
            # unrelated endpoint only to overwrite it on the first heartbeat.
            for field, port in (("base_url", 8188), ("agent_url", 9201)):
                expected = f"http://{host}:{port}"
                if field in item and item[field] != expected:
                    raise ValueError(f"new node {field} must match its physical host")
            for field in ("wsl_runtime", "dcgm_exporter_enabled"):
                if field in item and not isinstance(item[field], bool):
                    raise ValueError(f"new node {field} must be boolean")
        nodes.append(item)
    inventory_ids = {str(item["id"]) for item in nodes}
    if len(inventory_ids) != len(nodes):
        raise ValueError("inventory contains duplicate node IDs")
    if add_only and len(nodes) != 1:
        raise ValueError("--add-only requires exactly one node")
    if not add_only and inventory_ids != EXPECTED_IDS:
        raise ValueError(f"inventory node ids must be exactly {sorted(EXPECTED_IDS)}")
    return nodes


async def apply_inventory(
    session: AsyncSession, inventory: list[dict[str, Any]], *, add_only: bool = False
) -> None:
    if add_only:
        if len(inventory) != 1 or inventory[0]["mode"] not in {"DISABLED", "DRAINING"}:
            raise ValueError("add-only registration requires one isolated node")
        item = inventory[0]
        # Only reads existing nodes. No assignments, timestamps, mode changes or
        # label merges are performed on any existing row in add-only mode.
        for existing in (await session.scalars(select(Node))).all():
            if existing.id == item["id"]:
                raise ValueError("node already exists; add-only made no changes")
            labels = existing.labels or {}
            for key in ("host", "mac", "gpu_uuid"):
                value = str(labels.get(key, ""))
                if value and value.lower() == str(item.get(key, "")).lower():
                    raise ValueError(f"new node {key} is already assigned; no changes made")
    for item in inventory:
        node_id = str(item["id"])
        host = str(item["host"])
        node = await session.get(Node, node_id)
        if add_only and node is not None:
            raise ValueError("node already exists; add-only made no changes")
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
        labels = dict(node.labels or {})
        labels.update({"gpu": str(item.get("gpu", "unknown")), "host": host})
        for identity_field in ("hostname", "mac", "gpu_uuid"):
            if item.get(identity_field):
                value = str(item[identity_field])
                labels[identity_field] = value.lower() if identity_field == "mac" else value
        for label_field in ("wsl_runtime", "dcgm_exporter_enabled", "vram_class"):
            if label_field in item:
                labels[label_field] = item[label_field]
        node.labels = labels
        node.approved_at = node.approved_at or datetime.now(UTC)


async def apply(path: Path, *, add_only: bool = False) -> None:
    inventory = load_inventory(path, add_only=add_only)
    db = Database(get_settings())
    try:
        async with db.session() as session:
            await apply_inventory(session, inventory, add_only=add_only)
            await session.commit()
    finally:
        await db.close()
    if add_only:
        print(f"已新增隔离节点 {inventory[0]['id']}；已有节点未修改")
    else:
        print(f"已应用 {len(inventory)} 个节点；运行任务和健康字段未被覆盖")


def main() -> None:
    parser = argparse.ArgumentParser(description="应用原四节点清单，或安全增量注册单个隔离节点")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--add-only",
        action="store_true",
        help="只新增一个 DISABLED/DRAINING 节点；拒绝修改已有节点",
    )
    args = parser.parse_args()
    asyncio.run(apply(args.config.resolve(), add_only=args.add_only))


if __name__ == "__main__":
    main()
