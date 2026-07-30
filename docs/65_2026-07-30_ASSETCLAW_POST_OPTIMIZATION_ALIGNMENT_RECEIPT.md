# 动画管家优化后 GPU Control 第二轮对齐回执（发布前）

> 状态：`CODE ALIGNED IN WORKTREE / ARCHIVED SELF-TEST PASS / SOURCE COMMIT PENDING / PENDING_BUILD / JOINT ACCEPTANCE PENDING / RUNTIME UNCHANGED`
> 对齐日期：`2026-07-30`
> 输入文档：`06_POST_OPTIMIZATION_GPU_CONTROL_ALIGNMENT.md`
> 输入 SHA-256：`9b04040655a7ad7d3090e848dd86551547fbee8e45dd9074fa0374e951ca9e26`
> 仓库：`https://github.com/kiana0512/gpu_control.git`
> 审阅起点 HEAD：`63deec8f57dede18ee64703ccc2b2726032e2f07`；该提交不包含本轮尚未提交的
> `1.5.5` 候选变更，因此不得把它写成候选 source commit。
> 本轮只修改 GPU Control 自有控制面、调度、传输、可观测性和发布证据链；没有修改或重启生产
> 服务，没有创建、取消或改派生产任务，也没有修改 ImageClip/ModelViewCreator 的工作流、模型、
> 参数、提示词、图拓扑或输出语义。

## 1. 结论

GPU Control 已在当前工作树中补齐 G-P0-01～G-P0-07 的代码路径，并完成隔离自测。准确状态是
“代码与自测已对齐、源码提交和发布证据待完成”，不是“已发布”或“已验收”：

- 父子取消边界、父成功前产物门禁、持久取消 operation、父批次身份快照、混合兼容节点调度和
  prompt 提交崩溃恢复已形成对应实现；候选版本元数据统一为 `1.5.5`。
- 最终工作树的 Python 全量回归为 `144 passed / 0 failed`；Web Vitest 为 `8/8`，桌面/移动 mocked
  Playwright 为 `20/20`；Ruff、mypy、compileall、两套 Compose render 以及临时 PostgreSQL
  `0010 → 0011 → 0010 → 0011` 均通过。原始 JUnit/报告和 SHA-256 见 §6.1。
- 候选源码仍是未提交工作树；Docker 镜像、OCI/registry digest、SBOM、Git/LFS 对象和不可变
  回滚镜像均未形成，统一标记为 `PENDING_BUILD`。
- B1/B6/B30/B64/B97/B300、并发 `3 × B97`、真实节点离线/进程崩溃等联合测试尚未开始；生产
  10% → 50% → 100% 灰度和连续 7 天观察也保持 `PENDING`。

因此本文件可以作为动画管家与 GPU Control 的第二轮代码和隔离自测对账单，但必须等候选 source
commit、不可变发布证据和联合验收依次补齐后，才能升级为 `PRODUCTION ACCEPTED`。

## 2. 批准的 ImageClip 身份

GPU Control 接受动画管家第二轮文档冻结的以下身份，作为**新生产任务**的唯一批准基线：

```yaml
workflow_key: imageclip-rgba
workflow_version: 2026.07.30-691770c-r1
imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
pipeline_commit: 691770cd6a59fd7c51391456fe900dc57a313233
pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
output_node: "SaveImage #25"
```

接口字段使用 `pipeline_commit`；它与动画管家文档中的 `imageclip_commit` 是同一值。create 时从已启用
工作流版本生成不可变父批次快照，之后父 GET、分页 manifest、最终 artifact 元数据和 ZIP manifest
返回同一组值。节点当前标签与快照不一致时 fail closed。历史任务保留其原身份，不回填、不伪造为
`691770c`。

本次接受该身份不授权 GPU Control 改动 ImageClip 仓库内容；GPU Control 只校验并调度精确批准版本。

## 3. G-P0-01～07 回执

状态定义：

