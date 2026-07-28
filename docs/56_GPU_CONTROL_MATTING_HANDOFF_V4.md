# 动画管家 ↔ 统一调度中心批量抠图交接协议 V4

文档状态：`IMPLEMENTATION CANDIDATE / 等待双方真实联调冻结`
文档日期：2026-07-28
生产入口：`https://10.3.34.11`
批次 manifest 协议：`1.0`（V4 没有改变请求 manifest 字段）
生产工作流：`imageclip-rgba v2026.07.27-721f7d6-r1`
适用调用方：动画管家（AssetClaw）
取代文档：V1、V2、V3 及其临时补充说明

本文是动画管家调用统一调度中心执行批量 ImageClip RGBA 序列帧抠图的完整接口、状态和故障
处理合同。V4 保持 V3 的创建、查询、SSE、取消、manifest 和结果包格式，重点冻结以下生产语义：

1. 一帧传输或执行失败不会被伪装成“用户取消”；
2. 单帧失败隔离后，其余帧继续执行，但 `all_or_nothing` 批次不会发布部分结果；
3. 每次向 ComfyUI 上传后必须回读并核对字节数和 SHA-256，验证通过后才可提交 prompt；
4. 只有动画管家或管理员明确调用取消接口，父任务才能进入 `CANCELLING`；
5. Web 顶层一个业务批次只显示一行，逐帧状态只在详情分页中显示；
6. 系统重启、网络中断和节点暂时离线均从持久化 `batch_id` 恢复，不能重复创建业务批次。

本文确认后，双方只允许通过新协议版本修改字段或语义，不能在代码中暗改。

---

## 1. 2026-07-28 生产事件、事实和结论

### 1.1 受影响批次

```text
external_batch_id: assetclaw:VID_9D9EB9ACE6A1:matting:g1
batch_id:          d8ab774b-a895-4983-a92b-60e456b8140e
失败帧 ordinal:   34
输入相对路径:     video_01/0034.png
内部 job_id:      202ccad5-6642-4dde-a50d-e0e2c33229ec
```

动画管家没有发起取消，数据库中的 `cancel_requested=false`。旧实现却在一帧失败后把父任务置为
`CANCELLING`，导致 Web 显示“取消中”。这是统一调度中心的状态语义错误，不是动画管家误操作。

### 1.2 根因证据

主控保存的 ordinal 34 原始图片有效：

```text
size_bytes: 560646
sha256:     0f8f9d005b9a13772840010aeca9d44021bdd6192d67351f25168932b1b27c28
decode:     PNG 1080 × 1440 RGB，Pillow verify 通过
```

3090-A 的 ComfyUI 输入目录中，同一 job 的同名文件实际为 0 字节。ComfyUI `LoadImage` 因此报告
`Invalid data found when processing input`。旧客户端的上传重试使用 `overwrite=false`：第一次连接
中断在远端留下空文件，第二次请求虽然返回成功，却保留了原空文件，随后错误地提交了 prompt。

因此本事件不是：

- 动画管家取消；
- 动画管家源 PNG 损坏；
- ImageClip 工作流或模型内容改变；
- 4090 算力不足。

根因是“上传成功响应”没有与“远端最终字节完整”绑定，再叠加失败收尾错误映射为取消状态。

### 1.3 已完成的在线恢复

恢复过程没有重启 API 或任何 ComfyUI，也没有修改 ImageClip 工作流：

1. 释放 4090 的过期 job 租约和错误占用槽位；
2. 保留原父任务、逐帧 ordinal、原 child job ID 和审计历史；
3. 将失败/被动取消的子任务恢复为可执行状态，没有创建第二个业务批次；
4. 用 `overwrite=true` 重新上传 ordinal 34，并在 3090-A 容器内核对为 560646 字节和同一 SHA；
5. 只在两台 ComfyUI 队列均为空、数据库节点槽位均为 0 的安全窗口重启 Scheduler；
6. 4090 与 3090-A 随后同时领取该批次的不同帧。

2026-07-28 17:06（Asia/Singapore）复核：批次为 `RUNNING`，48 帧成功、2 帧运行、10 帧排队、
37 帧待物化、0 失败、0 取消，聚合进度 51.53%；4090 与 3090-A 均 `ONLINE / ACTIVE / 1/1`。
3090-B 正在 Windows/WSL2 改造期，当前 `OFFLINE`，不纳入本次恢复证明。

