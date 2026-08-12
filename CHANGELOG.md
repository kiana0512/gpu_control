# Changelog

## 2026-08-12 — Automatic retopology repeated-component fidelity v3.0.18

- Lock every repeated or slender high-model component to its measured count, center, endpoints,
  centerline, section radius and relative layer during the existing single analysis pass.
- Prevent curve smoothing or sparse section reconstruction from shortening logs, enlarging bends,
  reordering components or creating intersections that do not exist in the high model.
- Keep the fast publication policy unchanged: no direction review, visual score, topology-flow audit,
  FBX reimport or UV generation was added to the delivery path.
- Release Asset API `1.6.40-retopo-repeated-components-v1`, Blender Worker
  `1.4.40-retopo-repeated-components-v1`, and retopology package v3.0.18.

## 2026-08-12 — Automatic retopology trained-skill routing v3.0.17

- Fix Direct V2 task isolation so each job installs and invokes the approved
  `blender-retopology-compare-iterate` training skill for geometry construction; retain
  `blender-auto-retopo-align` only for coordinate restoration and server output.
- Stop inferring asset identity from filenames or a global AABB. Generated lows must be based on
  the real high mesh's silhouettes, sections, openings, negative space, components and attachments.
- Preserve the user-selected fast-delivery policy: no low-model direction review, topology-flow
  audit, silhouette scoring, FBX reimport, or UV generation was added to the publication path.
- Release Asset API `1.6.39-retopo-trained-skill-v1`, Blender Worker
  `1.4.39-retopo-trained-skill-v1`, and retopology package v3.0.17.

## 2026-08-12 — Automatic retopology build-first hotfix v3.0.16

- Remove the shape-authority plan guard from the generated-low critical path after a real task
  stopped at 7% with `RETOPOLOGY_OUTPUT_MISSING` despite a healthy Codex and Blender runtime.
- Keep source-manifest measurements, high-coordinate restoration, topology/UV preservation and the
  final `no_broken_faces` gate; diagnostic plan fields can no longer prevent Blend generation.
- Release Asset API `1.6.38-retopo-build-first-v1`, Blender Worker
  `1.4.38-retopo-build-first-v1`, and retopology package v3.0.16.

## 2026-08-12 — Automatic retopology one-pass plan guard v3.0.15

- Keep the v3.0.14 Codex generated-low fast path and correct its remaining live bottleneck: the
  task prompt now provides the exact plan-guard field names, enum values, evidence linkage and
  ordinary-Python invocation instead of leaving Codex to infer them.
- Run the generated-low shape-authority guard at most once per Codex turn. A malformed plan now
  fails explicitly instead of spending multiple model turns reading guard diagnostics and rewriting
  the same JSON.
- Keep topology construction, the Codex model, source-coordinate restoration, no-broken-face gate,
  optional preserved-UV policy and published artifacts unchanged.
- Release Asset API `1.6.37-retopo-plan-once-v2`, Blender Worker
  `1.4.37-retopo-plan-once-v2`, and retopology package v3.0.15.

## 2026-08-12 — Automatic retopology Codex fast path v3.0.14

- Keep the full trained skill package and all reference files hash-verified, while routing routine
  generated-low jobs through one complete SKILL read instead of repeatedly expanding four long
  reference documents and the guard implementation.
- Precompute bounded text-only component measurements during immutable FBX/OBJ/GLB/GLTF source
  preparation. Codex consumes those measurements directly and no longer starts an extra Blender
  measurement run or renders unused measurement/direction images.
- Preserve the same Codex model, topology construction methods, source-coordinate restoration,
  no-broken-face gate, optional preserved UV policy, artifact set, and source immutability checks.
- Add `result.json.timing_seconds` for source preparation, Codex generation, coordinate finalization,
  atomic publication, and total workflow timing.
- Release Asset API `1.6.36-retopo-fast-agent-v1`, Blender Worker
  `1.4.36-retopo-fast-agent-v1`, and retopology package v3.0.14.

## 2026-08-12 — Automatic retopology streamlined delivery v3.0.13

