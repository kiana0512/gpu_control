# 动画管家 ↔ GPU Control 批量抠图交接协议 V2

文档状态：`FROZEN / READY FOR REAL INTEGRATION`  
GPU Control 版本：`1.2.0`  
协议版本：manifest `1.0`  
生效日期：2026-07-24  
生产入口：`https://10.3.34.11`

本文件是动画管家调用 GPU Control 批量 ImageClip RGBA 抠图的唯一权威合同，取代
`GPU_CONTROL_MATTING_HANDOFF_V1.md` 和
`37_ANIMATION_MANAGER_BATCH_API_CONTRACT_DRAFT.md`。实现与本文件不一致时，双方先停止真实
发布并按 `batch_id`、`external_batch_id`、`X-Request-ID` 对账，不得静默降级或猜测字段。

## 1. 本次已经交付的能力

GPU Control 已在生产主控 4090 上完成以下能力：

- 一个动画业务批次只生成一个父任务，Web“任务”和“最近任务”不再按帧刷屏；
- 父任务详情按页显示帧序号、输入/输出相对路径、状态、节点、重试次数、错误和 SHA-256；
- 每帧仍是内部真实任务，可独立分配到 4090、3090-A、3090-B 并复用现有租约和故障重试；
- 有界投喂窗口默认 12，避免几千帧同时污染热任务队列；
- 一个调用方的批次最多并行使用三张卡，每个 GPU 同时只执行一帧；
- 输入 ZIP、manifest、路径、大小、逐帧 SHA-256 和图片可解码性在接单阶段严格校验；
- 只有所有帧成功且输出重新校验为带 Alpha 的 PNG 后，才原子发布完整结果 ZIP；
- 批次、帧、内部 job、执行节点和结果归档全链路可追溯；
- 重复网络请求使用同一幂等键只返回原批次，不重复抠图。

生产实测已用 3 帧真实图片同时命中三台机器，节点分布为
`control-4090: 1`、`worker-3090-a: 1`、`worker-3090-b: 1`，最终 ZIP、包内 manifest、
三份输出 SHA-256 和 RGBA PNG 均校验通过。

## 2. 双方不可跨越的职责边界

### 2.1 动画管家负责

1. 业务任务、飞书收发、视频/序列帧抽取、Cherry、编码、最终业务 ZIP 和发布仍在动画管家执行。
2. 为每个不可变输入批次生成唯一 `external_batch_id`，并保存 GPU Control 返回的 `batch_id`。
3. 生成符合第 5 节的 `ZIP_STORED` 和严格 manifest；ordinal、路径、大小和输入 SHA-256 是业务真值。
4. 网络重试复用相同 `Idempotency-Key`；输入、参数或 generation 改变时必须换
   `external_batch_id` 和幂等键，例如 `shot-010:g2`。
5. 只把 `SUCCEEDED` 当作完整成功；`FAILED`、`CANCELLED` 或查询异常不得偷偷改走本机。
6. 下载后先校验 HTTP 头中的整包 SHA-256，再校验 manifest、数量、路径、顺序、逐帧 SHA 和 Alpha，
   最后才把临时目录原子提升为正式输出目录。
7. 同一个 `COMFY_*` 调用只允许选择本机或 GPU Control 一条执行路径，禁止双方同时执行同一批。

### 2.2 GPU Control 负责

1. 接收和验证整个输入批次，固定精确的 ImageClip 工作流版本。
2. 将帧拆成内部任务，并根据在线状态、槽位和热模型动态分发到三台 GPU。
3. 保持 ordinal、原相对目录、输入到输出的映射不受机器选择和完成顺序影响。
4. 管理内部重试、主控重启恢复、取消、状态计数、节点记录和错误信息。
5. 在全量成功后重新验证输出并原子生成唯一完整结果包。
6. Web 只显示一条父任务；帧级信息只出现在父任务详情和 manifest 分页接口。

## 3. 连接、TLS、认证和租户身份

基础地址固定为：

```text
https://10.3.34.11
```

