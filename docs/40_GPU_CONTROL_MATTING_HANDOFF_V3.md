# 动画管家 ↔ GPU Control 批量抠图交接协议 V3

文档状态：`INTEGRATION CANDIDATE / GPU Control 侧压力验收通过，等待动画管家真实联调`
GPU Control 版本：`1.3.3`
批次 manifest 协议：`1.0`
文档日期：2026-07-27
生产入口：`https://10.3.34.11`

本文是动画管家调用 GPU Control 三节点集群执行 ImageClip RGBA 序列帧抠图的 V3 唯一交接
合同。它取代 V1、V2 以及早期草案。动画管家可以立即按本文做第二轮接口对齐；真实请求验收完成
后，双方只把文档状态从 `INTEGRATION CANDIDATE` 改为 `FROZEN`，不再暗改字段。

实现或响应与本文不一致时，双方必须停止该批次的后续 Cherry、编码和发布，使用
`external_batch_id`、`batch_id`、`X-Request-ID`、帧 ordinal 和 artifact SHA-256 对账。禁止猜测
字段、跳过校验、把部分结果当成功，或在远端状态不明确时静默切到本机重复执行。

## 1. V3 解决的核心问题

V3 同时解决以下生产问题：

1. 一个动画业务批次可以包含 1～5000 张序列帧，但 Web 顶层只显示一个父任务，不按帧刷屏。
2. 每帧仍是可追溯的内部任务，可以分散到 4090、3090-A、3090-B 并行处理。
3. GPU 完成顺序可以任意；最终结果必须按原 ordinal、目录和文件名完整重组。
4. 任何一帧失败、缺失、错名、错序、错 SHA、非 PNG 或无 Alpha，整批都不能发布。
5. 只有最终发布节点的 RGBA PNG 会进入结果包；预览、中间抠图、黑底图和调试图不会返回。
6. 每台 GPU 在签名心跳中报告 ImageClip Git 提交和确定性管线哈希；不一致节点不能接抠图。
7. 工作流创建时固定精确版本，节点领取前再次实时核对提交/哈希，杜绝“登记时一致、执行时漂移”。
8. 真实客户和压测客户在客户、任务列表、统计与调度优先级上隔离；压测只吃真实业务暂时不用的槽位。
9. 创建重放、进程重启、网络断线、节点 DHCP 地址变化、节点暂时离线均有明确恢复路径。

## 2. 当前三节点执行基线

| 节点 | 稳定 node_id | MAC（唯一硬件身份） | 当前地址 | 角色 |
|---|---|---|---|---|
| 4090 | `control-4090` | `58:11:22:c1:66:63` | `10.3.34.11` | 控制面、归档、可调度 GPU |
| 3090-A | `worker-3090-a` | `18:c0:4d:9f:13:13` | `10.3.34.12` | 主算力 |
| 3090-B | `worker-3090-b` | `2c:f0:5d:76:7b:70` | `10.3.34.4`（动态，曾规划 `.14`） | 主算力 |

IP 不是节点身份。节点代理根据到主控的实际路由发现当前 IPv4，使用节点专属 HMAC 签名心跳；主控只在
node_id、MAC、GPU UUID 和请求来源 IP 同时通过校验后更新 `base_url`/`agent_url`。因此 DHCP 地址
变化不会生成新节点，也不会把 B 误认成 A。网络层仍建议为三个 MAC 配置 DHCP 保留。

三台 ComfyUI 镜像基线：

```text
registry.local:5000/gpu-control/comfyui:projects-0.2.2
```

ImageClip 当前生产候选基线：

```text
repository:            /opt/imageclip
branch:                main
commit:                721f7d68635ee36d45f545ce2c82037046147442
pipeline_sha256:       00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
workflow_version:      2026.07.27-721f7d6-r1
API graph nodes:       44
generator graph digest: 797f423ca1808790162f8402bcec67f99420db9864b9409f60c317740e002eca
registry template SHA:   63e56d99bc125156016c544f26679406c84b3640123a8cec0ae762eb598c485c
only output node:      SaveImage #25
final upstream node:   CodexLazyShadowBypassV43 #57
```