- Remove generated-low direction rendering and the seven-view ZIP from Direct V2 publication.
- Export high/low FBX files without freshly reimporting them; the alignment report records the
  user-selected skip policy explicitly instead of claiming a readback pass.
- Do not create or modify UVs during automatic retopology. Preserve existing low UVs when present;
  allow UV0 when absent and defer new UV work to the dedicated UV stage.
- Replace the strict closed-manifold delivery gate with `no_broken_faces`: empty/non-finite meshes,
  face count not below the high, and zero-area/degenerate faces still fail; boundary, non-manifold,
  loose, duplicate, and orientation metrics remain advisory diagnostics.
- Keep immutable source hashing, source-coordinate restoration, saved-Blend fingerprint validation,
  FBX export hashes, bounded retry, and monotonic task progress.
- Release Asset API `1.6.35-retopo-fast-delivery-v1`, Blender Worker
  `1.4.35-retopo-fast-delivery-v1`, and retopology package v3.0.13.

## 2026-08-12 — Automatic retopology monotonic progress and failed-candidate fast retry v3.0.12

- Keep one public task progress bar monotonic across the single bounded retry. A rejected first
  candidate advances to the 50% retry boundary, and second-attempt Worker progress maps into the
  remaining half instead of returning to 0%/1%.
- When fresh-FBX dimension, bidirectional surface-distance, or FBX-readback validation already
  rejects a candidate, skip its seven-view render and immediately switch construction method.
- Keep all seven mandatory views for every numerically valid final candidate; no topology, shape,
  UV, source-preservation, or FBX-readback threshold is relaxed.
- Release Asset API `1.6.34-retopo-progress-speed-v1`, Blender Worker
  `1.4.34-retopo-progress-speed-v1`, and retopology package v3.0.12.

## 2026-08-11 — Automatic retopology bounded-retry method diversification v3.0.11

- Carry the authoritative Asset API `attempt_count` through the claim contract, Blender Worker,
  and Direct V2 server launcher instead of making both generated candidates indistinguishable.
- On the single bounded retry, forbid repeating a failed low-detail semantic proxy. Prefer a fresh
  high duplicate with guarded controlled reduction when source topology permits it; otherwise switch
  to measured per-component hybrid construction.
- Keep the v3.0.10 zero-boundary finish and all topology, shape, seven-view, source-preservation, UV,
  and FBX-readback gates unchanged.
- Release Asset API `1.6.33-retopo-retry-method-v1`, Blender Worker
  `1.4.33-retopo-retry-method-v1`, and retopology package v3.0.11.

## 2026-08-11 — Automatic retopology closed generated components v3.0.10

- Require every newly generated low component to close accidental boundary loops before UV creation:
  cap solid ends, bridge paired section rings, or fill only a simple component-local gap and
  triangulate only newly filled faces.
- Preserve true cavities and through-openings with inner/outer walls joined by a rim; prohibit broad
  caps across planned negative space, cross-component fills, or any repair to `SOURCE_HIGH`.
- Re-measure after generated-low construction finish and retain the strict zero-boundary, topology,
  shape, seven-view, UV and fresh-FBX gates.
- Release Asset API `1.6.32-retopo-closed-build-v1`, Blender Worker
  `1.4.32-retopo-closed-build-v1`, and retopology package v3.0.10.

## 2026-08-11 — Automatic retopology resilient live-process leases

- Keep an already-running Codex/Blender topology subprocess alive across transient Asset API
  connection errors, HTTP 429 responses, and HTTP 5xx responses instead of turning a short control
  plane restart into a false topology failure and eventual lease expiry.
- Preserve strict handling for definitive lease/cancellation responses: a non-retryable HTTP 4xx
  still stops the subprocess rather than publishing work without ownership.
- Release Asset API `1.6.31-retopo-resilient-leases-v1` and Blender Worker
  `1.4.31-retopo-resilient-leases-v1`; retopology package remains v3.0.9.

## 2026-08-11 — Automatic retopology durable Codex slot admission

- Make the Asset API's durable active-job assignment authoritative when admitting Codex-backed
  topology work, closing a stale/racing Worker claim window that could place two Codex topology
  processes on one machine.
