# 动画管家 × GPU Control 1.5.5 速度稳定性联合测试前回执

> 状态：`SOURCE_SELF_TESTED_AND_PUSHED / IMAGES_NOT_BUILT / INPUT_TRANSFER_BLOCKED / LOAD_PLAN_READY_NOT_EXECUTED / PRODUCTION_HOLD`
>
> 日期：`2026-07-30`
>
> 候选版本：GPU Control `1.5.5`
>
> 最终候选源码提交：`59b35d319d84715489dedbd22d81bc56719f57c8`
>
> 生产事实：GPU Control `1.5.4`；数据库 `20260729_0010`；本轮未部署、未迁移、未重启、未运行真实压测

本文件是 GPU Control 对动画管家第三轮输入
`07_GPU_CONTROL_1_5_5_CANDIDATE_REVIEW_AND_JOINT_INPUT.md` 的事实回执，也是进入双方真实联合测试前的
单一交接入口。它把本轮速度/稳定性代码、WebUI、六 API 压测工具、冻结素材校验和发布打包的状态一次
说明清楚；其中“源码候选已准备”不等于“镜像已构建”“生产已升级”或“联合验收已通过”。

## 1. 本轮结论

GPU Control 自有边界内的 1.5.5 速度与稳定性候选已形成，最终源码提交为
`59b35d319d84715489dedbd22d81bc56719f57c8`，最终离线验证汇总为：Python 188 passed / 0 failed；Ruff、mypy（34 个源文件）、compileall、Control/Node Compose 均通过；PostgreSQL 17.5 隔离 SQL 通过；Web Vitest 8/8、mocked Playwright 20/20、lint/format/typecheck/build 通过；plan-only 0 HTTP。本轮完成或准备的内容包括：

1. 批次状态和装配路径移除逐帧 artifact N+1 查询，调度投喂改为有全局、租户、批次和单轮上限的
   公平轮转；
2. 单图 API 与批次 feeder 使用统一的全局 admission 锁，再按稳定顺序获取租户锁，并在锁内重新计数，
   关闭跨租户并发穿透总队列上限的窗口；
3. materialize 的数据库提交结果不确定时先从新会话按精确 job ID 对账；只有确认服务端没有提交记录时
   才清理提升后的输入目录，查询失败或任一记录存在时保留现场并记录告警；
4. 节点心跳增加可选 GPU 型号，型号探测失败不会阻断 UUID、IP、pipeline 身份和健康心跳；父批次增加
   可核验的逐节点性能字段和完整性标志；
5. WebUI 源码候选已经完成任务/API/功能/工作流分类、真实阶段时间、性能分析和可解释调度界面；
6. 六 API、100+ VU 的综合压测工具和安全门禁已经准备，但**没有发送任何真实压测请求**；
7. 动画管家冻结素材的离线校验器已经准备并通过独立单元测试，但第三轮文档给出的 index SHA 非法，
   且真实 index、素材目录和生成器没有传输到 GPU Control，当前不能导入或声称 SHA 对齐；
8. 四组件可复现打包器的代码路径已修正并通过离线测试，但本轮没有构建镜像、生成正式 SBOM、推送
   registry、上传 Git LFS 或形成可部署候选。

当前精确决策仍是：**源码候选可供审阅；生产保持 1.5.4；等待动画管家修正并传输冻结输入，同时等待
用户明确下达真实压测窗口命令。**

## 2. 输入、历史回执与源码谱系

以下 SHA 属于不同对象，必须分别保存，不能互相覆盖：

