# 3090-B Substance 3D Baker API 对接使用文档

文档版本：`V1.1`
发布日期：`2026-07-29`
服务地址：`https://10.3.34.11`
提交接口：`POST /api/v1/assets/bake/process`
执行节点：`3090-B / native Windows`（唯一）

## 1. 给调用方的结论

该接口把低模、高模和可选 Cage 提交到统一调度中心，由 3090-B 的 Windows 原生 Adobe Substance 3D Baker CLI 执行，并异步返回可追踪任务。

- 内网调用默认**不需要 API Key**，服务端按 TCP 连接的真实来源 IP 自动登记客户。
- 不设置额外 IP 白名单；`X-API-Key` 仅保留旧客户端兼容能力。
- 提交成功立即返回 `202`、`job_id`、状态 URL 和 SSE URL；调用方不应长连接等待烘焙完成。
- 3090-B 原生 Windows 运行 4 个独立 Baker Worker；聚合容量为 `4`，单 Worker 始终只执行 `1` 个任务。
- 4 个 Worker 共用一个物理 GPU 围栏：首个 Baker 任务暂停 3090-B WSL ComfyUI，最后一个 Baker 任务结束后才恢复并验证 ComfyUI，避免逐任务反复切换。
- 只允许固定 profile 和有界参数，调用方不能传命令行、程序路径或任意 Baker 参数。
- 只有完整性、尺寸、SHA-256、Baker 版本和成功日志全部通过的原子产物才可下载。

## 2. 固定运行环境

| 项目 | 固定值 |
|---|---|
| 主机 | `3090-B / 10.3.34.14 / Windows native` |
| Worker ID | `asset-worker-3090-b-windows-01` ～ `asset-worker-3090-b-windows-04` |
| Adobe CLI | `C:\Program Files\Adobe\Adobe Substance 3D Designer\substance3d_baker.exe` |
| Baker 版本 | `15.1.0`（build `10084`） |
| EXE SHA-256 | `7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D` |
| GPU UUID | `GPU-092a5184-5857-d196-5df2-efa9503368aa` |
| GPU 后端 | `SAL,SoRa` |
| 聚合并发 | `4` 个独立执行槽；每个 Worker 为 `0/1` |

## 3. 支持的 profile

| profile | 必填文件 | 最终贴图 |
|---|---|---|
| `ao-self-v1` | `low_mesh` | `asset_ao.png` |
| `normal-dx-v1` | `low_mesh` + `high_mesh` | `asset_normal_dx.png` |
| `pbr-core-v1` | `low_mesh` + `high_mesh` | `asset_ao.png` + `asset_normal_dx.png` |
| `li3d-pbr-full-v2` | `low_mesh` + `high_mesh` + Base Color/Roughness/Metallic | 10 张最终 PBR/几何贴图 |

`cage_mesh` 对三个 profile 均为可选。当前法线输出为 DirectX 切线空间；不要在客户端把它误标为 OpenGL 法线。

## 4. 请求合同

请求类型为 `multipart/form-data`。

| 位置 | 名称 | 必填 | 说明 |
|---|---|---:|---|
| Form | `low_mesh` | 是 | 低模文件 |
| Form | `high_mesh` | 条件必填 | `normal-dx-v1`、`pbr-core-v1` 必填 |
| Form | `cage_mesh` | 否 | 可选 Cage 文件 |
| Form | `base_color_texture` | 完整 profile 必填 | 高模对应 Base Color |
| Form | `roughness_texture` | 完整 profile 必填 | 高模对应 Roughness |
| Form | `metallic_texture` | 完整 profile 必填 | 高模对应 Metallic |
| Form | `metadata` | 是 | JSON 字符串，结构见下方 |
| Header | `Idempotency-Key` | 是 | 1～128 字符；同一业务重试保持不变 |
| Header | `X-API-Key` | 否 | 默认不需要 |

支持的 mesh 扩展名为 `FBX`、`OBJ` 和单文件二进制 `GLB`；文件名不得包含路径。生产
`substance3d_baker.exe 15.1.0` 已对 GLB 做过原生读取验证，因此 Li3D 原始 GLB 高模可以直接与
拓扑/UV 交付的 FBX 低模配对提交，不会在接口层改写模型坐标、拓扑、UV 或材质。依赖外部
`.bin`/贴图文件的 `.gltf` 以及 Blender 工程 `.blend` 不属于该上传合同。元数据不允许未声明字段：

```json
{
  "external_asset_id": "chair-pbr-bake-001",
  "options": {
    "profile": "pbr-core-v1",
    "resolution": 2048,
    "texture_cache_mb": 32768
  }
}
```

允许值：

- `profile`：`ao-self-v1`、`normal-dx-v1`、`pbr-core-v1`、`li3d-pbr-full-v2`
- `resolution`：`256`、`512`、`1024`、`2048`、`4096`
- `texture_cache_mb`：`8192`、`16384`、`32768`
- `external_asset_id`：调用方稳定资产 ID；同一客户下必须唯一

