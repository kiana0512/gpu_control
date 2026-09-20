import asyncio,json
from pathlib import Path
from sqlalchemy import select
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.settings import get_settings
from packages.gpu_control_core.models import Node,Job,WorkflowVersion,WorkflowNodeCompatibility
async def main():
 d=Database(get_settings())
 async with d.session() as s:
  nodes=await s.scalars(select(Node))
  print('NODES',json.dumps([{'id':n.id,'mode':n.mode,'health':n.health,'jobs':n.current_jobs,'single_normal':n.labels.get('modelview_normal_single_flows_20260918')} for n in nodes]))
  flows=await s.scalars(select(WorkflowVersion).where(WorkflowVersion.enabled.is_(True),WorkflowVersion.workflow_key.in_(['modelview-inpaint','modelview-single-view','modelview-single-view-inpaint'])))
  for f in flows:
   c=await s.scalars(select(WorkflowNodeCompatibility).where(WorkflowNodeCompatibility.workflow_version_id==f.id))
   print('WORKFLOW',json.dumps({'key':f.workflow_key,'version':f.version,'sha256':f.template_sha256,'normal_binding':f.bindings.get('normal_image_filename'),'nodes':{v.node_id:v.compatible for v in c}}))
  jobs=await s.scalars(select(Job).where(Job.status.not_in(['SUCCEEDED','FAILED','CANCELED','CANCELLED','TIMED_OUT'])))
  print('ACTIVE_JOBS',json.dumps([{'id':j.id,'workflow':j.workflow_key,'status':j.status,'node':j.node_id} for j in jobs]))
  p=Path('/tmp/modelview-single-normal-20260918/https-verification.json')
  if p.exists():
   result=[]
   for r in json.loads(p.read_text()):
    j=await s.get(Job,r['job_id'])
    assert j.status=='SUCCEEDED' and j.workflow_version.startswith('2026.09.18-refcontrol-normal-single-view')
    prompt_files=list(Path(j.job_dir).glob('**/*prompt*.json'))
    result.append({'id':j.id,'node':j.node_id,'status':j.status,'version':j.workflow_version,'seed':j.parameters.get('noise_seed'),'normal_image':j.parameters.get('normal_image_filename'),'job_dir':j.job_dir,'prompt_files':[str(x) for x in prompt_files]})
   assert len({j['seed'] for j in result})==2 and all(j['normal_image'] for j in result)
   print('HTTPS_JOBS',json.dumps(result))
   Path('/srv/gpu-control/jobs/deployment-modelview-single-normal-20260918/https-jobs.json').write_text(json.dumps(result,indent=2))
 await d.close()
asyncio.run(main())
