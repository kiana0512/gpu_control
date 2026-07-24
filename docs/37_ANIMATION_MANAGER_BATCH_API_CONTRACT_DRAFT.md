# 动画管家 × GPU Control 批量抠图接口合同（评审稿 v0.1）

状态：`SUPERSEDED / DO NOT IMPLEMENT`。本草案包含 callback、best_effort、追加后缀命名、
失败帧重试等尚未落地的候选字段，不能再用于联调。2026-07-24 起唯一冻结合同是
`38_GPU_CONTROL_MATTING_HANDOFF_V2.md`。本文件仅保留评审历史。

总体设计和内部调度原理见 `36_2026-07-24_ANIMATION_BATCH_MATTING_DESIGN.md`。

## 1. 双方职责边界

### 动画管家负责

1. 为一个业务批次生成全局唯一且可长期追踪的 `external_batch_id`。
2. 按本合同生成 ZIP 和 manifest；manifest 的 `ordinal`、相对路径和 SHA-256 是业务真值。
3. 同一批次重试时复用同一个 `Idempotency-Key`，不得用相同 key 提交不同内容。
4. 保存创建响应中的 `batch_id`；轮询/SSE/回调中断后仍能用它恢复查询。
5. 下载结果 ZIP 后校验响应提供的 SHA-256，并按结果 manifest 对账。
6. 只有收到 `SUCCEEDED` 才把结果当作完整成功；`PARTIAL`、`FAILED` 不能静默当成功。

### GPU Control 负责

1. 流式接收并安全校验 ZIP、manifest、图片格式、路径、大小和逐帧 SHA-256。
2. 持久化父批次和所有帧，向 4090、3090-A、3090-B 动态拆分，不因节点完成顺序改变
   `ordinal`、目录或命名映射。
3. 对单帧执行租约、超时、重试、故障节点规避和全过程审计。
4. 在多用户、多批次之间公平调度；无其他请求时允许一个批次吃满三个执行槽。
5. 汇总前逐项检查数量、路径和输出 SHA-256，原子发布最终 ZIP 和 manifest。
6. 提供批次查询、帧分页、SSE、取消、失败帧重试、结果下载和可选签名回调。

## 2. 连接与认证

- 基础地址：`https://10.3.34.11`
- API 版本：路径 `/api/v1`，冻结后 v1 内只做向后兼容扩展。
- 支持现有 `X-API-Key: gpc_...`；也支持已在 Web UI 中唯一绑定的真实来源 IP 免 key。
- 生产系统对系统调用推荐 API Key；若继续免 key，动画管家必须提供固定来源 IP 并由双方确认
  Nginx 传递的是真实源地址。
- 每个请求和响应都使用 `X-Request-ID`；调用方可自行提供，不提供则由 GPU Control 生成。
- 所有时间为 UTC 的 RFC 3339，例如 `2026-07-24T03:20:15.123Z`。

## 3. 创建批次

### 3.1 请求

```http
POST /api/v1/batches/imageclip-rgba HTTP/1.1
Host: 10.3.34.11
X-API-Key: gpc_xxx                 # 若采用来源 IP 认证则省略
Idempotency-Key: animation:shot-010:v3
X-Request-ID: am-shot-010-v3-create-01
Content-Type: multipart/form-data; boundary=...

archive=<frames.zip; application/zip>
manifest=<下述 JSON; application/json>
```

`Idempotency-Key` 必填，1～128 个可打印 ASCII 字符。推荐由动画管家的业务任务 ID 和版本组成，
同一业务内容的网络重试必须复用；业务内容改变必须换 key。

### 3.2 manifest v1

```json
{
  "schema_version": "1.0",
  "external_batch_id": "shot-010-v3",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "callback_url": "https://animation.example.com/hooks/gpu-control",
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "scene_010/layer_a/frame_000001.png",
      "size_bytes": 4839201,
      "sha256": "64位小写十六进制"
    },
    {
      "ordinal": 1,
      "relative_path": "scene_010/layer_a/frame_000002.png",
      "size_bytes": 4851033,
      "sha256": "64位小写十六进制"
    }
  ]
}
```

字段合同：

| 字段 | 必填 | 规则 |
|---|---:|---|
| `schema_version` | 是 | v1 固定为 `1.0` |
| `external_batch_id` | 是 | 1～128 字符，在动画管家侧唯一且可追踪 |
| `failure_policy` | 是 | 首版固定 `all_or_nothing`；是否启用 `best_effort` 另行确认 |
| `output_naming` | 是 | `preserve_stem_png` 或 `append_rgba_suffix` |
| `parameters` | 否 | 对本批全部帧生效的 ImageClip 参数；默认 `{}` |
| `callback_url` | 否 | 未启用回调时省略，调用方使用轮询或 SSE |
| `frames` | 是 | 非空；`ordinal` 必须从 0 开始连续、唯一，数组顺序应与 ordinal 一致 |
| `relative_path` | 是 | UTF-8 NFC、POSIX `/` 分隔、相对路径；必须与 ZIP 图片条目完全一致 |
| `size_bytes` | 是 | ZIP 解压后的实际字节数 |
| `sha256` | 是 | 解压后原图的 SHA-256，小写十六进制 |

