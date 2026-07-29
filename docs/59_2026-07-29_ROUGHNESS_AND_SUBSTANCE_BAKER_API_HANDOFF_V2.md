# Li3D 粗糙度生成与 Substance PBR 烘焙 API 对接文档 V3

发布日期：2026-07-29
统一入口：`https://10.3.34.11`
控制面版本：`GPU Control Asset/Web 1.5.4`
适用对象：Li3D、动画管家及其他公司局域网调用方

## 1. 重要结论

本文件包含两条相互独立、可以串联使用的生产能力：

1. **粗糙度生成**：三台 GPU 节点均可执行，上传一张材质图，同步返回最终粗糙度图。
2. **Substance PBR 烘焙**：仅由 `3090-B` 原生 Windows 执行，异步返回 10 张最终 PBR/几何贴图。

共同约束：

- 公司局域网来源默认不需要 API Key，也不需要人工配置 IP 白名单；服务端按 TCP 真实来源 IP 自动登记、限额和隔离任务。
- 生产调用必须信任 `GPU_CONTROL_LAN_CA.crt`。临时 `-k` 只能用于排障，不能作为正式客户端配置。
- 根证书固定下载地址：`http://10.3.34.11/GPU_CONTROL_LAN_CA.crt`。这是公开根证书，不含私钥。
- 根证书文件 SHA-256：`ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b`。

Windows PowerShell 首次准备：

```powershell
$ca = "D:\Li3D\workspace\config\GPU_CONTROL_LAN_CA.crt"
New-Item -ItemType Directory -Force (Split-Path $ca) | Out-Null
Invoke-WebRequest "http://10.3.34.11/GPU_CONTROL_LAN_CA.crt" -OutFile $ca
(Get-FileHash $ca -Algorithm SHA256).Hash
```
- 每次业务尝试必须提供稳定且唯一的 `Idempotency-Key`；网络重试必须复用同一个值。
- 只发布经过完整性、文件类型、尺寸和 SHA-256 校验的最终产物；预览图、中间图和残缺结果不会作为成功结果返回。
- CPU 资产队列、GPU 推理队列和 Windows Baker 队列相互隔离。烘焙占用 3090-B 物理 GPU 时，调度中心会围栏其 WSL ComfyUI，避免两套运行时争抢同一张卡。

## 2. 粗糙度生成 API

### 2.1 接口

```text
POST /api/v1/services/modelview-roughness
Content-Type: multipart/form-data
```

| 字段 | 必填 | 说明 |
|---|---:|---|
| `image` | 是 | 输入材质图片，推荐 PNG |
| `parameters` | 否 | 只能省略或传 `{}`；调用方不能覆盖生产工作流、提示词、模型或采样参数 |
| `Idempotency-Key` | 建议 | Header；1～128 字符，同一次业务重试保持不变 |

接口为同步图片响应。建议连接超时至少 10 秒、读取超时至少 1900 秒。

### 2.2 cURL

```bash
curl --fail-with-body --show-error \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/services/modelview-roughness' \
  -H 'Idempotency-Key: li3d-chair-roughness-attempt-001' \
  -F 'image=@chair_material.png;type=image/png' \
  -D roughness.headers \
  --output chair_roughness.png
```

成功条件：

- HTTP `200`；
- `Content-Type` 是图片类型；
- Body 可被图片解码器打开；
- 响应头包含 `X-Job-ID`，排障时必须保留。

### 2.3 Python

```python
from pathlib import Path
import requests

base = "https://10.3.34.11"
with Path("chair_material.png").open("rb") as source:
    response = requests.post(
        f"{base}/api/v1/services/modelview-roughness",
        headers={"Idempotency-Key": "li3d-chair-roughness-attempt-001"},
        files={"image": ("chair_material.png", source, "image/png")},
        verify="GPU_CONTROL_LAN_CA.crt",
        timeout=(10, 1900),
    )

response.raise_for_status()
if not response.headers.get("Content-Type", "").startswith("image/"):
    raise RuntimeError(response.text)
Path("chair_roughness.png").write_bytes(response.content)
print("job_id=", response.headers.get("X-Job-ID"))
```