`pipeline_sha256` 不是 Git 提交的替代品。它按相对路径排序，覆盖 `ImageClip.json` 和
`Cherry_lizi` 下除 `__pycache__` 外的每个文件，再对“单文件 SHA + 相对路径”做组合 SHA-256。
提交和内容哈希必须同时相同，避免同一提交上出现未提交热改动。

## 3. 双方职责边界

### 3.1 动画管家负责

1. 飞书收发、图片/视频/ZIP 接收、视频抽帧、业务任务状态、Cherry、编码和最终业务发送。
2. 每个不可变抠图调用生成唯一 `external_batch_id`，保存 GPU Control 的 `batch_id`。
3. 生成严格 manifest 与 `ZIP_STORED` 输入包；ordinal、相对路径、大小和输入 SHA 是业务真值。
4. 同一个网络重试复用同一个 `Idempotency-Key`；输入或 generation 变化必须创建新 ID。
5. 只把父状态 `SUCCEEDED` 当完整成功；`FAILED`、`CANCELLED`、超时或查询异常不得继续后续流程。
6. 下载后完成整包、包内 manifest、集合、顺序、命名、逐帧 SHA、PNG/Alpha 全量校验。
7. 所有校验通过后才把同盘 staging 原子提升为正式 matte 目录。
8. 一个业务抠图调用只能选择本机或 GPU Control 一条路径，禁止双写、竞速和失败后静默改道。

### 3.2 GPU Control 负责

1. 验证整批输入并固定创建时最新启用的 ImageClip 精确工作流版本。
2. 把父批次有界拆分成内部帧 job，动态分配到三张 GPU；每张 GPU 同时只运行一帧。
3. 维护持久队列、租约、重试、取消、主控恢复、节点状态和全链路审计。
4. 保持 ordinal、输入相对路径、输出相对路径、输入 SHA 和输出 SHA 与执行节点无关。
5. 重新校验每个最终输出，只在全量成功后原子生成唯一结果 ZIP。
6. Web 顶层只展示父批次；帧级 job 仅在父详情和 manifest 分页接口中出现。
7. 执行前持续校验三节点 ImageClip Git/内容哈希，不允许漂移节点领取生产抠图。

### 3.3 GPU Control 明确不负责

- 不读取飞书，不做抽帧、Cherry、视频编码、业务 ZIP 和最终发送；
- 不理解动画管家的镜头业务语义，也不跨多个父批次自动拼业务结果；
- 不在 `FAILED` 后擅自改用另一套算法或本机管线；
- 不在 `SUCCEEDED` 前提供可被误认为完整结果的 artifact。

## 4. TLS、认证、租户与请求标识

基础地址：

```text
https://10.3.34.11
```

动画管家必须信任内网 CA：

```text
GPU Control 仓库路径：deploy/control-plane/nginx/certs/lan-ca.crt
CA 文件 SHA-256：ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b
```

正式环境禁止 `verify=False`、`curl -k` 或全局关闭 TLS 验证。CA 文件由主控复制到动画管家本机
只读路径，再由 `GPU_CONTROL_CA_BUNDLE` 指向该文件。

推荐每个动画管家实例使用专用 API Key：

```http
X-API-Key: gpc_<prefix>_<secret>
```

创建、查询、SSE、取消和下载必须使用同一客户身份。API Key 只放密钥管理或环境变量，不写入日志、
仓库、manifest、ZIP 或任务详情。暂时不带 Key 时可按 Nginx 看到的真实来源 IP 自动认证，但出口 IP
变化会成为另一个租户，无法查询原批次；因此 IP 模式只建议联调。

每次请求建议携带：

```http
X-Request-ID: am-<业务短ID>-<动作>-<序号>
```

长度不超过 64，只用字母、数字、点、下划线、冒号和短横线。响应中的 `X-Request-ID` 是实际采用
值，动画管家必须与 HTTP 状态、响应体、external ID 和 batch ID 一起记录。

## 5. 推荐端到端调用时序

```text
冻结输入帧集
  → 路径规范化并排序
  → 计算每帧 size/SHA-256
  → 生成 manifest 1.0
  → 生成只有图片条目的 ZIP_STORED
  → POST 创建批次
  → 持久化 batch_id
  → 每 3 秒 GET 父状态（SSE 只做提示）
  → SUCCEEDED 后读取唯一 result_archive
  → 下载并核对 X-Artifact-SHA256
  → 校验包内 manifest、集合、ordinal、路径、逐帧 SHA、PNG/Alpha
  → 同盘 staging 原子发布
  → 才进入 Cherry/编码/发送
```