| 对象 | SHA-256 / Git SHA | 事实 |
|---|---|---|
| 动画管家第三轮输入 `07_GPU_CONTROL_1_5_5_CANDIDATE_REVIEW_AND_JOINT_INPUT.md` | `f0a46e701022185397d5c1574f90e58cd33ccde785f8ba63c75e15f95d2f2da9` | GPU Control 本轮实际读取的输入文件 |
| 第三轮输入中声明其收到的旧版 65 号回执 | `40938b4884ba788f509fd0a4942ee10630962825a861bcad744bb85bbe383047` | 动画管家文档中的历史声明；不改写为仓库当前文件 |
| GPU Control 仓库当前 65 号历史回执 | `d2b5d3c2c908447f3beeee23b0d47f349da90a7d181589ef2fe9a2f907d05bb8` | 仓库归档文件的实际 SHA-256；65 号文件保持历史不变 |
| WebUI/V4.1 首个已推送候选源码 | `e726b93c45b8dbdffc9b013024aff6703967d866` | 已包含 WebUI 重构和前一轮 V4.1 对齐，不代表本轮最终速度稳定性提交 |
| 本轮最终 1.5.5 源码 | `59b35d319d84715489dedbd22d81bc56719f57c8` | 本文件交付的最终候选；必须以远端 GitHub 可解析结果为准 |

第三轮输入的状态是 `GPU CODE ALIGNED_NOT_TESTED / ASSETCLAW INPUTS FROZEN / PRODUCTION HOLD / JOINT ACCEPTANCE PENDING`。
本回执只关闭 GPU Control 能独立关闭的源码和离线工具项，不把动画管家的 `456 passed, 8 warnings` 或
`23 passed, 3 warnings` 合并进 GPU Control 的测试计数。

## 3. 冻结输入阻断：`PENDING_INPUT_CORRECTION_AND_TRANSFER`

第三轮输入把 `bundle_index.json` 的文件 SHA 写为：

```text
44e908d53eba884caaeeefa97f88115354739f444508755767b1ad05320d21987
```

该字符串有 **65 个十六进制字符**，不可能是 SHA-256。GPU Control 也没有在已交付位置收到以下实际
文件，因此不能用文档中的表格代替文件验证：

- `bundle_index.json`；
- `frozen_inputs/v4_1-20260730-r1/` 下 B1、B6、B30、B64、B97、B300 六个完整目录；
- `scripts/prepare_gpu_control_v4_1_benchmarks.py`。

GPU Control 已增加只读离线校验器
[`scripts/validate_assetclaw_v4_1_benchmarks.py`](../scripts/validate_assetclaw_v4_1_benchmarks.py) 和
[`tests/unit/test_assetclaw_benchmark_verifier.py`](../tests/unit/test_assetclaw_benchmark_verifier.py)。校验器
要求通过安全渠道取得可信的 **64 位** index SHA，并检查固定 session、六个 bundle、每目录恰好三个
文件、canonical JSON、ZIP/manifest/逐帧 size 与 SHA、PNG 尺寸和像素数，以及路径穿越、重复成员、
额外文件、符号链接、特殊文件、加密和压缩方式等边界；报告写入独立新目录且拒绝覆盖。其独立测试为
`21 passed / 0 failed`，非法 65 位 SHA 会在任何素材处理前以 `SHA256_INVALID` 退出。

在动画管家补交正确的 64 位 SHA 和实际文件前：

```yaml
frozen_inputs:
  acceptance_session_id: v4_1-20260730-r1
  status: PENDING_INPUT_CORRECTION_AND_TRANSFER
  bundle_index_file_sha256_match: false
  all_bundle_archive_sha256_match: false
  all_manifest_and_frame_hashes_match: false
  real_gpu_batch_created: false
```

以上 `false` 表示证据尚未形成，不表示素材内容已被判断为损坏。

## 4. 速度与稳定性候选改动

### 4.1 批次查询和装配热路径

- `latest_output_artifacts()` 使用窗口函数一次取回每个 job 最新的 output artifact；父状态同步不再为
  每个成功帧单独查询 artifact。
- 批次装配一次读取 child jobs 和最新 artifacts，再按持久 ordinal 组装；不改变输出顺序、Alpha、
  SHA 或最终 ZIP 合同。
- 查询优化只发生在 GPU Control 数据访问层，没有修改 ImageClip/ModelViewCreator 工作流、模型、
  prompt、推理参数、图拓扑或输出节点。