### 2.4 固定生产版本

| 项目 | 值 |
|---|---|
| ModelViewCreator commit | `d318bb392040e2d5f6bbd10ae61d832d36d3cb4a` |
| 原始工作流 SHA-256 | `8a52740b90ac47e77919b460a0e35241c94d91fde035effb3285600642e2ea38` |
| API 工作流版本 | `2026.07.29-d318bb39-roughness-v1` |
| 真实验收任务 | `acc3a27a-8775-4644-bfb6-07ad9b194ced` |
| 真实结果 | `880×1184 RGB PNG`，703642 bytes，约 19.73 秒 |

GPU Control 只同步并挂载上述用户批准的上游版本，不修改 ModelViewCreator 工作流语义。

## 3. Substance PBR 烘焙 API

### 3.1 接口与执行边界

```text
POST /api/v1/assets/bake/process
Content-Type: multipart/form-data
```

该任务只允许 `asset-worker-3090-b-windows-01`～`-04` 领取。四个独立 Worker 聚合为 4 个烘焙槽位，每个实例只执行一个任务；它们共用首任务进入、末任务退出的物理 GPU 围栏。3090-B 原生 Windows 使用固定的 Adobe Substance 3D Baker CLI；调用方不能传可执行文件路径或任意 CLI 参数。

### 3.2 推荐完整 Li3D profile

```json
{
  "external_asset_id": "li3d:chair:bake:001",
  "options": {
    "profile": "li3d-pbr-full-v2",
    "resolution": 2048,
    "texture_cache_mb": 32768
  }
}
```

`li3d-pbr-full-v2` 必须上传：

| Form 字段 | 必填 | 用途 |
|---|---:|---|
| `low_mesh` | 是 | 已有有效 UV 的最终低模，FBX/OBJ |
| `high_mesh` | 是 | 对应高模，FBX/OBJ |
| `base_color_texture` | 是 | 高模对应 Base Color，PNG/JPG/TIFF/TGA/EXR |
| `roughness_texture` | 是 | 高模对应 Roughness |
| `metallic_texture` | 是 | 高模对应 Metallic |
| `cage_mesh` | 否 | 可选 Cage，FBX/OBJ |
| `metadata` | 是 | 上述 JSON 字符串 |

其他兼容 profile：

- `ao-self-v1`：只需要 `low_mesh`；
- `normal-dx-v1`：需要 `low_mesh + high_mesh`；
- `pbr-core-v1`：需要 `low_mesh + high_mesh`，输出 AO 与 DirectX Normal。

### 3.3 完整 cURL 示例

```bash
curl --fail-with-body --show-error \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/bake/process' \
  -H 'Idempotency-Key: li3d-chair-bake-attempt-001' \
  -F 'low_mesh=@chair_low_uv.fbx;type=application/octet-stream' \
  -F 'high_mesh=@chair_high.fbx;type=application/octet-stream' \
  -F 'base_color_texture=@chair_basecolor.png;type=image/png' \
  -F 'roughness_texture=@chair_roughness.png;type=image/png' \
  -F 'metallic_texture=@chair_metallic.png;type=image/png' \
  -F 'metadata={"external_asset_id":"li3d:chair:bake:001","options":{"profile":"li3d-pbr-full-v2","resolution":2048,"texture_cache_mb":32768}}'
```

成功提交返回 HTTP `202`，示意：

```json
{
  "job_id": "UUID",
  "status": "QUEUED",
  "status_url": "/api/v1/assets/jobs/UUID",
  "events_url": "/api/v1/assets/jobs/UUID/events",
  "cancel_url": "/api/v1/assets/jobs/UUID/cancel",
  "timing": {
    "queue_position": 1,
    "estimated_start_seconds": 0
  }
}
```

### 3.4 Python 提交、轮询、下载与 SHA 校验

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
    "external_asset_id": "li3d:chair:bake:001",
    "options": {
        "profile": "li3d-pbr-full-v2",
        "resolution": 2048,
        "texture_cache_mb": 32768,
    },
}

