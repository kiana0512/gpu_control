"""Scoped rollout: run inside the control API container with an explicit bundle.

Only the user-approved modelview-inpaint LoRA/default prompt are published.
No business inputs, graph, sampler settings, or other workflows are changed.
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
from packages.gpu_control_core.workflow_cli import command_import, load_bundle, refresh_compatibility

BUNDLE = Path('/tmp/modelview-lora4500/manifest.yaml')
EVIDENCE = Path('/srv/gpu-control/jobs/deployment-modelview-lora4500-20260917')
SOURCE = Path('/srv/gpu-control/jobs/2026/09/17/e0508afb-4797-4d0b-91c4-5b5373988fca/input')
NODES = ['control-4090', 'worker-3090-a', 'worker-3090-b', 'worker-5070ti-01']
TERMINAL = ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT']
KEY = 'modelview-inpaint'


def audit(db, action, target, before, after):
    db.add(AuditLog(actor_id='codex-user-authorized', action=action,
                    target_type='deployment', target_id=target, before=before, after=after,
                    source_ip='10.3.34.11', request_id=uuid.uuid4().hex,
                    result='SUCCESS', created_at=datetime.now(UTC)))


async def canary(database, manifest, template, node_id):
    async with database.session() as db:
        node = await db.get(Node, node_id, with_for_update=True)
        original_mode = node.mode
        if original_mode != 'ACTIVE':
            raise RuntimeError(f'{node_id} expected ACTIVE, got {original_mode}')
        url = node.base_url
        node.mode = 'DRAINING'
        audit(db, 'node.drain.lora4500', node_id, {'mode': original_mode}, {'mode': 'DRAINING'})
        await db.commit()
    async with httpx.AsyncClient(base_url=url, timeout=120) as client:
        try:
            for _ in range(240):
                async with database.session() as db:
                    active = await db.scalar(select(func.count(Job.id)).where(
                        Job.node_id == node_id, Job.status.not_in(TERMINAL)))
                    assets = await db.scalar(select(func.sum(AssetWorker.current_jobs)).where(
                        AssetWorker.node_id == node_id))
                q = (await client.get('/queue')).json()
                if not active and not assets and not q.get('queue_running') and not q.get('queue_pending'):
                    break
                await asyncio.sleep(5)
            else:
                raise RuntimeError(f'{node_id} did not drain')
            info = await client.get('/object_info/LoraLoaderModelOnly')
            info.raise_for_status()
            assert template['21']['inputs']['lora_name'] in info.json()['LoraLoaderModelOnly']['input']['required']['lora_name'][0]
            parameters = {'noise_seed': 4500}
            input_hashes = {}
            subfolder = 'lora4500-verification-' + uuid.uuid4().hex
            for parameter, prefix in [('image_filename', 'image-'), ('material_image_filename', 'material_image-'), ('mask_filename', 'mask-')]:
                files = list(SOURCE.glob(prefix + '*.png'))
                assert len(files) == 1
                data = files[0].read_bytes()
                input_hashes[parameter] = hashlib.sha256(data).hexdigest()
                response = await client.post('/upload/image', data={'subfolder': subfolder, 'type': 'input', 'overwrite': 'false'}, files={'image': (prefix + 'canary.png', data, 'image/png')})
                response.raise_for_status()
                uploaded = response.json()
                parameters[parameter] = uploaded['subfolder'] + '/' + uploaded['name']
            prompt = render_workflow(manifest, template, parameters)
            assert prompt['60']['inputs']['text'] == template['60']['inputs']['text']
            assert prompt['15']['inputs']['steps'] == 2
            started = time.monotonic()
            response = await client.post('/prompt', json={'prompt': prompt, 'client_id': subfolder})
            response.raise_for_status()
            submitted = response.json()
            assert not submitted.get('node_errors'), submitted
            prompt_id = submitted['prompt_id']
            print(json.dumps({'node': node_id, 'prompt_id': prompt_id, 'state': 'submitted'}), flush=True)
            history = None
            for _ in range(480):
                response = await client.get('/history/' + prompt_id)
                response.raise_for_status()
                history = response.json().get(prompt_id)
                if history:
                    break
                await asyncio.sleep(5)
            assert history and history['status']['status_str'] == 'success', history
            outputs = history['outputs']['29']['images']
            assert len(outputs) == 1
            result = await client.get('/view', params=outputs[0])
            result.raise_for_status()
            im = Image.open(io.BytesIO(result.content))
            im.load()
            source = Image.open(next(SOURCE.glob('image-*.png')))
            assert im.size == source.size, (im.size, source.size)
            report = {'node_id': node_id, 'status': 'PASSED', 'prompt_id': prompt_id,
                      'version': manifest.version, 'template_sha256': template_digest(template),
                      'input_sha256': input_hashes, 'output_sha256': hashlib.sha256(result.content).hexdigest(),
                      'size': list(im.size), 'elapsed_seconds': round(time.monotonic()-started, 2)}
            (EVIDENCE / (node_id + '.png')).write_bytes(result.content)
            (EVIDENCE / (node_id + '.json')).write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
            return report
        finally:
            q = (await client.get('/queue')).json()
            # Never return a node to scheduling while our canary still runs.
            if not q.get('queue_running') and not q.get('queue_pending'):
                async with database.session() as db:
                    node = await db.get(Node, node_id, with_for_update=True)
                    if node.mode == 'DRAINING':
                        node.mode = original_mode
                        audit(db, 'node.restore.lora4500', node_id, {'mode': 'DRAINING'}, {'mode': original_mode})
                        await db.commit()


async def main():
    manifest, template = load_bundle(BUNDLE)
    assert manifest.workflow_key == KEY
    database = Database(get_settings())
    EVIDENCE.mkdir(exist_ok=True)
    async with database.session() as db:
        current = await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key == KEY, WorkflowVersion.enabled.is_(True)))
        old_version = current.version
        old = copy.deepcopy(current.template)
        old['21']['inputs']['lora_name'] = template['21']['inputs']['lora_name']
        old['60']['inputs']['text'] = template['60']['inputs']['text']
        assert old == template, 'Changes exceed the approved LoRA/default prompt scope'
        (EVIDENCE / 'old-template.json').write_text(json.dumps(current.template, indent=2))
    await command_import(BUNDLE)
    outcomes = await asyncio.gather(*(canary(database, manifest, template, n) for n in NODES), return_exceptions=True)
    errors = [str(r) for r in outcomes if isinstance(r, BaseException)]
    if errors:
        raise RuntimeError(errors)
    async with database.session() as db:
        new = await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key == KEY, WorkflowVersion.version == manifest.version).with_for_update())
        node = await db.get(Node, 'worker-5070ti-01', with_for_update=True)
        labels = copy.deepcopy(node.labels)
        previous_profile = copy.deepcopy(labels['validated_vram_profiles'][KEY])
        evidence_bytes = (EVIDENCE / 'worker-5070ti-01.json').read_bytes()
        labels['validated_vram_profiles'][KEY].update(version=new.version, template_sha256=new.template_sha256,
            evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest())
        node.labels = labels
        audit(db, 'workflow.vram_acceptance.lora4500', node.id, previous_profile, labels['validated_vram_profiles'][KEY])
        await db.flush()
        compatible = await refresh_compatibility(db, new)
        assert all(next(r for r in compatible if r['node_id']==n)['compatible'] for n in NODES), compatible
        old = await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==KEY, WorkflowVersion.version==old_version).with_for_update())
        old.enabled = False
        new.enabled = True
        audit(db, 'workflow.publish.lora4500', KEY, {'version': old_version}, {'version': new.version, 'template_sha256': new.template_sha256})
        await db.commit()
    (EVIDENCE / 'release.json').write_text(json.dumps({'previous_version': old_version, 'version': manifest.version, 'template_sha256': template_digest(template), 'compatibility': compatible, 'canaries': outcomes}, indent=2))
    await database.close()
    print('PUBLISHED ' + manifest.version, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