---

## 2. 双方职责和不可越界项

### 2.1 动画管家负责

1. 接收图片、视频和 ZIP，抽取序列帧，冻结本次不可变输入集合；
2. 生成唯一 `external_batch_id`，并持久化统一调度中心返回的 `batch_id`；
3. 按 ordinal 生成严格 manifest，计算每帧大小和 SHA-256，创建 `ZIP_STORED` 输入包；
4. 网络重试复用原 ZIP、原 manifest 和原 `Idempotency-Key`；
5. 只在用户真实点击取消或业务明确取消时调用取消接口，并记录操作者、时间、request ID；
6. 只把父状态 `SUCCEEDED` 当完整成功；其他状态禁止进入 Cherry、编码或最终发布；
7. 下载后核对整包 SHA、manifest、帧集合、ordinal、路径、命名、逐帧 SHA、PNG 和 Alpha；
8. 全部校验通过后，才把 staging 目录原子提升为动画管家的正式输出目录；
9. 查询不明、网络断开或动画管家重启时，继续查询已持久化的原 `batch_id`，不能静默改走本机。

### 2.2 统一调度中心负责

1. 接单时严格校验 ZIP、manifest、帧集合、大小、SHA 和图片可解码性；
2. 固定创建时启用的精确 ImageClip 工作流版本；
3. 有界拆分逐帧 child job，按照节点健康、兼容性、槽位、租户公平性和热工作流动态分发；
4. 节点执行前强制核对 ImageClip Git 提交、管线内容哈希和 ComfyUI class inventory；
5. 上传输入后回读远端字节并核对大小/SHA，通过后才提交 ComfyUI prompt；
6. 维护租约、重试、节点失联恢复、进度、逐帧审计以及真正的用户取消；
7. 一帧最终失败时继续处理其他帧，完成事实收集后把父任务收敛为 `FAILED`；
8. 只有全部帧成功才生成唯一结果 ZIP，任何失败批次都不暴露部分结果；
9. 顶层任务列表只显示父任务，逐帧 child job 只在详情和分页接口出现。

### 2.3 明确禁止

- 统一调度中心不得修改 ImageClip 仓库、工作流 JSON、节点参数、模型、提示词或最终输出语义；
- 不得用预览图、中间图、黑底图或调试图替代最终 RGBA PNG；
- 动画管家不得在远端状态不明确时建立第二个相同业务批次；
- 任一方不得把系统失败、连接中断或进程重启记为“用户取消”；
- 任一方不得在父任务未 `SUCCEEDED` 时发布已有的部分帧。

---

## 3. TLS、身份、幂等和追踪字段

基础地址：

```text
https://10.3.34.11
```

生产客户端必须信任仓库中的内网 CA：

```text
deploy/control-plane/nginx/certs/lan-ca.crt
SHA-256: ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b
```

禁止使用 `verify=False` 或 `curl -k`。推荐动画管家使用专用 API Key：

```http
X-API-Key: gpc_<prefix>_<secret>
```

创建、查询、SSE、取消、详情和下载必须使用同一客户身份。联调期可以按 Nginx 看到的真实来源 IP
自动认证，但来源 IP 变化后将无法访问原租户资源，所以生产必须使用稳定 API Key。

每个请求建议携带：

```http
X-Request-ID: am-<业务短ID>-<动作>-<序号>
```

必须持久化以下关联键：

| 字段 | 生成方 | 生命周期与用途 |
|---|---|---|
| `external_batch_id` | 动画管家 | 一个不可变业务 generation 永久唯一 |
| `Idempotency-Key` | 动画管家 | 创建重放复用；内容改变必须换 generation 和 key |
| `batch_id` | 统一调度中心 | 创建成功后持久化，后续查询/取消/下载的唯一主键 |
| `X-Request-ID` | 请求方或服务端 | 每次 HTTP 动作的日志与审计关联键 |
| ordinal | 动画管家 manifest | `0..N-1`，重试和跨节点执行都不能改变 |
| child job ID | 统一调度中心 | 逐帧内部审计，不作为动画管家重建父任务的依据 |

---

## 4. 创建批次

### 4.1 HTTP 请求