paths = {
    "low_mesh": Path("chair_low_uv.fbx"),
    "high_mesh": Path("chair_high.fbx"),
    "base_color_texture": Path("chair_basecolor.png"),
    "roughness_texture": Path("chair_roughness.png"),
    "metallic_texture": Path("chair_metallic.png"),
}
handles = {name: path.open("rb") for name, path in paths.items()}
try:
    files = {
        name: (paths[name].name, handle, "image/png" if "texture" in name else "application/octet-stream")
        for name, handle in handles.items()
    }
    response = requests.post(
        f"{BASE}/api/v1/assets/bake/process",
        headers={"Idempotency-Key": "li3d-chair-bake-attempt-001"},
        files=files,
        data={"metadata": json.dumps(metadata, ensure_ascii=False)},
        verify=CA,
        timeout=(10, 300),
    )
finally:
    for handle in handles.values():
        handle.close()

response.raise_for_status()
job = response.json()
job_id = job["job_id"]

while True:
    response = requests.get(
        f"{BASE}/api/v1/assets/jobs/{job_id}", verify=CA, timeout=(10, 30)
    )
    response.raise_for_status()
    job = response.json()
    print(job["status"], job["progress"], job["stage"], job["stage_message"])
    if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        break
    time.sleep(3)

if job["status"] != "SUCCEEDED":
    raise RuntimeError(job.get("error"))

output = Path("baker-result")
output.mkdir(exist_ok=True)
for artifact in job["artifacts"]:
    downloaded = requests.get(
        urljoin(BASE, artifact["download_url"]), verify=CA, timeout=(10, 300)
    )
    downloaded.raise_for_status()
    digest = hashlib.sha256(downloaded.content).hexdigest()
    if digest != artifact["sha256"]:
        raise RuntimeError(f"SHA mismatch: {artifact['filename']}")
    (output / artifact["filename"]).write_bytes(downloaded.content)
```

### 3.5 完整 profile 的原子交付物

任务只有下列文件全部生成并通过服务端校验后才会变为 `SUCCEEDED`：

| kind | 文件名 | 说明 |
|---|---|---|
| `base_color` | `asset_base_color.png` | 颜色贴图投射 |
| `roughness` | `asset_roughness.png` | 粗糙度投射 |
| `metallic` | `asset_metallic.png` | 金属度投射 |
| `ao` | `asset_ao.png` | AO |
| `normal_dx` | `asset_normal_dx.png` | DirectX 切线空间法线 |
| `normal_gl` | `asset_normal_gl.png` | OpenGL 切线空间法线 |
| `world_normal` | `asset_world_normal.png` | 世界空间法线 |
| `curvature` | `asset_curvature.png` | 曲率 |
| `thickness` | `asset_thickness.png` | 厚度 |
| `position` | `asset_position.png` | 位置 |
| `result` | `baker_result.json` | 固定版本、输入/输出 SHA、分辨率与执行摘要 |
| `log` | `baker.log` | 原生 CLI 审计日志 |

客户端只下载状态响应中 `artifacts` 列出的文件，不猜测服务器目录，也不接受中间文件。

### 3.6 SSE 与取消

```bash
curl -N --cacert GPU_CONTROL_LAN_CA.crt \
  'https://10.3.34.11/api/v1/assets/jobs/JOB_ID/events'

