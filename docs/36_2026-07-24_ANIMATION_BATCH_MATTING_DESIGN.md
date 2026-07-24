# 动画管家批量序列帧抠图设计

## 1. 目标与结论

动画管家提交的是一个或多个包含大量序列帧的业务批次。控制面需要把每一帧转换为可独立
重试的 ImageClip 子任务，动态分配到 4090、3090-A、3090-B，同时只向调用方和默认管理
页面呈现一个批次。最终交付必须满足：

- 输入相对目录不变；
- 每一帧都有稳定序号，输入、输出文件名存在明确映射；
- 不漏帧、不重复、不因完成先后不同而乱序；
- 单帧失败可以重试，节点故障不会丢失整个批次；
- 完成后先校验数量和 SHA-256，再原子发布结果包；
- API、事件、回调、日志和产物均可由 `batch_id`、`item_id`、`job_id` 互相追溯。

现有单图接口 `/api/v1/services/imageclip-rgba` 保持不变。批处理是新增的父子任务层，不能只
在前端折叠现有单图任务，否则无法正确完成批次级幂等、取消、补帧、汇总和交付。

## 2. 输入与输出契约

### 2.1 推荐输入：ZIP + manifest

新增：

```text
POST /api/v1/batches/imageclip-rgba
Content-Type: multipart/form-data

archive: frames.zip
manifest: {"external_batch_id":"shot-010-v3","failure_policy":"all_or_nothing"}
Idempotency-Key: animation-shot-010-v3
```

ZIP 是第一版标准格式，因为 HTTP multipart 本身不能可靠表达调用方的本地目录树。ZIP 内的
相对路径就是业务路径。ZIP 条目顺序不作为帧顺序依据；帧顺序使用 manifest 的显式列表，
没有显式列表时使用规范化相对路径的自然排序，并把最终序号固化到数据库。

后续可增加两种输入适配器，但它们进入系统后必须生成完全相同的内部 manifest：

1. `files[] + relative_paths[]`：适合少量文件；数组下标一一对应。
2. 对象存储 manifest：每项包含允许域名下的对象 URL、相对路径、大小和 SHA-256，适合超大
   批次，避免反复打包和上传。

不能让调用方只传一个本机目录字符串；该目录对 4090 主控不可见，也无法形成可靠的传输
和完整性边界。

### 2.2 创建响应

接口只负责持久化、校验和入队，成功后立即返回 `202`：

```json
{
  "batch_id": "...",
  "external_batch_id": "shot-010-v3",
  "status": "QUEUED",
  "total_items": 480,
  "status_url": "/api/v1/batches/...",
  "events_url": "/api/v1/batches/.../events",
  "manifest_url": "/api/v1/batches/.../manifest"
}
```

批次可能运行很久，不使用当前单图服务的同步 HTTP 等待方式。动画管家可以轮询、订阅 SSE，
或接收带签名的终态回调。

### 2.3 输出命名与目录

ImageClip 的 RGBA 产物统一为 PNG。默认规则为 `preserve_stem_png`：

```text
input : scene_010/layer_a/frame_000123.jpg
output: scene_010/layer_a/frame_000123.png
```

如果输入已经是 PNG，则完整相对路径和文件名保持不变。若同一目录存在会映射到同一个输出
路径的文件（例如 `frame_1.jpg` 与 `frame_1.webp`），接收阶段直接拒绝并报告冲突；也可由
调用方显式选择 `append_rgba_suffix`，输出为 `frame_1.jpg.rgba.png`。

结果 ZIP 包含：

```text
results/<原相对目录>/<输出文件名>
manifest.json
```

`manifest.json` 按稳定的 `ordinal` 排列，每项记录输入路径、输出路径、输入/输出 SHA-256、
尺寸、子任务 ID、执行节点、尝试次数和错误。ZIP 条目物理先后不承载业务顺序，manifest 才是
顺序和完整性的权威来源。

## 3. 数据模型

新增三组实体：

### `job_batches`

- `id`、`tenant_id`、`external_batch_id`；
- `status`、`failure_policy`、`output_naming`；
- `total_items`、`queued_items`、`running_items`、`succeeded_items`、`failed_items`；
- `manifest_sha256`、`request_id`、`trace_id`；
- 创建、开始、完成、取消时间和终态错误。

### `job_batch_items`

- `id`、`batch_id`、稳定 `ordinal`；
- `input_relative_path`、`output_relative_path`；
- 输入大小、SHA-256、图片尺寸和格式；
- `status`、`child_job_id`、尝试次数、最终节点和错误。

约束包括 `(batch_id, ordinal)`、`(batch_id, input_relative_path)` 和
`(batch_id, output_relative_path)` 唯一。路径比较前进行 Unicode 和分隔符规范化，避免大小写
或编码差异覆盖文件。

### `batch_artifacts` / `batch_events`

保存结果 ZIP、最终 manifest、诊断包及批次状态事件。每份产物记录相对路径、大小、
SHA-256 和内容类型。