```http
POST /api/v1/batches/imageclip-rgba HTTP/1.1
Host: 10.3.34.11
X-API-Key: gpc_xxx
Idempotency-Key: assetclaw:episode01:shot010:matting:g1
X-Request-ID: am-shot010-g1-create-01
Content-Type: multipart/form-data; boundary=...

archive=<frames.zip; application/zip>
manifest=<UTF-8 JSON 字符串>
```

multipart 字段名严格为 `archive` 和 `manifest`。创建接口同步完成上传落盘、ZIP 安全检查、逐帧
大小/SHA 和图片解码检查，成功返回 `202`，但不会同步等待 GPU 推理。

### 4.2 manifest 1.0

```json
{
  "schema_version": "1.0",
  "external_batch_id": "assetclaw:episode01:shot010:matting:g1",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "episode_01/shot_010/frame_000001.png",
      "size_bytes": 4839201,
      "sha256": "64位小写十六进制"
    }
  ]
}
```

冻结规则：

| 字段 | 规则 |
|---|---|
| `schema_version` | 只能为字符串 `1.0` |
| `external_batch_id` | 1～128 字符，同一客户永久唯一 |
| `failure_policy` | 只能为 `all_or_nothing` |
| `output_naming` | 只能为 `preserve_stem_png` |
| `parameters` | 当前只能发 `{}` |
| `frames` | 1～5000 项，数组顺序与 ordinal 一致 |
| `ordinal` | 严格连续 `0..N-1`，不可跳号、重复或倒序 |
| `relative_path` | UTF-8 NFC、POSIX `/`、安全相对路径 |
| `size_bytes` | 1～67108864，必须等于 ZIP 内真实字节数 |
| `sha256` | 真实输入字节的 64 位小写 SHA-256 |

manifest 为严格模式。禁止增加 `callback_url`、逐帧 parameters、客户端指定 node、`best_effort`
或其他未定义字段。

### 4.3 路径、命名与 ZIP

- 禁止绝对路径、反斜杠、空段、`.`、`..`、NUL、非 NFC 和大小写折叠后重名；
- 输出保留原相对目录与 stem，只把最后一个扩展名替换成 `.png`；
- 图片条目必须是 `ZIP_STORED`，不能 Deflate；
- ZIP 条目集合必须与 manifest 路径集合完全一致；
- 不能包含 README、manifest、副本、隐藏文件、显式目录或非图片条目；
- 禁止加密、符号链接、硬链接、设备文件、重复路径和目录逃逸；
- 图片必须是可解码 JPEG、PNG 或 WebP，单图最多 40000000 像素。

### 4.4 成功响应和幂等重放

首次接收返回 HTTP `202`：

```json
{
  "batch_id": "7f441948-886b-4ff5-81af-3354be978fdd",
  "external_batch_id": "assetclaw:episode01:shot010:matting:g1",
  "status": "QUEUED",
  "total_items": 480,
  "accepted_bytes": 123456789,
  "status_url": "/api/v1/batches/7f441948-886b-4ff5-81af-3354be978fdd",
  "events_url": "/api/v1/batches/7f441948-886b-4ff5-81af-3354be978fdd/events",
  "manifest_url": "/api/v1/batches/7f441948-886b-4ff5-81af-3354be978fdd/manifest"
}
```

同租户、同 key、同规范化 manifest、同工作流版本重放返回 HTTP `200` 和原 `batch_id`。创建
请求超时且不知道服务端是否接收时，必须用原 ZIP、原 manifest、原 key 重试。不能生成新 ID。

```text
同 key + 不同内容       → 409 IDEMPOTENCY_CONFLICT
不同 key + 相同 external → 409 EXTERNAL_BATCH_CONFLICT
```

---

## 5. 查询、进度、SSE 和排队反馈

父状态查询：

```http
GET /api/v1/batches/{batch_id}
X-API-Key: gpc_xxx
X-Request-ID: am-shot010-g1-status-0001
```

典型响应：

```json
{
  "batch_id": "...",
  "external_batch_id": "...",
  "status": "RUNNING",
  "workflow_key": "imageclip-rgba",
  "workflow_version": "2026.07.27-721f7d6-r1",
  "progress": 52.5,
  "counts": {
    "total": 480,
    "pending": 216,
    "queued": 9,
    "running": 2,
    "succeeded": 252,
    "failed": 1,
    "cancelled": 0
  },
  "node_distribution": {
    "control-4090": 84,
    "worker-3090-a": 86
  },
  "error": {
    "code": "COMFY_EXECUTION_ERROR",
    "message": "帧 34 video_01/0034.png: ..."
  },
  "artifacts": []
}
```

