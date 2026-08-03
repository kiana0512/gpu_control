# GPU Control 1.5.9 / Worker 1.2.5 统一发布与六 API 联合验收

- 日期：2026-08-03
- 当前生产总状态：`DEPLOYED_NOT_ACCEPTED`
- 1.5.9 / Worker 1.2.5 候选状态：`CANDIDATE_NOT_DEPLOYED`
- 最终源码门禁：`PENDING_FINAL_SOURCE_GATES`
- GitHub 源码推送授权：`PENDING_SOURCE_PUSH_AUTHORIZATION`
- 联合验收状态：`PENDING_JOINT_ACCEPTANCE`
- 适用范围：GPU Control 控制面、Scheduler、Asset API、Web UI、Linux Blender Worker、Windows Substance Agent v5、六 API 联合验收与发布证据

> 本文是本轮一次性交付的唯一发布与验收入口。任何 `PENDING_*` 字段在得到原始证据前都不得改写为通过，也不得据此宣称 `FROZEN`、`PRODUCTION ACCEPTED` 或 1.5.9 已部署。

## 1. 执行结论

本轮候选实现集中在 GPU Control 自身边界内：跨 GPU/Asset 平面的生产任务优先原子准入、Worker 进程代际与节点绑定、锁竞争快速失败、统一容量/ETA 口径、Worker/Agent 长上传期间的租约续期、六 API 精确产物契约与负载验收，以及五镜像可复现打包；既有 Web UI 可读性和运维入口纳入同一版本发布与复验。版本目标为 Control Plane `1.5.9`、Linux Blender Worker `1.2.5`；本轮实现已冻结为 `dac30c039f692cf8274eaff5430ca7ebfd97b201`，最终文档/发布 evidence commit 尚待冻结。早期 `b410a6a` 五个本地 candidate 镜像已被后续修复取代，禁止部署。小型备份、合成素材 r2 和 plan-only 报告可以继续作为辅助证据，但源码尚未推送，正式镜像、正式全量备份、生产灰度、六 API canary、浏览器验收和正式综合压测均未完成。

当前生产仍是分组件基线，状态继续保持 `DEPLOYED_NOT_ACCEPTED`：

| 组件 | 当前已记录生产基线 | 1.5.9 目标 | 本文状态 |
|---|---|---|---|
| API / Scheduler / Web | `1.5.7`，source `11844e7f...` | `1.5.9` 同一完整 commit | `PENDING_ROLLOUT` |
| Asset API | `1.5.8`，source `7f7fd197...` | `1.5.9` 同一完整 commit | `PENDING_ROLLOUT` |
| Linux Blender Worker | `1.2.4`，节点 revision 尚未完全统一 | `1.2.5` 同一完整 commit、同一镜像 digest | `PENDING_ROLLOUT` |
| Windows Substance Agent | v5 在线健康 | v5 长 SHA/上传租约续期候选 | `PENDING_WINDOWS_INSTALL_AND_CANARY` |
| 数据库 | `20260803_0012` | 本候选未新增 migration；发布前复核 head | `PENDING_DB_HEAD_VERIFY` |

## 2. 本轮候选变更

### 2.1 跨平面生产优先原子准入

候选源码已实现以下规则：

1. API 单任务、ImageClip 父批次、Asset UV、重拓扑审计、重拓扑处理和 PBR 创建统一识别负载测试身份。
2. 新任务准入使用 PostgreSQL 全局 advisory transaction lock，锁顺序固定为“全局准入锁 → tenant 锁 → 重新统计/校验 → 插入”。
3. 存在任何非终态生产 GPU job、父批次或 Asset job 时，新的测试请求返回 HTTP `503 LOAD_TEST_PREEMPTED`，同时返回 `Retry-After: 5`。
4. 未知状态或缺失 client identity 按生产流量 fail closed 处理。
5. 幂等重放在新工作准入门禁前返回已有资源，不因生产任务后来出现而制造第二个任务。
6. Scheduler feeder 使用同一全局锁，并按稳定顺序获取 tenant 锁后重新计数。

原子性边界必须准确理解：当生产插入先取得全局锁并完成准入后，并发的新测试任务不能穿透生产优先门禁。该机制不会中断已经 `RUNNING` 的工作，也不宣称测试事务先获得锁时能被回溯抢占。压力工具仍必须持续观察真实生产任务并停止继续发压。

### 2.2 长任务租约与上传恢复

