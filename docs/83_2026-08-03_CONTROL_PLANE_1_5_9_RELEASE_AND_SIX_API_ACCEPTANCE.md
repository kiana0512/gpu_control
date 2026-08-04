# GPU Control 1.5.9 / Worker 1.2.5 统一发布与六 API 联合验收

- 日期：2026-08-03；生产滚动回填：2026-08-04
- 当前生产总状态：`DEPLOYED_NOT_ACCEPTED`
- 1.5.9 / Worker 1.2.5 候选状态：`DEPLOYED_NOT_ACCEPTED`
- 已部署源码 `S`：`e81c4cb45a360b83561c154867da7ae86cbbbc70`
- GitHub 源码推送：`VERIFIED_ORIGIN_MAIN_e81c4cb45a360b83561c154867da7ae86cbbbc70`
- 联合验收状态：`PENDING_JOINT_ACCEPTANCE`
- 适用范围：GPU Control 控制面、Scheduler、Asset API、Web UI、Linux Blender Worker、Windows Substance Agent v6、六 API 联合验收与发布证据

> 本文是本轮一次性交付的唯一发布与验收入口。2026-08-04 已完成 1.5.9 / 1.2.5
> 生产滚动，但六 API canary、正式综合压测和联合签署尚未完成，因此状态只能是
> `DEPLOYED_NOT_ACCEPTED`，不得宣称 `FROZEN` 或 `PRODUCTION_ACCEPTED`。

## 1. 执行结论

本轮实现集中在 GPU Control 自身边界内：跨 GPU/Asset 平面的生产任务优先原子准入、Worker 进程代际与节点绑定、锁竞争快速失败、统一容量/ETA 口径、Worker/Agent 长上传期间的租约续期、六 API 精确产物契约与负载验收，以及五镜像可复现打包；Web UI 可读性、GPU 温度/功率遥测和运维入口纳入同一版本。生产镜像统一绑定源码 `e81c4cb45a360b83561c154867da7ae86cbbbc70`，Control Plane 为 `1.5.9`、Linux Blender Worker 为 `1.2.5`。早期 `b410a6a` 等 candidate 已被取代，禁止部署。生产滚动已经完成；正式综合压测、全部六 API canary、浏览器正式验收和联合签署仍待完成。

> 2026-08-04 追加门禁：源码 `4f055a0f284eed5e1a8274cef3922356b2023bc3` 及其
> `b1d54e077bf00cb49cc7763ed483045945387721` Git LFS 候选包已被后续 Codex
> 探针快速恢复修复取代，状态为 `SUPERSEDED_NOT_DEPLOYED`。生产 1.2.4 曾在三台真实
> `codex exec` 均成功后仍把瞬时失败缓存到下一轮约 30 分钟巡检；1.2.5 修复为健康时维持
> 30 分钟低频巡检，失败时只在 Worker 空闲按 60 秒基础退避重探，业务任务运行时不执行
> 恢复探针。新源码、新五镜像和新候选证据完成前，禁止部署上述旧候选包。

当前生产统一基线如下，状态保持 `DEPLOYED_NOT_ACCEPTED`：

| 组件 | 当前已记录生产基线 | 1.5.9 目标 | 本文状态 |
|---|---|---|---|
| API / Scheduler / Web | `1.5.9`，source `e81c4cb...` | `1.5.9` 同一完整 commit | `DEPLOYED_VERIFIED` |
| Asset API | `1.5.9`，source `e81c4cb...` | `1.5.9` 同一完整 commit | `DEPLOYED_VERIFIED` |
| Linux Blender Worker | `1.2.5`，三节点同一 image ID、同一 source | `1.2.5` 同一完整 commit、同一镜像 identity | `DEPLOYED_VERIFIED` |
| Windows Substance Agent | 四槽 `substance-baker-2026.08.03-v6`，`ONLINE/HEALTHY/0` | 精确 v6 身份 | `DEPLOYED_CANARY_PENDING` |
| 数据库 | `20260803_0012` | 本候选未新增 migration | `VERIFIED` |

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
- Windows Substance Agent v6 候选对多 GB 产物使用 4 MiB 分块计算 SHA，并在计算期间持续续租与 heartbeat；异步 `curl` 上传期间同样续租与 heartbeat，并校验进程退出码、服务端响应 JSON 和最终状态。v6 还要求 Kill、限时 WaitForExit、Refresh 与 HasExited 形成可证实终止链；任何一步不确定都上报 `SUBSTANCE_BAKER_TERMINATION_UNCONFIRMED`，服务端强制不可重试并保留 recovery interlock。
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

