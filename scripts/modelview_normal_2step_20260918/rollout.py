"""Verify the user-approved four-image normal-reference workflow before publication.

Run with the production API environment and /tmp/modelview-normal-2step-20260918 bundle.
Does not enable the candidate. Only the approved sampler step count changes.
"""

import asyncio
import copy
import hashlib
import io
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy import func, select

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import AssetWorker, AuditLog, Job, Node, WorkflowVersion
from packages.gpu_control_core.settings import get_settings
from packages.gpu_control_core.workflow import render_workflow, template_digest
from packages.gpu_control_core.workflow_cli import command_import, load_bundle

BUNDLE = Path("/tmp/modelview-normal-2step-20260918/manifest.yaml")
EVIDENCE = Path("/srv/gpu-control/jobs/deployment-modelview-normal-2step-20260918")
SOURCE = Path("/tmp/modelview-normal-2step-20260918/input")
NODES = ["control-4090", "worker-3090-a", "worker-3090-b"]
TERMINAL = ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]
KEY = "modelview-inpaint"


def audit(db, action, target, before, after):
    db.add(
        AuditLog(
            actor_id="codex-user-authorized",
            action=action,
            target_type="deployment",
            target_id=target,
            before=before,
            after=after,
            source_ip="10.3.34.11",
            request_id=uuid.uuid4().hex,
            result="SUCCESS",
            created_at=datetime.now(UTC),
        )
    )