- Linux Blender Worker `1.2.5` 候选在最终 multipart 上传期间每 15 秒续租；续租失败且 completion 未在 2 秒宽限内权威完成时取消上传并 fail closed；收到取消请求时停止上传；候选上传超时为 3600 秒。
- Windows Substance Agent v5 候选对多 GB 产物使用 4 MiB 分块计算 SHA，并在计算期间持续续租与 heartbeat；异步 `curl` 上传期间同样续租与 heartbeat，并校验进程退出码、服务端响应 JSON 和最终状态。
- 上述改动只解决 GPU Control/Worker 传输、租约和恢复，不改变业务算法输出语义。

### 2.3 可复现发布

候选打包器覆盖五个第一方镜像：API、Scheduler、Asset API、Web、Linux Blender Worker。Control `1.5.9` 与 Worker `1.2.5` 必须绑定同一个完整 40 位 Git commit。打包器要求：

- 工作树干净；
- GitHub origin 为 `https://github.com/kiana0512/gpu_control.git`；
- remote `main` 已包含目标 commit；
- 版本与目标一致，基础镜像 digest 固定；
- OCI 产物和 Docker-loadable 产物 config digest 一致；
- 五组件 SBOM 与最终 registry digest 一一绑定；
- 使用精确确认串 `PACKAGE_GPU_CONTROL_1.5.9_WORKER_1.2.5_<FULL_SHA>`。

打包器不会替代部署、registry push 或 Git LFS push。离线 OCI digest 也不能冒充 registry digest。

当前仍缺少对“将 `/opt/gpu-control` 当前完整 `main` 源码与提交历史推送到
`https://github.com/kiana0512/gpu_control.git` 的 `origin/main`”的精确授权。授权前不得执行
源码 push、Git LFS 发布或依赖已推送 SHA 的正式打包；本地只读验证与候选修复可以继续。

### 2.4 Worker 代际、节点绑定与容量真相

- Linux Asset Worker 每个进程生成唯一 `agent_instance_id` 和 `agent_started_at`；heartbeat、claim、job lease 绑定同一节点和进程代际，旧进程或错误节点不能继续领取。
- heartbeat 采用稳定的 Node/Worker/AssetJob 锁序；与续租、完成或 reaper 竞争时使用 PostgreSQL `NOWAIT` 快速返回可重试 `409 ASSET_WORKER_HEARTBEAT_BUSY_RETRY`，不等待成死锁。
- 节点 interrupt 对 Batch/Job 使用稳定排序与 `NOWAIT`；锁竞争返回 `409 NODE_INTERRUPT_BUSY_RETRY` 并写审计，不会部分取消或静默改写任务。
- Linux CPU 任务与 ComfyUI/GPU health 解耦，只服从节点 mode、人工保留及 Asset Worker 自身健康/容量；因此 GPU 忙不应让纯 CPU UV/拓扑任务错误排队。
- 3090-B 的 Linux CPU Worker 与 Windows Substance 槽使用各自容量门禁；Asset API 所有的 Substance drain/pending/fence/recovery 保持物理 GPU 互斥，operator 接管仍然优先。
- 公共 capacity、队列 ETA、管理页和实际 claim 共用同一 eligibility 计算；Codex 重拓扑只计入认证有效且探针新鲜健康的槽，Substance `total = used + available` 受四个物理槽和 fence 数共同约束。

### 2.5 已知发布兼容窗口

生产 Asset API `1.5.8` 的 `WorkerClaim` 使用 `extra="forbid"` 且不接受 `node_id`；Worker `1.2.5` 会发送 `node_id`，所以不能先升级 Worker，否则旧 API 会返回 HTTP 422。反向情况下，Asset API `1.5.9` 面对 Worker `1.2.4` 会返回 `200 / job=None` 并 fail closed，不会误领任务。故本次必须在零任务和外部 intake 冻结窗口中先升级 Asset API，再逐台升级 Worker；窗口内新的 Asset CPU 请求只允许排队，不得宣称完全无缝领取。

## 3. 所有权边界与明确不变项

本轮没有授权、也不得修改以下外部所有内容：

- ImageClip 或 ModelViewCreator Git 仓库；
- workflow JSON、custom node、模型、prompt、推理参数、采样步骤、分辨率、graph topology、最终 output node；
- UV/重拓扑业务 Skill、算法和输出语义；
- 用户批准的业务 pipeline commit、workflow identity 或 pipeline SHA。

本轮允许且实际针对的是 GPU Control 内部调度、准入、预热/亲和策略、队列反馈、传输、租约、产物校验、Web UI、可观测性和发布工程。不得用 preview 或中间产物替代批准的最终产物。