- `CODE_PRESENT`：当前未提交工作树中已看到实现与测试入口；
- `PASS_CURRENT_WORKTREE`：本轮最终工作树的原始测试报告、退出码和 SHA-256 已归档；
- `PENDING_BUILD`：必须等干净 source commit、镜像构建/推送和证据校验后才能关闭；
- `PENDING_JOINT_FAULT_INJECTION`：需要隔离环境真实进程、节点或传输故障，单元测试不能替代。

| ID | 当前代码状态 | 实现事实 | 稳定测试 ID | 当前测试证据 |
|---|---|---|---|---|
| G-P0-01 | `CODE_PRESENT` | 公共/管理 job cancel 对 batch-owned child 返回 `409 BATCH_CHILD_CANCEL_FORBIDDEN`；管理节点 interrupt 也不能绕过父批次合同，并写拒绝 audit | `TC-GP0-01-CHILD-CANCEL-REJECT` | `PASS_CURRENT_WORKTREE`；`backend-final.junit.xml` 包含 `test_batch_cancel_operation_child_guards_and_audit` |
| G-P0-02 | `CODE_PRESENT` | 父批次不是 `SUCCEEDED` 时，父 artifact、child artifact 列表和 child artifact 下载均返回 `409 ARTIFACT_NOT_READY`；Range 下载只对已发布结果开放 | `TC-GP0-02-ARTIFACT-BARRIER` | `PASS_CURRENT_WORKTREE`；同一集成测试覆盖 `QUEUED/RUNNING/ASSEMBLING`；真实断连故障注入仍 `PENDING` |
| G-P0-03 | `CODE_PRESENT` | 公共父取消要求显式 API Key 和稳定幂等键；POST 受理回执顶层为 `CANCEL_REQUESTED`，持久 GET 在途状态为 `CANCELLING`；operation、principal、source/IP、reason、request ID、计数与 audit 持久化；重放返回同一 operation；没有合法 operation 时调度器不得收敛出合法 `CANCELLED` | `TC-GP0-03-CANCEL-AUTH-DURABLE-IDEMPOTENT` | `PASS_CURRENT_WORKTREE`；全量 JUnit 包含 child guard、admin/public ack、非法 cancel 与持久审计回归 |
| G-P0-04 | `PENDING_BUILD` | 包、API/Scheduler/Asset API/Web 候选版本已统一到 `1.5.5`；Dockerfile/Compose 已增加 build revision、OCI labels、SBOM/provenance 配置和严格发布身份校验器 | `TC-GP0-04-RELEASE-IDENTITY` | 源码级 release/identity 测试已 `PASS_CURRENT_WORKTREE`；source commit、四镜像 registry digest、SBOM、LFS OID 和运行版本输出仍为 `PENDING_BUILD` |
| G-P0-05 | `CODE_PRESENT` | 数据模型/迁移新增父批次 `pipeline_commit`、`pipeline_sha256`、`output_node` 和阶段时间；create 固化快照，父 GET、分页 manifest、artifact/归档 manifest 返回同一身份 | `TC-GP0-05-IDENTITY-SNAPSHOT` | `PASS_CURRENT_WORKTREE`；全量 JUnit 覆盖身份快照/归档，临时 PostgreSQL 迁移报告验证字段和取消表 |
| G-P0-06 | `CODE_PRESENT` | Scheduler 对全部健康候选节点按顺序尝试领取兼容任务；首个节点不兼容时继续尝试后续节点，同时保留 primary-before-overflow 和 fail-closed 标签校验 | `TC-GP0-06-MIXED-NODE-SCHEDULING` | `PASS_CURRENT_WORKTREE`；JUnit 包含 `test_scheduler_skips_incompatible_node_and_claims_on_compatible_fallback` |
| G-P0-07 | `CODE_PRESENT` | 每个 job attempt 使用确定性 client ID；提交意图和 prompt attempt 先持久化；恢复只从 ComfyUI queue/history 对账，恰好一个匹配时收养，零个或多个匹配均 fail closed，不再次 POST | `TC-GP0-07-PROMPT-CRASH-WINDOW` | `PASS_CURRENT_WORKTREE`；JUnit 包含接受后崩溃、DB commit 前回执和禁止二次提交回归 |