客户端必须信任内网 CA，正式环境不要使用 `verify=False` 或 `curl -k`。CA 文件在 GPU Control
仓库的 `deploy/control-plane/nginx/certs/lan-ca.crt`。

### 推荐：专用 API Key

生产系统对系统调用推荐每个动画管家实例配置专用：

```http
X-API-Key: gpc_<prefix>_<secret>
```

创建、查询、取消和下载结果都必须使用同一 API 客户身份。API Key 能在动画管家出口 IP 变化时
继续访问原批次，是长期运行的首选方案。

### 兼容：真实来源 IP 自动认证

不带 API Key 时，GPU Control 会按 Nginx 看到的真实来源 IP 自动建立客户。此方式可以联调，
但来源 IP 改变后会成为另一个租户，新租户查询旧 `batch_id` 会得到 `404 BATCH_NOT_FOUND`。
因此正式运行前必须满足以下二选一：

- 使用固定 API Key；或
- 固定动画管家出口 IP，并在 Web“API 客户”中将该 IP 绑定到原客户。

所有请求建议携带合法的 `X-Request-ID`，长度不超过 64，字符范围为字母、数字、点、下划线、
冒号和短横线，例如 `am-shot010-g1-create-01`。响应总会返回最终采用的 `X-Request-ID`。

## 4. 推荐调用时序

```text
生成不可变帧集
  → 计算逐帧大小与 SHA-256
  → 生成严格 manifest
  → 生成 ZIP_STORED
  → POST 创建批次（上传并完成接单校验后返回 202）
  → 每 3 秒 GET 批次状态
  → SUCCEEDED 后读取 artifacts[0]
  → 下载 ZIP 并核对 X-Artifact-SHA256
  → 校验包内 manifest、路径、顺序、逐帧 SHA、PNG Alpha
  → 临时结果目录原子发布
```

创建接口不是长时间 GPU 推理请求，但会同步完成整个 ZIP 的落盘、逐帧 SHA 和图片解码检查后才
返回 `202`。大包上传和校验阶段客户端超时建议为 86400 秒；状态轮询单次超时可设 30 秒。

## 5. 创建批次

### 5.1 HTTP 请求

```http
POST /api/v1/batches/imageclip-rgba HTTP/1.1
Host: 10.3.34.11
X-API-Key: gpc_xxx                 # 推荐；来源 IP 模式可省略
Idempotency-Key: animation:shot-010:g1
X-Request-ID: am-shot010-g1-create-01
Content-Type: multipart/form-data; boundary=...

archive=<frames.zip; application/zip>
manifest=<JSON 字符串>
```

表单字段名称严格为 `archive` 和 `manifest`。`Idempotency-Key` 必填，长度 1～128。

### 5.2 manifest 1.0 的精确结构

```json
{
  "schema_version": "1.0",
  "external_batch_id": "animation:episode-01:shot-010:g1",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "episode_01/shot_010/frame_000001.png",
      "size_bytes": 4839201,
      "sha256": "64位小写十六进制"
    },
    {
      "ordinal": 1,
      "relative_path": "episode_01/shot_010/frame_000002.webp",
      "size_bytes": 4851033,
      "sha256": "64位小写十六进制"
    }
  ]
}
```

字段规则：

| 字段 | 当前规则 |
|---|---|
| `schema_version` | 只能是字符串 `1.0` |
| `external_batch_id` | 1～128 字符；去除首尾空白后必须不变；同一 API 客户内永久唯一 |
| `failure_policy` | 只能是 `all_or_nothing` |
| `output_naming` | 只能是 `preserve_stem_png` |
| `parameters` | 可省略，默认 `{}`；当前动画管家应发送 `{}`，新增参数需双方另行冻结 |
| `frames` | 1～5000 项，数组顺序必须与 ordinal 一致 |
| `ordinal` | 必须严格为 `0, 1, 2, ... N-1`，不得跳号或重复 |
| `relative_path` | 1～2048 字符，UTF-8 NFC，POSIX `/` 分隔的安全相对路径 |
| `size_bytes` | 原图实际字节数，1～67108864 |
| `sha256` | 原图实际内容的 64 位小写 SHA-256 |