ComfyUI 运行约束：

- 禁止主动执行 `/free`、模型驱逐或缓存清理；
- 禁止为本轮发布停止、启动或重启三个 ComfyUI 容器；
- 保留已批准模型和 workflow 的热缓存，优先使用 warm-node affinity 与预加载；
- 节点只能在无运行任务后经 `DRAINING → ACTIVE` 安全滚动；
- 回到 `ACTIVE` 前必须验证三节点外部 pipeline commit/SHA 完全一致。

## 4. 六 API 精确产物契约

验收不是“存在下载链接”即可通过。每个成功任务必须满足精确 kind 集合、精确基数、kind 唯一、合法 ID/文件名、`size > 0`、64 位十六进制 SHA-256，以及同源相对下载 URL。

| API | 成功产物的精确 kind 集合 | 精确数量 |
|---|---|---:|
| ImageClip RGBA 父批次 | `result_archive` | 1 |
| ModelView Roughness | `output` | 1 |
| UV | `blend`, `fbx`, `report`, `qa`, `fbx_qa` | 5 |
| 重拓扑审计 | `audit`, `manifest` | 2 |
| 重拓扑处理（无参考图） | `blend`, `fbx`, `process_report`, `baseline_audit`, `audit`, `manifest`, `comparison`, `agent_plan`, `agent_prompt`, `agent_events`，以及 `view_{high\|reference\|generated}_{front\|side\|top\|perspective}` 共 12 个视图 | 22 |
| 重拓扑处理（有参考图） | 上述 22 项，加 `reference_images` | 23 |
| PBR `ao-self-v1` | `ao`, `result`, `log` | 3 |
| PBR `normal-dx-v1` | `normal_dx`, `result`, `log` | 3 |
| PBR `pbr-core-v1` | `ao`, `normal_dx`, `result`, `log` | 4 |
| PBR `li3d-pbr-full-v2` | `base_color`, `roughness`, `metallic`, `ao`, `normal_dx`, `normal_gl`, `world_normal`, `curvature`, `thickness`, `position`, `result`, `log` | 12 |

每个产物下载后还必须满足：

1. HTTP body 非空且字节数与 metadata `size` 精确一致；
2. 响应头 `X-Artifact-SHA256` 与 metadata SHA 精确一致；
3. body 实算 SHA-256 与 metadata SHA 精确一致；
4. ImageClip 单数 `artifact` alias 必须与 artifacts 列表唯一元素完全相同；
5. Roughness 直接响应 SHA 必须与后续 artifact listing SHA 完全一致。

任何缺失、多余、重复 kind，或 size/SHA/URL 不符合契约，都必须使该 API 验收失败，不能只写 warning 后放行测试报告。

## 5. UV / 重拓扑 QA 语义

当前生产 UV 和重拓扑都采用 advisory QA：

- 算法质量检查未达阈值时，任务可以 `SUCCEEDED`，同时返回结构化 warning 和完整正式产物；
- 重拓扑正式交付必须包含 `blend`、`fbx` 及其余契约产物，不能只返回日志、计划或诊断文件；
- UV 正式交付必须包含五项契约产物；
- 前端必须展示“已交付但有质量警告”，不得伪装成严格 QA 通过。

以下硬完整性门禁仍然 fail closed，不能因 advisory QA 而放宽：身份、输入/输出 schema、manifest、租约所有权、产物非空、SHA 一致、原始输入保护和不可变工作流身份。

## 6. 六 API 综合压力验收计划

固定场景文件：`tests/load/scenarios/six_api_120_20260803.yaml`。

### 6.1 流量模型

| 场景 | 权重 |
|---|---:|
| ImageClip RGBA | 42 |
| ModelView Roughness | 23 |
| UV | 12 |
| 重拓扑审计 | 8 |
| 重拓扑处理 | 10 |
| PBR | 5 |

阶段为：1 用户 60 秒（spawn rate 1）、10 用户 120 秒（2）、25 用户 180 秒（5）、50 用户 300 秒（10）、100 用户 600 秒（20）、120 用户 600 秒（10）；模式为 `bounded_stress`。

### 6.2 固定业务身份

- ImageClip：workflow version `2026.07.30-691770c-r1`，commit `691770cd...`，pipeline SHA `00e710...`，批准的 final output 为 `SaveImage #25`。
- ModelView Roughness：workflow version `2026.07.29-d318bb39-roughness-v1`，commit `d318bb...`，pipeline SHA `8a5274...`。