项目 owner 已在 2026-08-03 明确授权把本轮完整更新同步到
`https://github.com/kiana0512/gpu_control.git` 的 `origin/main`。授权不等于已推送；只有当 source commit `S`
实际推送并经 `git ls-remote` 复验后，才能进入依赖已推送 SHA 的正式打包。

### 2.4 Worker 代际、节点绑定与容量真相

- Linux Asset Worker 每个进程生成唯一 `agent_instance_id` 和 `agent_started_at`；heartbeat、claim、job lease 绑定同一节点和进程代际，旧进程或错误节点不能继续领取。
- heartbeat 采用稳定的 Node/Worker/AssetJob 锁序；与续租、完成或 reaper 竞争时使用 PostgreSQL `NOWAIT` 快速返回可重试 `409 ASSET_WORKER_HEARTBEAT_BUSY_RETRY`，不等待成死锁。
- 节点 interrupt 对 Batch/Job 使用稳定排序与 `NOWAIT`；锁竞争返回 `409 NODE_INTERRUPT_BUSY_RETRY` 并写审计，不会部分取消或静默改写任务。
- Linux CPU 任务与 ComfyUI/GPU health 解耦，只服从节点 mode、人工保留及 Asset Worker 自身健康/容量；因此 GPU 忙不应让纯 CPU UV/拓扑任务错误排队。
- 3090-B 的 Linux CPU Worker 与 Windows Substance 槽使用各自容量门禁；Asset API 所有的 Substance drain/pending/fence/recovery 保持物理 GPU 互斥，operator 接管仍然优先。
- Asset API 只接受精确 `substance-baker-2026.08.03-v6` 身份领取 PBR。旧 v5 即使认证、进程探针和资源均健康也只能 `DRAINING`；v6 heartbeat 的本地 `current_jobs` 必须与该 Worker 的 durable live lease 数一致，宿主 native Baker 数也不能超过全部 live Substance lease，否则同样 fail closed。
- 公共 capacity、队列 ETA、管理页和实际 claim 共用同一 eligibility 计算；Codex 重拓扑只计入认证有效且探针新鲜健康的槽，Substance `total = used + available` 受四个物理槽和 fence 数共同约束。

### 2.5 已知发布兼容窗口

生产 Asset API `1.5.8` 的 `WorkerClaim` 使用 `extra="forbid"` 且不接受 `node_id`；Worker `1.2.5` 会发送 `node_id`，所以不能先升级 Linux Worker，否则旧 API 会返回 HTTP 422。反向情况下，Asset API `1.5.9` 面对 Worker `1.2.4` 会返回 `200 / job=None` 并 fail closed，不会误领任务。

Windows Agent 有独立的反向兼容窗口：旧 Asset API 要求 v5，而新 Asset API 只接受 v6。因此必须在零 PBR、宿主 native Baker 为零且 intake 冻结时，先把四个 Windows Agent 更新为 v6；它们在旧 API 下暂时 `DRAINING` 是预期行为。四槽脚本 SHA 和 v6 heartbeat 全部确认后再升级 Asset API 1.5.9，待四槽转为 `ONLINE` 后才逐台升级 Linux Worker。安全顺序固定为“**四 Windows Agent → Asset API → 三 Linux Worker**”，不能反转，也不得把兼容窗口宣称为无缝领取。