manifest 使用严格模式，未定义字段会被拒绝。当前禁止发送 `callback_url`、逐帧 `parameters`、
`best_effort`、`append_rgba_suffix` 等草案字段。

### 5.3 路径和输出映射

禁止绝对路径、空段、`.`、`..`、反斜杠、NUL、非 NFC 字符串。输入路径和计算后的输出路径按
大小写折叠后也必须唯一，防止在不同文件系统上覆盖。

输出始终把输入最后一个扩展名替换成 `.png`，目录和 stem 不变：

```text
input : episode_01/shot_010/frame_000001.jpg
output: episode_01/shot_010/frame_000001.png

input : episode_01/shot_010/frame_000002.png
output: episode_01/shot_010/frame_000002.png
```

因此同目录中 `frame_000001.jpg` 与 `frame_000001.webp` 会产生同一个输出路径，整个请求会被拒绝。

### 5.4 ZIP 合同

- 必须是标准 ZIP，所有图片文件必须使用 `ZIP_STORED`，不可 Deflate；
- 文件条目集合必须与 manifest 的 `relative_path` 集合完全相等；
- 不允许 ZIP 中多一份 README、隐藏文件、manifest、副本或少任何一帧；
- 不允许加密条目、符号链接、硬链接、设备文件、重复路径或逃逸路径；
- 建议不写显式目录条目，只写图片文件；
- 输入图片格式只允许可正确解码的 JPEG、PNG、WebP；
- 单图像素数不得超过 40000000；
- 服务会同时核对 ZIP entry size、实际读取字节数、manifest size 和 SHA-256。

示例 Python 打包：

```python
import hashlib
import json
import zipfile
from pathlib import Path

root = Path("frames")
paths = sorted(path for path in root.rglob("*") if path.is_file())
frames = []
for ordinal, path in enumerate(paths):
    relative = path.relative_to(root).as_posix()
    content = path.read_bytes()
    frames.append({
        "ordinal": ordinal,
        "relative_path": relative,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    })

manifest = {
    "schema_version": "1.0",
    "external_batch_id": "animation:episode-01:shot-010:g1",
    "failure_policy": "all_or_nothing",
    "output_naming": "preserve_stem_png",
    "parameters": {},
    "frames": frames,
}

with zipfile.ZipFile("frames.zip", "w", compression=zipfile.ZIP_STORED) as archive:
    for frame in frames:
        archive.write(root / frame["relative_path"], frame["relative_path"])

Path("manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
)
```

### 5.5 创建响应和幂等

第一次成功接单返回 HTTP `202`：

```json
{
  "batch_id": "7f441948-886b-4ff5-81af-3354be978fdd",
  "external_batch_id": "animation:episode-01:shot-010:g1",
  "status": "QUEUED",
  "total_items": 480,
  "accepted_bytes": 123456789,
  "status_url": "/api/v1/batches/7f441948-886b-4ff5-81af-3354be978fdd",
  "events_url": "/api/v1/batches/7f441948-886b-4ff5-81af-3354be978fdd/events",
  "manifest_url": "/api/v1/batches/7f441948-886b-4ff5-81af-3354be978fdd/manifest"
}
```

同一租户、同一 `Idempotency-Key`、同一规范化 manifest 和同一工作流版本重复提交时返回
HTTP `200` 和原 `batch_id`，不会生成新父任务或子任务。幂等记录当前保留 7 天。

以下两种复用会返回 `409`：

- 同一幂等键提交了不同 manifest：`IDEMPOTENCY_CONFLICT`；
- 换了幂等键但复用了已有 `external_batch_id`：`EXTERNAL_BATCH_CONFLICT`。

输入内容或 generation 改变时，两个 ID 必须一起改变。不要靠给同一 key 增加 HTTP 重试序号来
制造新业务批次。

## 6. 查询批次、帧和事件

### 6.1 父批次状态

```http
GET /api/v1/batches/{batch_id}
X-API-Key: gpc_xxx
```

