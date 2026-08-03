# Li3D UV 与自动重拓扑 API 对接文档 V5

> 最新对外业务合同统一使用 Asset V4 命名，见
> `81_2026-08-03_ASSET_V4_UV_RETOPOLOGY_LATEST_HANDOFF.md`。本文保留为历史技术记录；当前自动重拓扑
> 只接受 BLEND、生产使用 advisory，版本和参数范围以 81 号文档为准。

发布日期：2026-07-29
统一入口：`https://10.3.34.11`
控制面版本：`GPU Control Asset/Web 1.5.4`
适用对象：Li3D 资产客户端、动画管家及公司局域网调用方

> 1.5.5 候选新增服务端 `RETOPOLOGY_QA_ENFORCEMENT=strict|advisory`
> 开关。默认仍为 `strict`；只有运维显式启用 `advisory` 时，重拓扑生成任务的
> 几何质量门才降级为告警。该开关不是客户端参数，不修改外部 Skill 或算法。

## 1. 当前生产合同

- UV 与重拓扑均为异步资产任务，和 ComfyUI GPU 推理队列完全隔离。
- 公司局域网来源默认无需 API Key、无需配置 IP 白名单；系统按 TCP 真实来源 IP 自动建档、限流、审计和隔离任务。
- 正式调用必须信任 `GPU_CONTROL_LAN_CA.crt`；`-k/--insecure` 只能排障。
- 每次业务尝试必须发送稳定且唯一的 `Idempotency-Key`；网络重试复用原值。
- 服务端始终执行完整 QA 并保留原始报告。默认 `strict` 模式下，全部门禁通过后直接原子交付；不再进入人工复核状态，也没有管理员批准/驳回步骤。
- 重拓扑生成可由运维临时切到 `advisory`：几何质量阈值未通过时仍交付候选，并在 `options.qa_warning`、SSE 事件和 QA 制品中保留告警。UV 与独立重拓扑审计不受此开关影响。
- 制品缺失/空文件、JSON 或 manifest 不合法、任务/输入/对象身份不一致、源对象指纹变化、非法预览图等完整性与源保护错误始终硬拒绝，`advisory` 不能绕过。
- 输入文件只读，所有输出写入独立 Job 目录，不覆盖用户源文件。

## 2. 连接与通用状态接口

```bash
BASE_URL='https://10.3.34.11'
CA_FILE='/absolute/path/GPU_CONTROL_LAN_CA.crt'
```

提交成功返回 HTTP `202`：

```json
{
  "job_id": "UUID",
  "status": "QUEUED",
  "status_url": "/api/v1/assets/jobs/UUID",
  "events_url": "/api/v1/assets/jobs/UUID/events",
  "cancel_url": "/api/v1/assets/jobs/UUID/cancel",
  "timing": {"queue_position": 1, "estimated_start_seconds": 0}
}
```

通用接口：

```text
GET  /api/v1/assets/jobs/{job_id}          轮询最终事实
GET  /api/v1/assets/jobs/{job_id}/events   SSE 实时进度
POST /api/v1/assets/jobs/{job_id}/cancel   调用方显式取消
```

终态只有 `SUCCEEDED / FAILED / CANCELLED`。终态后 `elapsed_seconds` 冻结；SSE 断线不会取消任务，轮询结果始终是最终事实。

## 3. PBR UV API

### 3.1 提交

```text
POST /api/v1/assets/uv/process
Content-Type: multipart/form-data
```

| Form 字段 | 必填 | 说明 |
|---|---:|---|
| `asset` | 是 | `.fbx/.obj/.glb/.gltf/.blend` |
| `metadata` | 是 | JSON 字符串 |

完整 cURL：