## 3. 所有权边界与明确不变项

本轮没有授权、也不得修改以下外部所有内容：

- ImageClip 或 ModelViewCreator Git 仓库；
- workflow JSON、custom node、模型、prompt、推理参数、采样步骤、分辨率、graph topology、最终 output node；
- UV/重拓扑业务 Skill、算法和输出语义；
- 用户批准的业务 pipeline commit、workflow identity 或 pipeline SHA。

本轮允许且实际针对的是 GPU Control 内部调度、准入、预热/亲和策略、队列反馈、传输、租约、产物校验、Web UI、可观测性和发布工程。不得用 preview 或中间产物替代批准的最终产物。

ComfyUI 运行约束：

- `scripts/deploy_control.sh` 与 `scripts/deploy_node.sh` 已改为显式 build-only；默认拒绝执行，且不再调用无 service 范围的 `compose up`。它们不会触碰 ComfyUI，也不能替代本节的排空和逐服务滚动流程。

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

### 5.1 2026-08-04 真实生产并行证据

在三条动画管家 ImageClip 父批次持续运行、三台 ComfyUI 均保持原 container ID/缓存且未执行
restart、recreate 或 `/free` 的窗口中，生产 Asset 请求完成了以下自然流量验证：

- PBR `cc004096-8a08-4c65-8b7a-0a625e8ac886` 在 3090-B 安全 fence 后由 Windows-01
  完成，终态 `SUCCEEDED/100`；节点随后自动恢复 `ACTIVE` 并继续 ImageClip；
- UV `4322c38e-55c9-445b-8b1a-fa3c8dca4183` 与
  `dffcd257-9f4d-4b6a-9147-2910312368c5` 均为 `SUCCEEDED/100`，每项精确返回
  `blend,fbx,fbx_qa,qa,report` 五件正式产物；
- 重拓扑 `0e9dc7d5-8077-4e3b-bb9f-7f9bc3c55490` 与
  `c2b80552-0f3d-451a-9a1d-bbad3d6522c6` 均为 `SUCCEEDED/100`，每项精确返回
  22 件产物，包含正式 `retopology_final.blend`、`retopology_final.fbx`、manifest、审计、
  对比图和三组四视图；没有因 advisory QA 扣住 BLEND/FBX。

这些是当前生产版本的旁路兼容证据，不替代 1.5.9 灰度后的隔离 canary 和三重 SHA 验收。

同一窗口发现 3090-B 的可选 Node Agent GPU 指标在 WSL/Windows 共载竞争时反复
`ReadTimeout`；Comfy 作业、节点 heartbeat 与缓存命中仍正常。源码提交
`cdd89fbb4c77f7e403a0b3af0bb93c09e083beac` 为失败指标查询增加 30 秒有界退避，并继续让
Comfy `system_stats` 提供显存降级数据。该修复已通过全量 Python 单元/集成门禁、Ruff 与
Mypy，但尚未部署；必须随最终 Scheduler 在全空闲窗口灰度，不能热重启当前生产调度器。

## 6. 六 API 综合压力验收计划

基础场景文件：`tests/load/scenarios/six_api_120_20260803.yaml`。2026-08-04 业主要求的
扩展高压场景为：`tests/load/scenarios/six_api_120_extended_20260804.yaml`。

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

扩展场景仍保留审计批准的 120 用户硬上限，但把 120 用户平台期延长到 2400 秒；流量权重调整为
ImageClip 35、Roughness 20、UV 15、重拓扑审计 10、重拓扑处理 10、PBR 10，使 GPU 消耗类
占 65%、CPU 类占 35%，并提高 Windows Substance 覆盖。扩展场景本身总计 3660 秒，加 300 秒
teardown 和 540 秒预检/证据余量，正式变更窗口至少 **4500 秒（75 分钟）**。提高压力不能绕过
零生产任务、生产 watchdog、12 组隔离身份、备份和 live deployment receipt 门禁。