正式执行时必须从批准文档/manifest 读取完整 SHA，不得用本文省略展示的短 SHA 作为执行参数。

### 6.3 执行前置条件

1. 默认只能 plan-only；正式执行必须显式授权并填写变更单、时间窗口和精确 API allowlist。
2. 完成并验证备份，固定 fixtures 及其 SHA，使用隔离 tenant/session 和测试 client；密钥只经安全渠道交换。
3. 开始前 GPU job、父批次和 Asset job 的非终态数量必须全部为 0；无活动租约、无 pending/fence/recovery/manual/foreign Windows 任务，Comfy 队列为空。
4. 至少 3 个健康 GPU 节点、3 个在线 Asset worker、至少 1 个 CPU slot 和 1 个 Substance slot。
5. watchdog 一旦发现外来生产任务，立即停止新增压测流量；生产准入门禁为第二道保护，不替代 watchdog。
6. teardown 只能清理本次 session 创建的资源，不得跨 tenant 删除或取消用户任务。

### 6.4 验收阈值

| 指标 | 阈值 |
|---|---:|
| 失败率 | `<= 1%` |
| 异步提交 P95 | `<= 3000 ms` |
| Roughness 同步 E2E P95 | `<= 600000 ms` |
| 状态轮询 P95 | `<= 1500 ms` |
| artifact 下载 P95 | `<= 30000 ms` |
| queue wait P95 | `<= 900000 ms` |
| retry rate | `<= 5%` |

生命周期验收要求六个 API 各至少一个成功任务，且该任务的全部精确契约产物都完成三重 SHA 验证；本次注册工作还必须全部完成，或严格按 bounded-stress 的有界结算规则收敛。只看到 HTTP 2xx、任务终态或截图都不构成通过。

### 6.5 本轮待填结果

| 证据 | 状态 |
|---|---|
| 预检原始报告 | `PENDING_LOAD_PREFLIGHT` |
| plan-only 报告 | `LOCAL_PLAN_ONLY_COMPLETE`；不是正式执行授权或压测结果 |
| 正式执行 change/window/授权 | `PENDING_LOAD_AUTHORIZATION` |
| Locust/runner 原始结果 | `PENDING_SIX_API_LOAD_RESULT` |
| 六 API lifecycle + exact artifact 结果 | `PENDING_SIX_API_ARTIFACT_ACCEPTANCE` |
| 节点 CPU/GPU/VRAM/队列/租约监控 | `PENDING_LOAD_TELEMETRY` |
| 生产任务到达后拒绝新测试/停止发压证据 | `PENDING_PRODUCTION_PRIORITY_EVIDENCE` |
| 测试 session 清理证明 | `PENDING_LOAD_TEARDOWN` |

本地 plan-only 证据为 `/tmp/gpu-control-six-api-plan-b410a6a-r2.json`，文件 SHA-256 为
`f5d265baba60e79f6fd5837b48e12e7f214ddf425446899ed1931689378eb308`。报告明确记录
`mode=PLAN_ONLY`；该模式未发出 HTTP 请求、未创建任务，也未触碰生产。它按预期保留以下六项执行
阻塞，不能据此启动正式压力：

1. `ALLOW_LOAD_TEST` 尚未精确设为 `true`；
2. `LOAD_TEST_CONFIRMATION_TOKEN` 与本 session/target 不匹配；
3. 未从环境提供 `LOAD_TEST_API_KEYS`；
4. `LOAD_TEST_TENANT_IDS` 尚未与 API keys 做唯一一一绑定；
5. 未提供只读预检所需的 `LOAD_TEST_ADMIN_BEARER_TOKEN`；
6. 未指定一个全新的显式 `LOAD_TEST_RESULT_DIR`。

## 7. 已验证事实与最终源码门禁

### 7.1 当前可以确认

