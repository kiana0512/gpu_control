import concurrent.futures
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

ROOT=Path('/tmp/single-view-2step-20260920')
FILES=[
 ('/tmp/single-view-2step-20260920/ui.json','ModelViewCreator_flux_Single-view Generation.json','02ffdd63bde091469604e250e761853181450d94d9a01b6691c486b7c8177fc5'),
]
SSH5070=['-i','/srv/gpu-control/secrets/ssh/worker-5070ti-01-ed25519','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/srv/gpu-control/secrets/ssh/worker-5070ti-01-known_hosts']
NODES=[('control-4090',None,22,'comfyui-4090'),('worker-3090-a','lilithgames@10.3.34.12',22,'gpu-control-node-comfyui-1'),('worker-3090-b','gpucontrol@10.3.34.14',2222,'gpu-control-node-comfyui-1'),('worker-5070ti-01','gpucontrol@10.3.34.18',2222,'gpu-control-node-comfyui-1')]
def run(args):
 return subprocess.check_output(args,text=True).strip()
def sync(node):
 name,host,port,container=node
 ssh=(['sudo','-n','ssh',*SSH5070,'-p',str(port),host] if name=='worker-5070ti-01' else ['ssh','-o','BatchMode=yes','-p',str(port),host]) if host else None
 def command(args):
  return run([*ssh,shlex.join(args)]) if ssh else run(args)
 record={'node':name,'ui':{}}
 for index,(source,base,expected) in enumerate(FILES):
  assert hashlib.sha256(Path(source).read_bytes()).hexdigest()==expected
  staged=source
  if host:
   staged=f'/tmp/single-2step-20260920-{index}.json'
   scp=['sudo','-n','scp',*SSH5070,'-P',str(port)] if name=='worker-5070ti-01' else ['scp','-o','BatchMode=yes','-P',str(port)]
   run([*scp,source,host+':'+staged])
  target='/opt/modelviewcreator/'+base
  command(['sudo','-n','cp','-an',target,target+'.before-single-2step-20260920'])
  command(['sudo','-n','cp',staged,target])
  actual=command(['sudo','-n','docker','exec',container,'sha256sum','/opt/comfyui/user/default/workflows/'+base]).split()[0]
  assert actual==expected,(name,base,actual)
  record['ui'][base]=actual
 if name=='worker-5070ti-01':
  record['runtime']=json.loads(command(['sudo','-n','docker','inspect',container,'--format','{"image":{{json .Image}},"cmd":{{json .Config.Cmd}}}']))
  record['gpu']=command(['sudo','-n','docker','exec',container,'nvidia-smi','--query-gpu=uuid,name,memory.total','--format=csv,noheader'])
  record['models']={}
  for model in ['li3d_000004500.safetensors','flux2_klein_9b_refcontrol_normal.safetensors']:
   record['models'][model]=command(['sha256sum','/opt/modelviewcreator/model/lora/flux-kelin/'+model]).split()[0]
  (ROOT/'runtime5070.json').write_text(json.dumps(record,indent=2))
 print(json.dumps(record),flush=True)
 return record
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 records=list(pool.map(sync,NODES))
(ROOT/'ui-sync.json').write_text(json.dumps(records,indent=2))