即使 `counts.failed > 0`，父任务也可能暂时仍为 `RUNNING`：这表示失败帧已隔离，其他帧正在继续
处理，以取得完整审计事实；它不表示部分成功可发布。动画管家必须等待父终态。

建议每 3 秒轮询。连续网络错误使用 1、2、4、8、15、30 秒退避，始终查询同一 batch ID。
排队时向用户展示“已进入 GPU 集群队列”和 counts；不要把聚合 `progress` 当精确 ETA。

SSE：

```http
GET /api/v1/batches/{batch_id}/events
Accept: text/event-stream
```

SSE 只用于低延迟提示，GET 父状态始终是最终真相。SSE 断线不代表任务失败或取消。

逐帧分页：

```http
GET /api/v1/batches/{batch_id}/manifest?offset=0&limit=200
GET /api/v1/batches/{batch_id}/manifest?offset=0&limit=200&status=FAILED
```

`limit` 默认 200、最大 500，items 始终按 ordinal 排序。顶层任务列表不得展示内部 child job；
一个 5000 帧批次仍只显示一行父任务。

---

## 6. V4 状态机和取消语义

### 6.1 父状态机

```text
VALIDATING → QUEUED → RUNNING → ASSEMBLING → SUCCEEDED
                 │        │
                 │        ├─ 子帧最终失败：父任务保持 RUNNING，其他帧继续
                 │        │                    └─ 全部收敛后 → FAILED
                 │        │
                 └────────┴─ 明确取消请求 → CANCELLING → CANCELLED
任一不可恢复的父级系统错误 ─────────────────────────→ FAILED
```

终态只有 `SUCCEEDED`、`FAILED`、`CANCELLED`。当前没有 `PARTIAL`。

| 状态 | 动画管家含义 | 是否可发布 |
|---|---|---|
| `VALIDATING` | 正在检查 ZIP/manifest | 否 |
| `QUEUED` | 已受理，等待执行 | 否 |
| `RUNNING` | 正在处理；可能包含已隔离失败帧 | 否 |
| `ASSEMBLING` | 全帧成功，正在生成并验证结果包 | 否 |
| `SUCCEEDED` | 完整结果包已原子发布 | 校验通过后才可 |
| `FAILED` | 至少一帧或父级处理最终失败 | 否 |
| `CANCELLING` | 已收到明确取消请求，正在安全停止 | 否 |
| `CANCELLED` | 明确取消已收敛 | 否 |

### 6.2 唯一合法取消入口

```http
POST /api/v1/batches/{batch_id}/cancel
X-API-Key: gpc_xxx
Idempotency-Key: <external_batch_id>:cancel
X-Request-ID: am-shot010-g1-cancel-01
```

取消 key 必须等于 external ID 加 `:cancel`。统一调度中心只有在认证通过且收到该请求（或管理员
执行等价的已审计操作）后，才设置 `cancel_requested=true` 并进入 `CANCELLING`。

动画管家必须记录：业务操作者、业务原因、请求时间、request ID、HTTP 结果和最终父状态。没有
这条审计记录而服务端出现 `CANCELLING/CANCELLED`，双方应立即按异常对账，不能假定用户取消。

### 6.3 单帧最终失败

达到 child job 最大尝试次数后：

1. 该帧标记 `FAILED` 并保留最后错误、节点、attempts 和 job ID；
2. 父任务记录 `batch.item_failed_continuing`，但不设置 `cancel_requested`；
3. 未开始帧继续物化，已排队/运行帧继续收敛；
4. 所有帧终态后，父任务变成 `FAILED`；
5. 不进入 `ASSEMBLING`，不生成 `result_archive`，不发布任何部分结果。

Web 如果遇到升级前遗留的“错误 + CANCELLING”记录，显示“失败收尾中”，不得显示为用户取消。
该 `FAILING` 只是一种展示兼容标签，不是公开 API 状态。

---

## 7. ComfyUI 输入传输完整性门禁

每个 child job 在提交 prompt 前必须按以下事务执行：