- Continue filling remaining general CPU capacity with UV and Blender-only audit work while a
  machine's single Codex topology slot is occupied.
- Release Asset API `1.6.30-retopo-durable-codex-slot-v1`; Blender Worker and retopology package
  remain byte-identical at `1.4.30-retopo-shape-view-gate-v1` and v3.0.9.

## 2026-08-11 — Automatic retopology shape and seven-view gate v3.0.9

- Hotfix Asset API `1.6.29-retopo-shape-artifacts-v1` to atomically accept, hash-bind, validate,
  and publish the new shape report and seven-view ZIP; unknown or malformed evidence remains a hard
  422 rejection.
- Validate every freshly exported high/low FBX pair with a 3% maximum-axis dimension gate and 4%
  bidirectional P95 surface-distance gates before publication.
- Generate and retain front, back, left, right, top, bottom, and perspective opaque-orange wireframe
  evidence for every accepted delivery; reject missing or failed evidence.
- Classify shape/visual mismatch as a bounded generated-candidate QA failure so one fresh retry may
  replace the rejected candidate without weakening coordinate, topology, UV, or FBX integrity gates.
- Release Asset API `1.6.29-retopo-shape-artifacts-v1` and Blender Worker
  `1.4.30-retopo-shape-view-gate-v1`.

## 2026-08-11 — Automatic retopology generated-low cleanup v3.0.8

- Require scale-relative exact-duplicate and degenerate construction cleanup on the fresh generated
  low after modifiers/conversion/join and before UV creation; never modify `SOURCE_HIGH` or relax the
  final topology gate.
- Let an early generated-candidate QA rejection use the configured second attempt while the Asset API
  continues to suppress retries after the final alignment/FBX threshold.
- Release Asset API `1.6.27-retopo-generated-cleanup-v1` and Blender Worker
  `1.4.29-retopo-generated-cleanup-v1`.

## 2026-08-11 — Automatic retopology build-context completion v3.0.7

- Open the server-prepared, immutable working Blend before executing the one bounded generated build
  script, so `SOURCE_HIGH` exists in Blender instead of an empty factory-startup scene.
- Record the exact working-Blend SHA-256 in build-completion evidence and retain all existing output,
  topology, UV, coordinate and FBX readback gates.
- Release Asset API `1.6.26-retopo-build-context-v1` and Blender Worker
  `1.4.28-retopo-build-context-v1`.

## 2026-08-11 — Automatic retopology verified-report reconciliation v3.0.6

- Treat agent-written face, triangle and UV counts as advisory; Blender now supplies the authoritative
  generated-Blend, saved-Blend and FBX-readback metrics and writes them back to the report.
- Preserve all topology, UV, coordinate and FBX hard gates while no longer discarding a valid Blend
  merely because Codex omitted the textual `uv_layers` field.
- Return the bounded build-script exit evidence and Blender log tails when server completion itself
  fails, instead of masking that failure as an unexecuted script.
- Release Asset API `1.6.25-retopo-report-reconciliation-v1` and Blender Worker
  `1.4.27-retopo-report-reconciliation-v1`.

## 2026-08-11 — Automatic retopology build completion v3.0.5

- Execute exactly one bounded, regular, job-root `build_once.py`/`build.py` once when the completed
  Codex turn authored it but omitted the final Blender invocation.
- Reject missing, ambiguous, symlinked, empty or oversized scripts and retain fail-closed topology,
  UV, coordinate and FBX readback gates after server completion.
- Require `uv_layers >= 1` in the generation report before coordinate finalization.
- Released Asset API `1.6.24-retopo-build-completion-v1` and Blender Worker
  `1.4.26-retopo-build-completion-v1` with no ComfyUI restart.

## 2026-08-11 — Automatic retopology static-source reliability v3.0.4

- Unified FBX, GLB, GLTF and OBJ preparation behind one immutable `SOURCE_HIGH` manifest.
- Rejected unexecuted generated build scripts with an explicit diagnostic.
- Added an early UV hard gate and stage-specific topology evidence failures.
- Released Asset API `1.6.23-retopo-static-input-v1` and Blender Worker
  `1.4.25-retopo-static-input-v1` without restarting production ComfyUI services.