- 版本文件和候选构建入口已经统一指向 Control Plane `1.5.9`、Worker `1.2.5`。
- 核心实现已冻结为 `dac30c039f692cf8274eaff5430ca7ebfd97b201`（提交时间 `2026-08-03T22:13:27+08:00`）；其上只有 Web dev-dependency lockfile 安全修复以及 README、CHANGELOG 和本文 evidence 回填，不改变后端运行语义。已记录的 `origin/main` 仍为 `56035975cd9ca4b0c904e34aca11d30b8779d2cd`，因此这些修复尚未推送。
- 源码中已存在生产优先全局准入、精确 artifact 契约、Linux Worker 长上传续租与进程代际绑定、Windows Agent 长 SHA/上传续租、统一容量/ETA、锁竞争快速失败及五镜像发布校验实现和对应自动化用例。
- 当前最终修复工作树 Python 全量结果为 `425 passed, 9 skipped`；其中 8 条 skip 是必须连接 PostgreSQL 的锁竞争专项，另 1 条是基础质量镜像未安装可选 Locust 依赖。相同代码在一次性 PostgreSQL 17.5、loopback 且数据库名为 `gpu_control_test_*` 的隔离测试库上串行运行 Scheduler 锁、节点 interrupt、Worker generation/lease 并发用例，结果为 `8 passed`；该库使用 tmpfs，验证后已停止并自动移除。另在一次性容器安装项目锁定的 Locust `2.37.14` 后，负载工具专项为 `66 passed`。这些都是工作树证据，最终 commit 冻结后仍需复跑。
- 工作树原始 JUnit 暂存于 `/tmp/gpu-control-1.5.9-gates.Mmmdes/`：`python-full-current.xml` 为 63,543 bytes、SHA-256 `ae4dad58005934878386220b20e8b576f4e1368eef114869d2ee339011b541a9`；`postgres-concurrency.xml` 为 1,495 bytes、SHA-256 `f411d834aeafb5fd2e42380cf985f8dd5462c63a5484e8afe03c8921b4ddbbe0`。最终 commit 上的报告必须重新生成并进入正式 release archive，不能仅依赖 `/tmp`。
- Ruff 全仓、Mypy `36 source files`、compileall、`git diff --check`、SQLite 从 `0001` 到 `0012` 的完整迁移和控制面/GPU 节点两套 Compose 解析均通过。
- Web 当前候选工作树结果：测试 `16/16`，ESLint、Prettier、`vue-tsc` 和 Vite build 均通过；Vite 仅报告既有大 chunk advisory，不是构建失败。构建时发现的 `brace-expansion` high advisory 只存在于 dev dependency，已用 lockfile-only 补丁升级到修复版本；重新 `npm ci` 后完整 npm audit 和 `--omit=dev` audit 均为 0 vulnerability。
- 最终独立安全审计结果为 `P0=0 / P1=0`。审计覆盖全局/tenant/Node/Batch/Job/Worker 锁顺序、heartbeat/claim 进程代际、幂等过期重用、提交回执丢失后的输入与正式产物保全、实际 claim 与容量口径、精确压测清场、UV/拓扑 advisory 正式交付以及 Substance 恢复闭锁。
- 已知 P2/运维约束：时间轴仍依赖客户端提交的 `started_at`；1.5.8/1.2.4 到 1.5.9/1.2.5 存在上述短领取冻结窗；ETA 仍是队列近似值；两组 PostgreSQL 并发文件共用隔离测试库，禁止并行执行。最终可推送提交确定后仍须在完整 commit 上复跑并归档原始输出。因此当前状态继续保持 `PENDING_FINAL_SOURCE_GATES`。
- 1.5.8 生产基线此前已记录真实 PBR、UV 和重拓扑成功任务；这些仅证明旧基线部分能力，不是 1.5.9 canary，也不能替代本轮六 API 验收。

### 7.2 本地 candidate 镜像、备份与素材证据

以下五个镜像是在后续代际/锁序/容量修复之前从 `b410a6a` 构建的本地 candidate identity。它们没有 registry digest、SBOM 或正式归档，未部署到任何生产组件，且已统一标记为 `SUPERSEDED_REBUILD_REQUIRED`；不得填入正式发布 digest，也不得部署：

| 镜像 | 本地 candidate image ID |
|---|---|
| `gpu-control-api:1.5.9-candidate-b410a6a` | `sha256:8e5ca3b3326d5d4ce5bbe15137a038c281a69454ebf083e02020b381c4f8d047` · `SUPERSEDED_REBUILD_REQUIRED` |
| `gpu-control-scheduler:1.5.9-candidate-b410a6a` | `sha256:8ae8602003be2d36f1bbf201efefe92ce58c501ae0985c4e42fe18d390ff7ffc` · `SUPERSEDED_REBUILD_REQUIRED` |
| `unified-scheduler-asset-api:1.5.9-candidate-b410a6a` | `sha256:31511e02d2c2aad07f379639b94df6474296bae113f5560dd4577c5a54bd44ed` · `SUPERSEDED_REBUILD_REQUIRED` |
| `gpu-control-web:1.5.9-candidate-b410a6a` | `sha256:1e7d86bfc26cb4b896d0c4480a8b83b2de54157f8509e2ac622232811b5047b1` · `SUPERSEDED_REBUILD_REQUIRED` |
| `li3d/blender-worker:1.2.5-candidate-b410a6a` | `sha256:90d9c60e1d29bf3c1c123b787540c131049b7e2d7f31634954898b4492b2df6a` · `SUPERSEDED_REBUILD_REQUIRED` |

