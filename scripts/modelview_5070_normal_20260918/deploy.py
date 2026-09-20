"""Accept only the 5070 Ti against the already published two-step normal workflow."""
import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path

import httpx
from sqlalchemy import select, func
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import Node, Job, AssetWorker, WorkflowVersion
from packages.gpu_control_core.settings import get_settings
from packages.gpu_control_core.workflow import template_digest
from packages.gpu_control_core.workflow_cli import load_bundle, refresh_compatibility
import rollout

NODE = 'worker-5070ti-01'
EVIDENCE = Path('/srv/gpu-control/jobs/deployment-modelview-5070-normal-20260918')

async def main():
    action = sys.argv[1]
    EVIDENCE.mkdir(exist_ok=True)
    database = Database(get_settings())
    if action == 'drain':
        async with database.session() as db:
            node = await db.get(Node, NODE, with_for_update=True)
            assert node.mode == 'ACTIVE' and node.health == 'ONLINE'
            previous = {'mode': node.mode, 'profile': node.labels['validated_vram_profiles'][rollout.KEY],
                        'gpu_uuid': node.labels['gpu_uuid']}
            (EVIDENCE / 'previous.json').write_text(json.dumps(previous, indent=2))
            node.mode = 'DRAINING'
            rollout.audit(db, 'node.drain.normal_5070', NODE, {'mode': 'ACTIVE'}, {'mode': 'DRAINING'})
            await db.commit()
        for _ in range(240):
            async with database.session() as db:
                node = await db.get(Node, NODE)
                active = await db.scalar(select(func.count(Job.id)).where(Job.node_id == NODE, Job.status.not_in(rollout.TERMINAL)))
                assets = await db.scalar(select(func.sum(AssetWorker.current_jobs)).where(AssetWorker.node_id == NODE))
            async with httpx.AsyncClient(base_url=node.base_url, timeout=10) as client:
                r = await client.get('/queue'); r.raise_for_status(); queue = r.json()
            if not active and not assets and not queue['queue_running'] and not queue['queue_pending']:
                print('5070_IDLE', flush=True)
                break
            await asyncio.sleep(5)
        else:
            raise RuntimeError('Node remains drained; inspect active work')
    elif action == 'verify-enable':
        manifest, template = load_bundle(rollout.BUNDLE)
        assert manifest.version == '2026.09.18-refcontrol-normal-2step-r1'
        assert template_digest(template) == '65b0c6d0a3445d8318c411fd421b1721e33991494bd5b63d770bbcf29b67da7f'
        rollout.EVIDENCE = EVIDENCE
        report = await rollout.canary(database, manifest, template, NODE)
        runtime_bytes = Path('/tmp/modelview-5070-normal-20260918/runtime.json').read_bytes()
        runtime = json.loads(runtime_bytes)
        assert runtime['normal_lora_sha256'] == 'baa97f297d048330850f0cb8063392678927a34282de961760693f6897983fd2'
        assert runtime['ui_sha256'] == '81c7f6b2f427799ee0f185dbf817a29e5d6df5f618de8c31fe6ccf5aabbe0bdf'
        (EVIDENCE / 'runtime.json').write_bytes(runtime_bytes)
        async with database.session() as db:
            node = await db.get(Node, NODE, with_for_update=True)
            assert node.mode == 'DRAINING' and node.health == 'ONLINE'
            version = await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key == rollout.KEY, WorkflowVersion.enabled.is_(True)))
            assert version.version == manifest.version and version.template_sha256 == template_digest(template)
            previous = json.loads((EVIDENCE / 'previous.json').read_text())
            assert node.labels['gpu_uuid'] == previous['gpu_uuid']
            labels = copy.deepcopy(node.labels)
            profile = {'status': 'PASSED', 'node_id': NODE, 'gpu_uuid': node.labels['gpu_uuid'],
                       'min_vram_mb': 16000, 'version': version.version, 'template_sha256': version.template_sha256,
                       'evidence_sha256': hashlib.sha256((EVIDENCE / (NODE+'.json')).read_bytes()).hexdigest(),
                       'runtime_profile_sha256': hashlib.sha256(runtime_bytes).hexdigest()}
            labels['validated_vram_profiles'][rollout.KEY] = profile
            node.labels = labels
            await db.flush()
            compatibility = await refresh_compatibility(db, version)
            assert next(x for x in compatibility if x['node_id'] == NODE)['compatible']
            rollout.audit(db, 'workflow.vram_acceptance.normal_5070', NODE, previous['profile'], profile)
            node.mode = previous['mode']
            rollout.audit(db, 'node.restore.normal_5070', NODE, {'mode': 'DRAINING'}, {'mode': node.mode})
            await db.commit()
        (EVIDENCE / 'release.json').write_text(json.dumps({'status':'ENABLED','report':report,'profile':profile,'compatibility':compatibility}, indent=2))
        print('5070_NORMAL_2STEP_ENABLED', flush=True)
    else:
        raise ValueError(action)
    await database.close()

asyncio.run(main())