响应示例：

```json
{
  "batch_id": "...",
  "external_batch_id": "...",
  "status": "RUNNING",
  "workflow_key": "imageclip-rgba",
  "workflow_version": "2026.07.23-bb243808",
  "progress": 52.5,
  "counts": {
    "total": 480,
    "pending": 216,
    "queued": 9,
    "running": 3,
    "succeeded": 252,
    "failed": 0,
    "cancelled": 0
  },
  "node_distribution": {
    "control-4090": 84,
    "worker-3090-a": 86,
    "worker-3090-b": 85
  },
  "created_at": "2026-07-24T04:27:26.000000+00:00",
  "started_at": "2026-07-24T04:27:27.000000+00:00",
  "finished_at": null,
  "error": null,
  "artifacts": []
}
```

父状态机：

```text
VALIDATING → QUEUED → RUNNING → ASSEMBLING → SUCCEEDED
                         └────→ CANCELLING → CANCELLED
任一活动阶段 ─────────────────────────────→ FAILED
```

终态只有 `SUCCEEDED`、`FAILED`、`CANCELLED`。当前没有 `PARTIAL`。动画管家建议每 3 秒轮询；
暂时网络失败时继续查询同一个 `batch_id`，不要重建批次。

### 6.2 帧级 manifest 分页

```http
GET /api/v1/batches/{batch_id}/manifest?offset=0&limit=200
GET /api/v1/batches/{batch_id}/manifest?offset=0&limit=200&status=FAILED
```

`limit` 最大 500，默认 200。响应 `total` 始终是整批总帧数；使用 `status` 过滤时，它不是过滤后
的数量。`items` 始终按 ordinal 排序，包含：

- `ordinal`
- `input_relative_path`、`output_relative_path`
- `input_sha256`、成功后的 `output_sha256`
- `status`、内部 `job_id`、`node_id`、`attempts`
- 失败时的 `error.code` 和 `error.message`

动画管家正常执行不需要频繁抓取所有帧；父状态终态异常或人工排障时再分页读取。

### 6.3 SSE（可选）

```http
GET /api/v1/batches/{batch_id}/events
Accept: text/event-stream
```

SSE 发送持久化的父状态事件和 keepalive，终态后关闭。当前断线重连会从已有事件开头重放，
不承诺 `Last-Event-ID` 增量恢复；调用方必须以父状态 GET 为最终真相。首轮真实联调建议只用轮询。

## 7. 取消

```http
POST /api/v1/batches/{batch_id}/cancel
Idempotency-Key: <external_batch_id>:cancel
X-API-Key: gpc_xxx
```

取消幂等键必须精确等于 `external_batch_id + ":cancel"`。系统停止投喂未开始帧，取消已排队或运行
的内部任务，并最终收敛到 `CANCELLED`。若批次已是终态，重复取消直接返回当前状态。

取消不是立即强杀 GPU 的同步承诺；客户端提交取消后应继续轮询到终态。

## 8. 成功结果和动画管家校验

只有父状态为 `SUCCEEDED` 时，状态响应中的 `artifacts` 才会出现结果包：

```json
{
  "id": "5bb4a4e5-f79b-4c49-b46b-2e3443095f7f",
  "kind": "result_archive",
  "filename": "7f441948-886b-4ff5-81af-3354be978fdd-rgba.zip",
  "content_type": "application/zip",
  "size_bytes": 2189120,
  "sha256": "27421bc853ec4d6a64981856854ad07663d04e3aa48ff3bf39029ae1a05d1cb1",
  "download_url": "/api/v1/batches/.../artifacts/..."
}
```

下载必须继续使用同一 API 客户身份：

```http
GET /api/v1/batches/{batch_id}/artifacts/{artifact_id}
X-API-Key: gpc_xxx
```

HTTP 响应头 `X-Artifact-SHA256` 与状态响应 `artifacts[].sha256` 必须相同，且必须等于下载字节
重新计算的 SHA-256。

结果 ZIP 使用 `ZIP_STORED`，结构固定为：