### 6.2 固定业务身份

- ImageClip：workflow version `2026.07.30-691770c-r1`，commit `691770cd...`，pipeline SHA `00e710...`，批准的 final output 为 `SaveImage #25`。
- ModelView Roughness：workflow version `2026.07.29-d318bb39-roughness-v1`，commit `d318bb...`，pipeline SHA `8a5274...`。

正式执行时必须从批准文档/manifest 读取完整 SHA，不得用本文省略展示的短 SHA 作为执行参数。

### 6.3 执行前置条件

1. 默认只能 plan-only；正式执行必须显式授权并填写变更单、时间窗口和精确 API allowlist。
2. 完成并验证备份，固定 fixtures 及其 SHA，使用隔离 tenant/session 和测试 client；生产正式执行至少配置 **12 个唯一 API key，并一一对应 12 个唯一 tenant**。密钥只经安全渠道交换，计划、预检和结果只能记录数量、tenant ID 及 key index，禁止记录 key 值。
3. 开始前 GPU job、父批次和 Asset job 的非终态数量必须全部为 0；无活动租约、无 pending/fence/recovery/manual/foreign Windows 任务，Comfy 队列为空。
4. 至少 3 个健康 GPU 节点、3 个在线 Asset worker、至少 1 个 CPU slot 和 1 个 Substance slot。
5. watchdog 一旦发现外来生产任务，立即停止新增压测流量；生产准入门禁为第二道保护，不替代 watchdog。
6. teardown 只能清理本次 session 创建的资源，不得跨 tenant 删除或取消用户任务。
7. 基础场景时间窗口必须覆盖全部阶段 `1860s`、有界 teardown `300s` 以及预检/证据落盘余量 `540s`，合计至少 **2700 秒（45 分钟）**；扩展场景对应至少 **4500 秒（75 分钟）**。启动时剩余窗口也必须不少于所选场景的完整要求，不能让正式阶段或清场跨窗。
8. 正式执行必须通过环境安全注入并写入 `plan.json`、`preflight.json` 和 `summary.json` 的目标发布身份：完整 40 位小写 `source_revision`，以及 API、Scheduler、Asset API、Web、Worker 五个不可变 `sha256:<64-hex>` 镜像 digest。缺一、使用短 SHA、tag 或非 immutable digest 均 fail closed；确认 token 同时绑定这组身份，计划生成后替换身份会被 Locust 拒绝。预检还会实时读取 Control API 与 Asset API 的版本端点，只有两者的 `source_revision` 精确匹配计划且 package/build 对齐、provenance 完整时才允许发压。
9. 上述环境字段只是“一致性声明”，不是发布身份的权威来源。生产 runner 和 Locust 必须分别以固定 Git argv 验证 `origin` 是批准的 GitHub 仓库、`refs/heads/main` 的远端 tip 精确等于完整提交 `E`，本地 harness `HEAD == E` 且 tracked worktree clean；再从 `E` 用 `git show` 读取 `artifacts/control-plane/<version>/deployment/live-deployment-receipt.json` 并校验其 blob SHA-256。权威 receipt 必须使用 `gpu-control-live-deployment.v1`，状态精确为 `DEPLOYED_NOT_ACCEPTED / deployed=true / production_accepted=false`，并绑定源码 `S`、候选证据 path/blob SHA、五组件身份、七个实际容器和 Windows Substance v6 实装身份。receipt 再锚定 `gpu-control-release-candidate.v2` 父证据以验证 offline OCI manifest/config、OCI label 与 source revision；candidate 必须标记 `CANDIDATE_ARCHIVE_ONLY / deployed=false / production_accepted=false`，不能直接授权生产。`S` 必须是 `E` 的祖先；`PENDING_REGISTRY_PUSH` 不是部署 digest。三元组 `E + receipt path + receipt blob SHA-256`、候选父证据和预期 live inventory 同时进入确认 token、计划、HTTP 预检和最终结果，任一变化都会拒绝发压。
10. receipt 必须与当前部署做只读 live binding：以固定 `docker inspect` argv 校验本机 API、Scheduler、Asset API、Web、控制机 Blender Worker，以固定 `BatchMode=yes`、`StrictHostKeyChecking=yes` 的 SSH argv 校验 `lilithgames@10.3.34.12:22` 和 `gpucontrol@10.3.34.14:2222` 的 Blender Worker；七个容器的 `.Image` 必须逐项等于 receipt/候选证据的 `local_image_id`，三台 Worker 必须完全一致。另以固定 SSH argv 读取 3090-B Windows 实装 `Invoke-GPUControlSubstanceAgent.ps1` 的 SHA-256；Git blob SHA 与 Windows 实装字节 SHA 分开绑定，避免把 LF/CRLF 转换误判为漂移。HTTP 预检必须恰好看到 `asset-worker-3090-b-windows-01`、`-02`、`-03`、`-04` 四个在线实例且 `skill_version=substance-baker-2026.08.03-v6`。上述检查在发压前、Locust `test_stop` 和 wrapper 子进程退出后重复执行；任一运行中漂移均使本轮失败。容器名、组件、identity type、实测 image ID 与 Substance 身份写入 plan/preflight/summary，并由确认 token 绑定。结果 `manifest.json` 和 `summary.json` 必须保持 `external_anchor_status=PENDING_GIT_PUBLISH`；把最终结果 manifest 提交并推送到 GitHub 前，任何本机 checksums 都不得宣称正式接受。

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