以上 `PASS_CURRENT_WORKTREE` 均由 §6.1 的原始报告支撑；候选 source commit 形成后还必须把同一代码树
绑定到完整 40 位 SHA。这里的隔离测试不能替代真实三节点故障注入或固定素材 benchmark。

## 4. 取消状态兼容映射

动画管家文档要求取消接口的受理回执使用顶层 `status: CANCEL_REQUESTED`。当前候选实现严格在
**公共 `POST /api/v1/batches/{batch_id}/cancel` 响应边界**返回该值；数据库和 Scheduler 内部仍只
持久化既有的 `CANCELLING` 运行态，因此普通父批次 GET 在取消收敛过程中返回 `CANCELLING`。这两个
值分别表示“取消请求已受理”和“父批次正在取消”，不能互相覆盖：

```json
{
  "batch_id": "batch-uuid",
  "status": "CANCEL_REQUESTED",
  "cancel_status": "REQUESTED",
  "cancel_operation_id": "cancel-op-uuid",
  "cancel_requested_at": "RFC3339 UTC",
  "cancel_accepted_at": "RFC3339 UTC",
  "cancel_requested_by": "authenticated principal",
  "cancel_source": "public_api",
  "cancel_reason": "user requested cancellation",
  "cancel_request_id": "assetclaw-...-cancel-01",
  "cancel_idempotency_key": "assetclaw:...:cancel"
}
```

双方按下面的确定映射处理：

| 调用与协议语义 | GPU Control 字段 | 是否终态 |
|---|---|---|
| 公共 cancel POST 首次受理或未完成 operation 的幂等重放 | `status=CANCEL_REQUESTED` 且 `cancel_status=REQUESTED` | 否 |
| 普通父 GET 观察到取消正在收敛 | `status=CANCELLING` 且 `cancel_status=REQUESTED` | 否 |
| 最终父 GET，或已经完成 operation 的 cancel 重放 | `status=CANCELLED` 且 `cancel_status=COMPLETED`，并有 finished time、counts 与 audit reference | 是 |
| 没有本地取消意图却收到 `CANCELLED` | 动画管家继续按协议异常 fail closed | 是，但不视为合法成功取消 |

同一个 cancel idempotency key 的网络重放必须返回原 operation，不更换原 request ID/reason，也不创建
第二次取消。prompt 超时、节点离线、Scheduler 重启和普通单帧失败不得自行制造父取消 operation。
create/父 GET 的通用序列化仍使用持久状态，不得把一次 POST 回执的 `CANCEL_REQUESTED` 写回数据库，
也不得让 Scheduler 识别第二套在途状态。

## 5. 其他接口对齐事实

### 5.1 阶段时间与节点性能

当前工作树的父响应已经有 `created_at`、`validated_at`、`queued_at`、`started_at`、
`last_progress_at`、`execution_finished_at`、`assembling_at`、`artifact_ready_at`、`finished_at`、
`updated_at`，并按 attempt 聚合上传次数、prompt 次数、GPU service、P50/P95、节点分配和改派。

无法从权威数据计算的值继续返回 `null`，当前包括部分运行场景下的 `scheduler_restarts`、
`max_concurrent_prompts` 和 `straggler_ratio`；不得用常量或客户端估算伪造成真实测量。正式联测前必须
补齐这些权威采集字段或由双方明确从强制验收项中移除。

### 5.2 Range 与产物身份

结果下载基于 Starlette `FileResponse` 的单 Range 行为，响应附带 `Accept-Ranges: bytes` 和
`X-Artifact-SHA256`。最终归档仍必须由动画管家按 size、SHA、manifest、帧数、ordinal、路径与逐文件
哈希完成端到端验证。对 Range 的隔离 HTTP 自测不能替代中途断连、进程重启和真实大文件恢复测试。

### 5.3 Capacity

`GET /api/v1/scheduler/capacity` 是只读 advisory；create/admission 与持久父状态仍是是否接单的权威。
capacity 不暴露其他租户任务或节点地址，也不能作为重复创建相同父批次的依据。

## 6. 自测、故障注入与正式验收的边界