curl --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/jobs/JOB_ID/cancel'
```

SSE 用于低延迟进度展示，`GET /api/v1/assets/jobs/{job_id}` 的轮询结果是最终事实。终态以后 `elapsed_seconds` 停止增长。

## 4. 错误处理

| HTTP/终态 | 错误码 | 调用方动作 |
|---|---|---|
| 422 | `BAKE_INPUT_INVALID` | 检查 profile、缺少的文件、扩展名和 metadata |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一个幂等键提交了不同内容，改用新的业务尝试键 |
| 409 | `EXTERNAL_ASSET_CONFLICT` | 同一来源客户重复使用 `external_asset_id` |
| 429 | 配额/排队限制 | 使用指数退避，继续轮询已有任务，不重复创建 |
| FAILED | `SUBSTANCE_EXECUTION_FAILED` | 保存 job_id，由管理员检查原生 Baker 日志 |
| FAILED | `SUBSTANCE_RESULT_INVALID` | 输出数量、尺寸、SHA、版本或成功标记未通过；不会发布半套结果 |

## 5. 上线与验收状态

| 项目 | 状态 |
|---|---|
| GPU Control Asset API / WebUI 1.5.4 | 已上线，容器 healthy；GPU API/调度器未因本次发布重启 |
| 粗糙度真实 GPU 任务 | 已通过 |
| Baker API 完整 profile 原子交付集成测试 | 已通过，相关回归 10/10 |
| 3090-B Baker Agent | 四实例 `-01`～`-04` 均 ONLINE，WebUI 聚合为 `0/4`～`4/4` |
| 3090-B 原生 10 图最终实单 | **已通过**；一次执行成功，完整原子交付 |
| 并发实单 | 四请求全部成功；已观察两组同秒双路执行，单槽串行限制已解除 |

### 5.1 3090-B 原生 Windows 最终实测证据

- 任务 ID：`63deafd4-5eca-4399-b7a3-e491001289bf`
- 外部资产 ID：`acceptance:baker:full:20260729:02`
- Worker：`asset-worker-3090-b-windows`
- Profile：`li3d-pbr-full-v2`
- 分辨率：`512×512`（验收尺寸）
- 结果：`SUCCEEDED`，一次尝试，30 秒
- 原子产物：10 张最终 PNG + `baker_result.json` + `baker.log`，共 12 项

| 最终文件 | SHA-256 |
|---|---|
| `asset_base_color.png` | `c951b5a39c2c84d6499f33b77928c2e9555d29b90786f52b72bb3057d4dc7eb4` |
| `asset_roughness.png` | `42191746b3c21c76e126e1e20eb17c6071083a1afdd36460d34ccd1e1359cdd7` |
| `asset_metallic.png` | `d626faf2ba2485b2a3fe48b32a86f9513b6949a2981e0b2766cb97b8b83db46b` |
| `asset_ao.png` | `c7e1e54e547a0fad2d357d5102bdd0279134861cfc0ac505aa112fe31eeda72e` |
| `asset_normal_dx.png` | `465e158b36f92336bfa5e6b304808349795945e5ce90a1ea51667e576f818d4c` |
| `asset_normal_gl.png` | `fd00f75b41abd64ee5706650e0b332782184eee9586338f6a94df6fc874c42e3` |
| `asset_world_normal.png` | `5035d449b2d6ca443d7a99d5ddd4ee6dfb642fba399a640a4a6461962024b9f0` |
| `asset_curvature.png` | `8637f1cb57480dc656003af257abfb664ddb48207883a3e663ca5dadcb994db3` |
| `asset_thickness.png` | `490440e36262a0fb29eed1de3c37b7dd4fc724a69f9844c1ba040ba2447a667f` |
| `asset_position.png` | `32603bc6e2f79d806770942b018b590129593af0a8c2630f5a0e7eb25ea3e0cc` |

调用方现在可以直接进行真实生产接入；无需 API Key，也不需要预先登记白名单。服务端仍会按真实来源 IP 自动建档、限流、审计和隔离任务。

### 5.2 四实例并发实测证据

| 任务 ID | Worker | 状态 | 原子产物 | 执行耗时 |
|---|---|---|---:|---:|
| `313a5f85-160d-482f-9562-5ca9faa24c5a` | `asset-worker-3090-b-windows-03` | `SUCCEEDED` | 12 | 38 秒 |
| `e7ea24e3-bfb9-4333-9c5c-9ecc611a2236` | `asset-worker-3090-b-windows-04` | `SUCCEEDED` | 12 | 24 秒 |
| `b54fd692-a311-457f-a8fc-effef888dab8` | `asset-worker-3090-b-windows-03` | `SUCCEEDED` | 12 | 37 秒 |
| `14ae0edb-1f64-4479-a598-8580bb118f82` | `asset-worker-3090-b-windows-04` | `SUCCEEDED` | 12 | 23 秒 |

四个 Worker 的当前心跳均为 `ONLINE / 0/1`。本轮真实数据验证了并行领取、原生 CLI 执行、共享围栏、12 件套原子发布和终态耗时冻结。