```text
manifest.json
results/<output_relative_path 1>
results/<output_relative_path 2>
...
```

包内 `manifest.json` 精确结构：

```json
{
  "schema_version": "1.0",
  "batch_id": "...",
  "external_batch_id": "...",
  "total": 2,
  "items": [
    {
      "ordinal": 0,
      "input_relative_path": "episode_01/shot_010/frame_000001.jpg",
      "input_sha256": "...",
      "output_relative_path": "episode_01/shot_010/frame_000001.png",
      "output_sha256": "...",
      "status": "SUCCEEDED",
      "job_id": "...",
      "node_id": "worker-3090-a",
      "attempts": 1
    }
  ]
}
```

动画管家发布前必须依次验证：

1. 整包 SHA-256 与响应头及 artifact 元数据一致；
2. `batch_id`、`external_batch_id` 与请求记录一致；
3. `total` 与原 manifest 帧数一致；
4. items 数量正确，ordinal 恰好为 `0..N-1`，无缺失、重复或乱序；
5. 每项输入路径和输入 SHA 与原 manifest 完全一致；
6. 每项输出路径与本合同命名规则一致且唯一；
7. ZIP 中除 `manifest.json` 外的文件集合恰好等于 `results/<output_relative_path>` 集合；
8. 每个输出实际 SHA 等于 `output_sha256`；
9. 每个输出可解码为 PNG 且包含 Alpha 通道；
10. 全部通过后才把 staging 目录原子改名为正式目录并进入后续动画流程。

任何一步失败都应将该业务批次标为“远端结果校验失败”，保留原 ZIP、响应头、batch ID 和 request
ID 供排障，不得把不完整目录交给后续编码。

## 9. 错误处理合同

创建阶段常见 HTTP 错误：

| HTTP | `detail.code` | 含义与处理 |
|---:|---|---|
| 400 | `MANIFEST_INVALID` | 字段、ordinal、路径、命名或策略不符合合同；修复输入并使用新业务 ID |
| 401/403 | `AUTH_FAILED` | API Key 无效、过期或客户停用；不要重建批次 |
| 404 | `WORKFLOW_NOT_FOUND` | ImageClip 工作流未启用；通知 GPU Control 运维 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同 key 内容不同；检查动画管家 generation/幂等逻辑 |
| 409 | `EXTERNAL_BATCH_CONFLICT` | external ID 已被另一 key 使用；检查业务 ID 生命周期 |
| 413 | `BATCH_TOO_LARGE` | manifest、帧数、单帧、ZIP 或解压总量超限；拆批后使用新 ID |
| 422 | `ARCHIVE_ENTRY_INVALID` | ZIP 格式、压缩方法、条目类型或路径非法 |
| 422 | `FRAME_SET_MISMATCH` | ZIP 文件集合与 manifest 不一致 |
| 422 | `FRAME_HASH_MISMATCH` | entry 大小、实际字节数或 SHA 不一致 |
| 422 | `IMAGE_INVALID` | 不是合法 JPEG/PNG/WebP 或像素超限 |
| 422 | `WORKFLOW_RENDER_FAILED` / `INPUT_INVALID` | parameters 不被工作流接受或结构过深 |
| 429 | Nginx/客户限流 | 按退避重试相同请求和同一幂等键，不得换 key |

FastAPI 对缺少表单字段、缺少幂等头等结构错误可能返回标准 HTTP `422` 校验响应。调用端应同时
记录 HTTP 状态、响应 JSON 和 `X-Request-ID`。

运行阶段父批次可能 `FAILED`，常见错误包括子任务实际错误，以及 `CHILD_JOB_MISSING`、
`OUTPUT_MISSING`、`OUTPUT_HASH_MISMATCH`、`OUTPUT_ALPHA_MISSING`、`OUTPUT_IMAGE_INVALID`、
`BATCH_ASSEMBLY_INTERNAL_ERROR`。失败时不会提供可冒充完整成功的结果 artifact。动画管家应读取
父 `error` 和失败帧分页，通知运维或根据业务 generation 发起一个全新的批次；当前没有
`retry-failed` 公共端点。