```text
读取主控冻结输入
  → 流式计算本地 size + SHA-256
  → POST /upload/image，overwrite=true，使用 job 独立子目录
  → GET /view?type=input 回读远端最终字节
  → 流式计算远端 size + SHA-256
  → 本地与远端 size/SHA 完全一致
  → 才允许 POST /prompt
```

默认最多进行 3 次上传完整性尝试，采用短指数退避。重试仍使用同一 job 目录和文件名，但必须
`overwrite=true`，从而修复前一次中断遗留的零字节或截断文件。

| 失败场景 | 错误码 | 行为 |
|---|---|---|
| 本地输入为 0 字节 | `INPUT_INVALID` | 不上传、不提交 prompt |
| 上传/回读超时 | `COMFY_TIMEOUT` | 在内部预算内重试 |
| 连接中断 | `COMFY_CONNECT_ERROR` | 在内部预算内重试 |
| 远端大小或 SHA 不一致 | `COMFY_UPLOAD_INTEGRITY_FAILED` | 强制覆盖后重试 |
| 3 次仍不一致 | 上述最后错误 | child job 失败，由调度器执行 job 重试策略 |

上传验证失败不消耗 ComfyUI 推理，因为 prompt 尚未提交。远端输入通过核对后仍可能发生真正的
工作流执行错误；两者在错误码、日志和逐帧详情中必须区分。

---

## 8. 重试、恢复和去重

### 8.1 三层重试边界

1. **HTTP 创建重试（动画管家）**：同 ZIP、manifest、key 重放，获得原 batch ID；
2. **输入传输重试（Comfy 客户端）**：单 child job 内最多 3 次覆盖上传和回读校验；
3. **child job 重试（Scheduler）**：节点、租约或可恢复执行错误，最多 3 次 job attempt。

三层 attempt 必须分开记录，不能把一次上传重试显示成新的动画业务任务。

### 8.2 控制面或 Scheduler 重启

- PostgreSQL 是父任务、逐帧状态、幂等、租约和审计的真相来源；
- Redis 只用于通知，不是唯一状态存储；
- Scheduler 重启后先恢复过期租约并对账 Comfy queue/history，再继续原 child job；
- 动画管家始终查询原 batch ID，不重新上传或新建 generation；
- 发现 DB job 为运行中但 Comfy queue/history 已终态时，必须以审计方式收敛并释放节点槽位。

### 8.3 节点离线

当前有效执行节点是 4090 和 3090-A。3090-B 离线不会导致父任务取消；新帧只分配给兼容在线
节点。正在节点上运行的 job 只有在租约/心跳超时和 Comfy 状态对账后才能重试，禁止仅因一次
心跳抖动就失败用户任务。

---

## 9. 完整结果和原子发布

只有父状态 `SUCCEEDED` 时，响应中才会出现唯一 `result_archive`：

```json
{
  "id": "artifact-uuid",
  "kind": "result_archive",
  "filename": "<batch_id>-rgba.zip",
  "content_type": "application/zip",
  "size_bytes": 2189120,
  "sha256": "64位小写 SHA-256",
  "download_url": "/api/v1/batches/<batch_id>/artifacts/<artifact_id>"
}
```

下载：

```http
GET /api/v1/batches/{batch_id}/artifacts/{artifact_id}
X-API-Key: gpc_xxx
```

结果 ZIP：

```text
manifest.json
results/<原相对目录>/<原 stem>.png
```

动画管家必须依次验证：

1. `X-Artifact-SHA256`、artifact 元数据 SHA 和下载字节重算 SHA 三者一致；
2. 包内 batch ID/external ID 等于本地持久化记录；
3. total 和 items 数量等于原输入；
4. ordinal 恰好为 `0..N-1`，无缺失、重复和乱序；
5. 每项输入路径和输入 SHA 等于原 manifest；
6. 输出路径满足 `preserve_stem_png` 且大小写折叠后唯一；
7. ZIP 文件集合恰好等于 `manifest.json + results/...`；
8. 每个输出实际 SHA 等于 output SHA；
9. 每个输出是可解码 PNG，且含 Alpha 通道；
10. 全部通过后才同盘原子发布，再进入 Cherry/编码/发送。

任一项失败都拒绝发布，并保存 ZIP、响应头、batch ID、request ID 和校验错误供双方排查。

---

## 10. 管线版本硬门禁

当前批准基线：