`parameters` 第一版对整个批次一致。如果将来需要逐帧不同参数，应增加显式的
`frame.parameters`，不能通过文件名暗示。

### 3.3 ZIP 合同

- ZIP 中只放 manifest 列出的图片和必要目录；不放第二份 manifest。
- 支持 `.png`、`.jpg`、`.jpeg`、`.webp`，实际解码格式必须与内容一致。
- ZIP 图片条目集合必须与 `frames[].relative_path` 集合完全相等，缺少或多出都拒绝整批。
- 禁止绝对路径、盘符、空路径、`.`、`..`、反斜线、NUL、符号链接、硬链接和设备文件。
- 规范化后大小写冲突或 Unicode 冲突视为重复路径并拒绝。
- 不依赖 ZIP 条目物理顺序；权威顺序只来自 `ordinal`。
- macOS 生成的 `__MACOSX`、`.DS_Store` 等额外文件应在动画管家打包前排除。

### 3.4 命名规则

`preserve_stem_png`：

```text
scene/layer/frame_000001.png  -> scene/layer/frame_000001.png
scene/layer/frame_000002.jpg  -> scene/layer/frame_000002.png
```

如果多个输入会映射到同一输出（如同目录下的 `frame_1.jpg` 和 `frame_1.webp`），创建阶段返回
`OUTPUT_PATH_CONFLICT`。调用方可改用 `append_rgba_suffix`：

```text
scene/layer/frame_000002.jpg -> scene/layer/frame_000002.jpg.rgba.png
```

### 3.5 成功响应

全部接收、解压、校验并持久化后返回：

```http
HTTP/1.1 202 Accepted
X-Request-ID: am-shot-010-v3-create-01
Content-Type: application/json

{
  "batch_id": "b9bf8a86-...",
  "external_batch_id": "shot-010-v3",
  "status": "QUEUED",
  "total_items": 480,
  "accepted_bytes": 2283910221,
  "status_url": "/api/v1/batches/b9bf8a86-...",
  "events_url": "/api/v1/batches/b9bf8a86-.../events",
  "manifest_url": "/api/v1/batches/b9bf8a86-.../manifest",
  "callback_secret": "仅在配置 callback_url 时返回且只显示一次"
}
```

在接收大 ZIP 时，HTTP 连接只等待上传和校验，不等待 GPU 出图。动画管家上传超时和 GPU 批次
执行超时必须分开配置。

相同 key、相同内容重复提交返回 `200` 和原 `batch_id`；相同 key、不同 manifest/文件散列
返回 `409 IDEMPOTENCY_CONFLICT`。

## 4. 查询批次

```http
GET /api/v1/batches/{batch_id}
```

```json
{
  "batch_id": "b9bf8a86-...",
  "external_batch_id": "shot-010-v3",
  "status": "RUNNING",
  "progress": 42.5,
  "counts": {
    "total": 480,
    "pending": 270,
    "queued": 6,
    "running": 3,
    "succeeded": 201,
    "failed": 0,
    "cancelled": 0
  },
  "node_distribution": {
    "control-4090": 69,
    "worker-3090-a": 66,
    "worker-3090-b": 66
  },
  "created_at": "2026-07-24T03:20:15.123Z",
  "started_at": "2026-07-24T03:20:18.003Z",
  "finished_at": null,
  "error": null,
  "artifacts": []
}
```

外部批次状态：

| 状态 | 含义 |
|---|---|
| `VALIDATING` | 正在校验上传内容，通常不会作为创建后的首个状态 |
| `QUEUED` | 已持久化，等待生成子任务或执行槽 |
| `RUNNING` | 至少一帧正在执行或已成功，仍有未完成帧 |
| `ASSEMBLING` | 所有帧成功，正在校验并生成结果包 |
| `SUCCEEDED` | 完整结果已原子发布，可下载 |
| `PARTIAL` | 仅在双方启用 best-effort 后出现，存在缺失帧 |
| `FAILED` | 至少一帧重试耗尽或汇总校验失败 |
| `CANCELLING` | 正在停止排队/运行帧 |
| `CANCELLED` | 已取消，不会再产生新执行 |

`progress` 仅用于展示，不作为成功依据；调用方必须看终态和 counts。执行过程中允许因重试保持
不变，但不得倒退。完成条件是 `status=SUCCEEDED` 且 `succeeded=total`。

## 5. 帧清单与分页