本地小型备份位于 `/srv/gpu-control/backups/20260803T130246Z-small`，绑定 Git commit
`b410a6a7994cdd06335a106ad5257eafb6378fdf`。`BACKUP_COMPLETE` 状态为 `COMPLETE`，其记录的
`SHA256SUMS_SHA256` 与实算值均为
`f3ed94fc180ed7897c04d275305ce4acf67b7f4e8bf3fe158a3c48841a6220ca`，清单内 `14/14` 项复验通过。
该备份明确记录 `MODE=small`、`QUIESCE_CHECK=NOT_ENFORCED`，所以只作为本地恢复辅助证据，不能替代
正式压测要求的空闲窗口、强制 quiesce、restore verify 和 24 小时内完整备份。

六 API 合成素材 r2 位于 `/tmp/gpu-control-six-api-b410a6a-r2`，生成过程不读取生产用户素材且不需要
网络。素材 manifest SHA-256 为
`573fbfe1926f440199cd89db8fc52b4ec49aa3f50bc67eca20943e0d21dc3d1b`，`SHA256SUMS` 文件 SHA-256 为
`a4f76bdd6ca833ee259f273586d57df0163a76ad561b84872fadc3f15a5785a7`，清单内 `26/26` 项全部通过。
Blender 5.1.2 验证报告 `passed=true`，报告 SHA-256 为
`dae142590c97e5823182da7a1aa052c0fef5c1a5c1fc8ca733f706434eaae51e`。这些结果只证明素材完整可读，
不等于六 API 已完成真实 canary 或压力验收。素材内容可复用，但该目录的 provenance 仍绑定历史
`b410a6a`；正式压测前必须以最终 source identity 重新生成索引/provenance，不能把旧路径冒充最终发布证据。

### 7.3 最终门禁必须回填

| 门禁 | 状态 |
|---|---|
| 最终完整 source commit 上 Python unit/integration | `PENDING_FINAL_SOURCE_GATES` |
| 最终完整 source commit 上 Ruff/Mypy/compileall | `PENDING_FINAL_SOURCE_GATES` |
| 最终完整 source commit 上 Web 全门禁复跑 | `PENDING_FINAL_WEB_GATES` |
| 两套 Compose config 解析 | `PENDING_FINAL_COMPOSE_GATES` |
| 发布打包器/身份校验器专项测试 | `PENDING_FINAL_PACKAGING_GATES` |
| 无越界 workflow/Skill 变更审计 | `PENDING_FINAL_BOUNDARY_AUDIT` |

## 8. 安全发布、灰度与回滚

### 8.1 发布前证据

| 字段 | 必填值 |
|---|---|
| 最早本地代码候选 Git commit | `b410a6a7994cdd06335a106ad5257eafb6378fdf`；已被后续修复取代，`SUPERSEDED` |
| 本轮实现 Git commit | `dac30c039f692cf8274eaff5430ca7ebfd97b201`；`LOCAL_COMMITTED_NOT_PUSHED` |
| 最终可推送完整 Git commit | `PENDING_FINAL_COMMIT` |
| 完整仓库/历史推送到指定 GitHub `origin/main` 的精确授权 | `PENDING_SOURCE_PUSH_AUTHORIZATION` |
| GitHub `main` 包含该 commit | `PENDING_GITHUB_MAIN` |
| API image digest / SBOM | `PENDING_API_DIGEST` / `PENDING_API_SBOM` |
| Scheduler image digest / SBOM | `PENDING_SCHEDULER_DIGEST` / `PENDING_SCHEDULER_SBOM` |
| Asset API image digest / SBOM | `PENDING_ASSET_API_DIGEST` / `PENDING_ASSET_API_SBOM` |
| Web image digest / SBOM | `PENDING_WEB_DIGEST` / `PENDING_WEB_SBOM` |
| Worker image digest / SBOM | `PENDING_WORKER_DIGEST` / `PENDING_WORKER_SBOM` |
| OCI/Docker config digest 对照 | `PENDING_CONFIG_DIGEST_MATCH` |
| release archive 与 Git LFS 证据 | `PENDING_RELEASE_ARCHIVE_LFS` |
| 小型候选备份 | `/srv/gpu-control/backups/20260803T130246Z-small`；清单 SHA `f3ed94fc...a6220ca`；`LOCAL_SMALL_BACKUP_ONLY` |
| 全量备份路径、SHA、恢复验证 | `PENDING_BACKUP_AND_RESTORE_VERIFY` |
| 回滚版本和镜像 digest | `PENDING_ROLLBACK_IDENTITY` |