查询或下载旧批次突然返回 404 时，首先核对调用身份是否从 IP 客户切换到了另一个 IP 或 API Key，
不要立刻重复提交。

## 10. 容量、限制和拆批建议

| 项目 | 生产值 |
|---|---:|
| 单批最大帧数 | 5000 |
| manifest 最大字节 | 4 MiB |
| 单帧最大字节 | 64 MiB |
| 单图最大像素 | 40000000 |
| ZIP 最大字节 | 100 GiB |
| 解压后所有帧总字节 | 100 GiB |
| Nginx multipart 入口 | 101 GiB（给 100 GiB archive 留表单开销） |
| multipart 临时盘 | 主控已挂载的 `JOB_ROOT`，不占 API 容器 overlay |
| 批次有界投喂窗口 | 12 帧 |
| 同一租户批次最大运行帧 | 3 |
| 单 GPU 同时任务 | 1 |
| 内部单帧最大尝试次数 | 3 |
| 推荐状态轮询 | 3 秒 |
| 创建/上传客户端超时 | 86400 秒 |

100 GiB 和 5000 帧是防护硬上限，不是建议每次打满。首轮生产建议每批 30～300 帧，验证素材
尺寸、吞吐和动画管家发布链路后再逐步扩大。一个镜头或业务 generation 应保持一个批次；若必须
因容量拆分，动画管家负责生成明确的分片 external ID，并在业务层汇总分片，GPU Control 不会把
多个父批次自动拼成一个。

## 11. Web 展示与人工排障

- 任务列表和总览只显示一条父批次，不显示内部每帧 job；
- 父行展示总进度、成功/总数和节点分布；
- 点击“查看”后，在详情中分页查看全部帧；
- 成功后详情显示结果 ZIP 的大小、SHA-256 和下载按钮；
- 运维可用父 `batch_id`、帧 `job_id`、`external_batch_id` 或请求 ID 查日志；
- 管理员下载使用 `/admin/batches/...`，动画管家不得使用管理端 JWT 或管理端下载 URL。

## 12. 当前明确不支持的能力

以下是旧设计稿里的未来候选项，不属于 1.2.0，不得发送或依赖：

- `callback_url` 和终态 Webhook；
- `failure_policy=best_effort` 或 `PARTIAL`；
- `output_naming=append_rgba_suffix`；
- `POST .../retry-failed`；
- 每帧不同 parameters；
- 对象存储 URL 输入；
- 暂停/继续批次；
- 依赖 `Last-Event-ID` 的精确 SSE 续传；
- 在一个批次内混用本机与 GPU Control 执行。

需要这些能力时必须升协议版本并由双方重新冻结，不能在 manifest 中提前加字段。

## 13. 动画管家真实联调验收单

双方先做以下轻量验收，复杂压力测试另行执行：

1. 30 帧、含两层相对目录和中文 NFC 路径，结果目录、命名、ordinal 和 SHA 全部一致；
2. 同时提交两个 30 帧批次，Web 只出现两个父行，三台 GPU 都能获得帧；
3. 创建请求超时后复用同 key 重发，返回同 `batch_id` 且 Web 不多一行；
4. 故意制造一帧错误 SHA，创建应被 422 拒绝且不产生父任务；
5. 运行中取消一个批次，最终为 `CANCELLED`，无成功 artifact；
6. 成功包逐项通过动画管家的整包/逐帧/Alpha/路径原子发布校验；
7. 动画管家进程重启后，只凭持久化的 `batch_id` 能恢复轮询和下载；
8. 若采用 IP 认证，切换出口 IP 的演练必须证明不会误建租户；正式建议直接验证 API Key。

联调时双方共同保存：`external_batch_id`、`batch_id`、创建 `X-Request-ID`、帧数、输入总字节、
创建耗时、完成耗时、三节点分布、artifact SHA 和动画管家最终发布目录。全部通过后再把动画管家
默认模式从本机/混合测试切换为生产 `gpu_control` 路由。
