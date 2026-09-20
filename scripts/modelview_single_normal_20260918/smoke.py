import concurrent.futures
import hashlib
import io
import json
import ssl
import time
import uuid
from pathlib import Path
import httpx
from PIL import Image

ROOT=Path('/tmp/modelview-single-normal-20260918')
CTX=ssl.create_default_context(cafile='/certs/lan-ca.crt')
def test(key):
    fields=['image','material_image','normal_image']
    if key.endswith('inpaint'):fields.insert(2,'mask')
    files={field:(field+'.png',(ROOT/'input'/f'{field}-canary.png').read_bytes(),'image/png') for field in fields}
    request_key='normal-rollout-'+str(uuid.uuid4())
    with httpx.Client(base_url='https://10.3.34.11',verify=CTX,timeout=2700,trust_env=False) as client:
        url='/api/v1/services/'+key
        missing={k:v for k,v in files.items() if k!='normal_image'}
        invalid=client.post(url,files=missing)
        assert invalid.status_code==422,(invalid.status_code,invalid.text[:1000])
        assert any(e['loc'][-1]=='normal_image' for e in invalid.json()['detail'])
        start=time.monotonic()
        result=client.post(url,files=files,headers={'Idempotency-Key':request_key})
        assert result.status_code==200,(result.status_code,result.text[:1000])
        assert result.headers['content-type']=='image/png'
        assert Image.open(io.BytesIO(result.content)).size==(2048,2048)
        sha=hashlib.sha256(result.content).hexdigest()
        assert sha==result.headers['x-artifact-sha256']
        report={'workflow':key,'job_id':result.headers['x-job-id'],'sha256':sha,'seconds':round(time.monotonic()-start,2),'missing_normal':422,'request_key':request_key}
        (ROOT/f'{key}-https.png').write_bytes(result.content)
        retry=client.post(url,files=files,headers={'Idempotency-Key':request_key})
        assert retry.status_code==200 and retry.headers['x-job-id']==report['job_id'] and retry.content==result.content
        report['idempotent_retry']='PASSED'
        (ROOT/f'{key}-https.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)
        return report
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    reports=list(pool.map(test,['modelview-single-view','modelview-single-view-inpaint']))
(ROOT/'https-verification.json').write_text(json.dumps(reports,indent=2))