## 1.5.12（自动拓扑与原坐标对齐包 v3.0.0）— 2026-08-11

- 完整替换为 `blender-auto-retopo-align-server-package-v3.0.0`，固定 ZIP SHA-256、24 文件清单和
  `server/verify_package.py` 自检，并把 package/skill 身份绑定到 API、队列与三台 Worker。
- 同任务低模改由新包一次生成并恢复高模原矩阵；不再进入旧 ICP、二次 UV、七视图 Agent 复核或
  自动第二次建模，避免服务器适配层再次改变新低模。
- 原子交付对齐 Blend、高/低模 FBX、generation/result/source manifest、对齐报告和事件日志；Asset
  API 对坐标声明、矩阵/中心/尺寸、手性、拓扑/UV 指纹和 FBX 新导入证据执行 fail-closed 校验。
- 坐标异常使用不可重试的 `RETOPOLOGY_COORDINATE_MISMATCH`；成功只表示源坐标恢复与回读通过，
  保留 `generated_for_user_inspection` 语义，不伪造自动视觉验收。

## 1.5.12（Direct V2 进度与重试热修复）— 2026-08-11

- Direct V2 将 10 分钟正常估算与 2 小时硬超时安全上限分离；进度按阶段经过时间增长，不再在
  进程实际完成前长时间占住 92%/99%，超过正常窗口后 ETA 显示未知而非伪造 116 分钟。
- Asset API 在新的活动尝试开始时重置进度，并把前一轮进度写入耐久事件，修复“第二轮从头生成却
  一直显示 99%”。
- 拓扑后对齐/视觉 QA 明确失败后不再重跑一次完整 Codex 生成；仍保留瞬时 Worker/网络故障的
  自动重试，且不放宽几何、UV、七视图和 FBX 重导门禁。

## 1.5.12（Direct V2 源轴向与视觉 QA 热修复）— 2026-08-11

- Direct V2 生成低模以高模原轴向为第一候选，只允许整体等比缩放和中心平移；通过原有几何门禁后
  不再让通用 ICP 给细长或近对称模型加入微小误旋转，失败时仍回退 proper-rotation 求解。
- 原高模和原低模保持不可变；只在交付 `BAKE_LOW` 副本上消除零面积几何、三角化 N-gon 并统一闭合
  壳体朝外法线，随后重新生成/验证 UV、执行七视图和 FBX 全新场景回读。
- 独立视觉 QA 新增高低模确定性轮廓叠加图，并明确初始移开展示图仅是审计证据；仍硬拒绝错误镜像、
  长刺、穿插、折叠、方向或主要轮廓错误，不用数值覆盖真实视觉不匹配。

## 1.5.11（全服务稳定性与并发槽位修复）— 2026-08-10

- 保留每台 Linux Worker 一个 Codex 拓扑执行槽的安全边界；拓扑占用 Codex 时，其余 CPU 槽位仍可
  领取 UV 与 Blender 审计任务，避免多核机器因一个拓扑任务整体闲置，也不会在同机租入第二个拓扑。
- Asset Worker claim 新增向后兼容的 `accepts_codex_jobs` 能力位；先升级 Asset API、再滚动 Worker 时，
  老 Worker 与新 Worker 都能安全接单，升级过程中不会产生租约等待或重复执行。
- WSL 节点默认不再向 Prometheus 宣告不存在的 DCGM `:9400` 目标；Node Exporter 与 Node Agent 的
  主机/GPU 指标继续保留，也可通过显式标签重新启用 DCGM 抓取，消除 3090-B 的持续误告警。
- Scheduler 将 ComfyUI 核心存活/队列探测与大体积 `/object_info` 能力清单分离；清单在满载 WSL
  节点超时时保留上次兼容结果并退避 60 秒，不再把在线的 3090-B 反复判为离线、造成接单偏少。