### 4.2 公平、有界 feeder

- 单轮可物化数量同时受系统剩余队列、租户剩余队列、父批次 feeder window 和单 tick 上限约束；
- 在候选批次之间逐帧 round-robin，避免一个大批次占满当轮预算；
- 只锁定本轮实际选择的 batch/item，锁内重新读取和校验状态，不再把所有待处理批次或远超预算的帧
  一次性锁住；
- 物化和数据库写入保持明确边界，避免在调度事件循环中无界同步展开大批量工作。

这些改变减少数据库往返和锁竞争，但不会扩大任务并发、跳过 admission、改变工作流身份或用预览产物
替代批准的最终产物。

### 4.3 全局 admission 与锁顺序

API 单任务创建和 Scheduler 批次投喂使用同一全局 transaction advisory lock，再按排序后的 tenant ID
获取租户锁；在锁全部持有后重新统计系统和租户队列，再决定可接收数量。固定顺序为：

```text
global admission lock → sorted tenant lock(s) → recount → create/materialize
```

这关闭了不同租户同时提交时各自看到旧总数、一起越过 `system_max_queued` 的竞争窗口，也避免 API 与
batch feeder 采用相反锁序。任何被限流或容量拒绝的任务仍按既有错误合同返回，不偷偷丢弃或改写。

### 4.4 commit-unknown 现场保护

内部数据库 `COMMIT` 抛错不能证明提交失败。候选实现记录本轮准备提交的精确 child job ID 和已提升的
输入根目录；发生 commit-unknown 时使用新会话逐个核对：

- 全部 job ID 确认不存在：允许清理本轮新提升目录；
- 任一 job ID 已存在：保留目录，交给持久任务继续引用；
- 对账查询本身失败或结果不确定：保留目录并记录告警，禁止冒险删除。

该规则优先保护已经被服务端提交、但客户端未收到成功回执的任务，避免形成数据库记录指向已删除输入
的不可恢复故障。

### 4.5 节点和父批次性能证据

Node Agent 可以通过 `nvidia-smi --query-gpu=name` 上报经过校验的 `gpu_model`。探测失败时只省略该
可选字段并定期重试，不影响节点 UUID/IP/pipeline 身份和基础心跳。父批次 `performance.nodes[]` 按
持久 attempt/item 证据返回：

- `node_id`、`gpu_model`、worker/source；
- `frames_assigned`、`frames_final_assignment`、成功/失败帧；
- upload/prompt attempt；
- `gpu_service_ms`、单帧 P50/P95、节点开始/结束时间；
- `reassignments_in/out`、当前配置的 `max_concurrent_prompts`；
- 输入像素、GPU 样本完整性和可计算的 throughput。

成功帧在最终节点缺少完整 GPU 时间样本时，父级和节点级完整性均为 false，吞吐、MPix/s 和拖尾返回
`null`，不以客户端估算或常量填充。没有批次绑定的持久 Scheduler 重启证据时，`scheduler_restarts`
仍返回 `null`。

## 5. 拖尾口径的合同澄清

GPU Control 按联合验收合同 03 的**分数公式**实现：

```text
straggler_ratio =
  (latest_node_finished_at - median(participating_node_finished_at))
  / (execution_finished_at - started_at)
```

只有父批次终态、至少两个参与节点、所有参与节点完成时间权威且落在正的父 GPU wall 内时才返回数值，
否则返回 `null`。因此合同目标“拖尾不超过 15%”应比较 `straggler_ratio <= 0.15`。

动画管家 06 号文档中出现的 `1.08` 和 `<= 1.15` 是“完成时间倍率”口径，与上述分数口径相差 1：

```text
finish_multiplier = 1 + straggler_ratio
```

双方联合报告应优先保留字段的正式分数语义；如动画管家继续显示倍率，应使用新字段或在展示层明确做
`1 + ratio`，不能把 `1.15` 直接与 `straggler_ratio` 比较。

