# 统一调度中心 Blender PBR UV Asset API V1

文档状态：`IMPLEMENTED / A+B IMAGE STAGED AND ACCEPTED / CONTROL PLANE NOT ENABLED`
设计输入 SHA-256：`a3a0dac42a7f2f77de251518f56947d6659b2d1870feab7017c279c439e2f90d`
候选版本：`Unified Scheduling Center 1.4.0-dev`
Blender 基线：`5.1.2`
官方 Linux x64 归档 SHA-256：`21a6ab66b2a8b9f035fdb39c6445cdbe91e2fe1dcff30786148b757df7f9a9c5`

## 1. 架构结论

Blender 自动拆 UV 复用统一调度中心的 PostgreSQL、API Key、TLS、幂等、审计和日志规范，但不进入
GPU job 表、不领取 GPU node lease，也不修改 GPU Scheduler。它是独立的 Asset Processing 平面：

```text
调用方
  └─ HTTPS /api/v1/assets/*
       └─ Asset API（4090）
            ├─ asset_jobs / asset_workers / asset_artifacts
            └─ HMAC + lease 拉取协议
                 ├─ 4090 CPU Blender Worker（可并发）
                 ├─ 3090-A CPU Blender Worker（可并发）
                 └─ 3090-B CPU Blender Worker（可并发）

GPU API / GPU Scheduler / ComfyUI 三节点：保持独立、继续处理推理任务
```

统一 Web 产品名已改为“统一调度中心”。资产处理候选页已作为独立 Web-only 变更上线到
`/asset-processing`；页面明确显示后端尚未启用，不会伪造 Worker 在线或任务数量。

## 2. 并发模型

Blender Worker 不是“一台 CPU 一个槽位”。每台机器上限由
`ASSET_WORKER_MAX_CONCURRENCY` 独立配置，Worker 用心跳报告实际槽位，Asset API 只在同时满足以下
条件时继续发任务：

- `current_jobs < max_concurrency`；
- 最近心跳未超过 30 秒；
- Blender 精确版本为 5.1.2；
- `load_1m / cpu_count <= 0.85`；
- 可用内存不少于 8192 MiB；
- 作业未取消且队列状态为 `QUEUED`。

建议首轮压测值，不是永久值：

| 节点 | 已知 CPU | 初始并发 | 压测上探建议 |
|---|---:|---:|---:|
| 4090 控制机 | 待实测 | 2 | 保持控制面 p95 延迟稳定后再升 |
| 3090-A | 32 逻辑核 | 3 | 4～6 |
| 3090-B | 128 逻辑核 | 8 | 12～16 |

并发不能只按核数计算。FBX 大小、网格面数、Blender 峰值内存和打包阶段都会改变安全上限。验收时要
同时观察控制面 API p95、ComfyUI 推理耗时、系统 load、内存余量、swap 和 Blender 单任务耗时；任何
GPU 业务指标恶化就降低本机 Asset 并发。Worker 容器不挂 NVIDIA 设备，并显式设置
`CUDA_VISIBLE_DEVICES=""`。

## 3. 外部创建 API

出于安全和可复现性，V1 不接受服务器本地 `input_file` 路径；外部调用方必须上传文件。

```http
POST /api/v1/assets/uv/unwrap
X-API-Key: gpc_<prefix>_<secret>
Idempotency-Key: asset:chair:g1
X-Request-ID: asset-chair-g1-create-01
Content-Type: multipart/form-data

asset=<chair.fbx>
metadata={...JSON...}
```

`metadata`：

```json
{
  "external_asset_id": "asset:chair:g1",
  "options": {
    "resolution": 2048,
    "padding_px": 10,
    "hard_edge_angle_degrees": 75.0,
    "hidden_axis": "auto",
    "texel_density_mode": "uniform",
    "qa_profile": "pbr-v1"
  }
}
```

规则：

- 输入格式：`.fbx`、`.obj`、`.glb`、`.gltf`、`.blend`；
- 文件名必须是安全 basename，不接受路径；
- 默认最大上传 2 GiB；
- resolution 只允许 1024、2048、4096、8192；
- metadata 严格模式，未知字段返回 422；
- `external_asset_id` 在同一客户内永久唯一；输入改变必须增加 generation；
- 幂等 hash 覆盖 external ID、规范化 options 和输入文件 SHA-256；
- 同 key 同内容返回原 job，HTTP 200；同 key 不同内容返回 409。

首次接受返回 HTTP 202：

```json
{
  "job_id": "uuid",
  "external_asset_id": "asset:chair:g1",
  "job_type": "UV_UNWRAP",
  "status": "QUEUED",
  "progress": 0,
  "input_sha256": "...",
  "status_url": "/api/v1/assets/jobs/<job_id>"
}
```

### 3.1 cURL 完整调用

拆 UV 是异步任务，不会像单图片 GPU 服务一样让创建请求一直等待 Blender 完成。调用方先得到
`job_id`，再查询状态；只有 `SUCCEEDED` 才会返回四个最终 artifact。