`records.json` 必须逐任务保存服务端原始终态 JSON 和 artifact listing metadata；每件 artifact 必须记录 `kind`、`id`、`filename`、metadata size/SHA、响应头 `X-Artifact-SHA256`、body 实际 size/SHA。任何带 query/fragment 的下载 URL 或凭据形态字段均拒绝进入正式证据，API key 和管理员 token 永不落盘。

### 6.5 本轮待填结果

| 证据 | 状态 |
|---|---|
| 预检原始报告 | `PENDING_LOAD_PREFLIGHT` |
| plan-only 报告 | `LOCAL_PLAN_ONLY_COMPLETE`；不是正式执行授权或压测结果 |
| 用户启动授权 | `AUTHORIZED_BY_USER`；仍须补齐精确 change/window、备份与所有 fail-closed 门禁 |
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
- `dac30c039f692cf8274eaff5430ca7ebfd97b201` 是早期核心实现基线，不再是最终冻结版本；其后已加入 Worker 计数原子修复、Substance 终止证明/recovery fence 与 v6 身份门禁等后端语义修复。最终完整 commit 仍为 `PENDING_FINAL_COMMIT`。已记录的 `origin/main` 仍为 `56035975cd9ca4b0c904e34aca11d30b8779d2cd`，因此这些修复尚未推送。
- 源码中已存在生产优先全局准入、精确 artifact 契约、Linux Worker 长上传续租与进程代际绑定、Windows Agent 长 SHA/上传续租、统一容量/ETA、锁竞争快速失败及五镜像发布校验实现和对应自动化用例。
- 当前最终修复工作树的唯一用例汇总为 `471 passed, 0 skipped`：禁网核心 unit/integration（不重复 load 专项）为 `379 passed, 11 skipped`，随后在一次性 PostgreSQL 17.5、loopback 且数据库名为 `gpu_control_test_*` 的 tmpfs 隔离库上把该 11 条锁/并发用例全部跑通，结果 `11 passed`；负载/发布证据专项在注入项目锁定的 Locust `2.37.14` 后为 `81 passed`。临时 PostgreSQL 容器验证后已停止并自动移除。
- 上述运行是 source commit `S` 创建前的完整工作树证据；没有沿用旧 JUnit 路径或旧 SHA。最终 `S` 创建后必须重新生成并归档报告，不能用本段摘要替代原始证据。
- Ruff 全仓、Mypy `36 source files`、compileall、`git diff --check`、SQLite 从 `0001` 到 `0012` 的完整迁移和控制面/GPU 节点两套 Compose 解析均通过。
- Web 当前候选工作树结果：测试 `16/16`，ESLint、Prettier、`vue-tsc` 和 Vite build 均通过；Vite 仅报告既有大 chunk advisory，不是构建失败。构建时发现的 `brace-expansion` high advisory 只存在于 dev dependency，已用 lockfile-only 补丁升级到修复版本；重新 `npm ci` 后完整 npm audit 和 `--omit=dev` audit 均为 0 vulnerability。
- 早期 `P0=0 / P1=0` 结论曾被后续审计推翻：旧 Substance Agent 会吞掉 Kill/WaitForExit 失败，普通 retryable fail 可错误释放物理 GPU fence。当前工作树已用 v6 专用错误、服务端强制 `RECOVERY_REQUIRED`、heartbeat 计数矛盾门禁和两阶段恢复修复；定向契约/行为测试已通过，最终独立边界与发布 provenance 增量审计结论为 `P0=0 / P1=0`。
- 已知 P2/运维约束：时间轴仍依赖客户端提交的 `started_at`；1.5.8/1.2.4 到 1.5.9/1.2.5 存在上述短领取冻结窗；ETA 仍是队列近似值；Asset 平面暂无自身完整的 system/daily 队列 cap，所以正式 120 VU 必须继续使用 bounded lifecycle、生产让路 watchdog 和独占测试 tenant；三组 PostgreSQL 并发文件共用隔离测试库，禁止并行执行。工作树门禁已通过，当前状态为 `PASSED_WORKTREE_PENDING_FINAL_COMMIT`。
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
| 最终工作树 Python unit/integration/load | `PASSED_471_TESTS_0_SKIPPED` |
| Ruff / Mypy 36 files / compileall / diff-check | `PASSED` |
| Web 16 tests / ESLint / Prettier / vue-tsc / Vite build | `PASSED_WITH_EXISTING_CHUNK_ADVISORY` |
| 两套 Compose config 解析 | `PASSED` |
| 发布打包器/身份校验器专项测试 | `PASSED_IN_81_LOAD_RELEASE_TESTS` |
| 无越界 workflow/Skill 变更审计 | `PASSED_P0_0_P1_0` |
| 上述结果绑定的最终 source commit `S` | `PENDING_FINAL_COMMIT_AND_REPLAY_EVIDENCE` |