```http
GET /api/v1/batches/{batch_id}/manifest?offset=0&limit=200&status=FAILED
```

响应按 `ordinal` 升序。每项至少包含：

```json
{
  "ordinal": 17,
  "input_relative_path": "scene/layer/frame_000018.jpg",
  "output_relative_path": "scene/layer/frame_000018.png",
  "input_sha256": "...",
  "output_sha256": "...",
  "status": "SUCCEEDED",
  "job_id": "...",
  "node_id": "worker-3090-a",
  "attempts": 1,
  "error": null
}
```

管理 Web UI 默认只显示父批次；进入批次详情后才分页显示这些帧，避免任务列表刷屏。

## 6. SSE 进度

```http
GET /api/v1/batches/{batch_id}/events
Accept: text/event-stream
Last-Event-ID: 182
```

服务端事件具有递增 `id`，客户端断线后用 `Last-Event-ID` 续传。主要事件：

- `batch.queued`
- `batch.running`
- `batch.progress`（聚合/限频发送，不为每个内部状态刷一条）
- `frame.failed`（包含 ordinal、路径、error code，不包含敏感内部栈）
- `batch.assembling`
- `batch.succeeded` / `batch.failed` / `batch.cancelled`

SSE 只是加速状态通知；断线或主控重启后，动画管家仍以 `GET batch` 的持久化状态为准。

## 7. 结果下载

`SUCCEEDED` 响应中的 artifacts 示例：

```json
{
  "id": "...",
  "kind": "result_archive",
  "filename": "shot-010-v3-rgba.zip",
  "content_type": "application/zip",
  "size_bytes": 1942839201,
  "sha256": "...",
  "download_url": "/api/v1/batches/{batch_id}/artifacts/{artifact_id}"
}
```

ZIP 内容：

```text
results/scene_010/layer_a/frame_000001.png
results/scene_010/layer_a/frame_000002.png
manifest.json
```

内部 `manifest.json` 按 ordinal 升序包含全部输入/输出映射及散列。动画管家下载后必须：

1. 校验整个 ZIP 的 SHA-256；
2. 校验 `manifest.total == manifest.items.length`；
3. 校验 ordinal 从 0 连续到 `total-1`；
4. 校验每项输出存在且 SHA-256 相同；
5. 校验后再原子替换自身业务目录，不能边解压边向下游暴露。

首版下载支持完整文件；是否需要 HTTP Range/断点续传由真实结果包大小决定。

## 8. 终态回调（可选）

如果使用回调，GPU Control 只在数据库已提交的终态后 POST。请求头：

```text
Content-Type: application/json
X-GPU-Control-Timestamp: 1784863215
X-GPU-Control-Signature: <hex hmac-sha256>
X-Request-ID: ...
```

签名算法：

```text
signature = HEX(HMAC-SHA256(callback_secret, timestamp + "." + raw_body))
```

动画管家先校验时间偏差（建议不超过 5 分钟）和签名，再按 `event + batch_id` 幂等消费。GPU
Control 对非 2xx 回调指数退避重试，动画管家收到重复回调必须返回相同 2xx。

当前 GPU Control 默认拒绝回调到私网地址以防 SSRF。如果动画管家回调地址位于同一 LAN，
双方必须二选一：

1. 第一阶段不使用回调，仅使用轮询/SSE；或
2. GPU Control 增加“客户级精确回调主机/IP + 端口”白名单后再启用私网回调。

不能为了联调直接放开任意私网回调。

## 9. 取消和失败帧重试

```http
POST /api/v1/batches/{batch_id}/cancel
Idempotency-Key: animation:shot-010:v3:cancel
```

取消是幂等的。未执行帧立即取消，运行帧尽最大努力停止；已经成功的产物保留到批次保留期结束。

```http
POST /api/v1/batches/{batch_id}/retry-failed
Idempotency-Key: animation:shot-010:v3:retry:1
```

只重新物化失败或缺失帧；已校验成功帧不重复运行。响应返回原 `batch_id` 和新的 retry generation。
输入内容或参数发生变化时必须创建新批次，不能使用 retry-failed 偷换内容。

## 10. 错误响应

统一结构：

```json
{
  "detail": {
    "code": "FRAME_HASH_MISMATCH",
    "message": "frame 17 的 SHA-256 与 manifest 不一致",
    "batch_id": null,
    "ordinal": 17,
    "relative_path": "scene/layer/frame_000018.png",
    "request_id": "..."
  }
}
```

首版错误码至少包括：