```bash
BASE_URL='https://10.3.34.11'
API_KEY='gpc_<prefix>_<secret>'
IDEMPOTENCY_KEY='asset:chair:g1'

CREATE_RESPONSE="$(curl --fail-with-body --silent --show-error \
    --cacert /path/to/lan-ca.crt \
    -X POST "$BASE_URL/api/v1/assets/uv/unwrap" \
    -H "X-API-Key: $API_KEY" \
    -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
    -H 'X-Request-ID: asset-chair-g1-create-01' \
    -F 'asset=@chair.fbx' \
    -F 'metadata={"external_asset_id":"asset:chair:g1","options":{"resolution":2048,"padding_px":10,"hard_edge_angle_degrees":75.0,"hidden_axis":"auto","texel_density_mode":"uniform","qa_profile":"pbr-v1"}}')"

JOB_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$CREATE_RESPONSE")"
curl --fail-with-body --silent --show-error \
  --cacert /path/to/lan-ca.crt \
  -H "X-API-Key: $API_KEY" \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID"
```

在已按来源 IP 唯一登记的内网客户端上，可以省略 `X-API-Key`；同一来源 IP 如果命中多个客户，服务会
返回 `409 CLIENT_IP_CONFLICT`，不会猜测客户身份。生产调用必须安装并使用 LAN CA，禁止用 `-k` 跳过
TLS 验证。

### 3.2 Python 完整调用、轮询、下载和 SHA 校验

```python
import hashlib
import json
import time
from pathlib import Path

import requests

BASE_URL = "https://10.3.34.11"
CA_FILE = "/path/to/lan-ca.crt"
API_KEY = "gpc_<prefix>_<secret>"  # 按来源 IP 唯一登记时可删除该请求头
HEADERS = {"X-API-Key": API_KEY}

metadata = {
    "external_asset_id": "asset:chair:g1",
    "options": {
        "resolution": 2048,
        "padding_px": 10,
        "hard_edge_angle_degrees": 75.0,
        "hidden_axis": "auto",
        "texel_density_mode": "uniform",
        "qa_profile": "pbr-v1",
    },
}

with Path("chair.fbx").open("rb") as source:
    response = requests.post(
        f"{BASE_URL}/api/v1/assets/uv/unwrap",
        headers={
            **HEADERS,
            "Idempotency-Key": "asset:chair:g1",
            "X-Request-ID": "asset-chair-g1-create-01",
        },
        files={"asset": ("chair.fbx", source, "application/octet-stream")},
        data={"metadata": json.dumps(metadata, ensure_ascii=False)},
        timeout=(10, 120),
        verify=CA_FILE,
    )
response.raise_for_status()
job = response.json()
job_id = job["job_id"]

deadline = time.monotonic() + 3600
while True:
    response = requests.get(
        f"{BASE_URL}/api/v1/assets/jobs/{job_id}",
        headers=HEADERS,
        timeout=(10, 30),
        verify=CA_FILE,
    )
    response.raise_for_status()
    job = response.json()
    if job["status"] == "SUCCEEDED":
        break
    if job["status"] in {"FAILED", "CANCELLED"}:
        raise RuntimeError(job.get("error") or job["status"])
    if time.monotonic() >= deadline:
        raise TimeoutError(f"asset job still {job['status']}: {job_id}")
    time.sleep(3)

expected_names = {
    "model_PBR_UV.blend",
    "model_PBR_UV.fbx",
    "model_report.json",
    "model_QA.json",
}
artifacts = job["artifacts"]
if {item["filename"] for item in artifacts} != expected_names:
    raise RuntimeError("incomplete or unexpected artifact set")

output_dir = Path("result")
output_dir.mkdir(exist_ok=True)
for artifact in artifacts:
    response = requests.get(
        f"{BASE_URL}{artifact['download_url']}",
        headers=HEADERS,
        timeout=(10, 300),
        verify=CA_FILE,
    )
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != artifact["sha256"] or digest != response.headers["X-Artifact-SHA256"]:
        raise RuntimeError(f"SHA-256 mismatch: {artifact['filename']}")
    (output_dir / artifact["filename"]).write_bytes(response.content)
```

调用方不要根据 `progress=100` 猜测完成，也不要扫描服务端目录。`SUCCEEDED`、四个固定文件名和逐件
SHA-256 三项同时成立，才是一次完整交付。

## 4. 查询、取消和容量

```http
GET  /api/v1/assets/jobs/{job_id}
POST /api/v1/assets/jobs/{job_id}/cancel
GET  /api/v1/assets/capacity
```

状态机：

```text
QUEUED → CLAIMED → RUNNING → SUCCEEDED
   │         │          ├─→ QUEUED（可重试失败/租约恢复）
   │         │          └─→ FAILED
   └─────────┴────────────→ CANCELLING → CANCELLED
```

容量接口是 `advisory=true` 的瞬时值，只用于 UI 和排队提示，不预留 CPU 槽位。

## 5. Worker 协议与故障恢复