async def canary(database, manifest, template, node_id):
    async with database.session() as db:
        node = await db.get(Node, node_id, with_for_update=True)
        original_mode = node.mode
        if original_mode != "ACTIVE":
            raise RuntimeError(f"{node_id} expected ACTIVE, got {original_mode}")
        url = node.base_url
        node.mode = "DRAINING"
        audit(
            db,
            "node.drain.refcontrol_normal",
            node_id,
            {"mode": original_mode},
            {"mode": "DRAINING"},
        )
        await db.commit()
    async with httpx.AsyncClient(base_url=url, timeout=120) as client:
        try:
            for _ in range(240):
                async with database.session() as db:
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
                q = (await client.get("/queue")).json()
                if (
                    not active
                    and not assets
                    and not q.get("queue_running")
                    and not q.get("queue_pending")
                ):
                    break
                await asyncio.sleep(5)
            else:
                raise RuntimeError(f"{node_id} did not drain")
            info = await client.get("/object_info/LoraLoaderModelOnly")
            info.raise_for_status()
            assert (
                template["21"]["inputs"]["lora_name"]
                in info.json()["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]
            )
            assert (
                template["78"]["inputs"]["lora_name"]
                in info.json()["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]
            )
            parameters = {"noise_seed": 4500}
            input_hashes = {}
            subfolder = "normal-verification-" + uuid.uuid4().hex
            for parameter, prefix in [
                ("image_filename", "image-"),
                ("material_image_filename", "material_image-"),
                ("mask_filename", "mask-"),
                ("normal_image_filename", "normal_image-"),
            ]:
                files = list(SOURCE.glob(prefix + "*.png"))
                assert len(files) == 1
                data = files[0].read_bytes()
                input_hashes[parameter] = hashlib.sha256(data).hexdigest()
                response = await client.post(
                    "/upload/image",
                    data={"subfolder": subfolder, "type": "input", "overwrite": "false"},
                    files={"image": (prefix + "canary.png", data, "image/png")},
                )
                response.raise_for_status()
                uploaded = response.json()
                parameters[parameter] = uploaded["subfolder"] + "/" + uploaded["name"]
            prompt = render_workflow(manifest, template, parameters)
            assert prompt["60"]["inputs"]["text"] == template["60"]["inputs"]["text"]
            assert prompt["15"]["inputs"]["steps"] == 2
            assert prompt["77"]["inputs"]["pixels"] == ["79", 0]
            assert prompt["74"]["inputs"]["latent"] == ["77", 0]
            started = time.monotonic()
            response = await client.post("/prompt", json={"prompt": prompt, "client_id": subfolder})
            response.raise_for_status()
            submitted = response.json()
            assert not submitted.get("node_errors"), submitted
            prompt_id = submitted["prompt_id"]
            print(
                json.dumps({"node": node_id, "prompt_id": prompt_id, "state": "submitted"}),
                flush=True,
            )
            history = None
            for _ in range(480):
                response = await client.get("/history/" + prompt_id)
                response.raise_for_status()
                history = response.json().get(prompt_id)
                if history:
                    break
                await asyncio.sleep(5)
            assert history and history["status"]["status_str"] == "success", history
            outputs = history["outputs"]["29"]["images"]
            assert len(outputs) == 1
            result = await client.get("/view", params=outputs[0])
            result.raise_for_status()
            im = Image.open(io.BytesIO(result.content))
            im.load()
            source = Image.open(next(SOURCE.glob("image-*.png")))
            assert im.size == source.size, (im.size, source.size)
            report = {
                "node_id": node_id,
                "status": "PASSED",
                "prompt_id": prompt_id,
                "version": manifest.version,
                "template_sha256": template_digest(template),
                "input_sha256": input_hashes,
                "output_sha256": hashlib.sha256(result.content).hexdigest(),
                "size": list(im.size),
                "elapsed_seconds": round(time.monotonic() - started, 2),
            }
            (EVIDENCE / (node_id + ".png")).write_bytes(result.content)
            (EVIDENCE / (node_id + ".json")).write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
            return report
        finally:
            q = (await client.get("/queue")).json()
            # Never return a node to scheduling while our canary still runs.
            if not q.get("queue_running") and not q.get("queue_pending"):
                async with database.session() as db:
                    node = await db.get(Node, node_id, with_for_update=True)
                    if node.mode == "DRAINING":
                        node.mode = original_mode
                        audit(
                            db,
                            "node.restore.refcontrol_normal",
                            node_id,
                            {"mode": "DRAINING"},
                            {"mode": original_mode},
                        )
                        await db.commit()


async def main():
    manifest, template = load_bundle(BUNDLE)
    assert manifest.workflow_key == KEY
    database = Database(get_settings())
    EVIDENCE.mkdir(exist_ok=True)
    async with database.session() as db:
        current = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_key == KEY, WorkflowVersion.enabled.is_(True)
            )
        )
        old_version = current.version
        assert current.version == "2026.09.18-refcontrol-normal-4step-r1"
        expected = copy.deepcopy(current.template)
        assert expected["15"]["inputs"]["steps"] == 4
        expected["15"]["inputs"]["steps"] = 2
        assert expected == template, "Only BasicScheduler #15 steps may change"
        assert template["60"]["inputs"]["text"] == current.template["60"]["inputs"]["text"]
        (EVIDENCE / "old-template.json").write_text(json.dumps(current.template, indent=2))
        (EVIDENCE / "previous.json").write_text(
            json.dumps({"version": old_version, "template_sha256": current.template_sha256})
        )
        candidate = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_key == KEY, WorkflowVersion.version == manifest.version
            )
        )
        if candidate is not None:
            assert not candidate.enabled
            assert candidate.template_sha256 == template_digest(template)
    if candidate is None:
        await command_import(BUNDLE)
    outcomes = await asyncio.gather(
        *(canary(database, manifest, template, n) for n in NODES), return_exceptions=True
    )
    errors = [str(r) for r in outcomes if isinstance(r, BaseException)]
    if errors:
        raise RuntimeError(errors)
    (EVIDENCE / "verification.json").write_text(
        json.dumps(
            {
                "previous_version": old_version,
                "version": manifest.version,
                "template_sha256": template_digest(template),
                "canaries": outcomes,
                "deferred_nodes": ["worker-5070ti-01"],
            },
            indent=2,
        )
    )
    await database.close()
    print("VERIFIED_NOT_PUBLISHED " + manifest.version, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