### 8.2 灰度顺序

1. 冻结发布身份，完成最终门禁、备份及恢复抽检；预拉取全部镜像，但不 recreate 服务。
2. 在调用方/网关冻结新的 Asset 提交，保存三节点原始 mode；将三节点设为 operator-owned `DRAINING`，确认 GPU jobs/batches、Asset jobs、lease、Comfy 队列、Windows Baker/native process 全部归零。
3. 仅替换 Asset API 为 `1.5.9`，检查健康、完整 source SHA、数据库 head、日志和零 lease。此时旧 Worker `1.2.4` 必须只得到 `200 / job=None` 并安全停止领取；不得把这个短领取冻结窗宣称为无缝服务。
4. 三节点保持 `DRAINING`，按 `control-4090 → worker-3090-a → worker-3090-b` 逐台只替换 Linux Worker 为 `1.2.5`；每步验证 Worker revision、fresh heartbeat、唯一 `agent_instance_id`、节点绑定、零 current job/lease。Worker 没有 Compose healthcheck，不能只看 `compose ps`。
5. 三个 Worker 全部兼容后，只恢复一个节点的原 schedulable mode，跑受控 UV/拓扑 CPU canary，核对终态、正式产物、SHA 和租约归零；逐节点重复并恢复原 mode。
6. 依次只替换 API、Web、Scheduler；Scheduler 最后。每一步检查健康、版本、完整 SHA、日志、零任务和零异常锁等待。
7. 所有 Compose 更新必须指定单个目标 service，并使用 `--no-deps --no-build --pull never --force-recreate`；禁止调用会全栈 reconcile 的部署脚本。发布前后保存三台 ComfyUI 的 container ID、StartedAt、RestartCount、队列和 workflow SHA，证明没有停止、重启、`/free` 或清缓存。
8. Windows Agent 如需安装新候选，作为单独变更处理：先 drain Substance 槽并确认无 PBR/外来进程；安装后验证 parser、heartbeat、长上传和恢复。不得与五镜像滚动混在同一步。
9. 六 API 各跑一个真实 canary，逐项校验终态、request/trace ID、时间字段和精确 artifact 三重 SHA；随后完成浏览器正式 QA。
10. 只有上述通过、解除 intake freeze 且生产窗口无异常，才允许进入受控综合压测。

当前发布结果：`PENDING_ROLLOUT`。

### 8.3 回滚

- 保持 intake freeze 和三节点 operator-owned `DRAINING`，按 `Scheduler → Web → API → Linux Worker 逐台回 1.2.4 → Asset API 最后回 1.5.8` 反向回滚。Worker `1.2.4` 在 Asset API `1.5.9` 下只会 fail closed；Asset API 最后回旧后才恢复旧协议领取。
- 本候选未增加 migration，仍需在操作前确认数据库 head 为 `20260803_0012`；不得随意回滚数据库。
- 不回滚、修改或重写外部 ImageClip/ModelView/UV/Retopo pipeline。
- 若 Windows v5 新候选异常，优先隔离/drain 3090-B 对应槽位并保留证据；禁止恢复会驱逐 ComfyUI 缓存的旧行为。
- 回滚后重复健康检查、版本核验、队列/租约核验和最小 canary。

回滚演练结果：`PENDING_ROLLBACK_DRILL`。

## 9. Canary 与浏览器验收回填

| 项目 | 状态 |
|---|---|
| ImageClip exact-artifact canary | `PENDING_CANARY_IMAGECLIP` |
| Roughness exact-artifact canary | `PENDING_CANARY_ROUGHNESS` |
| UV advisory + 5 artifacts canary | `PENDING_CANARY_UV` |
| Retopo process advisory + 22/23 artifacts canary | `PENDING_CANARY_RETOPOLOGY` |
| Retopo audit 2 artifacts canary | `PENDING_CANARY_RETOPOLOGY_AUDIT` |
| PBR profile-specific artifact canary | `PENDING_CANARY_PBR` |
| Windows 长上传/续租/恢复 canary | `PENDING_WINDOWS_CANARY` |
| Web browser desktop/responsive QA | `PENDING_BROWSER_QA` |
| 三节点版本、pipeline SHA 与 cache continuity | `PENDING_NODE_ALIGNMENT_AND_CACHE_CONTINUITY` |