## 6. 工作流身份与接口边界

本轮继续使用动画管家批准的新任务身份，不修改外部业务管线：

```yaml
workflow_key: imageclip-rgba
workflow_version: 2026.07.30-691770c-r1
pipeline_commit: 691770cd6a59fd7c51391456fe900dc57a313233
pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
output_node: "SaveImage #25"
assembling_field: assembling_at
accepted_legacy_alias: assembling_started_at
```

create 固化父批次身份，父 GET、manifest 和最终 artifact 必须返回同一身份；节点标签不一致时 fail
closed。历史任务保留原身份，不回填为新版本。父成功前仍禁止父/子 artifact 下载；batch-owned child
取消仍返回 `409 BATCH_CHILD_CANCEL_FORBIDDEN`；公共父取消继续要求认证、稳定幂等键和持久
operation/audit。

## 7. WebUI 候选状态

WebUI 重构已在 Git 提交 `e726b93c45b8dbdffc9b013024aff6703967d866` 中形成源码候选：

- 任务中心可按业务功能、公共 API、工作流和状态分类，并支持搜索；
- 列表展示开始、结束、真实排队、纯 GPU wall、总耗时和九阶段时间线；
- 分析页提供阶段占比、瓶颈诊断、逐节点性能和证据完整性，不把缺失值显示为 0；
- 调度页把 GPU 与 CPU Asset 平面、节点槽位、分配原则和当前限制写成可理解的运行事实；
- 字号、密度、响应式布局和详情层级已调整，保留已有 API 和任务分配合同。

该候选已经完成 Vitest `8/8`、mocked Playwright 桌面/移动 `20/20`，以及 lint、format check、Vue
typecheck 和 production build。它是 GitHub 源码证据，**生产 Web 容器仍是 1.5.4，本轮没有热更新或
替换生产容器**。

## 8. 六 API 压测工具：已准备、未执行

[`67_2026-07-30_SIX_API_MIXED_LOAD_TEST_RUNBOOK.md`](67_2026-07-30_SIX_API_MIXED_LOAD_TEST_RUNBOOK.md)
和配套代码覆盖：

1. ImageClip batch；
2. ModelView roughness；
3. UV process；
4. retopology audit；
5. retopology process；
6. Substance bake。

示例按 `1 → 10 → 25 → 50 → 100 → 120` VU 分阶段，约 70:30 的 GPU-consuming/CPU 规划权重；正式
执行前必须重新确认权重。默认入口只生成 plan，wrapper 与 Locust 文件各自重复 fail-closed 门禁；生产
执行还要求明确开关、变更单、窗口、完整备份、精确确认令牌、隔离测试身份、外部真实素材、批准
workflow SHA、三 GPU/Asset Worker 健康和活动队列为 0。session teardown 只处理本 session 创建且
仍未终态的任务。

本轮只做了离线计划和工具测试，**没有启动 Locust、没有向任何 API 发送压力请求、没有创建或取消生产
任务，也没有把三台设备拉满**。只有用户后续明确下达压测命令并且所有运行门禁同时通过，才允许执行。

## 9. 打包、镜像和发布状态

[`68_2026-07-30_CONTROL_PLANE_1_5_5_REPRODUCIBLE_PACKAGING.md`](68_2026-07-30_CONTROL_PLANE_1_5_5_REPRODUCIBLE_PACKAGING.md)
中的打包器已把 OCI attested solve 与 Docker-loadable solve 分开，并要求两者 image config digest 一致；
错误输出有长度上限和常见凭据遮蔽。代码和单元测试就绪不代表产物已经形成。