现有 `jobs` 增加可空的 `batch_id`、`batch_item_id`。每一帧仍是现有任务状态机中的真实任务，
从而直接复用节点租约、ComfyUI 上传、重试、超时、单帧产物和审计逻辑。

## 4. 分发与公平调度

大量帧不能一次性全部放入现有 `QUEUED` 队列。批次接收后先完整记录 item，再由批次 feeder
维护一个有界就绪窗口：

- 默认窗口为 `在线执行槽数 × 4`，当前三卡为 12 帧；
- 一个 item 进入窗口时才物化为现有 `jobs` 子任务；
- 子任务结束后立即补充一个 item，数据库中即使有数万帧也不会让热队列扫描退化；
- 当前三台机器仍各自保持 `max_concurrency=1`，最多并行处理三帧。

调度采用两级公平：先在 API 客户之间公平，再在同一客户的活跃批次之间 deficit
round-robin。一个批次在没有竞争者时可占用三台机器；出现其他用户或批次后，下一个空闲槽
优先给等待时间更久、获得服务更少的批次。这样既能吃满三张卡，也不会让一个几万帧批次
饿死其他 API 请求。

节点选择继续使用现有兼容性、健康、显存、租约和热工作流亲和逻辑。批次主要使用相同的
ImageClip 工作流，模型常驻能减少切换。节点失败时只回收对应帧的租约；可重试错误优先排除
刚失败的节点一次，输入无效等确定性错误不重试。

## 5. 汇总、完整性与终态

每个子任务成功后，在同一个数据库事务中锁定 batch item、登记输出 SHA-256，并更新父批次
计数；重复完成通知因 item 已是终态而成为无操作。最后一项进入终态时，由单实例聚合锁执行：

1. 校验 item 总数、ordinal 连续性、输入/输出路径唯一性；
2. 对每个成功产物重新计算 SHA-256，并确认图片可解码；
3. 根据稳定 ordinal 生成最终 manifest；
4. 在临时路径创建 ZIP，写完并 `fsync` 后原子改名；
5. 记录 ZIP 大小和 SHA-256，再把批次改为终态；
6. 终态提交后才发送回调，避免调用方下载到半成品。

默认 `all_or_nothing`：任何帧在重试耗尽后，批次为 `FAILED`，不发布“完整结果”链接，但保留
已完成帧和诊断 manifest 供定向重试。可选 `best_effort` 才允许发布部分结果，并必须在响应和
manifest 中明确列出缺失项。

批次重试只重新物化失败/缺失 item，已校验成功的帧不重复计算。批次取消会立即取消尚未物化
和排队的 item，并向运行中的三个子任务发送现有取消请求。

## 6. API 与 Web UI

新增调用方接口：

- `POST /api/v1/batches/imageclip-rgba`
- `GET /api/v1/batches/{batch_id}`
- `GET /api/v1/batches/{batch_id}/events`
- `GET /api/v1/batches/{batch_id}/manifest`
- `GET /api/v1/batches/{batch_id}/artifacts/{artifact_id}`
- `POST /api/v1/batches/{batch_id}/cancel`
- `POST /api/v1/batches/{batch_id}/retry-failed`

Web 默认把一个批次显示为一行，展示“成功/总数”、整体进度、三节点分布、失败帧数、耗时和
结果下载。展开批次后才分页查询帧级子任务。独立单图任务继续按当前方式显示。总览分别统计
业务批次数与执行帧数，避免几百帧把“最近任务”和趋势图刷屏。

## 7. 安全、配额与运维约束

- 解压时拒绝绝对路径、`..`、反斜线逃逸、符号链接、硬链接和设备文件；
- 限制压缩包大小、解压总量、压缩比、文件数、单帧大小、像素数和允许格式，防止 ZIP bomb；
- 拒绝规范化后的重复输入/输出路径；所有写入先进入 `.staging`，校验成功后原子提升；
- 批次幂等键以 `tenant + Idempotency-Key` 唯一，请求 manifest 和逐帧 SHA 参与指纹；
- 配额分为每日批次数、每日帧数、单批最大帧数/字节数、活跃批次数和存储量，不能再直接套用
  当前单图 `max_queued=20`；
- 批次、帧、节点尝试、回调和下载都进入审计日志；保留期到期后由受控清理任务删除。

## 8. 实施与验收顺序

1. 数据库迁移、批次领域模型、路径/ZIP 校验和单元测试；
2. 异步创建/查询/事件/取消 API，保持原单图 API 回归通过；
3. feeder、两级公平调度、子任务终态回写和聚合器；
4. Web 批次列表、详情、帧分页、节点分布和结果下载；
5. 动画管家联调：先用 30 帧 × 2 批次，再用真实目录和命名；
6. 故障验收：重复提交、断网、单节点重启、单帧坏图、重试耗尽、取消、主控重启后续跑；
7. 三卡轻量容量测试，确认无漏帧、重复、路径变化、散列不一致和其他用户饥饿。

开始实现前只需从动画管家侧确认三个外部事实：典型/最大帧数与字节数、输入当前是 ZIP 还是
对象存储、失败时要求整批失败还是允许部分结果。其余设计不依赖调用方内部实现。