```bash
curl --fail-with-body --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/uv/process" \
  -H 'Idempotency-Key: li3d-chair-uv-attempt-001' \
  -H 'X-Request-ID: li3d-chair-uv-001' \
  -F 'asset=@chair.fbx;type=application/octet-stream' \
  -F 'metadata={"external_asset_id":"li3d:chair:uv:001","options":{"hidden_axis":"auto","hard_edge_angle_degrees":75,"resolution":2048,"padding_px":10,"texel_density_mode":"uniform","qa_profile":"pbr-v1"}}'
```

参数边界：

- `resolution`：`1024 / 2048 / 4096 / 8192`；
- `padding_px`：`2～128`；
- `hard_edge_angle_degrees`：`1～179`；
- `hidden_axis`：`auto / x+ / x- / y+ / y- / z+ / z-`；
- `texel_density_mode` 固定为 `uniform`；
- `qa_profile` 固定为 `pbr-v1`。

### 3.2 自动修复与质量门

服务端执行切缝、展开、统一 Texel Density、排版以及可恢复退化 UV 修复，然后依次执行：

1. Blender 原工程 UV QA；
2. 导出 FBX；
3. 重新导入导出的 FBX；
4. FBX 回读 UV QA。

退化 UV 面、翻转、越界、非法重叠、硬边未切开或 FBX 回读丢 UV 等硬错误在自动修复后仍存在时，任务返回 `FAILED`，不会发布半套结果。

### 3.3 固定五件套

只有五件套全部通过后才会 `SUCCEEDED`：

1. `<stem>_PBR_UV.blend`
2. `<stem>_PBR_UV.fbx`
3. `<stem>_PBR_UV_report.json`
4. `<stem>_PBR_UV_QA.json`
5. `<stem>_PBR_UV_FBX_QA.json`

## 4. 自动重拓扑 API

### 4.1 输入合同

```text
POST /api/v1/assets/retopology/process
Content-Type: multipart/form-data
```

| Form 字段 | 必填 | 说明 |
|---|---:|---|
| `project` | 是 | 一个包含高模、参考低模和当前低模的 `.blend` |
| `metadata` | 是 | 对象名、目标面数、算法约束、参考图声明和用户要求 |
| `reference_images` | 否 | 0～32 张；文件名必须与 `metadata.reference_views` 一一对应 |

高模决定最终形状和轮廓；参考低模提供布线与面数参考；当前低模是待修复对象。三者均不得覆盖。

### 4.2 推荐 cURL

```bash
curl --fail-with-body --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/retopology/process" \
  -H 'Idempotency-Key: li3d-crate-retopo-attempt-001' \
  -H 'X-Request-ID: li3d-crate-retopo-001' \
  -F 'project=@crate.blend;type=application/octet-stream' \
  -F 'reference_images=@crate_front.png;type=image/png' \
  -F 'reference_images=@crate_side.png;type=image/png' \
  -F 'metadata={"external_asset_id":"li3d:crate:retopo:001","options":{"high_object":"crate_high","reference_object":"crate_reference_low","low_object":"crate_current_low","generated_low_object":"crate_generated_v001","algorithm":"agent","topology_style":"mixed","topology_mode":"mixed","target_faces":1200,"planar_reduction":true,"planar_angle_threshold":5.0,"preserve_sharp":true,"preserve_boundary":true,"preserve_hard_edges":true,"preserve_components":true,"allow_triangles":true,"allow_ngons":false,"render_resolution":512,"max_repair_rounds":2,"require_closed":false},"reference_views":[{"filename":"crate_front.png","view":"front"},{"filename":"crate_side.png","view":"side"}],"user_request":"平面自动降面；曲面、孔洞、硬边及组件边界保留必要密度；四边面为主，允许受控三角面，禁止 N-gon。"}'
```

当前生产参数：

