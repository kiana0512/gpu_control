import asyncio
import copy
import json
import tempfile
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import Node
from packages.gpu_control_core.settings import Settings
from packages.gpu_control_core.workflow import validated_node_vram_requirement
from scripts.bootstrap_nodes import apply_inventory

profile = {'status':'PASSED','node_id':'worker-audit-a','version':'v1','template_sha256':'a'*64,'gpu_uuid':'GPU-12345678-1234-1234-1234-123456789abc','evidence_sha256':'b'*64,'runtime_profile_sha256':'c'*64,'min_vram_mb':16000}
labels = {'gpu_uuid':profile['gpu_uuid'],'validated_vram_profiles':{'workflow-audit':profile}}
args = {'declared_min_vram_mb':24000,'node_id':profile['node_id'],'workflow_key':'workflow-audit','workflow_version':'v1','template_sha256':profile['template_sha256']}
initial = validated_node_vram_requirement(reported_labels=labels,**args)
changed = copy.deepcopy(labels)
changed['runtime_profile_sha256']='d'*64
changed['runtime_image_id']='sha256:'+'e'*64
changed['runtime_flags']=['changed-offload-policy']
changed_result = validated_node_vram_requirement(reported_labels=changed,**args)

async def duplicate_registration():
    with tempfile.TemporaryDirectory(prefix='gpucontrol-audit-') as tmp:
        db = Database(Settings(_env_file=None,database_url=f'sqlite+aiosqlite:///{Path(tmp)/"audit.db"}'))
        try:
            async with db.engine.begin() as conn:
                await conn.run_sync(Node.__table__.create)
            item = {'id':'worker-audit-a','mode':'DISABLED','pool':'PRIMARY','host':'10.3.34.18','hostname':'audit-host','mac':'12:34:56:78:90:ab','gpu':'audit-gpu','gpu_uuid':profile['gpu_uuid']}
            async with db.session() as first, db.session() as second:
                await apply_inventory(first,[item],add_only=True)
                await apply_inventory(second,[{**item,'id':'worker-audit-b'}],add_only=True)
                await first.commit()
                try:
                    await second.commit()
                except IntegrityError:
                    await second.rollback()
            async with db.session() as session:
                nodes=list((await session.scalars(select(Node))).all())
                return {'registered_rows':len(nodes),'host_unique_count':len({n.labels['host'] for n in nodes}),'gpu_uuid_unique_count':len({n.labels['gpu_uuid'] for n in nodes}),'modes':[n.mode for n in nodes]}
        finally:
            await db.close()

print(json.dumps({'runtime_change':{'initial_minimum':initial,'changed_runtime_minimum':changed_result},'interleaved_add_only':asyncio.run(duplicate_registration()),'scope':'synthetic inputs and isolated temporary SQLite only; no production changes'},indent=2))