| 项目 | 当前状态 |
|---|---|
| 1.5.5 API/Scheduler/Asset API/Web 镜像构建 | `NOT_EXECUTED` |
| OCI manifest / registry manifest digest | `NOT_AVAILABLE` |
| 固定 SBOM generator 与正式 SBOM/provenance | `PENDING` |
| Registry push | `NOT_EXECUTED` |
| Git LFS 分片与远端对象核验 | `NOT_EXECUTED` |
| 1.5.4 rollback 不可变镜像和实测恢复时间 | `PENDING_EVIDENCE` |
| 生产 Compose / API / Scheduler / Web 替换 | `NOT_EXECUTED` |
| 生产数据库 `0010 → 0011` | `NOT_EXECUTED` |

在上述证据形成前，本候选不能填写为 `SELF_TESTED_BUILT_NOT_DEPLOYED`，更不能标为已发布或已验收。

## 10. 验证结果与证据边界

本轮最终 GPU Control 验证汇总：Python 188 passed / 0 failed；Ruff、mypy（34 个源文件）、compileall、Control/Node Compose 均通过；PostgreSQL 17.5 隔离 SQL 通过；Web Vitest 8/8、mocked Playwright 20/20、lint/format/typecheck/build 通过；plan-only 0 HTTP。

已独立确认的子项：

- 冻结素材离线校验器：`21 passed / 0 failed`；
- Web Vitest：`8 passed / 0 failed`；
- mocked Playwright：`20 passed / 0 failed`；
- Web lint、format、Vue typecheck、production build：通过；
- 六 API 压测 plan：只生成计划，报告阻断项，`0` 个 HTTP 压测请求；
- Python 全量 `188 passed / 0 failed`；Ruff、mypy（34 个源文件）、compileall、Control/Node
  Compose render 和隔离 PostgreSQL 17.5 SQL 均通过。

这里不引用生产健康截图替代原始报告，不把动画管家侧测试计入 GPU Control，也不把 Fake/SQLite/临时
PostgreSQL 测试写成真实三 GPU 联合测试。

## 11. 当前交接状态

```yaml
gpu_control_candidate:
  version: "1.5.5"
  source_commit: "59b35d319d84715489dedbd22d81bc56719f57c8"
  source_status: SOURCE_SELF_TESTED_AND_PUSHED
  final_test_summary: "PYTHON_188_PASS / STATIC_PASS / POSTGRESQL_PASS / WEB_8_PLUS_20_PASS / LOAD_NOT_EXECUTED"
  webui_candidate_commit: e726b93c45b8dbdffc9b013024aff6703967d866
  images: IMAGES_NOT_BUILT
  registry: NOT_PUSHED
  git_lfs_release_archive: NOT_UPLOADED
  production_deployment: NOT_EXECUTED
  production_database_migration: NOT_EXECUTED
  production_version: "1.5.4"
  production_database_revision: "20260729_0010"
  load_tooling: READY
  real_load_test: NOT_EXECUTED
  joint_fault_injection: NOT_EXECUTED
  joint_benchmark: BLOCKED_BY_INPUT_TRANSFER_AND_EXPLICIT_WINDOW
  frozen_inputs: PENDING_INPUT_CORRECTION_AND_TRANSFER
  production_rollout: HOLD
```

## 12. 动画管家需要补回的最小内容

请动画管家下一次只补交以下确定信息，避免继续用文字声明替代文件：

1. `bundle_index.json` 的正确 64 位 SHA-256；
2. 实际 `bundle_index.json`；
3. `frozen_inputs/v4_1-20260730-r1/` 六个 bundle 的完整目录；
4. 实际 `prepare_gpu_control_v4_1_benchmarks.py` 及其 SHA-256；
5. 确认拖尾以 `straggler_ratio <= 0.15` 为正式字段口径，或明确倍率仅为展示派生值；
6. 通过安全渠道交换隔离 tenant/API key/CA，不把密钥写进 Markdown 或 Git；
7. 与 GPU Control 共同确认真实压测和故障注入窗口。

收到并离线核验这些输入后，下一阶段才是 B1/B6/B30/B64/B97/B300、三节点 B97、`3 × B97` 和故障
注入。没有用户明确测试命令时，GPU Control 保持待命，不自行执行真实负载、重启、节点离线或生产迁移。