## 8. 安全发布、灰度与回滚

### 8.1 发布前证据

| 字段 | 必填值 |
|---|---|
| 最早本地代码候选 Git commit | `b410a6a7994cdd06335a106ad5257eafb6378fdf`；已被后续修复取代，`SUPERSEDED` |
| 本轮指标稳定性修复 Git commit | `cdd89fbb4c77f7e403a0b3af0bb93c09e083beac`；`PUSHED_GITHUB_MAIN` |
| 最终已部署源码 commit `S` | `e81c4cb45a360b83561c154867da7ae86cbbbc70` |
| 完整仓库/历史推送到指定 GitHub `origin/main` 的精确授权 | `AUTHORIZED_BY_OWNER_2026-08-03` |
| GitHub `main` 包含指标修复 commit | `VERIFIED_cdd89fbb4c77f7e403a0b3af0bb93c09e083beac` |
| API local image ID / SBOM | `sha256:2984e2151a26e57953ba48816a4162214d73ea8ba7733ed91b31de78a59c5880` / `BUILDKIT_SBOM_ATTESTED` |
| Scheduler local image ID / SBOM | `sha256:d0c8b718f1a108297b6180486494d283997c76211b29bc5e0766a68ae53174c5` / `BUILDKIT_SBOM_ATTESTED` |
| Asset API local image ID / SBOM | `sha256:dcaeb9aa320b51eb742428c736168ca0cab660ca9ead79c348fddd6caaef8bff` / `BUILDKIT_SBOM_ATTESTED` |
| Web local image ID / SBOM | `sha256:8fb78526c69d760ef74e244f73229949c02908ff4c884915c36ef2d3315f78f5` / `BUILDKIT_SBOM_ATTESTED` |
| Worker local image ID | `sha256:877080c375d12dc3e20fdfe153e8dbfc5e5cff23716a74b6f9a8f8560aecd619`，三节点一致 |
| OCI 版本/源码标签 | 五镜像均验证为目标版本并绑定 `e81c4cb45a360b83561c154867da7ae86cbbbc70` |
| release archive 与 Git LFS 证据 | `PENDING_RELEASE_ARCHIVE_LFS` |
| 小型候选备份 | `/srv/gpu-control/backups/20260803T130246Z-small`；清单 SHA `f3ed94fc...a6220ca`；`LOCAL_SMALL_BACKUP_ONLY` |
| 全量备份路径、SHA、恢复验证 | `PENDING_BACKUP_AND_RESTORE_VERIFY` |
| 回滚版本和镜像 digest | `PENDING_ROLLBACK_IDENTITY` |