- 新增 3090-B WSL2 性能探针：同分辨率 ImageClip 最近 5 帧中位数只与原生 3090-A 比较，至少
  3 个样本且持续达到 2 倍才告警；同时告警持续不可用与 10 分钟内反复上下线，避免冷启动误报。
- Node Agent 新增签名保护的 WSL2 深度状态查询，直接采集内核 boot ID/uptime、归一化 CPU load、
  内存/交换分区和 Linux PSI；Scheduler 只将其作为提前告警证据，探针失败不会把节点踢下线。
- Node Agent GPU 温度、功耗与功耗上限正式导出到 Prometheus，补齐无 DCGM 的 3090-B WSL2 高温
  告警；指标只用于观测和人工 Drain，不会中断运行中的生产任务。
- 修复 3090-A Nunchaku 节点元数据探测依赖的空 `models/checkpoints` 目录；未增加、删除或修改任何
  外部工作流、模型、自定义节点、prompt、推理参数、图拓扑或输出语义。
- Python Web/API 安全依赖和前端构建依赖升级到已修复版本；补齐全量静态检查、类型检查、测试、
  镜像身份、三节点滚动、六 API 真实压力测试和结果证据门禁。
- Asset Worker 心跳表移除对 8 行固定容量无收益的时间索引，启用 HOT 更新与合理 autovacuum 阈值，
  消除数百万次心跳造成的持续表/索引膨胀和数据库维护抖动。
- 六 API 压测器的重拓扑验收合同从退役 V1 的 22/23 件产物更新为 Direct V2 的 7 件正式产物，
  防止成功的 Direct V2 任务被测试工具误判失败。
- 生产压测 session 防重由“最近 500 条 test 历史”改为控制面按规范 UUIDv4 对 GPU job、ImageClip
  batch 和 Asset job 三个命名空间做精确全局计数；历史超过 500 条后仍能 fail closed 地证明新 session
  无碰撞，不删除或改写历史审计记录。
- 六 API 合成 `.blend` 测试件显式关闭 Blender 压缩并校验原始 `BLENDER` 文件签名；Direct V2
  测试场景只携带一个高模，三角色高/参考/低模型仍只用于拓扑审计，避免测试输入制造伪失败。
- Direct V2 对直接 `.blend` 输入不生成 FBX 专用的 `source-manifest.json` 时，GPU Control 在 Codex
  完成后只读确认唯一源高模和最终低模，再补齐同一交付报告后进入既有坐标恢复；不修改拓扑、网格、
  旋转、缩放或外部 Direct V2 包。
- 压测启动器收到 Ctrl+C 时先把中断转交 Locust，并给 session 范围清场最多 360 秒；只有清场超时
  才逐级 terminate/kill，避免操作员停止无效测试时跳过 teardown。
- 重拓扑的坐标原样交付/安全恢复 v2 保持不变；本次更新不修改拓扑算法、几何结果或烘焙合同。

## 1.5.10（部分成功与失败帧修复）— 2026-08-05

- 序列帧子任务在 `COMFY_TIMEOUT`、`JOB_TIMEOUT`、`GPU_OOM` 或执行错误后，按最多三次且跨物理节点的方式重试；成功帧不会因单帧失败被清理。
- 新增终态 `PARTIAL_SUCCESS`、结构化 `failed_items` 和只含已验证成功帧的 `ZIP_STORED` 结果包；包内 `total` 保持原批次总帧数，ordinal 允许缺口。
- API、SSE、下载端点和 Web UI 均识别部分成功；GPU OOM 证据持久化异常类型、Comfy 节点、物理节点与原始摘要。

## 1.5.9（候选）— 2026-08-03

- UV 与自动重拓扑的生产默认策略统一为 `advisory`：几何质量未达标保留结构化告警，但通过身份、
  manifest、文件完整性、租约和 SHA 门禁的正式 BLEND/FBX 继续原子交付；完整性错误仍硬失败。
- Blender Worker 1.2.5 在最终 multipart 上传期间持续续租，取消或续租失败会安全终止上传，避免
  大文件或拥塞时出现“计算成功但交付失败”。