| 证据层级 | 可证明的内容 | 不能证明的内容 | 当前状态 |
|---|---|---|---|
| 静态检查 | 语法、格式、Compose 展开、版本字段/迁移/路由存在 | 运行并发、数据库锁、真实节点/网络行为 | `PASS_CURRENT_WORKTREE` |
| 隔离单元/集成测试 | SQLite/临时目录/Fake ComfyUI 下的取消、门禁、幂等、恢复和调度分支 | PostgreSQL 真实竞争、ComfyUI 真实执行、节点离线和生产负载 | `PASS_CURRENT_WORKTREE` |
| 临时 PostgreSQL 迁移 | PostgreSQL 17 上 0010→0011→0010→0011 可逆，目标字段/表存在 | 生产迁移、真实旧批次兼容、生产回滚时间 | `PASS_CURRENT_WORKTREE` |
| 候选镜像隔离烟测 | 四组件镜像可启动、版本/revision/OCI labels/SBOM 一致 | 三节点业务速度、生产升级和回滚时间 | `PENDING_BUILD` |
| 联合故障注入 | 节点离线、Scheduler 崩溃、prompt 回执丢失、下载中断等端到端恢复 | 生产 7 天稳定性 | `PENDING_JOINT_FAULT_INJECTION` |
| 固定素材 A/B | B1/B6/B30/B64/B97/B300 与 `3 × B97` 的真实速度、质量和拖尾 | 全量生产长期稳定性 | `PENDING_JOINT_BENCHMARK` |
| 生产灰度 | 10% → 50% → 100% 和连续 7 天生产指标 | 无 | `PENDING_PRODUCTION_ROLLOUT` |

本轮自测只能在隔离数据库、Fake ComfyUI 或候选容器中执行。真实故障注入必须先确认隔离 tenant、
API key、安全窗口和当前无生产活动任务；本文件不授权直接断开节点、重启 Scheduler 或更换生产镜像。

### 6.1 可归档自测证据槽

下面槽位记录最终工作树的同轮归档证据；候选 source commit 尚未形成，因此状态明确保留
`SOURCE_BINDING_PENDING`。若之后修改任何 Python、Vue、迁移、Dockerfile、Compose、发布或压测源码，
必须重新运行受影响门禁，不能根据旧滚屏输出追认：

```yaml
self_test_evidence:
  status: PASS_CURRENT_WORKTREE_SOURCE_BINDING_PENDING
  session_id: v155-final-worktree-20260730T0854Z
  source_commit: PENDING_SOURCE_COMMIT
  worktree_diff_sha256: PENDING_SOURCE_COMMIT
  environment: isolated containers; repository read-only; production_access=false
  started_at: "2026-07-30T08:54:00Z"
  finished_at: "2026-07-30T09:04:23Z"
  commands:
    backend_pytest:
      command: "python -m pytest tests -q -p no:cacheprovider --junitxml=/evidence/backend-final.junit.xml"
      isolation: "network=none; source=read-only; cpu=2; memory=3g"
      exit_code: 0
      passed: 144
      failed: 0
      errors: 0
      report_path: artifacts/control-plane/1.5.5/evidence/tests/backend-final.junit.xml
      report_sha256: e39a07eb309276d91eda192bb4b7f23e7ff0477c134da94ab07810588b0dda09
    targeted_gp0:
      command: "named G-P0 testcases included in the same 144-test suite"
      exit_code: 0
      failed: 0
      report_path: artifacts/control-plane/1.5.5/evidence/tests/backend-final.junit.xml
      report_sha256: e39a07eb309276d91eda192bb4b7f23e7ff0477c134da94ab07810588b0dda09
    ruff_mypy_compile:
      command: "Ruff full scope; mypy 34 files; compileall; two Compose config renders"
      exit_code: 0
      report_path: artifacts/control-plane/1.5.5/evidence/tests/static-validation.md
      report_sha256: f26dfdab5ec20aac1dad07de63ab966f3b80d7f3a0e7b64af67187e8ce4d1287
    web_vitest_build_lint_format:
      command: "format + ESLint + vue-tsc + Vitest + Vite build + mocked Playwright desktop/mobile"
      exit_code: 0
      passed: "Vitest 8/8; browser QA 20/20"
      failed: 0
      report_path: artifacts/control-plane/1.5.5/evidence/tests/web-validation.md
      report_sha256: 2ae3967742db4dbd7c45e6598e5e868364c693eb26c0a75a195a1f012e6f4cdd
      junit_path: artifacts/control-plane/1.5.5/evidence/tests/web-vitest.junit.xml
      junit_sha256: e2b1d20a5f73cfbf51ab540ab69474ff0ba9cdf85194d7c83648ded388a3ff3f
    compose_render:
      command: "docker compose ... config --quiet (control-plane and gpu-node)"
      exit_code: 0
      report_path: artifacts/control-plane/1.5.5/evidence/tests/static-validation.md
      report_sha256: f26dfdab5ec20aac1dad07de63ab966f3b80d7f3a0e7b64af67187e8ce4d1287
    migration_postgresql:
      production_database_used: false
      from_revision: "20260729_0010"
      upgrade_revision: "20260730_0011"
      downgrade_revision: "20260729_0010"
      reupgrade_revision: "20260730_0011"
      command: "disposable PostgreSQL 17.5: upgrade 0010; upgrade 0011; downgrade 0010; re-upgrade 0011; catalog verify"
      exit_code: 0
      report_path: artifacts/control-plane/1.5.5/evidence/tests/migration-postgresql.md
      report_sha256: 5ffdfa0f586e7efc01c6569edd3167b264ef77a3dc965f4dcbce393ce2d007ed
```