Worker 每轮发送 HMAC 签名心跳，签名覆盖 method、path、原始 JSON、timestamp 和 nonce。领取使用
数据库行锁和 `SKIP LOCKED`，因此三台机器多进程同时领取时同一 job 只能成功一次。领取后返回 256 bit
随机 lease token；输入下载、进度、完成和失败都必须携带 `X-Asset-Lease`。

租约默认 300 秒。运行过程中 Worker 每 15 秒续租并读取 `cancel_requested`。租约过期时：

- 未超过最大尝试数：清除 Worker 和 lease，原 job 返回 `QUEUED`；
- 达到最大尝试数：变为 `FAILED/ASSET_LEASE_EXPIRED`；
- 已请求取消：变为 `CANCELLED`；
- 原 Worker 的 `current_jobs` 同步减一。

这套租约与 GPU node lease 完全分表，互不抢占。

## 6. 最终产物合同

成功完成必须一次上传且原子发布四个最终文件：

| kind | 固定文件名 | 用途 |
|---|---|---|
| `blend` | `model_PBR_UV.blend` | Blender 主文件 |
| `fbx` | `model_PBR_UV.fbx` | 交换格式 |
| `report` | `model_report.json` | 处理参数与对象统计 |
| `qa` | `model_QA.json` | QA 结论与硬失败列表 |

Asset API 先写随机 staging 目录，校验四个文件非空、QA JSON 可解析且 `hard_failures=[]`，再把目录
原子 rename 为 `output`，写入四条带 SHA-256 的 artifact 记录，最后才把父 job 改为 `SUCCEEDED`。
任何失败都不暴露部分 artifact。

查询成功 job 后，每个 artifact 含 `download_url`、`size_bytes` 和 `sha256`；下载响应同时返回
`X-Artifact-SHA256`。调用方必须下载后复算 SHA。

## 7. 当前算法实现与未冻结项

候选 Blender 脚本已实现：格式导入、应用 scale、按 75° 硬边/边界标 seam、Conformal unwrap、平均
island scale、0～1 pack、UV 越界/非有限值硬失败检查、保存 BLEND、导出 FBX 和生成报告。

Blender 5.1.2 立方体真实执行已通过，但以下生产算法项仍是阻塞项，不能把当前状态标记为
`FROZEN`：

1. 精确的 illegal overlap 检测；
2. flipped UV 检测；
3. hard edge 必须 UV split 的全量验证；
4. stretch p90/p95 真实计算，而非只记录阈值；
5. 圆柱隐藏纵缝、端盖分离和机械平面结构化规则；
6. strip/quad straighten；
7. 不同输入格式的材质、坐标系、单位和骨骼保真；
8. Blender runtime 已固定官方来源与归档 SHA，但 SBOM 和签名仍待补齐；
9. 三节点真实并发和对 GPU 推理零回归的联合压测。

当前 QA 文件会明确写出上述生产 QA extension 尚未冻结，避免把 MVP 误标成最终算法。

## 8. 部署与验收门禁

当前生产有任务，禁止启用 Asset 控制面和常驻 Worker。两台 3090 只完成了镜像预置与一次性验收。
后续安全顺序：

1. 等 GPU 父批次和普通 GPU job 清空；
2. 对现有 Blender 5.1.2 runtime 与 `li3d/blender-worker:1.0.0` 补做 SBOM/扫描；
3. 备份数据库，离线校验 migration `20260727_0006`；
4. 先启动 Asset API，不启动 Worker，验证 GPU API/Scheduler 无回归；
5. 仅在 3090-B 启动 1 个 Asset 槽位，用非生产模型验证四个 artifact；
6. 依次启用 3090-A、4090 CPU Worker；
7. 按 2→4→8→更高并发阶梯压测，不允许直接打满 128 核；
8. 验证 GPU 推理 p95、错误率、节点心跳和 Web 状态无退化；
9. 完成第 7 节全部 QA 后才签署 V1 FROZEN。

## 9. 当前验证证据

协议测试和真实 Blender 验收已验证：

- Asset 创建与输入 SHA；
- 幂等重放；
- CPU Worker 4 槽注册和容量统计；
- HMAC 领取和 lease 下载；
- 进度与续租；
- 四个最终 artifact 原子发布；
- artifact SHA 响应；
- 非法扩展名和未知 options 拒绝；
- 原批量抠图 V3 回归。

协议定向测试结果：`3 passed`；当前全量单元/集成回归：`80 passed`。

Blender Worker 固定镜像：

- image ID：`sha256:8b926307d52d393e995cf9e32fba6abf362c6b1ce3790f43f790bc8a50b08a64`；
- 分发归档 SHA-256：`7c41594e5035a37a23d4baae12582862cd198942bf7a398a4a7e7500d9aaeaeb`；
- 3090-A 与 3090-B 均在 `--network none` 下创建立方体、拆 UV、导出 BLEND/FBX、生成 report/QA；
- 两机均得到 `qa_passed=true`、`hard_failures=0` 和 `blender_worker_acceptance=PASSED`。

这是 Worker 运行时与最小管线验收，不等于第 7 节复杂生产模型算法验收。实机记录见
`44_2026-07-27_UNIFIED_WEB_AND_BLENDER_WORKER_STAGING_RECORD.md`。
