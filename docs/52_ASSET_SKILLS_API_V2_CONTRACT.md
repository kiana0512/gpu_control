# 统一调度中心 Blender Asset API V2 对接契约

> **历史文档：** 当前接口、自动候选、多视角、SSE、审核门禁和真实测试以
> [55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md](55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md)
> 为准。

文档日期：2026-07-28
接口状态：`SOURCE IMPLEMENTED / TESTED / NOT DEPLOYED`
生产入口：`https://10.3.34.11`（部署后生效）
适用功能：PBR UV 展开、拓扑三模型审计

> 当前生产后端仍有任务。本次只完成源码、隔离测试与文档，没有重启、迁移或部署生产容器。

## 1. 能力边界

### 1.1 PBR UV

输入一个模型文件，支持：

- `.fbx`
- `.obj`
- `.glb`
- `.gltf`
- `.blend`

可选参数：

| 参数 | 默认值 | 合法值/范围 | 说明 |
|---|---:|---|---|
| `hidden_axis` | `auto` | `x+ x- y+ y- z+ z- auto` | `auto` 当前确定性映射为 `y+` |
| `hard_edge_angle_degrees` | `75` | `1..179` | 硬边角度 |
| `resolution` | `2048` | `1024/2048/4096/8192` | 目标贴图分辨率 |
| `padding_px` | `10` | `2..128` | UV 岛间距 |
| `texel_density_mode` | `uniform` | `uniform` | 固定均匀密度 |
| `qa_profile` | `pbr-v1` | `pbr-v1` | 固定 QA 规则 |

Skill 只负责 UV，不生成 Base Color、Normal、Roughness 等 PBR 贴图，也不覆盖原文件。

### 1.2 拓扑

当前可自动执行的是“三模型拓扑审计”，不是自动生成新低模。输入必须是一个 `.blend` 工程，且工程内
包含：

- 高模对象：最终形状权威；
- 参考低模对象：布线风格与面数参考；
- 当前待检查低模对象：本次被审计对象。

输出数值审计与版本清单后，父任务固定进入 `WAITING_REVIEW`。这是有意设计的正确状态：轮廓、
自相交、UV、烘焙质量和四视图一致性仍需人工复核。当前版本不会伪造“已自动拓扑完成”。

## 2. 身份认证

支持二选一：

1. `X-API-Key: gpc_<prefix>_<secret>`；
2. 不传 Key，由控制面根据真实来源 IP 匹配唯一已启用客户。

生产调用必须校验 LAN CA，不建议长期使用 `curl -k`：

```bash
CA_FILE=/path/to/GPU_CONTROL_LAN_CA.crt
BASE_URL=https://10.3.34.11
API_KEY='gpc_xxx_xxx'
```

## 3. UV V2 提交

### 3.1 cURL

```bash
MODEL_FILE=/absolute/path/chair.fbx
EXTERNAL_ID='asset:chair:uv:20260728:001'
IDEMPOTENCY_KEY='asset:chair:uv:20260728:001'

curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/uv/process" \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "X-Request-ID: li3d-chair-uv-001" \
  -F "asset=@${MODEL_FILE};type=application/octet-stream" \
  -F 'metadata={"external_asset_id":"asset:chair:uv:20260728:001","options":{"hidden_axis":"y+","hard_edge_angle_degrees":75,"resolution":2048,"padding_px":10,"texel_density_mode":"uniform","qa_profile":"pbr-v1"}};type=application/json'
```

成功创建返回 HTTP `202`；同一幂等键、相同输入和参数重复提交返回原任务 HTTP `200`。同一幂等键但
内容或参数不同返回 `409 IDEMPOTENCY_CONFLICT`。

### 3.2 Python

```python
import json
from pathlib import Path

import httpx

base_url = "https://10.3.34.11"
api_key = "gpc_xxx_xxx"
model = Path("/absolute/path/chair.fbx")
metadata = {
    "external_asset_id": "asset:chair:uv:20260728:001",
    "options": {
        "hidden_axis": "y+",
        "hard_edge_angle_degrees": 75,
        "resolution": 2048,
        "padding_px": 10,
        "texel_density_mode": "uniform",
        "qa_profile": "pbr-v1",
    },
}

with model.open("rb") as handle, httpx.Client(
    base_url=base_url,
    verify="/path/to/GPU_CONTROL_LAN_CA.crt",
    timeout=httpx.Timeout(30, read=3600),
) as client:
    response = client.post(
        "/api/v1/assets/uv/process",
        headers={
            "X-API-Key": api_key,
            "Idempotency-Key": metadata["external_asset_id"],
            "X-Request-ID": "li3d-chair-uv-001",
        },
        data={"metadata": json.dumps(metadata, ensure_ascii=False)},
        files={"asset": (model.name, handle, "application/octet-stream")},
    )
    response.raise_for_status()
    created = response.json()
    print(created["job_id"], created["status"])
```

## 4. UV 最终输出合同

如果输入名为 `chair.fbx`，成功任务必须且只能原子发布以下五项：

```text
chair_PBR_UV.blend
chair_PBR_UV.fbx
chair_PBR_UV_report.json
chair_PBR_UV_QA.json
chair_PBR_UV_FBX_QA.json
```

如果输入名为 `chair.source.fbx`，文件名前缀完整保留为 `chair.source`。控制面不采用 Worker 上传时声称的
任意文件名，而是依据原始输入名重建并校验最终文件名。

发布门禁：

1. 五个文件全部非空；
2. BLEND QA JSON 可解析；
3. FBX 回读 QA JSON 可解析；
4. 两份 QA 均为 `passed=true`；
5. 两份 `hard_failures` 均为空；
6. 输出目录此前不存在；
7. 五项在同一原子发布动作后才对客户可见。