生产数据库最后核验修订仍为 `20260729_0010`。候选迁移 `20260730_0011` 的
`down_revision` 正是 `20260729_0010`；在完整 `1.5.5` drain/备份/回滚门禁前不得对生产执行。隔离
PostgreSQL 的 upgrade → downgrade → re-upgrade 报告只能证明迁移可逆性，不能冒充生产迁移完成证据。

## 7. Docker、Git/LFS 与归档证据槽位

当前候选工作树配置了 `1.5.5` 的 API、Scheduler、Asset API、Web 构建，以及
`artifacts/control-plane/1.5.5/release-parts/*.part-*` 的 Git LFS 跟踪规则；这不等于镜像或 LFS 对象已经生成和
上传。不可变发布证据如下，未填项必须保持原值：

```yaml
release_candidate:
  version: "1.5.5"
  source_repository: "https://github.com/kiana0512/gpu_control.git"
  source_commit: PENDING_BUILD
  git_tag: PENDING_BUILD
  source_commit_pushed: false
  worktree_clean_for_build: false
  production_version: "1.5.4"
  production_database_revision_last_verified: "20260729_0010"
  candidate_database_revision: "20260730_0011"
  production_migration_applied: false

  images:
    api:
      configured_tag: "gpu-control-api:1.5.5"
      image_id: PENDING_BUILD
      oci_digest: PENDING_BUILD
      registry_manifest_digest: PENDING_BUILD
      sbom_path: PENDING_BUILD
      sbom_sha256: PENDING_BUILD
    scheduler:
      configured_tag: "gpu-control-scheduler:1.5.5"
      image_id: PENDING_BUILD
      oci_digest: PENDING_BUILD
      registry_manifest_digest: PENDING_BUILD
      sbom_path: PENDING_BUILD
      sbom_sha256: PENDING_BUILD
    asset_api:
      configured_tag: "unified-scheduler-asset-api:1.5.5"
      image_id: PENDING_BUILD
      oci_digest: PENDING_BUILD
      registry_manifest_digest: PENDING_BUILD
      sbom_path: PENDING_BUILD
      sbom_sha256: PENDING_BUILD
    web:
      configured_tag: "gpu-control-web:1.5.5"
      image_id: PENDING_BUILD
      oci_digest: PENDING_BUILD
      registry_manifest_digest: PENDING_BUILD
      sbom_path: PENDING_BUILD
      sbom_sha256: PENDING_BUILD

  offline_archive:
    directory: "artifacts/control-plane/1.5.5/release-parts/"
    manifest_sha256: PENDING_BUILD
    parts: PENDING_BUILD
    lfs_oids: PENDING_BUILD
    lfs_pointer_check: PENDING_BUILD
    lfs_fsck: PENDING_BUILD
    lfs_remote_push: PENDING_BUILD

  rollback:
    version: PENDING_BUILD
    registry_manifest_digest: PENDING_BUILD
    command: PENDING_BUILD
    estimated_recovery_time: PENDING_BUILD

  documentation_archive:
    receipt_sha256: PENDING_BUILD
    evidence_manifest: PENDING_BUILD
    archive_commit: PENDING_BUILD
    archive_commit_pushed: false
```

