"""Scoped two-workflow/four-node validation and atomic publication."""
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
from packages.gpu_control_core.workflow_cli import load_bundle, command_import, refresh_compatibility
import canary

ROOT = Path('/tmp/modelview-single-normal-20260918')
EVIDENCE = Path('/srv/gpu-control/jobs/deployment-modelview-single-normal-20260918')
KEYS = ['modelview-single-view', 'modelview-single-view-inpaint']
NODES = ['control-4090','worker-3090-a','worker-3090-b','worker-5070ti-01']
LABEL = 'modelview_normal_single_flows_20260918'
DIGESTS = ['6b5543d043b2920add573cf2015379dfb621bc1a238a17d2ad037b64b8800805', '2022768e4f2f604a5e5a385730a3406f2713ee0a8db060d0e788d0a0aca632d8']

async def main():
    action=sys.argv[1]
    database=Database(get_settings())
    EVIDENCE.mkdir(exist_ok=True)
    if action=='verify':
        for key,digest in zip(KEYS,DIGESTS):
            manifest,template=load_bundle(ROOT/key/'manifest.yaml')
            assert template_digest(template)==digest
            async with database.session() as db:
                old=await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==key,WorkflowVersion.enabled.is_(True)))
                assert old and old.version.startswith('2026.09.17-')
                candidate=await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==key,WorkflowVersion.version==manifest.version))
                if candidate:
                    assert not candidate.enabled and candidate.template_sha256==digest
                canary.EVIDENCE=EVIDENCE/key
                canary.EVIDENCE.mkdir(exist_ok=True)
                (canary.EVIDENCE/'previous.json').write_text(json.dumps({'version':old.version,'template_sha256':old.template_sha256}))
            if candidate is None:
                await command_import(ROOT/key/'manifest.yaml')
            outcomes=await asyncio.gather(*(canary.canary(database,manifest,template,n) for n in NODES),return_exceptions=True)
            errors=[str(r) for r in outcomes if isinstance(r,BaseException)]
            if errors: raise RuntimeError(errors)
            (canary.EVIDENCE/'verification.json').write_text(json.dumps(outcomes,indent=2))
        print('ALL_EIGHT_VERIFIED_NOT_PUBLISHED',flush=True)
    elif action in ('drain','restore'):
        async with database.session() as db:
            for node_id in NODES:
                node=await db.get(Node,node_id,with_for_update=True)
                expected='ACTIVE' if action=='drain' else 'DRAINING'
                assert node.mode==expected,(node_id,node.mode)
                node.mode='DRAINING' if action=='drain' else 'ACTIVE'
                canary.audit(db,'node.'+action+'.single_normal',node_id,{'mode':expected},{'mode':node.mode})
            await db.commit()
        if action=='drain':
            for _ in range(240):
                ready=True
                async with database.session() as db:
                    for node_id in NODES:
                        node=await db.get(Node,node_id)
                        active=await db.scalar(select(func.count(Job.id)).where(Job.node_id==node_id,Job.status.not_in(canary.TERMINAL)))
                        assets=await db.scalar(select(func.sum(AssetWorker.current_jobs)).where(AssetWorker.node_id==node_id))
                        async with httpx.AsyncClient(base_url=node.base_url,timeout=15) as client:
                            r=await client.get('/queue');r.raise_for_status();q=r.json()
                        ready &= not active and not assets and not q['queue_running'] and not q['queue_pending']
                if ready: break
                await asyncio.sleep(5)
            else: raise RuntimeError('Drain timed out; nodes remain DRAINING for inspection')
            print('FOUR_NODES_IDLE',flush=True)
    elif action=='publish':
        runtime_bytes=(ROOT/'runtime5070.json').read_bytes()
        async with database.session() as db:
            node5070=await db.get(Node,'worker-5070ti-01',with_for_update=True)
            original_profiles=copy.deepcopy(node5070.labels.get('validated_vram_profiles',{}))
            for node_id in NODES:
                node=await db.get(Node,node_id,with_for_update=True)
                labels=copy.deepcopy(node.labels);labels[LABEL]='validated';node.labels=labels
            for key,digest in zip(KEYS,DIGESTS):
                manifest,template=load_bundle(ROOT/key/'manifest.yaml')
                outcomes=json.loads((EVIDENCE/key/'verification.json').read_text())
                assert {o['node_id'] for o in outcomes}==set(NODES)
                assert all(o['status']=='PASSED' and o['template_sha256']==digest for o in outcomes)
                new=await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==key,WorkflowVersion.version==manifest.version).with_for_update())
                old=await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==key,WorkflowVersion.enabled.is_(True)).with_for_update())
                previous=json.loads((EVIDENCE/key/'previous.json').read_text())
                assert old.version==previous['version'] and new.template_sha256==digest and not new.enabled
                labels=copy.deepcopy(node5070.labels)
                profile={'status':'PASSED','node_id':node5070.id,'gpu_uuid':labels['gpu_uuid'],'min_vram_mb':16000,
                         'version':new.version,'template_sha256':digest,
                         'evidence_sha256':hashlib.sha256((EVIDENCE/key/'worker-5070ti-01.json').read_bytes()).hexdigest(),
                         'runtime_profile_sha256':hashlib.sha256(runtime_bytes).hexdigest()}
                labels['validated_vram_profiles'][key]=profile;node5070.labels=labels
                await db.flush()
                compat=await refresh_compatibility(db,new)
                assert {r['node_id'] for r in compat if r['compatible']}==set(NODES),compat
                old.enabled=False;new.enabled=True
                canary.audit(db,'workflow.publish.single_normal',key,{'version':old.version,'profile':original_profiles[key]},
                             {'version':new.version,'template_sha256':digest,'profile':profile})
            await db.commit()
        (EVIDENCE/'runtime5070.json').write_bytes(runtime_bytes)
        (EVIDENCE/'release.json').write_text(json.dumps({'status':'PUBLISHED','workflows':KEYS,'nodes':NODES,'template_sha256':DIGESTS},indent=2))
        print('BOTH_WORKFLOWS_PUBLISHED',flush=True)
    else: raise ValueError(action)
    await database.close()

asyncio.run(main())
