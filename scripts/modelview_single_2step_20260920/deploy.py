import asyncio,copy,hashlib,json,sys
from pathlib import Path
from sqlalchemy import select,func
import httpx
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.settings import get_settings
from packages.gpu_control_core.models import Node,Job,AssetWorker,WorkflowVersion
from packages.gpu_control_core.workflow import template_digest
from packages.gpu_control_core.workflow_cli import load_bundle,command_import,refresh_compatibility
import canary
ROOT=Path('/tmp/single-view-2step-20260920')
E=Path('/srv/gpu-control/jobs/deployment-single-view-2step-20260920')
KEY='modelview-single-view'
OLD='2026.09.18-refcontrol-normal-single-view-4step-r1'
NODES=['control-4090','worker-3090-a','worker-3090-b','worker-5070ti-01']
async def main():
 d=Database(get_settings());E.mkdir(exist_ok=True)
 m,t=load_bundle(ROOT/KEY/'manifest.yaml');digest=template_digest(t)
 action=sys.argv[1]
 if action=='verify':
  async with d.session() as s:
   old=await s.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==KEY,WorkflowVersion.version==OLD))
   assert old.enabled
   expected=copy.deepcopy(old.template);assert expected['15']['inputs']['steps']==4
   expected['15']['inputs']['steps']=2;assert expected==t,'Unexpected graph change'
   (E/'previous.json').write_text(json.dumps({'version':old.version,'sha256':old.template_sha256}))
  await command_import(ROOT/KEY/'manifest.yaml')
  canary.EVIDENCE=E
  results=await asyncio.gather(*(canary.canary(d,m,t,n) for n in NODES),return_exceptions=True)
  errors=[str(x) for x in results if isinstance(x,BaseException)]
  if errors:raise RuntimeError(errors)
  (E/'verification.json').write_text(json.dumps(results,indent=2))
  print('FOUR_CANARIES_PASSED',digest,flush=True)
 elif action in ['drain','restore']:
  async with d.session() as s:
   for name in NODES:
    n=await s.get(Node,name,with_for_update=True)
    before='ACTIVE' if action=='drain' else 'DRAINING';assert n.mode==before,(name,n.mode)
    n.mode='DRAINING' if action=='drain' else 'ACTIVE'
    canary.audit(s,'node.'+action+'.single2',name,{'mode':before},{'mode':n.mode})
   await s.commit()
  if action=='drain':
   for _ in range(240):
    ready=True
    async with d.session() as s:
     for name in NODES:
      n=await s.get(Node,name)
      j=await s.scalar(select(func.count(Job.id)).where(Job.node_id==name,Job.status.not_in(canary.TERMINAL)))
      a=await s.scalar(select(func.sum(AssetWorker.current_jobs)).where(AssetWorker.node_id==name))
      async with httpx.AsyncClient(timeout=15) as c:
       r=await c.get(n.base_url+'/queue');r.raise_for_status();q=r.json()
      ready &= not j and not a and not q['queue_running'] and not q['queue_pending']
    if ready:break
    await asyncio.sleep(5)
   else:raise RuntimeError('Drain timeout; do not modify UI')
  print(action.upper()+'_DONE',flush=True)
 elif action=='publish':
  reports=json.loads((E/'verification.json').read_text())
  assert len(reports)==4 and all(r['status']=='PASSED' and r['template_sha256']==digest for r in reports)
  runtime=(ROOT/'runtime5070.json').read_bytes()
  async with d.session() as s:
   old=await s.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==KEY,WorkflowVersion.version==OLD).with_for_update())
   new=await s.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==KEY,WorkflowVersion.version==m.version).with_for_update())
   assert old.enabled and not new.enabled and new.template_sha256==digest
   n=await s.get(Node,'worker-5070ti-01',with_for_update=True)
   labels=copy.deepcopy(n.labels);previous=copy.deepcopy(labels['validated_vram_profiles'][KEY])
   labels['validated_vram_profiles'][KEY]={'status':'PASSED','node_id':n.id,'gpu_uuid':labels['gpu_uuid'],'min_vram_mb':16000,'version':m.version,'template_sha256':digest,'evidence_sha256':hashlib.sha256((E/(n.id+'.json')).read_bytes()).hexdigest(),'runtime_profile_sha256':hashlib.sha256(runtime).hexdigest()}
   n.labels=labels;await s.flush()
   compat=await refresh_compatibility(s,new)
   assert {x['node_id'] for x in compat if x['compatible']}==set(NODES),compat
   pending=list(await s.scalars(select(Job).where(Job.workflow_key==KEY,Job.workflow_version==OLD,Job.status.not_in(canary.TERMINAL))))
   old.enabled=bool(pending);new.enabled=True
   canary.audit(s,'workflow.publish.single2',KEY,{'version':OLD,'profile':previous},{'version':m.version,'sha256':digest,'legacy_pending':[j.id for j in pending]})
   await s.commit()
  (E/'runtime5070.json').write_bytes(runtime)
  (E/'release.json').write_text(json.dumps({'version':m.version,'sha256':digest,'legacy_pending':[j.id for j in pending]},indent=2))
  print('PUBLISHED',m.version,'legacy_pending',len(pending),flush=True)
 else:raise ValueError(action)
 await d.close()
asyncio.run(main())