建议按以下可复现顺序收口；每一步失败即停止，不得继续标记下一步完成：

1. 在隔离环境跑完代码、迁移和前端自测，保存原始报告及 SHA-256；
2. 提交并推送一个干净的候选 source commit，确认远端可解析该 40 位 SHA；
3. 只从该 commit 构建四个镜像，嵌入相同 `version=1.5.5` 和 `revision=<source commit>`；
4. 推送 registry，记录不可变 manifest digest，导出并校验与 manifest digest 绑定的 SBOM/provenance；
5. 导出离线镜像归档、分片、生成 manifest/SHA，将 `*.part-*` 作为标准 Git LFS pointer 提交并上传；
6. 执行 `git lfs fsck --pointers`、`git lfs fsck --objects` 和远端无待推对象检查；
7. 用 `make verify-release-identity` 校验 source commit、四镜像 labels/digest 和四份 SBOM；
8. 把真实值回填本节，生成回执/evidence manifest 的 SHA，再提交并推送文档归档 commit。

这套顺序允许 source commit 与最终文档归档 commit 分开：镜像必须绑定前者，后者只补充不可变构建
证据。任何可变 tag 都不能代替 registry manifest digest；任何普通 Git 大 blob 都不能冒充 LFS pointer。

## 8. 联合测试与生产门禁

| 项目 | 状态 | 关闭条件 |
|---|---|---|
| B1/B6/B30/B64/B97/B300 固定素材包、manifest 与输入 SHA | `PENDING_INPUT` | 动画管家提供冻结包，双方核对 SHA |
| 本机 4070 Ti、集群 1/2/3 节点热跑，每组至少 5 次 | `PENDING_JOINT_BENCHMARK` | 原始 JSON、节点 performance、阶段时间、artifact/质量报告齐全 |
| 并发 `3 × B97` | `PENDING_JOINT_BENCHMARK` | 无重复执行/发布、无孤儿批次，速度/拖尾完整报告 |
| 节点离线、prompt 回执丢失、Scheduler 重启、artifact 篡改、Range 中断 | `PENDING_JOINT_FAULT_INJECTION` | 在隔离环境按稳定 test ID 输出 report.json/report.md、request/trace IDs |
| 隔离 tenant/API key 与安全窗口 | `PENDING_SECURE_EXCHANGE` | 密钥通过安全渠道交换，不写入 Markdown/Git |
| 10% → 50% → 100% 灰度 | `PENDING_PRODUCTION_ROLLOUT` | 前述代码、镜像、故障和 benchmark 门禁全部通过 |
| 连续 7 天观察与联合签署 | `PENDING` | 双方共同签署 `FROZEN / PRODUCTION ACCEPTED` |

在这些项目完成前，当前生产仍以 `1.5.4` 事实为准；`1.5.5` 只能称为工作树候选版本。

## 9. 本轮变更边界声明

- 未部署、未重启、未 drain 任何生产节点，未运行生产数据库迁移；
- 未创建、取消、恢复、改派或下载任何生产批次；
- 未修改外部 ImageClip/ModelViewCreator 仓库及其业务语义；
- 未把动画管家提供的测试 API secret 写入仓库；
- 未声称 Docker 打包、registry push、Git/LFS push、真实故障注入、固定素材 benchmark 或生产灰度已完成；
- 所有 `PENDING_*` 必须由原始证据关闭，不接受截图或“已对齐”口头结论。

首轮差异和生产事实基线继续见
`docs/64_2026-07-30_ASSETCLAW_GPU_CONTROL_V4_1_RECEIPT.md`；本文件只记录动画管家优化后第二轮
合同的候选代码对齐状态与下一步不可变发布槽位。