- `target_faces`：最小 `50`；它是期望值，不允许为了精确命中而破坏轮廓或丢组件；
- `algorithm`：`agent / quadriflow / cleanup_existing`；推荐使用 `agent`；
- `topology_style`：`mixed / quad_dominant / preserve_existing`，默认 `mixed`；
- `topology_mode`：`mixed / quad_dominant`，默认 `mixed`；
- `planar_reduction=true`：只在识别出的近似平面区域溶解冗余内部边；
- `planar_angle_threshold`：平面判定角度，默认 `5.0` 度；
- `preserve_hard_edges / preserve_components`：生产默认都为 `true`；
- `allow_triangles=true / allow_ngons=false`：默认允许受控三角面补充，但禁止 N-gon；
- `max_repair_rounds`：`0～2`；
- `reference_images`：最多 32 张，支持 `front/side/top/perspective/detail/other`。

### 4.3 自动算法与 RetopoFlow 边界

- 三台 Asset Worker 均安装 RetopoFlow，并通过真实 Blender operator 健康探针。
- RetopoFlow 本身是交互式人工建模工具，不伪装成无人值守算法；自动 API 使用可审计的 Agent 决策、`cleanup_existing` 或适用时的 QuadriFlow。
- 多组件、开放边界或非流形高模不会盲目使用不适用的 QuadriFlow；Worker 会确定性回退并把原因写入 manifest。
- 平面区域允许大幅合并冗余边；曲面、轮廓、孔洞、硬边和组件边界必须保留必要密度。

### 4.4 质量评估与可回滚交付策略

以下质量项始终测量并写入 process report、final audit、manifest 和事件：

- `audit_passed=true`；
- `topology_goal_met=true`；
- `automatic_final_promotion_allowed=true`；
- 所有重要组件均保留，不能丢失、错误合并或错位；
- 三轴尺寸相对误差、包围盒中心偏移、正/侧/顶/透视轮廓均在生产阈值内；
- 不存在自交、破面、非流形、游离几何、零面积面、错误法线或 N-gon；
- 报告真实返回 face/triangle/quad/N-gon 数量，不用三角面数冒充目标面数；
- 四边面为主、三角面只用于平面隐藏区/收口/过渡，不允许扭曲四边面。

服务端策略：

| `RETOPOLOGY_QA_ENFORCEMENT` | 质量项未通过时 | 完整性或源保护失败时 |
|---|---|---|
| `strict`（默认） | `FAILED`，候选作为诊断制品可下载 | 硬拒绝 |
| `advisory` | `SUCCEEDED`，正常交付 BLEND/FBX，同时携带 QA 告警 | 硬拒绝 |

`strict` 模式失败返回：

```json
{
  "status": "FAILED",
  "error": {
    "code": "RETOPOLOGY_QUALITY_GATE_FAILED",
    "message": "候选未满足拓扑目标或硬性 QA；不可交付"
  }
}
```

`advisory` 模式不会伪造 QA 通过。任务成功交付，但状态中的原始判断和失败项仍然存在：

```json
{
  "status": "SUCCEEDED",
  "delivery_ready": true,
  "stage_message": "候选已生成并交付；严格 QA 未通过，告警与完整报告已保留",
  "options": {
    "qa_warning": {
      "code": "RETOPOLOGY_QUALITY_GATE_WARNING",
      "enforcement": "advisory",
      "audit_passed": false,
      "topology_goal_met": false,
      "automatic_final_promotion_allowed": false,
      "failures": ["SIGNED_AUDIT_FAILED", "NGONS=1"]
    }
  }
}
```

对应终态 SSE 事件的 `details.event` 为
`asset.succeeded_with_warnings`，并包含 `quality_gate_passed=false`、
`quality_failures` 和 `warning_code=RETOPOLOGY_QUALITY_GATE_WARNING`。恢复严格门只需由
运维将环境变量改回 `strict` 并安全滚动 Asset API；Worker、外部 Skill 和算法无需修改。

### 4.5 交付内容

普通客户端只需要显示并下载最终 BLEND/FBX。服务端在 `strict` 通过或 `advisory` 接受交付后，
把 Worker 上传的不可变候选字节发布为以下正式 artifact 合同：