- Windows Substance Baker Agent v6 在大文件分块哈希、最终上传和服务端校验阶段持续续租与心跳；
  无法证实 native Baker 已终止时强制进入 `RECOVERY_REQUIRED`，curl 退出码或服务端响应异常继续
  fail closed，且不会清理或重启 ComfyUI 模型缓存。
- 发布打包和身份验证扩展为五个一方镜像：API、Scheduler、Asset API、Web 与 Blender Worker；
  控制面 1.5.9 和 Worker 1.2.5 必须绑定同一个已推送的 40 位 Git SHA、不可变镜像身份和 SBOM。
- 六 API 压测入口修复独立脚本导入路径，保留真实业务优先、空闲窗口、备份、凭据、allowlist、
  fixtures、逐级升压和精确清场门禁；未显式 `--execute` 时不会发出任何 HTTP 请求。
- GPU 与 Asset 新任务使用同一 PostgreSQL 全局准入事务锁；任何真实非终态任务都会原子拒绝新的
  测试流量，返回 `503 LOAD_TEST_PREEMPTED`，未知租户或未知状态按生产流量 fail closed。
- 六 API 正式压测仅认 `SUCCEEDED`，并按 API 固定 kind 集合和基数逐件校验非零 size、metadata
  SHA、`X-Artifact-SHA256` 与下载 body SHA；缺件、多件、重复 kind 或三重 SHA 漂移整轮失败。
- Linux Worker heartbeat、claim 和任务租约绑定物理 `node_id` 与唯一进程代际；活跃持久租约禁止
  旧进程、重复实例或节点迁移造成二次领取，过期租约只能经受控对账后恢复。
- Linux CPU claim 不依赖 ComfyUI/GPU health，但仍遵守管理员 mode 和 manual reservation；实际 claim、
  capacity 与 ETA 共用门禁，Asset API 自有 Substance drain 不会错误阻塞 CPU UV/重拓扑。
- 管理员接管 drain 时保留 Substance pending/fence/recovery；节点 interrupt 使用稳定 Batch/Job 锁序和
  PostgreSQL `NOWAIT`，锁竞争返回可重试 409，不再等待成调度死锁或产生部分取消。
- 1.5.8 Asset API 不接受 Worker 1.2.5 新增的 `node_id` claim 字段，旧 Asset API 又只接受 Windows
  Agent v5。正式滚动必须在零任务且 intake 冻结的窗口严格按“四个 Windows v6 Agent → Asset API
  1.5.9 → 三台 Linux Worker 1.2.5”执行；兼容窗口内不绕过 fail-closed 门禁。先前 `b410a6a`
  五个本地镜像已作废，最终 SHA 冻结后全部重建。
- Web lockfile 将仅开发链路中的 `brace-expansion` 更新到修复版本；完整 npm audit 与生产依赖 audit
  均为 0 vulnerability，业务依赖、页面行为和运行时镜像内容不变。
- 不修改 ImageClip、ModelViewCreator、UV/重拓扑 Skill、工作流、模型、prompt、参数、图拓扑或
  输出语义；三节点空闲滚动、真实六 API 验收和观察完成前保持候选状态。

## 1.5.8（候选）— 2026-08-03

- 三台 Asset Worker 改用各自独立、可写且持久化的 Codex home；真实探针、认证轮换、TLS 环境、
  超时回收和同节点调用串行化恢复，不再跨节点复用 refresh token。
- Codex 管理视图与领取门禁使用同一组新鲜度证据；只认精确 Linux Worker、在线心跳、有效认证和
  未过期真实探针，过期状态显示为 `STALE`，不再出现“页面健康但任务持续排队”。
- Substance Baker Agent v4 上报宿主级进程、Agent 代际和启动时间；长烘焙持续续租，租约歧义恢复
  同时要求零 Baker 进程与其后的 ComfyUI 空闲证据，签名 nonce 禁止重放。
- 每个 Substance 槽位增加全局单实例锁与持久作业互锁，Agent 重启、孤儿 Baker 或重复 Agent 均
  fail closed，不能造成同槽双领或提前恢复 3090-B。