## 5. cURL 提交示例

### 5.1 AO 自烘焙

```bash
curl --fail-with-body --show-error \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/bake/process' \
  -H 'Idempotency-Key: chair-ao-attempt-1' \
  -F 'low_mesh=@chair_low.fbx;type=application/octet-stream' \
  -F 'metadata={"external_asset_id":"chair-ao-001","options":{"profile":"ao-self-v1","resolution":2048,"texture_cache_mb":32768}}'
```

### 5.2 完整 Li3D PBR 烘焙

```bash
curl --fail-with-body --show-error \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/bake/process' \
  -H 'Idempotency-Key: chair-pbr-full-attempt-1' \
  -F 'low_mesh=@chair_low.fbx;type=application/octet-stream' \
  -F 'high_mesh=@chair_high.fbx;type=application/octet-stream' \
  -F 'cage_mesh=@chair_cage.fbx;type=application/octet-stream' \
  -F 'base_color_texture=@chair_basecolor.png;type=image/png' \
  -F 'roughness_texture=@chair_roughness.png;type=image/png' \
  -F 'metallic_texture=@chair_metallic.png;type=image/png' \
  -F 'metadata={"external_asset_id":"chair-pbr-001","options":{"profile":"li3d-pbr-full-v2","resolution":2048,"texture_cache_mb":32768}}'
```

成功提交返回 `202 Accepted`。同一幂等键和同一内容再次提交返回已有任务；不要因为仍在排队而生成新键重复提交。

## 6. Python 完整示例

```python
import json
import time
from pathlib import Path
from urllib.parse import urljoin
import requests

BASE = "https://10.3.34.11"
CA = "GPU_CONTROL_LAN_CA.crt"

metadata = {
    "external_asset_id": "chair-pbr-001",
    "options": {
        "profile": "li3d-pbr-full-v2",
        "resolution": 2048,
        "texture_cache_mb": 32768,
    },
}

with Path("chair_low.fbx").open("rb") as low, Path("chair_high.fbx").open("rb") as high:
    response = requests.post(
        f"{BASE}/api/v1/assets/bake/process",
        headers={"Idempotency-Key": "chair-pbr-attempt-1"},
        files={
            "low_mesh": ("chair_low.fbx", low, "application/octet-stream"),
            "high_mesh": ("chair_high.fbx", high, "application/octet-stream"),
        },
        data={"metadata": json.dumps(metadata, ensure_ascii=False)},
        verify=CA,
        timeout=(10, 300),
    )

response.raise_for_status()
job = response.json()
job_id = job["job_id"]
print("job_id =", job_id, "queue =", job["timing"]["queue_position"])

while True:
    status_response = requests.get(
        f"{BASE}/api/v1/assets/jobs/{job_id}", verify=CA, timeout=(10, 30)
    )
    status_response.raise_for_status()
    job = status_response.json()
    print(job["status"], job["progress"], job["stage"], job["stage_message"])
    if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        break
    time.sleep(3)

if job["status"] != "SUCCEEDED":
    raise RuntimeError(job.get("error"))

output_dir = Path("baker-result")
output_dir.mkdir(exist_ok=True)
for artifact in job["artifacts"]:
    result = requests.get(urljoin(BASE, artifact["download_url"]), verify=CA, timeout=(10, 300))
    result.raise_for_status()
    if result.headers.get("X-Artifact-SHA256") != artifact["sha256"]:
        raise RuntimeError("server SHA header mismatch")
    (output_dir / artifact["filename"]).write_bytes(result.content)
```

## 7. 状态查询与排队反馈

```bash
curl --cacert GPU_CONTROL_LAN_CA.crt \
  'https://10.3.34.11/api/v1/assets/jobs/JOB_ID'
```

关键字段：

| 字段 | 含义 |
|---|---|
| `status` | `QUEUED`、`RUNNING`、`CANCELLING`、`SUCCEEDED`、`FAILED`、`CANCELLED` |
| `progress` | 0～100 的整体进度 |
| `stage` / `stage_message` | 当前安全阶段及人类可读说明 |
| `timing.queue_position` | 该任务在 Baker 独立队列中的位置 |
| `timing.estimated_start_seconds` | 预计开始等待秒数 |
| `timing.elapsed_seconds` | 已执行秒数；终态后停止增长 |
| `timing.estimated_remaining_seconds` | Worker 最近上报的预计剩余秒数 |
| `worker_id` | 正常为 `asset-worker-3090-b-windows-01` ～ `-04` |
| `delivery_ready` | 仅 `SUCCEEDED` 为 `true` |
| `artifacts` | 终态成功后出现的原子交付物清单 |