### 8.2 灰度顺序

1. 冻结发布身份，完成最终门禁、备份及恢复抽检；预拉取全部镜像，但不 recreate 服务。
2. 在调用方/网关冻结新的 Asset 提交，保存三节点原始 mode；将三节点设为 operator-owned `DRAINING`，确认 GPU jobs/batches、Asset jobs、lease、Comfy 队列、Windows Baker/native process 全部归零。
3. 保持旧 Asset API 与 intake freeze，依次停止并替换四个 Windows scheduled Agent 为精确 v6 脚本；每槽核对脚本 SHA、单实例 mutex、host probe `HEALTHY/0` 和 v6 heartbeat。旧 API 此时应返回 `DRAINING`，不得尝试绕过版本门禁，也不得触碰 ComfyUI。
4. 四槽 v6 身份全部出现后，仅替换 Asset API 为 `1.5.9`，检查健康、完整 source SHA、数据库 head、日志和零 lease；等待四槽 fresh heartbeat 全部变为 `ONLINE`。同时旧 Linux Worker `1.2.4` 必须只得到 `200 / job=None` 并安全停止领取。
5. 三节点保持 `DRAINING`，按 `control-4090 → worker-3090-a → worker-3090-b` 逐台只替换 Linux Worker 为 `1.2.5`；每步验证 Worker revision、fresh heartbeat、唯一 `agent_instance_id`、节点绑定、零 current job/lease。Worker 没有 Compose healthcheck，不能只看 `compose ps`。
6. 三个 Worker 全部兼容后，只恢复一个节点的原 schedulable mode，跑受控 UV/拓扑 CPU canary；随后恢复 3090-B 的原 mode并跑单笔 PBR v6 canary，核对终态、正式产物、SHA、租约归零、无 recovery label 和 ComfyUI 身份连续。
7. 依次只替换 API、Web、Scheduler；Scheduler 最后。每一步检查健康、版本、完整 SHA、日志、零任务和零异常锁等待。
8. 所有 Compose 更新必须指定单个目标 service，并使用 `--no-deps --no-build --pull never --force-recreate`；禁止调用会全栈 reconcile 的部署脚本。发布前后保存三台 ComfyUI 的 container ID、StartedAt、RestartCount、队列和 workflow SHA，证明没有停止、重启、`/free` 或清缓存。
9. 六 API 各跑一个真实 canary，逐项校验终态、request/trace ID、时间字段和精确 artifact 三重 SHA；随后完成浏览器正式 QA。
10. 只有上述通过、解除 intake freeze 且生产窗口无异常，才允许进入受控综合压测。

