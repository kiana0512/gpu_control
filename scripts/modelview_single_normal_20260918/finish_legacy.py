"""Allow already-queued old-version jobs to finish without changing their graph/input."""
import asyncio,json
from pathlib import Path
from sqlalchemy import select
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.settings import get_settings
from packages.gpu_control_core.models import Job,WorkflowVersion
from canary import audit,TERMINAL

async def main():
 d=Database(get_settings())
 key='modelview-single-view-inpaint'
 version='2026.09.17-li3d4500-single-view-inpaint-2step-r1'
 async with d.session() as s:
  old=await s.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==key,WorkflowVersion.version==version).with_for_update())
  pending=list(await s.scalars(select(Job).where(Job.workflow_key==key,Job.workflow_version==version,Job.status.not_in(TERMINAL))))
  ids=[j.id for j in pending]
  if pending:
   assert not old.enabled
   old.enabled=True
   audit(s,'workflow.legacy_jobs_drain',key,{'enabled':False},{'enabled':True,'pending_job_ids':ids,'reason':'Preserve queued jobs admitted during node drain; new API selects newer enabled version'})
   await s.commit()
   print('TEMPORARILY_ENABLED_FOR_EXISTING_JOBS',ids,flush=True)
 if ids:
  for _ in range(540):
   async with d.session() as s:
    pending=list(await s.scalars(select(Job).where(Job.workflow_key==key,Job.workflow_version==version,Job.status.not_in(TERMINAL))))
    if not pending:
     old=await s.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_key==key,WorkflowVersion.version==version).with_for_update())
     old.enabled=False
     audit(s,'workflow.legacy_jobs_drained',key,{'enabled':True},{'enabled':False,'job_ids':ids})
     await s.commit()
     jobs=list(await s.scalars(select(Job).where(Job.id.in_(ids))))
     result=[{'id':j.id,'status':j.status,'version':j.workflow_version,'node':j.node_id} for j in jobs]
     Path('/srv/gpu-control/jobs/deployment-modelview-single-normal-20260918/legacy-drain.json').write_text(json.dumps(result,indent=2))
     print('LEGACY_DRAIN_COMPLETE',json.dumps(result),flush=True)
     break
   await asyncio.sleep(5)
  else: raise RuntimeError('Legacy jobs still active; old version remains enabled for safe completion')
 await d.close()
asyncio.run(main())
