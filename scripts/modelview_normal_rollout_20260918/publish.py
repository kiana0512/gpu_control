"""Explicit staged publication / UI synchronization maintenance operations."""

import asyncio
import json
import sys

import httpx
from sqlalchemy import select, func
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import WorkflowVersion, Node, Job, AssetWorker
from packages.gpu_control_core.settings import get_settings
from packages.gpu_control_core.workflow_cli import refresh_compatibility
from rollout import audit, EVIDENCE, KEY, NODES, TERMINAL


async def main():
    action = sys.argv[1]
    database = Database(get_settings())
    async with database.session() as db:
        if action == "publish":
            report = json.loads((EVIDENCE / "verification.json").read_text())
            assert len(report["canaries"]) == 3
            assert all(x["status"] == "PASSED" for x in report["canaries"])
            new = await db.scalar(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_key == KEY,
                    WorkflowVersion.version == report["version"],
                )
                .with_for_update()
            )
            assert new.template_sha256 == report["template_sha256"]
            compatible = await refresh_compatibility(db, new)
            assert all(
                next(x for x in compatible if x["node_id"] == n)["compatible"] for n in NODES
            )
            assert not next(x for x in compatible if x["node_id"] == "worker-5070ti-01")[
                "compatible"
            ]
            old = await db.scalar(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_key == KEY,
                    WorkflowVersion.version == report["previous_version"],
                )
                .with_for_update()
            )
            assert old.enabled and not new.enabled
            old.enabled = False
            new.enabled = True
            audit(
                db,
                "workflow.publish.refcontrol_normal",
                KEY,
                {"version": old.version},
                {"version": new.version, "template_sha256": new.template_sha256},
            )
            await db.commit()
            report["compatibility"] = compatible
            report["status"] = "PUBLISHED"
            (EVIDENCE / "release.json").write_text(json.dumps(report, indent=2))
            print("PUBLISHED " + new.version)
        elif action in ("drain", "restore"):
            for node_id in NODES:
                node = await db.get(Node, node_id, with_for_update=True)
                before = node.mode
                if action == "drain":
                    assert before == "ACTIVE", (node_id, before)
                    node.mode = "DRAINING"
                else:
                    assert before == "DRAINING", (node_id, before)
                    node.mode = "ACTIVE"
                audit(
                    db,
                    "node." + action + ".normal_ui_sync",
                    node_id,
                    {"mode": before},
                    {"mode": node.mode},
                )
            await db.commit()
        else:
            raise ValueError(action)
    if action == "drain":
        for _ in range(240):
            ready = True
            async with database.session() as db:
                for node_id in NODES:
                    node = await db.get(Node, node_id)
                    active = await db.scalar(
                        select(func.count(Job.id)).where(
                            Job.node_id == node_id, Job.status.not_in(TERMINAL)
                        )
                    )
                    assets = await db.scalar(
                        select(func.sum(AssetWorker.current_jobs)).where(
                            AssetWorker.node_id == node_id
                        )
                    )
                    async with httpx.AsyncClient(base_url=node.base_url, timeout=10) as client:
                        response = await client.get("/queue")
                        response.raise_for_status()
                        queue = response.json()
                    ready &= (
                        not active
                        and not assets
                        and not queue["queue_running"]
                        and not queue["queue_pending"]
                    )
            if ready:
                print("ALL_THREE_IDLE")
                break
            await asyncio.sleep(5)
        else:
            raise RuntimeError("Drain timed out; nodes left DRAINING for inspection")
    await database.close()


asyncio.run(main())