- 新增迁移 `20260803_0012` 保存 Agent/进程证据；修复控制面部署脚本遗漏 `asset-api` 镜像的问题。
- 不修改外部 ImageClip/ModelView 工作流、模型、prompt、参数、图拓扑或输出语义；生产任务清零、
  v4 Agent 协调升级和真实 PBR canary 完成前保持候选状态。

## 1.5.7 — 2026-08-03

- 修复 Windows Substance Baker 等待当前 ComfyUI 帧时 reservation 续租未提交的问题；PBR 取得
  3090-B 下一轮优先权后，Scheduler 不会在 TTL 回滚窗口继续派入新帧，CPU Asset Worker 仍独立接单。
- Windows Baker 升级为精确 v3 身份：不调用 `/free`，不停止或重启 ComfyUI；每次烘焙前后硬校验
  ComfyUI 容器 `Id`、`StartedAt`、`RestartCount`、运行状态与健康状态。
- 连续性异常进入持久 `recovery_required` 闭锁，成功回执必须携带 no-explicit-eviction 和进程连续性
  证据；旧 v2 Agent fail closed，不能继续领取任务。
- Asset Web/API 展示真实 3090-B 共享关系、PBR 等待原因、Baker 进程槽位和下一轮预约；恢复、离线、
  管理员保留、外部 GPU 活动与未纳管队列不会误报为可切换烘焙。
- 版本、Compose 默认镜像和 release identity 门禁统一为 1.5.7；未修改任何外部 workflow、模型、
  prompt、采样参数或输出语义。

## 1.5.6 — 2026-07-30

- Retopology advisory 模式把通过完整性门禁的候选 BLEND/FBX 以正式 `blend`/`fbx` artifact 合同交付；几何 QA 告警与诊断证据保留，不再把用户需要的模型文件隐藏为诊断件。
- Scheduler advisory lock 改为专用 autocommit 连接并持续验证 backend/锁所有权，消除长期 `idle in transaction`；失锁后停止领取并执行受控接管恢复。
- Windows Substance Baker 与 ComfyUI 的 3090-B 物理 GPU 互斥改为持久 pending/fence/recovery 闭锁，加入生产优先、两阶段租约恢复、健康标签合并和统一锁序。
- 所有 Asset 完成入口在正式发布 artifact 前执行取消安全点，避免取消与完成并发时错误交付。
- 六 API 压测器区分同步端到端与异步 admission，自动恢复同步 Roughness 孤儿、执行严格范围清场，并支持有界极限压力的完整证据门禁。
- WebUI 延续已发布的任务/API/工作流分类、完整阶段时间、性能分析和可解释调度界面；本补丁从同一 source revision 重建四个控制面镜像，避免组件版本漂移。

## 1.5.5（候选）— 2026-07-30

- 对齐动画管家优化后的 V4.1 合同：父批次固化 ImageClip 身份快照、完整阶段时间、节点/attempt 性能证据，并在 create、父 GET、分页 manifest 和最终 ZIP manifest 返回同一身份。
- 封闭批次子任务取消和产物旁路；父批次完整 `SUCCEEDED` 前，父/子结果均不可读取，节点中断也不能绕过持久化父取消操作。
- 父取消改为显式 API Key、稳定幂等键、持久 operation 和完整审计；公共 cancel POST 受理回执使用 `CANCEL_REQUESTED`，持久父 GET 在收敛期间仍使用 `CANCELLING`；没有合法取消 operation 的终态不会被补写成合法 `CANCELLED`。
- 新增从生产基线 `20260729_0010` 升级的候选迁移 `20260730_0011`；该迁移尚未在生产执行，必须随完整 1.5.5 drain、备份和回滚门禁发布。
- 调度器遍历全部可用节点寻找兼容任务，避免首个不兼容节点造成队头阻塞；prompt 提交使用持久确定性 client ID，崩溃恢复只做对账，不重复 POST。
- API、Scheduler、Asset API、Web 和节点代理增加统一版本/源码 revision 证据；候选镜像带 OCI labels，发布校验器要求 clean commit、registry digest 和与 manifest 绑定的 SBOM。
- 新增根目录和 Web 专用 `.dockerignore`，排除 `.git`、LFS 归档、缓存、运行时数据与本地密钥，缩小候选镜像构建上下文并避免把宿主数据带入镜像。
- 未修改 ImageClip 工作流、模型、提示词、参数、图拓扑或输出语义；本候选版本在联合故障注入、固定素材基准和灰度完成前不标记为生产验收。