队列与 GPU 推理队列隔离。聚合调度容量是 4；同一物理 GPU 上并发的 4 个 Baker 进程共享围栏状态。WebUI 把四个实例聚合成一行显示 `当前占用/4`，不会把旧单实例心跳重复计数。

## 8. SSE 实时进度

```bash
curl -N --cacert GPU_CONTROL_LAN_CA.crt \
  'https://10.3.34.11/api/v1/assets/jobs/JOB_ID/events'
```

事件名为 `asset-progress`，数据包含 `status`、`stage`、`progress`、`message`、`estimated_remaining_seconds`、`created_at`。断线重连时把最后收到的事件序号放进 `Last-Event-ID` 请求头，服务端会从后续事件继续发送。轮询状态是最终事实，SSE 只用于低延迟 UI 展示。

## 9. 取消

```bash
curl --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/jobs/JOB_ID/cancel'
```

- `QUEUED`：立即变为 `CANCELLED`。
- 已执行：变为 `CANCELLING`，Worker 在安全点停止并恢复 WSL ComfyUI。
- 终态任务：幂等返回当前状态。

## 10. 结果与 SHA-256

成功任务的 `artifacts` 每项包含：`id`、`kind`、`filename`、`content_type`、`size_bytes`、`sha256`、`download_url`。下载响应头还会返回 `X-Artifact-SHA256`。调用方应计算本地 SHA-256 并与 JSON/响应头双重核对。

可能的最终文件：

- `asset_base_color.png`
- `asset_roughness.png`
- `asset_metallic.png`
- `asset_ao.png`
- `asset_normal_dx.png`
- `asset_normal_gl.png`
- `asset_world_normal.png`
- `asset_curvature.png`
- `asset_thickness.png`
- `asset_position.png`
- `baker_result.json`
- `baker.log`

服务端先在 staging 中验证全部合同，再原子发布；缺图、尺寸错误、日志缺少成功标记、EXE SHA 不符或 JSON 不一致时不会发布半套结果。

## 11. 错误与处理

| HTTP/状态 | 错误码 | 说明与处理 |
|---|---|---|
| 422 | `BAKE_INPUT_INVALID` | profile/文件组合、扩展名或 metadata 不合法 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一幂等键对应了不同请求 |
| 409 | `EXTERNAL_ASSET_CONFLICT` | 同一客户重复使用 `external_asset_id` |
| `FAILED` | `SUBSTANCE_EXECUTION_FAILED` | Windows Baker 执行失败；管理员查看原生日志 |
| `FAILED` | `SUBSTANCE_RESULT_INVALID` | 输出完整性/尺寸/SHA/版本合同未通过 |
| 404 | `ASSET_JOB_NOT_FOUND` | 当前来源 IP 无权查看该任务，或 ID 不存在 |

失败任务不会自动无限重试，也不会把部分 PNG 返回给用户。修复基础设施后，调用方使用新的 `external_asset_id` 和新的幂等键发起明确的新尝试。

## 12. 调用方验收清单

1. 使用 LAN CA 完成 TLS 校验。
2. 不带 API Key 能从真实来源 IP 提交。
3. `202` 响应包含 `job_id/status_url/events_url/cancel_url`。
4. 排队时显示 `queue_position` 和预计开始时间。
5. SSE 断线可用 `Last-Event-ID` 恢复。
6. 成功后只下载 `artifacts` 清单中的原子产物并校验 SHA-256。
7. `normal-dx-v1`/`pbr-core-v1` 始终上传高模。
8. 不直连 3090-B、不自行启动 Baker；物理 GPU 围栏与 WSL 恢复由四实例 Agent 统一管理。

## 13. 2026-07-29 并发实测

四个实例均保持 `ONLINE` 心跳，Worker ID 为 `-01`～`-04`。使用真实 FBX 与真实 Base Color/Roughness/Metallic 输入提交四个完整 profile 请求，四个请求全部 `SUCCEEDED`、每个均发布 10 张最终 PNG、`baker_result.json` 与 `baker.log`（12 项）。其中两组任务分别在同一秒由 `-03` 与 `-04` 同时领取，已证明不再是单槽串行；四实例容量由控制面持续上报和聚合显示。

| 任务 ID | Worker | 结果 | 执行耗时 |
|---|---|---|---:|
| `313a5f85-160d-482f-9562-5ca9faa24c5a` | `-03` | `SUCCEEDED` / 12 项 | 38 秒 |
| `e7ea24e3-bfb9-4333-9c5c-9ecc611a2236` | `-04` | `SUCCEEDED` / 12 项 | 24 秒 |
| `b54fd692-a311-457f-a8fc-effef888dab8` | `-03` | `SUCCEEDED` / 12 项 | 37 秒 |
| `14ae0edb-1f64-4479-a598-8580bb118f82` | `-04` | `SUCCEEDED` / 12 项 | 23 秒 |