创建接口会同步完成上传落盘、ZIP 安全检查、集合核对、每帧 SHA 和图片解码后才返回 `202`，但不会
同步等待 GPU 推理。大包上传客户端超时建议 86400 秒；单次状态查询超时 30 秒。

## 6. 创建批次 API

### 6.1 请求

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

字段名严格是 `archive` 和 `manifest`。`Idempotency-Key` 必填，长度 1～128。

### 6.2 manifest 1.0

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

| 字段 | 冻结规则 |
|---|---|
| `schema_version` | 只能是字符串 `1.0` |
| `external_batch_id` | 1～128 字符；同一客户永久唯一；建议包含业务 ID、阶段和 generation |
| `failure_policy` | 只能是 `all_or_nothing` |
| `output_naming` | 只能是 `preserve_stem_png` |
| `parameters` | 当前发送 `{}`；新增参数必须升合同版本 |
| `frames` | 1～5000 项，数组顺序与 ordinal 一致 |
| `ordinal` | 严格为 `0..N-1`，不能跳号、重复或倒序 |
| `relative_path` | UTF-8 NFC、POSIX `/`、安全相对路径，1～2048 字符 |
| `size_bytes` | 实际字节数，1～67108864 |
| `sha256` | 实际输入内容的 64 位小写 SHA-256 |

manifest 是严格模式，未定义字段会被拒绝。当前不要发送 `callback_url`、帧级 parameters、
`best_effort`、`append_rgba_suffix` 或客户端指定 node_id。

### 6.3 路径与命名

禁止绝对路径、空段、`.`、`..`、反斜杠、NUL、非 NFC 和大小写折叠后重名。输出将最后一个扩展名
替换成 `.png`，原目录与 stem 不变：

```text
input : episode_01/shot_010/frame_000001.webp
output: episode_01/shot_010/frame_000001.png
```

同目录的 `frame_000001.jpg` 与 `frame_000001.webp` 会冲突，创建请求会被拒绝。

### 6.4 ZIP 合同

- 必须为标准 ZIP；所有图片条目使用 `ZIP_STORED`，不能 Deflate；
- 条目集合必须与 manifest 路径集合完全相等；
- 不能包含 README、manifest、副本、隐藏文件、显式目录或任何非图片文件；
- 禁止加密、符号链接、硬链接、设备文件、重复/逃逸路径；
- 图片必须是可解码 JPEG、PNG 或 WebP，单图像素数不超过 40000000；
- 服务同时核对 entry size、实际读取大小、manifest size 和 SHA-256。

### 6.5 成功与幂等响应

首次接受返回 HTTP `202`：

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

同租户、同幂等键、同规范化 manifest、同工作流版本重放返回 HTTP `200` 和原 `batch_id`。幂等记录
保留 7 天。以下复用返回 `409`：

- 同 key 不同内容：`IDEMPOTENCY_CONFLICT`；
- 换 key 但复用 external ID：`EXTERNAL_BATCH_CONFLICT`。

创建超时且不确定是否受理时，必须用原 ZIP、原 manifest、原 key 重试。输入改变就同时增加
generation 并更换 external ID/key，不能把修改后的数据塞进旧请求。

## 7. 查询、排队反馈和恢复

```http
GET /api/v1/batches/{batch_id}
X-API-Key: gpc_xxx
```

父状态示例：

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
  "created_at": "2026-07-27T04:27:26+00:00",
  "started_at": "2026-07-27T04:27:27+00:00",
  "finished_at": null,
  "error": null,
  "artifacts": []
}
```

状态机：

```text
VALIDATING → QUEUED → RUNNING → ASSEMBLING → SUCCEEDED
                         └────→ CANCELLING → CANCELLED
