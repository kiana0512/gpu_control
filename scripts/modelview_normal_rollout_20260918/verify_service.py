"""Read and persist acceptance evidence from the real business API task."""

import asyncio
import hashlib
import json
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import Job, Node, WorkflowVersion
from packages.gpu_control_core.settings import get_settings


async def main():
    dbs = Database(get_settings())
    async with dbs.session() as db:
        job = await db.get(Job, "56268552-d449-4fdb-9a4b-3cee07cc08e5")
        assert job.status == "SUCCEEDED"
        root = Path(job.job_dir)
        prompt = json.loads((root / "workflow/rendered.api.json").read_text())
        assert prompt["79"]["inputs"]["image"].startswith(job.id + "/normal_image-")
        assert prompt["77"]["inputs"]["pixels"] == ["79", 0]
        assert prompt["74"]["inputs"]["latent"] == ["77", 0]
        assert prompt["14"]["inputs"]["noise_seed"] == job.parameters["noise_seed"]
        assert prompt["14"]["inputs"]["noise_seed"] != 238589373036856
        uploaded = json.loads((root / "comfy/upload.responses.json").read_text())
        assert len(uploaded) == 4
        outputs = list((root / "output").glob("*.png"))
        assert len(outputs) == 1
        with Image.open(outputs[0]) as image:
            image.load()
            assert image.size == (2048, 2048)
        versions = list(
            (
                await db.scalars(
                    select(WorkflowVersion).where(
                        WorkflowVersion.workflow_key.in_(
                            [
                                "modelview-inpaint",
                                "modelview-single-view",
                                "modelview-single-view-inpaint",
                            ]
                        ),
                        WorkflowVersion.enabled.is_(True),
                    )
                )
            ).all()
        )
        nodes = list((await db.scalars(select(Node))).all())
        report = {
            "status": "PASSED",
            "job_id": job.id,
            "node_id": job.node_id,
            "version": job.workflow_version,
            "noise_seed": job.parameters["noise_seed"],
            "uploaded_images": 4,
            "output_count": 1,
            "output_size": [2048, 2048],
            "output_sha256": hashlib.sha256(outputs[0].read_bytes()).hexdigest(),
            "active_workflows": {v.workflow_key: v.version for v in versions},
            "nodes": [{"id": n.id, "mode": n.mode, "health": n.health} for n in nodes],
        }
        Path(
            "/srv/gpu-control/jobs/deployment-modelview-normal-20260918/service-acceptance.json"
        ).write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    await dbs.close()


asyncio.run(main())