## 10. 动画管家（AssetClaw）联合交接

GPU Control 完成发布后需提供：

- 最终 source commit、五镜像 registry digest、SBOM、数据库 revision、发布时间和回滚身份；
- 六 API endpoint、认证身份、tenant/session、CA 使用方式；密钥不得写入 Markdown；
- 生产优先准入、幂等、取消、恢复和 advisory QA 的最终契约；
- 六个 canary 的原始请求/父状态/child job/request ID/trace ID/artifact metadata；
- 每个 artifact 的 metadata/header/body 三重 SHA 结果；
- 正式综合压测原始报告、节点级性能和异常/重试统计。

动画管家应当：

1. 将 UV/重拓扑 warning 展示为“任务已交付但有质量警告”，不要把它改写成失败或隐藏正式 BLEND/FBX。
2. 同时兼容 HTTP 200/202 和幂等重放返回，保存响应中的权威 job/batch ID。
3. SSE 用于及时更新，GET 状态作为权威恢复来源；断线后能重连并从原 ID 恢复。
4. 下载时执行 size 与三重 SHA 校验，不能只验证文件名或 HTTP 200。
5. 返回其应用 commit/build、CA 校验状态、六 API request/job ID、幂等重放、SSE 重连、取消、完整产物报告和原始 JSON。

动画管家联合回执：`PENDING_ASSETCLAW_RECEIPT`。

## 11. 最终签署条件与状态推进

状态只能依次推进：

```text
CANDIDATE_NOT_DEPLOYED → DEPLOYED_NOT_ACCEPTED → PRODUCTION_ACCEPTED
```

候选必须先在安全灰度后从 `CANDIDATE_NOT_DEPLOYED` 推进为 `DEPLOYED_NOT_ACCEPTED`；只有以下项目全部有原始证据且无未解释失败时，才能继续推进到 `PRODUCTION_ACCEPTED`：

- 最终源码 commit 已在 GitHub `main`，工作树干净且最终全量门禁通过；
- 五镜像 digest、SBOM、archive、Git LFS、备份和回滚身份完整可复验；
- 安全灰度和六 API canary 通过，未清理或重启 ComfyUI，三节点外部 pipeline 身份一致；
- Web 浏览器验收通过；
- 六 API bounded-stress 正式报告达到阈值，并证明真实生产任务优先；
- 固定 B97 的 1/2/3 节点同素材基准和并发 `3×B97` 结果达到联合合同阈值；
- 节点离线、上传损坏、prompt 超时、Scheduler 重启、非法取消、workflow 漂移、artifact 篡改、create 响应丢失与幂等重放等完整故障矩阵通过；
- 动画管家完成双向回执；
- 连续观察 7 天无新的 P0/P1 稳定性问题。

在此之前，本轮统一结论保持：

```text
CURRENT_PRODUCTION = DEPLOYED_NOT_ACCEPTED
CONTROL_1_5_9_WORKER_1_2_5 = CANDIDATE_NOT_DEPLOYED
FINAL_SOURCE_GATES = PENDING_FINAL_SOURCE_GATES
SOURCE_PUSH_AUTHORIZATION = PENDING_SOURCE_PUSH_AUTHORIZATION
FORMAL_LOAD_TEST = PENDING_SIX_API_LOAD_RESULT
FIXED_B97_AND_3XB97 = PENDING_FIXED_BENCHMARK
FAULT_INJECTION_MATRIX = PENDING_FAULT_MATRIX
SEVEN_DAY_OBSERVATION = PENDING_7_DAY_OBSERVATION
ASSETCLAW_RECEIPT = PENDING_ASSETCLAW_RECEIPT
JOINT_ACCEPTANCE = PENDING_JOINT_ACCEPTANCE
```

## 12. 索引文件更新

- `README.md` 已追加本文入口；生产证据未回填前，顶部生产基线仍保持当前 1.5.7/1.5.8 分组件事实。
- `CHANGELOG.md` 的 `1.5.9（候选）` 已补充跨 GPU/Asset 全局原子生产优先准入，以及六 API 精确 artifact 集合、基数和三重 SHA fail-closed；部署前继续保留“候选”字样。
- `docs/00_START_HERE.md` 已追加本文编号和结构表入口；实际灰度前继续保留当前分组件生产基线。