任一活动阶段 ─────────────────────────────→ FAILED
```

排队时动画管家应向用户显示“已进入 GPU 集群队列”，并展示 `counts.pending/queued/running/succeeded`
及 `progress`。1.3.3 起 `progress` 在同一 batch 内保证单调不减，范围为 0–100，终态成功为 100；
它是聚合进度而不是 ETA。当前批次合同不承诺精确完成时间和全局 queue_position；不要把估算时间当 SLA。三槽位
是共享资源，真实业务优先，节点切工作流和首次模型加载也会改变耗时。

建议 3 秒轮询；连续网络错误用 1、2、4、8、15、30 秒退避，但始终查询同一 batch ID。动画管家
重启后从持久化 batch ID 恢复，不重新创建。SSE 可作低延迟提示：

```http
GET /api/v1/batches/{batch_id}/events
Accept: text/event-stream
```

SSE 不是最终真相，断线后仍以 GET 为准。

## 8. 帧级详情（只进入父任务详情，不刷屏）

```http
GET /api/v1/batches/{batch_id}/manifest?offset=0&limit=200
GET /api/v1/batches/{batch_id}/manifest?offset=0&limit=200&status=FAILED
```

`limit` 默认 200、最大 500。`items` 永远按 ordinal 排序，包含输入/输出相对路径和 SHA、状态、内部
job ID、node ID、attempts 及错误。顶层“任务”和“最近任务”只显示父批次；内部帧 job 只能在父
任务详情、分页接口和运维日志中查看。

这是 UI 与 API 的共同红线：一个 3000 帧批次只能产生一行父任务，不能产生 3000 行顶层记录。

## 9. 取消

```http
POST /api/v1/batches/{batch_id}/cancel
X-API-Key: gpc_xxx
Idempotency-Key: <external_batch_id>:cancel
```

取消键必须精确等于 external ID 加 `:cancel`。系统停止投喂未开始帧，取消排队/运行内部任务并最终
收敛到 `CANCELLED`。取消不是同步强杀承诺；提交后继续轮询终态。终态批次重复取消返回当前状态。

## 10. 完整成功和结果下载

只有 `status=SUCCEEDED` 时才出现：

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

下载继续使用同一客户身份。HTTP 头 `X-Artifact-SHA256`、artifact 元数据、下载字节重算值必须三者
相等。结果包使用 `ZIP_STORED`：

```text
manifest.json
results/<原相对目录>/<原 stem>.png
```

包内 manifest 每项包含 ordinal、输入路径/SHA、输出路径/SHA、`SUCCEEDED`、job ID、node ID 和
attempts。动画管家发布前必须按顺序验证：

1. 整包 SHA 与响应头及元数据一致；
2. batch ID/external ID 与本地记录一致；
3. total 和 items 数量等于原输入；
4. ordinal 恰好为 `0..N-1`，无缺失、重复、乱序；
5. 输入路径与输入 SHA 逐项等于原 manifest；
6. 输出路径等于命名规则且大小写折叠后唯一；
7. ZIP 文件集合恰好等于 `manifest.json + results/...`；
8. 每个输出字节 SHA 等于 output SHA；
9. 每个输出可解码为 PNG 且包含 Alpha 通道；
10. 全部通过后才原子发布并进入 Cherry/编码。

任一步失败都保留原 ZIP、响应头、batch ID、request ID 和错误说明，旧正式目录保持不变。

## 11. 最新管线同步和硬门禁

管线升级必须按以下事务化顺序进行：

1. 暂停新 ImageClip 接单或确认旧版本无活动批次；
2. 在主控 fetch 远端 `main`，只允许 fast-forward；
3. 用增量 bundle/受控 Git 同步 A/B，保留模型清单等现场本地文件；
4. 三台分别核对完整 40 位提交和确定性 pipeline SHA；
5. 确认三台 `/object_info` 都包含新 API 图的所有 class type；
6. 从唯一最终 SaveImage 反向生成 API 祖先子图，拒绝第二输出和 bypass/missing 节点；
7. 新版本先禁用导入，计算三节点兼容性；
8. 三台节点代理签名上报完全相同提交/哈希；
9. 新版本兼容三台后启用，旧版本停用；
10. 做单帧与三帧真实 smoke，校验只有最终 RGBA artifact；
11. 之后才恢复批量真实请求和压测。

调度领取时不会只相信历史兼容表，而会再次直接比较工作流 `node_labels` 与节点最新签名心跳。
提交/哈希缺失或任一不等均不领取。Scheduler 还会每 60 秒从每台 ComfyUI 的实时
`/object_info` 采集当前生产工作流涉及的 class inventory，逐版本刷新兼容表；任一模板 class 缺失、
inventory 尚未取得、显存不足或标签不一致都按 fail closed 处理。这样即使有人任务中途修改
`/opt/imageclip`，或插件在容器重启后导入失败，之后的新领取都会停止。正在运行的一帧仍按既有
租约收敛，运维应检查其结果或重跑整个 generation。

## 12. 调度、并发与真实/测试隔离

- 三台节点每卡 `max_concurrency=1`，集群最多同时执行 3 帧；
- 批次 feeder 默认最多保留 12 个内部帧进入热队列，避免几千帧淹没普通 API；
- 同一租户批次最多并行占 3 卡；
- 调度先过滤在线/模式/心跳/工作流兼容/管线哈希，再做租户公平与优先级老化；
- 生产客户永远先于测试客户；只有没有可执行生产 job 时，测试 job 才能用空闲槽位；
- PostgreSQL 行锁、`SKIP LOCKED` 和唯一租约保证多调度循环不会重复领取；
- 节点掉线、租约超时和可重试执行错误按最大 3 次尝试恢复；
- ImageClip 与 ModelView 混合时，调度记录切换成本；正确性和生产优先级高于盲目减少切换。

压测客户端必须标记 `client_kind=test`，request ID 使用 `lt-<run_id>-...`，不得冒充动画管家生产
客户。Web/API 客户、任务、仪表盘均可按“真实/测试”切换，默认只展示真实业务。

## 13. 容量和建议值

| 项目 | 当前值 |
|---|---:|
| 单批最大帧 | 5000 |
| manifest 最大 | 4 MiB |
| 单帧最大 | 64 MiB |
| 单图最大像素 | 40000000 |
| ZIP/解压总量最大 | 100 GiB |
| Nginx multipart 上限 | 101 GiB |
| feeder 窗口 | 12 帧 |
| 同租户批次运行上限 | 3 帧 |
| 单 GPU 并发 | 1 |
| 单帧最大尝试 | 3 |
| 推荐轮询 | 3 秒 |
| 上传客户端超时 | 86400 秒 |

硬上限不是推荐打满值。真实联调先做 1 帧、30 帧、两个并发 30 帧，再逐步扩大。容量拆分由动画
管家生成明确分片 external ID 并在业务层汇总，GPU Control 不会跨父批次自动拼包。

## 14. 错误处理矩阵

| HTTP/阶段 | code/状态 | 动画管家动作 |
|---|---|---|
| 400 | `MANIFEST_INVALID` | 修正输入；内容改变后使用新 generation/ID |
| 401/403 | `AUTH_FAILED` | 检查 Key/客户状态，不要重建批次 |
| 404 | `WORKFLOW_NOT_FOUND` | 通知 GPU Control，禁止本地静默接管 |
| 409 | `IDEMPOTENCY_CONFLICT` | 检查复用 key 时是否改了内容 |
| 409 | `EXTERNAL_BATCH_CONFLICT` | 检查 external ID 生命周期 |
| 413 | `BATCH_TOO_LARGE` | 按业务边界拆批并使用新 ID |
| 422 | `ARCHIVE_ENTRY_INVALID` | 修复 ZIP 类型、压缩方式或路径 |
| 422 | `FRAME_SET_MISMATCH` | 重新核对 ZIP 与 manifest 集合 |
| 422 | `FRAME_HASH_MISMATCH` | 重新冻结输入并计算 size/SHA |
| 422 | `IMAGE_INVALID` | 修复格式/损坏/像素超限 |
| 422 | `WORKFLOW_RENDER_FAILED` | 停止请求并通知双方接口维护人 |
| 429 | 限流 | 对同请求、同 key 指数退避 |
| 5xx/网络 | 状态不明确 | 同 key 重试创建或继续查原 batch ID |
| 运行失败 | 父 `FAILED` | 读取父 error 与失败帧；新 generation 全批重提 |

运行阶段常见错误包括 `CHILD_JOB_MISSING`、`OUTPUT_MISSING`、`OUTPUT_HASH_MISMATCH`、
`OUTPUT_ALPHA_MISSING`、`OUTPUT_IMAGE_INVALID` 和 `BATCH_ASSEMBLY_INTERNAL_ERROR`。失败批次不会
提供结果 artifact。当前没有公开的 `retry-failed`；需要重跑时创建全新 generation。

## 15. 当前明确不支持

- `callback_url` 和终态 Webhook；
- `best_effort`、`PARTIAL` 或部分结果发布；
- `append_rgba_suffix`；
- 公开 `retry-failed`；
- 每帧不同 parameters；
- 对象存储 URL 输入；
- 暂停/继续；
- 依赖 `Last-Event-ID` 的精确 SSE 续传；
- 同一批次混用动画管家本机与 GPU Control；
- 客户端指定执行 GPU。

## 16. 动画管家第二轮真实验收

GPU Control 侧已先完成以下真实基线，完整数字见
[41_2026-07-27_GPU_CONTROL_1_3_2_STRESS_AND_PIPELINE_RECORD.md](41_2026-07-27_GPU_CONTROL_1_3_2_STRESS_AND_PIPELINE_RECORD.md)：

- 20 个隔离 test 客户、60 个 7:3 随机混合真实任务：60/60 首尝试成功、60/60 产物通过；
- 6 帧与 30 帧真实父任务：幂等重放、三节点拆分、单调进度、ZIP、路径、顺序、命名、逐帧 SHA
  和 Alpha 全部通过；
- 30 帧批次只有一条父记录，30 条内部 job 不进入顶层任务列表；
- 高压中两次真实 production canary 均越过 test 队列先执行；
- 三节点提交、pipeline SHA、Comfy class inventory 与 node-agent 1.3.3 已复核一致。

以下项目仍需动画管家用其真实目录、中文文件名、持久化数据库和 Cherry 发布链路共同确认：

按以下顺序执行并共同保存证据：

1. **TLS/身份**：用 CA 严格校验；API Key 或固定来源 IP 能创建并查询同一租户资源。
2. **1 帧**：返回 v4.3 最终 RGBA，Alpha、命名、输入/输出 SHA 和 node ID 正确。
3. **30 帧嵌套路径**：含中文 NFC 和两级目录，ordinal/目录/文件名完全保持。
4. **两个并发父任务**：Web 顶层只有 2 行，不出现 60 行内部帧；三节点都参与。
5. **幂等重放**：创建超时场景同 key 重试，返回同 batch ID，Web 不新增父行。
6. **错误 SHA**：创建必须被 422 拒绝，不产生可执行父任务。
7. **取消**：运行中取消最终收敛 `CANCELLED`，没有 result artifact。
8. **动画管家重启恢复**：只凭持久化 batch ID 恢复轮询、下载和发布。
9. **结果篡改拒绝**：整包/单帧 SHA、缺帧、错序、错名、无 Alpha 任一种都不能进入 Cherry。
10. **管线漂移演练**：一台节点报告错误哈希时不再领取 ImageClip，另两台继续；恢复一致后重新加入。
11. **7:3 混合压力**：ImageClip:ModelView=7:3 随机乱序，生产请求不被测试客户饿死。

每轮证据至少记录：时间、external ID、batch ID、request ID、帧数、输入字节、工作流版本、三节点
提交/哈希、创建耗时、排队/执行/总耗时、节点分布、attempts、artifact SHA、动画管家最终目录。

## 17. 对齐确认项

动画管家回复以下确认即可进入真实联调：

- [ ] 接受 manifest 1.0、ZIP_STORED 和 all-or-nothing；
- [ ] 接受 V3 路径/命名与全量结果校验规则；
- [ ] 持久化 external ID、batch ID、request ID 和幂等键；
- [ ] 状态不明确时不创建第二份业务任务；
- [ ] `SUCCEEDED` 且十项结果校验全部通过后才原子发布；
- [ ] 远端失败不会静默改走本机；
- [ ] 顶层父任务不按帧刷屏，帧详情只在父任务内；
- [ ] 信任指定内网 CA，不关闭 TLS；
- [ ] 接受当前生产工作流 `2026.07.27-721f7d6-r1` 与提交/哈希硬门禁；
- [ ] 接受真实/压测客户严格隔离及 7:3 混合压力验收。

双方确认后，GPU Control 会在本文末追加真实联调 batch ID、artifact SHA、三节点分配和最终验收
时间，并把文档状态改为 `FROZEN / PRODUCTION ACCEPTED`。
