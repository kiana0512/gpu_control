"""Canary helpers for the two user-approved single-view normal-reference workflows.

Imported by deploy.py; does not enable a candidate. No standalone rollout entrypoint.
"""

import asyncio
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

from packages.gpu_control_core.models import AssetWorker, AuditLog, Job, Node
from packages.gpu_control_core.workflow import render_workflow, template_digest

EVIDENCE = Path("/srv/gpu-control/jobs/deployment-modelview-single-normal-20260918")
SOURCE = Path("/tmp/modelview-single-normal-20260918/input")
TERMINAL = ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]


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
                template["43" if manifest.workflow_key == "modelview-single-view" else "65"]["inputs"]["lora_name"]
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
                if parameter not in manifest.bindings:
                    continue
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
            single = manifest.workflow_key == "modelview-single-view"
            fixed = "40" if single else "62"
            assert prompt[fixed]["inputs"]["text"] == template[fixed]["inputs"]["text"]
            assert prompt["15"]["inputs"]["steps"] == (4 if single else 2)
            assert prompt["44" if single else "66"]["inputs"] == {"pixels": ["42" if single else "64", 0], "vae": ["3", 0]}
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


if __name__ == "__main__":
    raise SystemExit("Use the scoped deploy.py driver; this file only provides helpers.")