任何一步失败，任务不得变成 `SUCCEEDED`，也不会把中间产物作为最终结果返回。

## 5. 拓扑审计提交

### 5.1 输入工程约定

一个 `.blend` 工程包含三套唯一可识别的 Mesh Object。例如：

```text
crate_high
crate_reference_low
crate_current_low
```

### 5.2 cURL

```bash
PROJECT_FILE=/absolute/path/crate_retopology_review.blend
EXTERNAL_ID='asset:crate:retopo:audit:20260728:001'

curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/retopology/audit" \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: $EXTERNAL_ID" \
  -H "X-Request-ID: li3d-crate-retopo-001" \
  -F "project=@${PROJECT_FILE};type=application/octet-stream" \
  -F 'metadata={"external_asset_id":"asset:crate:retopo:audit:20260728:001","options":{"high_object":"crate_high","reference_object":"crate_reference_low","low_object":"crate_current_low","require_closed":false}};type=application/json'
```

当前返回制品：

```text
retopology_audit.json
retopology_manifest.json
```

成功执行后的业务状态是 `WAITING_REVIEW`，进度 `95`。`audit_passed=true` 只代表确定性数值检查通过，
不代表最终新低模已经被批准。

## 6. 查询、排队反馈和取消

### 6.1 查询

```bash
JOB_ID='<create response job_id>'
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -H "X-API-Key: $API_KEY" \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID"
```

主要字段：

```json
{
  "job_id": "...",
  "external_asset_id": "...",
  "job_type": "UV_PROCESS_V2",
  "status": "QUEUED",
  "progress": 0,
  "worker_id": null,
  "attempt_count": 0,
  "input_sha256": "...",
  "error": null,
  "artifacts": []
}
```

状态语义：

| 状态 | 客户动作 |
|---|---|
| `QUEUED` | 正常排队，继续轮询，不要重复生成新 external ID |
| `CLAIMED/RUNNING` | Worker 已领取/执行，继续轮询 |
| `WAITING_REVIEW` | 拓扑审计已完成，下载审计结果并进入人工复核 |
| `SUCCEEDED` | 下载全部 artifacts 并逐项校验 SHA-256 |
| `FAILED` | 读取 `error.code/message`，按业务策略处理 |
| `CANCELLED` | 终止后续处理 |

建议轮询间隔 2–5 秒，并加入随机抖动。不要通过并发重复 POST 代替状态查询。

### 6.2 取消

```bash
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST \
  -H "X-API-Key: $API_KEY" \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID/cancel"
```

## 7. 下载与 Hash 校验

只有 `SUCCEEDED` 或 `WAITING_REVIEW` 才返回 `artifacts`。每项包含 `download_url` 和 `sha256`。

```bash
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -H "X-API-Key: $API_KEY" \
  -o chair_PBR_UV.fbx \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID/artifacts/$ARTIFACT_ID"

sha256sum chair_PBR_UV.fbx
```

下载响应同时返回 `X-Artifact-SHA256`，调用方必须验证它、JSON 中的 SHA 和本地文件 SHA 三者一致。

## 8. Skill 与运行时一致性

固定版本：

```text
Blender: 5.1.2
Worker Skill version: asset-skills-2026.07.28
blender-pbr-uv/unwrap_fbx.py:
  ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758
blender-pbr-uv/qa_uv.py:
  bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d
blender-retopology-compare-iterate/audit_pair.py:
  a6575902cfacd7b8106f9c887069d717a880d870fc48a6295431cdcf717a9dc4
```

Worker 每次任务执行前都重新核对脚本 Hash；不一致则拒绝执行。Blender 使用
`--factory-startup --disable-autoexec`，且 Asset Worker 设置 `CUDA_VISIBLE_DEVICES` 为空，不占用 GPU
推理任务槽位。

主控预检：

```bash
cd /opt/gpu-control
scripts/verify_asset_skills.sh
scripts/codex_asset_runtime_preflight.sh
```

Codex CLI 负责分析、计划和结构化解释；实际模型处理只能由固定 Hash 的确定性 Blender Worker 执行。

## 9. 统一 WebUI

源码中的 `/asset-processing` 已改为读取 `GET /admin/asset-processing` 的真实数据，展示：

- 真实 Worker 心跳、Blender/Skill 版本与 CPU 槽位；
- UV/拓扑任务筛选；
- 父任务状态、Worker、进度；
- 每个父任务详情内的输入 SHA、交付物、大小、逐项 SHA 和错误；
- 拓扑 `WAITING_REVIEW` 状态。
- 运维管理员可通过 `POST /admin/asset-jobs/{job_id}/cancel` 取消尚未终态的
  CPU 资产任务；请求体必须包含 `{"reason":"...","confirm":true}`，操作写入
  `audit_logs`。外部调用方仍使用 `POST /api/v1/assets/jobs/{job_id}/cancel`。

页面不再硬编码节点 IP、候选槽位或四件套输出。

## 10. 部署边界

本文件描述的是已经通过隔离契约测试的源码，不代表已部署。生产上线必须等待当前任务清空，并按顺序：

1. 备份数据库；
2. 校验两套 Skill Hash；
3. 构建新 API、Asset API、Web、Blender Worker 镜像；
4. 先启用主控 Asset Worker Canary；
5. 用 Golden Asset 验证 UV 五件套和拓扑 `WAITING_REVIEW`；
6. 再滚动启用其他 CPU Worker；
7. 全程不修改 ImageClip、ModelViewCreator 或 ComfyUI 工作流。