| HTTP | code | 含义/调用方动作 |
|---:|---|---|
| 400 | `MANIFEST_INVALID` | 修正 JSON、字段或 ordinal 后换新 key 提交 |
| 401/403 | `AUTH_FAILED` | 检查 API Key/来源 IP，不自动无限重试 |
| 409 | `IDEMPOTENCY_CONFLICT` | 相同 key 内容不同，停止并人工排查 |
| 409 | `OUTPUT_PATH_CONFLICT` | 调整输入命名或使用 suffix 规则 |
| 413 | `BATCH_TOO_LARGE` | 拆批或双方调整已批准限额 |
| 422 | `ARCHIVE_ENTRY_INVALID` | 修正 ZIP 路径/类型 |
| 422 | `FRAME_SET_MISMATCH` | ZIP 与 manifest 的文件集合不同 |
| 422 | `FRAME_HASH_MISMATCH` | 重新打包/传输，不进入 GPU 队列 |
| 422 | `IMAGE_INVALID` | 对应文件无法安全解码或尺寸超限 |
| 429 | `RATE_LIMITED` | 按 `Retry-After` 退避后重试 |
| 503 | `STORAGE_UNAVAILABLE` | 保持同一 key 退避重试 |

批次运行后的单帧错误通过查询/SSE/回调报告，不把已经返回 202 的创建请求改成同步错误。

## 11. 容量与保留期（双方待填数值）

以下必须在真实样本测量后冻结，GPU Control 会按客户配置而不是写死全局：

| 项目 | 建议起始值 | 最终值 |
|---|---:|---:|
| 单批最大帧数 | 5,000 | 待确认 |
| 单帧压缩前最大值 | 64 MiB | 待确认 |
| 单批解压后最大值 | 100 GiB | 待确认 |
| 最大图片像素 | 100 MP | 待确认 |
| 同客户活跃批次数 | 4 | 待确认 |
| 三卡并行帧数 | 3（每卡 1） | 3 |
| feeder 就绪窗口 | 12 | 压测后确认 |
| 成功结果保留期 | 7 天 | 待确认 |
| 失败诊断保留期 | 14 天 | 待确认 |
| 上传连接超时 | 30 分钟 | 待确认 |

单图实时请求使用 `NORMAL` 优先级，动画序列帧子任务使用 `BATCH` 优先级并可老化，确保批量
工作吃满空闲三卡但不把普通 API 永久堵住。

## 12. 双方必须确认的清单

动画管家侧需要给出：

- [ ] 当前能输出 ZIP、多个 multipart 文件，还是对象存储 URL？
- [ ] 一个批次典型/最大帧数、单帧尺寸、总字节数和目录层级？
- [ ] 输入主要是 PNG 还是包含 JPG/WEBP？
- [ ] 是否能计算并提供逐帧 SHA-256？
- [ ] `ordinal` 是从现有业务清单获得，还是只能从文件名推导？
- [ ] 输出必须覆盖原目录，还是下载到独立结果目录后由动画管家合并？
- [ ] 整批必须全成功，还是允许带缺失清单的 partial？
- [ ] 使用轮询、SSE，还是终态回调？回调地址是否为私网？
- [ ] 使用 API Key 还是固定来源 IP 认证？
- [ ] 结果被动画管家成功接收后，是否需要显式 ACK 以允许提前清理？

GPU Control 侧确认：

- [x] 三个节点各一个执行槽，可将同一批次的不同帧分发到不同节点。
- [x] 单帧现有工作流、租约、重试、节点追踪、产物 SHA-256 可复用。
- [x] 单图 API 保持兼容，批处理不会替换现有入口。
- [ ] 上表容量数值与存储保留期冻结。
- [ ] 私网回调策略冻结。
- [ ] HTTP Range/断点上传和断点下载是否进入 v1 范围冻结。

## 13. 联调验收用例

第一轮不做复杂压力测试，必须逐项通过：

1. 30 帧单目录：三台 GPU 都参与，结果 30/30，ordinal、路径、SHA 全匹配。
2. 30 帧多层目录：相同文件名位于不同目录，结果树不串目录。
3. 两个批次同时提交：两批均有进展，不能由第一批独占到结束。
4. 批次执行时提交一个普通单图请求：下一个空闲槽优先服务普通请求，批次随后继续。
5. 创建请求在收到响应前断开并使用相同幂等 key 重试：只能存在一个 batch。
6. 人为重启一台 3090：仅受影响帧重试，最终不漏帧、不重复。
7. ZIP 少一帧、多一帧、散列错误、坏图、路径穿越：整批在进 GPU 队列前拒绝。
8. 单帧重试耗尽：批次 FAILED，失败 ordinal/路径/节点/尝试可追溯；retry-failed 只补失败帧。
9. 运行中取消：不再物化新帧，批次最终 CANCELLED。
10. 结果下载中断后重下：ZIP SHA 不变，动画管家校验后才对下游发布。

通过后保存一份双方共同签字的请求 manifest、最终 manifest、ZIP SHA、批次事件、三节点分配
和故障恢复证据，作为 v1 接口验收基线。