```text
repository:       /opt/imageclip
branch:           main
commit:           721f7d68635ee36d45f545ce2c82037046147442
pipeline_sha256:  00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
workflow_version: 2026.07.27-721f7d6-r1
only output node: SaveImage #25
```

工作流创建时固定版本，节点领取前再次核对 Git 提交、确定性管线 SHA、模型/节点标签和实时
`/object_info` class inventory。任一不一致节点 fail closed，不领取新帧。管线升级只能在活动批次
排空后按受控提交同步并进行真实 smoke；统一调度中心不得自行修改外部工作流内容。

---

## 11. 客户端示例

### 11.1 cURL 创建

```bash
curl --fail-with-body \
  --cacert /path/to/GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/batches/imageclip-rgba' \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" \
  -H 'Idempotency-Key: assetclaw:episode01:shot010:matting:g1' \
  -H 'X-Request-ID: am-shot010-g1-create-01' \
  -F 'archive=@frames.zip;type=application/zip' \
  -F 'manifest=<manifest.json'
```

### 11.2 Python 创建与查询

```python
import json
import time
from pathlib import Path

import httpx

base = "https://10.3.34.11"
headers = {
    "X-API-Key": "从密钥管理读取，不要写死",
    "Idempotency-Key": "assetclaw:episode01:shot010:matting:g1",
    "X-Request-ID": "am-shot010-g1-create-01",
}

with httpx.Client(verify="/path/to/GPU_CONTROL_LAN_CA.crt", timeout=86400) as client:
    with Path("frames.zip").open("rb") as archive:
        response = client.post(
            f"{base}/api/v1/batches/imageclip-rgba",
            headers=headers,
            files={"archive": ("frames.zip", archive, "application/zip")},
            data={"manifest": Path("manifest.json").read_text(encoding="utf-8")},
        )
    response.raise_for_status()
    accepted = response.json()
    batch_id = accepted["batch_id"]  # 立即持久化

    while True:
        status = client.get(
            f"{base}/api/v1/batches/{batch_id}",
            headers={"X-API-Key": headers["X-API-Key"]},
            timeout=30,
        )
        status.raise_for_status()
        body = status.json()
        if body["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(3)

    if body["status"] != "SUCCEEDED":
        raise RuntimeError(f"batch did not succeed: {body}")
```

生产代码还必须实现结果 ZIP 的十项验证，不能仅以 HTTP 200 或文件存在作为成功。

---

## 12. 错误处理矩阵

| 阶段 | code/状态 | 动画管家动作 |
|---|---|---|
| 创建 400 | `MANIFEST_INVALID` | 修正输入；内容改变后换 generation/ID |
| 创建 401/403 | `AUTH_FAILED` | 检查身份，不重建批次 |
| 创建 409 | `IDEMPOTENCY_CONFLICT` | 检查是否用同 key 发送了不同内容 |
| 创建 409 | `EXTERNAL_BATCH_CONFLICT` | 检查 external ID 生命周期 |
| 创建 413 | `BATCH_TOO_LARGE` | 按业务边界拆批并使用新 ID |
| 创建 422 | `FRAME_SET_MISMATCH` | 对齐 ZIP 与 manifest 集合 |
| 创建 422 | `FRAME_HASH_MISMATCH` | 重新冻结输入并计算 size/SHA |
| 创建 422 | `IMAGE_INVALID` | 修复损坏图片或格式/像素限制 |
| 创建 429 | 限流 | 对同请求、同 key 退避重试 |
| 创建 5xx/网络 | 状态不明确 | 用原 ZIP/manifest/key 重放，不新建业务任务 |
| 运行中 failed>0 | 父仍 RUNNING | 等待其余帧收敛，不取消、不发布 |
| 父 FAILED | 完整失败 | 读取父 error/失败帧；修正原因后新 generation 全批重提 |
| 父 CANCELLING | 只应源于明确取消 | 对账动画管家取消审计；继续轮询终态 |
| 查询网络错误 | 状态不明确 | 退避后继续查原 batch ID |

---

## 13. 双方真实验收清单

### 13.1 GPU Control 自动回归