- `kind=blend`、`filename=retopology_final.blend`；
- `kind=fbx`、`filename=retopology_final.fbx`。

底层文件 SHA-256 不因发布别名改变。只有 `strict` 失败的诊断任务继续返回
`candidate_blend/candidate_fbx`，客户端不得把诊断候选当作正式交付。调度中心同时保留 process
report、baseline/final audit、manifest、Agent prompt/plan/events 和高模/参考低模/结果的正侧顶透视
对照图，供审计和故障定位。

## 5. Python 通用提交、轮询和 SHA 校验

```python
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urljoin
import requests

BASE = "https://10.3.34.11"
CA = "GPU_CONTROL_LAN_CA.crt"

metadata = {
    "external_asset_id": "li3d:chair:uv:001",
    "options": {
        "hidden_axis": "auto",
        "hard_edge_angle_degrees": 75,
        "resolution": 2048,
        "padding_px": 10,
        "texel_density_mode": "uniform",
        "qa_profile": "pbr-v1",
    },
}
with Path("chair.fbx").open("rb") as source:
    response = requests.post(
        f"{BASE}/api/v1/assets/uv/process",
        headers={"Idempotency-Key": "li3d-chair-uv-attempt-001"},
        files={"asset": ("chair.fbx", source, "application/octet-stream")},
        data={"metadata": json.dumps(metadata, ensure_ascii=False)},
        verify=CA,
        timeout=(10, 300),
    )
response.raise_for_status()
job_id = response.json()["job_id"]

while True:
    response = requests.get(f"{BASE}/api/v1/assets/jobs/{job_id}", verify=CA, timeout=30)
    response.raise_for_status()
    job = response.json()
    print(job["status"], job["progress"], job["stage"], job["stage_message"])
    if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        break
    time.sleep(3)

if job["status"] != "SUCCEEDED":
    raise RuntimeError(job.get("error"))

output = Path("asset-result")
output.mkdir(exist_ok=True)
for artifact in job["artifacts"]:
    result = requests.get(urljoin(BASE, artifact["download_url"]), verify=CA, timeout=300)
    result.raise_for_status()
    digest = hashlib.sha256(result.content).hexdigest()
    if digest != artifact["sha256"]:
        raise RuntimeError(f"SHA mismatch: {artifact['filename']}")
    (output / artifact["filename"]).write_bytes(result.content)
```

## 6. 客户端必须遵循

1. 只将 `SUCCEEDED` 视为成功；`95%`、候选生成或诊断制品都不是最终交付。
2. 不实现人工批准/驳回；只把 `SUCCEEDED` 视为可交付。若存在 `options.qa_warning`，界面必须明确显示“已交付但 QA 有告警”，并保留 QA 报告下载入口。
3. 下载后验证 `SHA256(body) == artifact.sha256 == X-Artifact-SHA256`。
4. 网络重试复用原幂等键，不能用新键制造重复任务。
5. 不把 SSE 断线、Worker 临时离线或单步错误解释成用户取消。
6. 普通页面显示简洁错误摘要；完整 Blender/Codex 日志放“高级诊断”。
7. 保存 `job_id / request_id / external_asset_id / input_sha256`，用于全链路追踪。

## 7. 生产验收基线

- 三台 Asset Worker 独立于 GPU 推理槽运行；CPU 资产任务不会占 ComfyUI 队列。
- 三台 Worker 的 Blender、技能脚本和 RetopoFlow 探针已纳入心跳与 WebUI。
- UV 已执行失败样本修复后的并发实测，成功任务全部一次执行并完成五件套原子交付。
- 重拓扑已执行多 Worker、多视图真实任务；服务端保留候选、四视图、审计、Agent 证据与 SHA。
- 生产 1.5.4 仍使用严格质量门。1.5.5 候选提供受控、可回滚的 `advisory` 模式，供 Skill/算法迭代期先交付候选；它不改变 QA 测量结果，也不放松完整性和源保护。
