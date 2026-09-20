"""Record non-secret runtime facts for this acceptance; no changes on the GPU host."""
import json
import subprocess
from pathlib import Path
from urllib.request import urlopen

ssh = ['sudo','-n','ssh','-p','2222','-i','/srv/gpu-control/secrets/ssh/worker-5070ti-01-ed25519',
       '-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
       '-o','UserKnownHostsFile=/srv/gpu-control/secrets/ssh/worker-5070ti-01-known_hosts','gpucontrol@10.3.34.18']
def remote(command):
    return subprocess.check_output([*ssh, command], text=True).strip()

image = remote('docker inspect gpu-control-node-comfyui-1 --format "{{.Image}}"')
command = json.loads(remote('docker inspect gpu-control-node-comfyui-1 --format "{{json .Config.Cmd}}"'))
hashes = remote('docker exec gpu-control-node-comfyui-1 sha256sum /opt/comfyui/user/default/workflows/ModelViewCreator_flux_fill_inpaint.json /opt/modelviewcreator/model/lora/flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors').splitlines()
with urlopen('http://10.3.34.18:8188/system_stats',timeout=15) as response:
    stats = json.load(response)
runtime = {'image_id':image,'command':command,'ui_sha256':hashes[0].split()[0],
           'normal_lora_sha256':hashes[1].split()[0],'system_stats':stats}
assert runtime['ui_sha256'] == '81c7f6b2f427799ee0f185dbf817a29e5d6df5f618de8c31fe6ccf5aabbe0bdf'
assert runtime['normal_lora_sha256'] == 'baa97f297d048330850f0cb8063392678927a34282de961760693f6897983fd2'
Path('/tmp/modelview-5070-normal-20260918/runtime.json').write_text(json.dumps(runtime,sort_keys=True,indent=2))
print('RUNTIME_AND_HASHES_VERIFIED')