## 1.3.3 — 2026-07-27

- 修复批量父任务与单帧任务进度在 ComfyUI 内部节点切换时回退的问题；任务进度和聚合进度现在均为单调值。
- 新增真实批量序列帧验收器，强制校验幂等重放、父子聚合、目录顺序、文件名、SHA-256、PNG 解码与 Alpha 通道。
- 完成 20 个隔离测试客户、60 个真实 7:3 混合任务持续高压，60/60 首次执行成功、60/60 产物通过。

## 1.3.2 — 2026-07-27

- 修正 ComfyUI UI workflow 中 `control_after_generate` 控制值导致的 API
  KSampler 参数错位，发布 ImageClip `2026.07.27-721f7d6-r1`。
- 节点健康检查定期采集实际 ComfyUI class inventory；工作流兼容性现在对缺失
  节点类型 fail closed，避免任务提交后才发现插件未加载。
- 补齐只读模型挂载下 ComfyUI-Nunchaku 启动所需的空模型目录，并固化到
  Ubuntu 初始化与模型同步脚本。
- 压测器改为按 API 返回 job ID 追踪，不受 Nginx 请求 ID 重写影响；任一推理
  失败、产物缺失或校验失败都会使整轮压测失败。

## 1.3.1 — 2026-07-27

- 将 ImageClip 生产工作流升级到远端 `main` 提交 `721f7d6`（v4.3），API 模板只保留最终 `SaveImage #25` 的祖先子图。
- 节点签名心跳新增 ImageClip Git 提交和确定性内容哈希；调度领取时实时硬校验，不一致节点不能接抠图任务。
- 修复三节点 NVIDIA 内核/用户态版本不一致导致 GPU 利用率和显存指标失真的问题。

## 1.3.0 — 2026-07-27

- 新增真实客户与压力测试客户分类；管理台总览、任务和 API 客户按类型隔离展示。
- 调度器保证兼容的真实业务优先领取 GPU，测试任务仅使用真实租户并发限制之外的空闲算力。
- 修正预计清空时间的在线槽位计算，三台 ACTIVE GPU 均计入实时吞吐能力。
- 调度策略页升级为三节点实时视图，展示当前执行槽位、节点健康、已启用工作流和实际分配规则。
- 新增可复现的 7:3 ImageClip/ModelView 混合真实 GPU 压测工具、产物 SHA/图片/Alpha 校验和容量报告。

## 1.2.0 — 2026-07-24

- 新增 ImageClip RGBA 序列帧批次 API：严格 manifest、ZIP_STORED、逐帧大小/SHA-256/图片校验与租户幂等。
- 新增父批次、帧、事件和批次产物持久化；内部帧任务分布到三台 GPU，支持有界投喂、重试、取消和重启恢复。
- 结果仅在全量帧 PNG/Alpha、路径、顺序和 SHA-256 校验通过后原子发布，失败不暴露部分结果。
- Web 任务列表以一个父批次展示，逐帧进度、节点分布、错误和完整结果归档统一置于详情页。
- 新增动画管家 V2 接口交接、生产部署记录和批次安全/幂等/隔离/归档测试。

## 1.0.0-rc1 — 2026-07-21

- 全量替换旧单机 SQLite/React/进程管理架构。
- 新增 PostgreSQL 任务真相、asyncio 单主调度、3090 主池与 4090 Reserve/Overflow。
- 新增可复现 ComfyUI 镜像、Fake ComfyUI、Node Agent、gpuctl 与三机部署脚本。
- 新增 FastAPI 公共/管理 API、RBAC、审计、回调、工作流注册和安全存储。
- 新增 LiClick 风格 Vue 管理台以及 Prometheus/Grafana/Loki/Alloy/Alertmanager。
- 新增空机部署、灾备、故障和 30 项验收文档。