### 8.2.1 2026-08-04 实际滚动记录

- 排空门禁：三台节点 `DRAINING/current_jobs=0`，父批次、GPU/Asset 子任务、活动租约均为 0；三台 ComfyUI 的 running/pending 队列均为 0。
- Windows Baker：先安装四槽 v6；旧 Asset API 下四槽按设计进入 `DRAINING`，切换 Asset API 1.5.9 后全部恢复 `ONLINE`，身份均为 `substance-baker-2026.08.03-v6`，宿主进程探针均为 `HEALTHY/0`。实装 Agent 脚本 SHA-256 为 `889af86a61f62bb769f06728b8f466555bac56c2190ae97626b17fe09f05761d`。
- Linux Worker：按 `control-4090 → worker-3090-a → worker-3090-b` 逐台替换为 1.2.5；三台运行 image ID 均为 `sha256:877080c375d12dc3e20fdfe153e8dbfc5e5cff23716a74b6f9a8f8560aecd619`，每台均产生新的唯一 `agent_instance_id` 并恢复新鲜心跳。
- 控制面：按 `API → Web → Scheduler` 单服务替换，所有 Compose 命令均使用 `--no-deps --no-build --pull never --force-recreate`；API、Asset API、Web、Scheduler 均为 `healthy`、RestartCount=0。
- Node Agent 遥测：三台逐台排空，仅更新并重启 `gpu-node-agent.service`；实装模块 SHA-256 为 `f504302990bcc93c6fcf17ff374db79d60c0dcfbdbfa5adfbc2f3931569830be`。`/admin/nodes` 已实际返回三台 `gpu_temperature_c` 与 `gpu_power_w`，不支持或离线时 Web 显示 `—`。
- 缓存连续性：未停止、重启或重建任何 ComfyUI 容器，未调用 `/free`，未清理模型缓存；3090-A/B ComfyUI RestartCount 均保持 0。
- 接单恢复：`control-4090=OVERFLOW`、`worker-3090-a=ACTIVE`、`worker-3090-b=ACTIVE`，三台 `ONLINE`。本机与两台远端 `.env` 的持久版本标签已同步为 1.5.9/1.2.5，避免后续 Compose 操作回退旧镜像。
- 兼容性：外部六 API 路径和请求/响应合同未改；源码测试、Web 构建和滚动后的 HTTPS/API 健康检查通过。真实六 API canary 与正式综合压测仍未完成，不得把本记录升级为生产验收。

当前发布结果：`DEPLOYED_NOT_ACCEPTED`。

### 8.3 回滚

- 保持 intake freeze 和三节点 operator-owned `DRAINING`，按 `Scheduler → Web → API → Linux Worker 逐台回 1.2.4 → Asset API 最后回 1.5.8` 反向回滚。Worker `1.2.4` 在 Asset API `1.5.9` 下只会 fail closed；Asset API 最后回旧后才恢复旧协议领取。
- 本候选未增加 migration，仍需在操作前确认数据库 head 为 `20260803_0012`；不得随意回滚数据库。
- 不回滚、修改或重写外部 ImageClip/ModelView/UV/Retopo pipeline。
- 若 Windows v6 候选异常，保持 PBR intake freeze 并隔离/drain 3090-B Substance 槽位，保留宿主进程、脚本 SHA、heartbeat 与 recovery label 证据。禁止为了恢复接单而放宽为 v5 身份，也禁止恢复会驱逐 ComfyUI 缓存的旧行为。
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
CONTROL_1_5_9_WORKER_1_2_5 = DEPLOYED_NOT_ACCEPTED
DEPLOYED_SOURCE = e81c4cb45a360b83561c154867da7ae86cbbbc70
FINAL_SOURCE_GATES = PASSED_PREDEPLOY_GATES_PLUS_TELEMETRY_REGRESSION
SOURCE_PUSH_AUTHORIZATION = AUTHORIZED_BY_OWNER_2026-08-03
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