- [x] Python 源码编译检查通过；
- [x] 复现首次上传留下 0 字节远端文件；
- [x] 第二次使用 `overwrite=true` 覆盖并回读验证成功；
- [x] Web 构建通过，新增“失败收尾中”并区分“取消中”；
- [x] 原生产父任务保持同 batch ID 恢复，4090 与 3090-A 同时继续执行；
- [ ] 活动生产队列排空后构建并滚动发布正式 Scheduler/API 镜像；
- [ ] 发布后执行 1 帧、6 帧和 64 帧真实回归并保存 artifact SHA。

### 13.2 动画管家联合验收

1. **单帧正常**：创建、查询、下载、十项校验全部通过；
2. **30 帧嵌套路径**：中文 NFC、两级目录、ordinal、文件名和 SHA 全部保持；
3. **三个并发视频**：两个在线节点均参与，不因一个长视频让其他父任务永远尚未分配；
4. **创建响应丢失**：原 key 重放返回相同 batch ID，Web 不新增父任务；
5. **零字节上传注入**：远端第一次留下空文件，系统自动覆盖修复且不提交损坏 prompt；
6. **单帧永久失败**：其余帧继续，父任务最终 FAILED，无 artifact，Web 不显示为用户取消；
7. **真实取消**：动画管家记录取消审计，父任务 CANCELLING → CANCELLED，无 artifact；
8. **动画管家重启**：仅凭持久化 batch ID 恢复轮询和下载，不建立重复任务；
9. **Scheduler 重启**：安全窗口重启后原 batch/job ID、ordinal、attempts 和进度连续；
10. **结果篡改**：缺帧、错序、错名、错误 SHA 或无 Alpha 均不能进入 Cherry；
11. **管线漂移**：错误提交/哈希节点停止领新帧，兼容在线节点继续；
12. **3090-B 回归**：B 完成 Windows/WSL2 改造后，用相同门禁和测试加入，不修改协议。

每项证据必须包含：测试时间、external ID、batch ID、request ID、帧数、输入字节、工作流版本、
节点分布、每节点成功数、attempts、父终态、artifact SHA 和动画管家最终发布目录。

---

## 14. 发布门禁与当前待办

本 V4 的源码修复已经完成本地编译、上传完整性单测和 Web 构建验证，但为保护当前运行中的 GPU
任务，不能在活动槽位上直接替换 Scheduler/API。正式发布顺序必须是：

1. 等待所有 GPU 父任务进入终态；
2. 检查 PostgreSQL 两个在线节点 `current_jobs=0`；
3. 检查 4090、3090-A 的 Comfy `/queue` 均无 running/pending；
4. 构建带固定版本号的新 API/Scheduler/Web 镜像并记录 image digest；
5. 先滚动更新 Web，再更新 API/Scheduler；不重启 ComfyUI；
6. 检查容器健康、Scheduler 心跳、节点心跳、Redis subscriber 和 DB 租约；
7. 执行自动回归和联合验收；
8. 更新本文状态为 `FROZEN / PRODUCTION ACCEPTED`，记录 Git commit、镜像 digest 和验收 ID；
9. 最后再提交/推送 Git 和 LFS 归档。

在第 8 步完成前，V4 是实施候选而不是已经部署完成的虚假声明。旧生产任务的定点恢复已经生效；
永久修复必须通过上述安全滚动门禁后才算上线。

---

## 15. 双方确认回执

动画管家请逐项回复：

- [ ] 接受 manifest 1.0、`ZIP_STORED` 和 `all_or_nothing`；
- [ ] 接受 V4 状态机：失败收尾不等于用户取消；
- [ ] 只通过明确取消 API 发起取消，并保存完整取消审计；
- [ ] 持久化 external ID、idempotency key、batch ID 和 request ID；
- [ ] 状态不明确时只重放原 key 或查询原 batch ID，不创建第二份任务；
- [ ] 父 RUNNING 且 failed>0 时继续等待，不发布部分结果；
- [ ] 只有 `SUCCEEDED` 且十项校验通过后才原子发布；
- [ ] 接受工作流提交/内容哈希硬门禁和统一调度中心不改外部工作流的边界；
- [ ] 按第 13.2 节共同完成真实验收并保存证据；
- [ ] 3090-B 重新加入只扩容执行节点，不改变 V4 API 合同。

双方确认并完成真实验收后，在本文追加最终 Git commit、镜像 digest、真实 batch ID、artifact SHA、
节点分配和确认人/时间，文档状态改为 `FROZEN / PRODUCTION ACCEPTED`。
