# LI3D Agent、Blender Skill 与统一 WebUI 融合方案

文档日期：2026-07-28
文档状态：`PHASE A SOURCE IMPLEMENTED / TESTED / NOT DEPLOYED`
实施边界：GPU Control 控制面、Asset API、Scheduler、Web UI、Worker 管理与制品管理
生产影响：无；本文编写期间没有迁移数据库、启动 Agent、部署 Worker、重启后端或修改业务管线

## 1. 评审输入与完整性记录

本方案基于以下输入完成评审：

| 输入 | SHA-256 / 版本 |
|---|---|
| `LI3D_Unified_AI_Asset_Processing_Cluster_Design_v1.0.md` | `3d84d7fa08613edf5e437873418b1c14dcdac2669ec450f172f52f532c0c350c` |
| `blender-pbr-uv-codex-cloud.zip` | `f2efbb58f786d2cc9eab6f6af0307d25676ae15e60626f5b436814e726964ea2` |
| `blender-retopology-compare-iterate-server-package-v1.0.0.zip` | `439331e1651ce7e49f34423c172f0c5c0ea317d3333e2758c3f9dc478845c147` |
| Retopology Skill package | `1.0.0-portable`，包内 `SHA256SUMS.txt` 全部校验通过 |
| 本机 Codex CLI | `codex-cli 0.146.0-alpha.3.1` |

UV Skill 的关键文件指纹：

| 文件 | SHA-256 |
|---|---|
| `SKILL.md` | `37de0b496030e7b20151c7d5cbcf340ed4cd2ea36c132e50fc57743f5b4d427e` |
| `references/pbr-uv-standard.md` | `06872924e99f2e856c36e3e5e0aefce23c06554e6809125a4fa7ec41970c75cb` |
| `scripts/unwrap_fbx.py` | `ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758` |
| `scripts/qa_uv.py` | `bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d` |

Retopology Skill 的关键文件指纹：

| 文件 | SHA-256 |
|---|---|
| `SKILL.md` | `e0bb19bcd35ec20a810cdc9f72905e2823052df429dde98110ec8b352ac3d7e4` |
| `scripts/audit_pair.py` | `a6575902cfacd7b8106f9c887069d717a880d870fc48a6295431cdcf717a9dc4` |

两个 Skill 已从用户提供的本地包安装到本机 Codex CLI 标准目录：

```text
/home/lilithgames/.codex/skills/blender-pbr-uv
/home/lilithgames/.codex/skills/blender-retopology-compare-iterate
```

关键文件 SHA-256 已由 `scripts/verify_asset_skills.sh` 复核。Compose 源码只读挂载该固定目录，Worker
每次执行前再次验证执行脚本 Hash。当前改动尚未部署，其他节点也尚未同步，不能把“本机已安装”等同于
“生产 Release 已发布”。后续只能同步这个固定 Release，不能运行时引用或自动更新下载目录内容。

## 2. 结论

设计文档提出的“统一控制面、隔离执行面”方向正确，也与现有系统兼容。落地时应在现有 GPU Control
仓库中渐进扩展，不新建第二套 Web UI、认证、数据库或不可见的旁路调度器：

```text
统一 Web UI / Unified API
       │
       ├─ GPU 推理域（现有）
       │    ├─ 单图 Job
       │    ├─ 序列帧 Batch
       │    └─ ComfyUI GPU Worker
       │
       └─ 资产处理域（增量扩展现有 Asset API）
            ├─ Asset Job（用户看到的一行）
            ├─ Workflow / Task / Attempt（详情时间线）
            ├─ Codex CLI Agent Runtime（分析、计划、解释）
            ├─ Blender Runtime（确定性执行、QA、导出）
            ├─ Human Review（拓扑四视图复核）
            └─ Artifact / Skill Release（版本与结果真相）
```

用户只看到一个业务 Job。Agent、Blender、QA、修复轮次和每次 Attempt 都放在 Job 详情，不能让每个
内部步骤占据任务中心一行。

## 3. 现有系统可直接复用的能力

仓库并非从零开始，以下基础已经存在：

- `asset_jobs`：持久资产任务、客户隔离、状态、进度、错误和源文件 Hash；
- `asset_workers`：Blender Worker 心跳、CPU/内存/负载和并发槽位；
- `asset_artifacts`：最终制品文件名、类型、大小和 SHA-256；
- `asset_idempotency_keys`：客户级幂等；
- Asset API：提交、状态、取消、容量、Worker claim、租约续期、失败恢复和最终下载；
- Blender Worker：独立容器、输入 Hash 复核、无 GPU 模式、并发执行和四件套上传；
- 最终制品原子发布：四件齐全、QA 无 hard failure 后才发布；
- Nginx：外部 Asset API 与内部 Worker API 已有独立路由；
- 统一 Web 产品壳：名称已是“统一调度中心”，导航已有“资产处理”；
- PostgreSQL、Redis、Loki、Alloy、审计、客户身份和 LAN CA 可继续复用。

因此不应另起 `li3d-unified-control-center` 仓库，也不应把 Codex、Blender 或 Skill 塞进 ComfyUI 容器。

## 4. 当前实现与目标之间的真实缺口

| 范围 | 当前实现 | 目标缺口 |
|---|---|---|
| Web UI | `/asset-processing` 是静态候选数据 | 必须改为真实 API 数据、作业列表、时间线、Runtime、Skill、Review |
| Asset Job | 单一 `UV_UNWRAP` 一步任务 | 缺 Workflow、Task、Dependency、Attempt、Event、Repair Round |
| Worker | 只有 CPU Blender Worker | 缺 `LLM_AGENT` Runtime、Agent Slot 和 Process 生命周期 |
| 调度 | Asset Worker 自己轮询 FIFO Job | 缺依赖调度、能力匹配、公平性、Attempt lease/fencing |
| Skill | 只有字符串 `skill_version` | 缺不可变 Release、文件 Hash、批准、Canary、回滚与任务快照 |
| Artifact | 仅四个 UV 最终文件 | 缺 inventory、plan、validation、preview、comparison、diagnostics 等类型 |
| UV QA | 当前仓库 `blender_uv.py` 明示 overlap/stretch QA 未完成 | 不能宣称已达到提供的 PBR UV Skill 标准 |
| Retopology | 无重拓扑执行器和 Web Review | 提供的 Skill 只有规则与审计，不能冒充全自动重拓扑 |
| Node 视图 | GPU Node 与 Asset Worker 分开 | 需要统一只读投影视图，展示同一物理机上的多个 Runtime |
| Admin API | Web 无 Asset admin 查询接口 | 需要管理员资产列表、详情、Runtime、Skill、Review API |

### 4.1 不采用大爆炸式 Node → Runtime → Slot 重构

现有 GPU Scheduler 已承载生产任务，直接替换 Node/Job 表风险过大。采用两步策略：

1. **先统一管理视图**：主控 Admin API 聚合现有 GPU Node、Asset Worker 和未来 Agent Runtime，返回统一
   `PhysicalNodeView/RuntimeView/SlotView`，底层表暂不强行合并。
2. **再规范化资源模型**：Agent/Blender 工作流稳定后，引入持久 Runtime/Slot 表；GPU Node 通过兼容层
   映射，避免一次迁移破坏现有调度。

## 5. 两个 Skill 的正确职责

### 5.1 `blender-pbr-uv`

该 Skill 同时包含领域判断规则和可执行 Blender 脚本。生产中必须拆成两个权限层使用：

### Agent 可见层

- `SKILL.md`；
- `references/pbr-uv-standard.md`；
- 资产 inventory、预览、用户需求和受控 Operator Catalog；
- 只允许输出符合 Schema 的 `uv_plan.json` 或 `NEEDS_REVIEW`。

Agent 不得启动 Blender、执行 Skill 脚本、联网下载工具、SSH、访问 Docker Socket或写生产资产。

### Blender Worker 可执行层

- 固定版本 `unwrap_fbx.py`、`qa_uv.py`，或将其审核后封装为等价白名单 Operator；
- Blender 5.1+ 固定版本；
- 输入只读、Attempt 输出独立；
- 对 `.blend` 禁止 autoexec；
- BLEND 与 FBX 回读 QA 都必须通过；
- 输出集合、名称和 Hash 经控制面校验后原子发布。

提供的 Skill 要求 hard-edge、0–1、翻转、退化、意外重叠、密度、拉伸和 FBX 回读。现有仓库
`packages/asset_processing/blender_uv.py` 只实现了其中一部分，并明确声明 overlap/stretch 需要扩展。因此
首个生产 UV 闭环必须使用批准 Skill 的完整 QA，或补齐等价测试后再进入 `FROZEN`；不能只改版本字符串。

### 5.2 `blender-retopology-compare-iterate`

该 Skill 的本质是“对照、迭代和审计”，不是一个现成的自动重拓扑算法：

- 高模是形状权威；
- 用户参考低模是拓扑风格权威；
- 每个候选必须保留为独立版本，不能覆盖高模或参考低模；
- `audit_pair.py` 可确定性检查拓扑统计、源模型指纹、边界、重复、法线和尺寸；
- silhouette、自相交、UV、烘焙质量仍需四视图或其他 QA；
- RetopoFlow 是可选 GUI 工具，Headless Worker 不能声称使用了 RetopoFlow。

因此第一版应命名为 `RETOPO_COMPARE_REVIEW_V1`，流程是：

```text
上传高模 + 参考低模 + 候选低模
  → 安全预检与对象选择
  → Blender baseline audit（固定高模/参考低模 fingerprint）
  → 四视图与三行对照图渲染
  → Codex CLI 解释问题并生成结构化 repair_plan
  → Policy 校验
  → 人工修改或已批准的确定性 Operator 生成新候选
  → strict audit + baseline preservation
  → Web UI 人工批准/拒绝/再修订
```

在没有经过批准的候选生成器之前，系统只能自动分析、审计和编排，不能宣传“一键自动 AI 重拓扑”。

## 6. Codex CLI Runtime 的接入边界

本机实际执行 `codex exec --help` 已确认当前 CLI 支持：

- `codex exec` 和 stdin prompt；
- `--json` JSONL 输出；
- `--output-schema`；
- `--output-last-message`；
- `--sandbox workspace-write`；
- `--ephemeral`；
- `--cd` 和独立工作目录。

但当前版本是 alpha 构建，且 CLI 事件、认证并发、token 刷新和限额都不能仅凭帮助文本视为生产稳定。
Phase 0 必须先固定精确二进制版本并通过 1/2 并发兼容测试。设计文档中的 4 并发只能作为测试项，
不能作为初始生产值。

官方 Codex 页面强调可将重复工作保存为 Skill、为 Codex 提供可组合 CLI，但生产 Runtime 仍需由系统
自己负责权限、超时、取消、结果契约和评测：<https://developers.openai.com/codex/use-cases>。

### 6.1 Agent 运行目录

```text
/srv/gpu-control/agent-jobs/<job>/<task>/<attempt>/
├── workspace/
│   ├── AGENTS.md                  只读固定策略
│   ├── .agents/skills/<release>/  只读固定 Skill
│   ├── input/                     只读 inventory/preview/requirements/audit
│   ├── schema/                    只读输出 Schema
│   └── output/                    唯一可写业务输出
├── runtime/
│   ├── prompt.txt
│   ├── events.jsonl
│   ├── final_message.json
│   ├── stdout.log
│   ├── stderr.log
│   └── process.json
└── manifest.json
```

每次运行初始化最小 Git 仓库，但 FBX、BLEND、图片和运行日志不得进入 Git。任务默认无 Session 依赖；
重试通过 Artifact 重建上下文。

### 6.2 Agent 输出合同

UV Agent 只输出：

```text
UV_PLAN_READY | NEEDS_REVIEW
uv_plan.json
```

Retopology Agent 只输出：

```text
REPAIR_PLAN_READY | NEEDS_REVIEW | REJECT_CANDIDATE
retopo_review.json
repair_plan.json（可选）
```

Plan 禁止 Python、Shell、URL、任意路径、环境变量、SQL、动态模块和未知 Operator。退出码 0 不等于
成功；还必须通过 JSON Schema、Policy、Skill Release、输入版本和 Operator Catalog 校验。

## 7. 资产工作流定义

### 7.1 `UV_PROCESS_V1`

| 序号 | Task | Runtime | 主要输出 |
|---:|---|---|---|
| 1 | `ASSET_PRECHECK` | deterministic | `inventory.json`、安全报告、预览 |
| 2 | `UV_ANALYZE_AND_PLAN` | `LLM_AGENT/CODEX_CLI` | `uv_plan.json` 或 `NEEDS_REVIEW` |
| 3 | `PLAN_VALIDATE` | control plane | `plan_validation.json` |
| 4 | `UV_EXECUTE` | `BLENDER` | 独立候选 BLEND/FBX、执行报告 |
| 5 | `UV_QA_BLEND` | `BLENDER` | BLEND QA |
| 6 | `UV_QA_FBX_READBACK` | `BLENDER` | FBX 回读 QA |
| 7 | `QA_DECISION` | control plane | pass/repair/review |
| 8 | `PACKAGE_OUTPUT` | deterministic | deliverable + manifest + SHA-256 |

自动修复默认最多 2 轮，同一指标无改善时立即转人工复核。每一轮生成新的 Task Attempt 和 Plan Revision，
不覆盖上一轮。

### 7.2 `RETOPO_COMPARE_REVIEW_V1`

| 序号 | Task | Runtime | 主要输出 |
|---:|---|---|---|
| 1 | `ASSET_PRECHECK` | deterministic | 三模型 inventory、对象映射 |
| 2 | `RETOPO_BASELINE_AUDIT` | `BLENDER` | 高模/参考低模 fingerprint、统计 |
| 3 | `RETOPO_RENDER_COMPARE` | `BLENDER` | front/side/top/perspective 三行对照图 |
| 4 | `RETOPO_AGENT_REVIEW` | `LLM_AGENT` | 结构化问题和修复计划 |
| 5 | `REVIEW_GATE` | human/control | 批准、拒绝或要求新候选 |
| 6 | `RETOPO_STRICT_AUDIT` | `BLENDER` | strict audit、source preservation |
| 7 | `PACKAGE_OUTPUT` | deterministic | 被批准候选、对照图、audit、manifest |

如果未来接入自动/半自动重拓扑 Operator，在 4 和 5 之间增加新的 `RETOPO_EXECUTE`，不能复用 Agent
进程直接修改模型。

## 8. 数据模型的增量升级

保留现有 `asset_jobs` 作为父任务，新增以下资产域表，避免直接重写生产 GPU `jobs`：

```text
asset_workflow_runs
asset_tasks
asset_task_dependencies
asset_task_attempts
asset_task_events
asset_artifact_links
asset_skill_releases
asset_operator_releases
asset_runtime_registrations
asset_runtime_slots
asset_runtime_leases
asset_review_requests
```

必须满足：

- Task 领取、Attempt、Lease 与递增 fencing token 在同一 PostgreSQL 事务创建；
- Worker 完成上报必须携带 attempt、lease 和 fencing token；
- 旧 token、迟到成功和重复 complete 被拒绝；
- Redis 只做事件通知，清空 Redis 不丢任务；
- 每个 Attempt 保存 Control、Workflow、CLI、Skill、Schema、Blender、Operator 和输入 Artifact 的版本快照；
- 最终 Artifact 两阶段提交，只有父 Job 成功才对业务调用方可见。

现有 `AssetJob.lease_token_hash` 可兼容旧 `UV_UNWRAP`，但新多步骤工作流不能继续把 Job 本身当作唯一
执行租约。

## 9. API 演进与兼容

保留现有接口，不打断已经对接的 UV 客户：

```text
POST /api/v1/assets/uv/unwrap
GET  /api/v1/assets/jobs/{job_id}
POST /api/v1/assets/jobs/{job_id}/cancel
```

新增通用资产接口：

```text
POST /api/v1/assets/uploads
POST /api/v1/asset-jobs
GET  /api/v1/asset-jobs/{job_id}
POST /api/v1/asset-jobs/{job_id}/cancel
POST /api/v1/asset-jobs/{job_id}/tasks/{task_id}/retry
POST /api/v1/asset-reviews/{review_id}/decision
GET  /api/v1/asset-jobs/{job_id}/artifacts
```

通用创建接口通过 `job_type` 选择 `UV_PROCESS_V1` 或 `RETOPO_COMPARE_REVIEW_V1`。大型多文件资产先上传
形成 Artifact ID，再创建 Job；不能把高模、参考低模、候选低模和多张参考图全部塞进一个长期 multipart
请求。

旧 `/uv/unwrap` 内部映射到 `UV_PROCESS_V1`，响应继续兼容原 `job_id/status/status_url`；新的 Task 详情
只作为向后兼容的附加字段。

## 10. 统一 Web UI 信息架构

### 10.1 主导航

建议保留现有框架，调整为：

```text
总览
任务中心
计算资源
资产处理
工作流
Skill 管理
API 客户
调度策略
告警
审计日志
日志中心
系统信息
```

“任务中心”是所有业务父任务的统一入口；“资产处理”是 UV/拓扑的领域工作台；“计算资源”按物理机
展示多个 Runtime；“Skill 管理”只管理不可变 Release，不在线编辑 Skill 内容。

### 10.2 总览

总览增加四类卡片：

- 业务队列：GPU 单图、序列帧批次、UV、拓扑复核；
- Runtime 槽位：GPU_COMFY、LLM_AGENT、BLENDER、REVIEW；
- 今日结果：成功、失败、等待复核、修复轮次；
- 版本健康：Skill mismatch、Blender mismatch、Agent auth、Artifact 容量。

真实用户与测试用户继续隔离显示，压力测试数据不能混入生产统计。

### 10.3 任务中心

每个业务 Job 只占一行：

| 字段 | 内容 |
|---|---|
| ID/名称 | external ID、Job ID |
| 类型 | 抠图批次、局部重绘、UV、拓扑复核 |
| 当前阶段 | Agent 规划、Blender 执行、QA、等待复核、打包 |
| 状态 | QUEUED/RUNNING/WAITING_REVIEW/SUCCEEDED/FAILED |
| 资源 | 当前 Runtime/Node，多个 Task 时显示分布 |
| 进度 | Task 加权进度，不能用 CLI 文本伪造百分比 |
| 等待 | 队列位置和 advisory ETA |
| 操作 | 查看、取消、按权限重试 |

筛选维度：生产/测试、业务域、Job Type、Runtime、状态、客户、节点和时间。

### 10.4 资产处理工作台

将当前静态候选页改为六个页签：

1. **作业**：UV/拓扑父任务列表、创建入口和容量；
2. **工作流**：`UV_PROCESS_V1`、`RETOPO_COMPARE_REVIEW_V1` 版本与健康；
3. **运行时**：Agent/Blender Slot、队列、版本、认证健康和资源；
4. **Skill**：Release、SHA-256、适用 Task、Schema、测试、Canary、生产版本；
5. **人工复核**：待审 UV/拓扑任务；
6. **API 调用**：上传、创建、查询、取消、复核和下载示例。

当前页面中的硬编码 IP、槽位和“候选功能”文案全部由 Admin API 返回，不再写死在 Vue 文件中。

### 10.5 Job 详情

```text
父任务摘要
  ├─ 输入 Artifact 与 SHA-256
  ├─ Workflow DAG / 时间线
  │    ├─ Task 状态、Runtime、Node、Attempt、耗时
  │    └─ 重试、修复轮次、错误分类
  ├─ Agent Plan（结构化字段，不展示内部推理）
  ├─ Blender 执行报告和 Operator 日志
  ├─ QA 指标与硬失败
  ├─ Artifact 列表、预览、Hash 和下载
  ├─ 人工复核
  └─ 审计/诊断包
```

Agent JSONL 和完整 stderr 进入日志/诊断，不在普通用户页面暴露内部推理、认证或敏感环境信息。

### 10.6 拓扑复核 UI

拓扑 Review 页面必须同时展示：

- 三行：高模、参考低模、当前候选低模；
- 四列：front、side、top、perspective；
- 同尺度、同方向、同 framing；
- 顶点/边/面、三角/四边/N-gon、边界、非流形、重复和尺寸偏差；
- 高模与参考低模 fingerprint 是否保持；
- Agent 问题摘要与修复建议；
- `批准候选 / 拒绝 / 请求新版本`，所有决定带理由并进入审计。

页面不能把 audit 通过等同于视觉轮廓通过，也不能把 RetopoFlow 标为已使用，除非执行证据明确存在。

### 10.7 计算资源

顶层仍按 4090、3090-A、3090-B 三台物理机显示，展开后显示 Runtime：

```text
4090 控制中心
  CONTROL_PLANE  HEALTHY
  LLM_AGENT      1/1 RUNNING
  BLENDER_CPU    0/2 IDLE
  GPU_COMFY      0/1 ACTIVE/RESERVED
```

这只是统一投影，不把同一物理机复制成多台节点。Runtime 可独立 DRAINING/DISABLED；GPU Blender 与
ComfyUI 未来共用物理 GPU fencing lease，CPU Blender 不申请 GPU。

## 11. 管理端 API

Web UI 需要新增只供管理员访问的读写接口：

```text
GET  /admin/unified-jobs
GET  /admin/asset-jobs/{id}
GET  /admin/runtime-overview
GET  /admin/asset-workflows
GET  /admin/skill-releases
POST /admin/skill-releases/{id}/promote
POST /admin/skill-releases/{id}/rollback
GET  /admin/reviews
POST /admin/reviews/{id}/decision
POST /admin/asset-tasks/{id}/retry
```

第一阶段可以由主 API 以数据库读模型聚合现有表。管理操作仍使用当前管理员 JWT、`reason + confirm`、
审计日志和角色权限；不能让浏览器直接访问内部 Worker API。

## 12. Skill Release 生命周期

Skill Release 是不可变对象：

```text
DRAFT → VALIDATED → CANARY → PRODUCTION → RETIRED
                     └──────────────→ ROLLED_BACK
```

至少记录：

- name、semantic version、source commit/package hash；
- 每个文件 SHA-256 和 bundle SHA-256；
- SKILL.md 描述与入口；
- 允许的 Task Types；
- 输出 Schema 和 Operator Catalog 版本；
- Blender/Codex 最低与固定版本；
- 静态校验、Contract Test、Golden Asset、Canary 结果；
- 发布人、批准人、时间和回滚目标。

任务创建时固定 Release ID；排队期间升级生产 Skill 不能改变已创建 Job。三个节点只同步 Blender
执行所需的固定脚本/Operator；Agent Skill 初期仅部署到 4090 Agent Runtime。Web UI 发现 Hash 不一致时
显示 `SKILL_MISMATCH` 并停止相关 Runtime 领取新任务。

## 13. 安全边界

- Agent 任务进程无 root、无 sudo、无 Docker Socket、无 SSH、无数据库/Redis访问；
- Agent 只看当前 Job、只写 output、默认不能访问局域网；
- Codex 认证只给 Runtime Daemon，不挂载进 Workspace，不进入日志或 Artifact；
- 上传的文件名、模型内容、贴图文字、metadata 和 prompt 都是不可信数据，不是系统指令；
- Blender 使用 `--factory-startup --disable-autoexec`，任务容器默认无网络；
- `.blend` 不执行驱动/脚本，Plan 不接受任意 Python；
- Skill 脚本只有进入审核过的 deterministic tool 白名单后才能由 Blender Worker 执行；
- 所有完成上报使用 lease + fencing token + 两阶段 Artifact 提交；
- UI 不显示内部推理和 Secret。

## 14. 分阶段落地

### Phase A：Web UI 真实化与统一只读视图

不改变生产调度行为：

1. 新增 Asset Admin 只读 API；
2. 把静态资产页面改成真实数据；
3. 任务中心增加业务域筛选和父任务详情入口；
4. 计算资源增加 Runtime 展开视图；
5. 增加 Skill Release 只读页和版本不一致提示。

验收：浏览器没有硬编码节点/IP/容量；GPU 任务行为完全不变。

### Phase B：Workflow/Task/Attempt 基础

1. 新增资产工作流与 Attempt 表；
2. 为旧 `UV_UNWRAP` 建兼容映射；
3. 实现依赖、租约、fencing、事件和两阶段完成；
4. Web 展示真实时间线；
5. 暂不启动 Codex Runtime。

### Phase C：Codex CLI Phase 0

1. 固定 CLI 版本和认证 Provider；
2. 实现 Runtime Adapter、Workspace Builder、JSONL Parser 和 Schema Validator；
3. 只运行合成 inventory，不接生产资产；
4. 验证 100 次、取消、超时、崩溃、无僵尸、1/2 并发和 Secret 泄漏；
5. 结果只进入测试客户视图。

### Phase D：UV Canary

1. 发布固定 `blender-pbr-uv` Skill Release；
2. 对一个 Golden Asset 跑 Agent Plan → Blender → 双 QA → Package；
3. 与 Skill 直接执行基线比较；
4. 通过后小流量生产 Canary；
5. 不修改 ImageClip/ModelViewCreator 或 GPU 工作流。

### Phase E：Retopology Compare/Review

1. 发布 Retopology Skill Release；
2. 接入 baseline/strict audit 和四视图渲染；
3. Agent 只解释和给 repair plan；
4. 上线人工复核；
5. 没有确定性生成器时不启用自动 `RETOPO_EXECUTE`。

### Phase F：组合工作流

UV 和拓扑稳定后，再评估 Retopo → UV → Texture/ComfyUI 组合工作流。每个 Runtime 继续独立，跨步骤
只通过已提交 Artifact，不通过本地路径或进程直连。

## 15. 首个建议闭环

首个闭环选择 `UV_PROCESS_V1`，不是拓扑：

1. 使用一个结构清晰、无敏感内容的 Golden FBX；
2. 预检生成 inventory 和固定预览；
3. Codex CLI 加载固定 UV Skill，只输出 `uv_plan.v1`；
4. Schema/Policy 拒绝未知 Operator；
5. Blender Worker 使用固定 Blender 与审核脚本执行；
6. BLEND QA 与 FBX 回读 QA 都通过；
7. 原子发布模型、报告、QA、manifest；
8. Web UI 展示父任务一行和完整步骤时间线；
9. 分别验证取消、Agent 非法输出、Blender crash、Scheduler 重启和迟到结果。

该闭环是验证架构的最小单元。通过前，不扩展自动修复、Retopology 生成或跨 GPU 组合任务。

## 16. 进入实施前的硬门槛

- [ ] 生产维护窗口与数据库备份已确认；
- [ ] Phase A 只读 API/UI 方案通过；
- [ ] Codex CLI 精确版本、认证方式与并发限制确定；
- [ ] 两个 Skill 固定 Release 存储位置确定，不引用下载目录；
- [ ] UV Plan Schema 与 Operator Catalog 评审完成；
- [ ] UV Skill 完整 QA 与当前 Worker 行为差异有 Golden Test；
- [ ] Retopology 明确为 Compare/Review，不宣传自动生成；
- [ ] Artifact 容量、保留期和备份位置确定；
- [ ] 测试客户与真实客户继续隔离；

## 17. 2026-07-28 Phase A 实施增量

本次已在未部署源码中完成：

- 新增 `POST /api/v1/assets/uv/process`，作业类型 `UV_PROCESS_V2`；
- UV 输入支持 FBX、OBJ、GLB/GLTF、BLEND；
- UV 固定输出 `<stem>_PBR_UV.blend/.fbx/_report.json/_QA.json/_FBX_QA.json` 五件套；
- BLEND 与 FBX 回读 QA 同时通过后原子发布；
- 新增 `POST /api/v1/assets/retopology/audit`；
- 拓扑审计只接受一个包含 high/reference/low 三对象的 BLEND 工程；
- 拓扑审计完成后固定进入 `WAITING_REVIEW`，不会宣称自动重拓扑完成；
- Blender Worker 改为直接执行固定 Hash 的 Skill 脚本，并启用 `--disable-autoexec`；
- 新增 `GET /admin/asset-processing` 真实管理读模型；
- `/asset-processing` Web 页面已移除硬编码 IP、槽位与候选四件套；
- 新增 Codex CLI 与 Skill Hash 预检脚本；
- API 契约见 `docs/52_ASSET_SKILLS_API_V2_CONTRACT.md`。

验证结果：

- Asset API 契约：5 项测试通过；
- Web：ESLint、TypeScript/Vite 构建、3 项 Vitest 通过；
- 两套 Skill 的 6 个关键文件 SHA-256 通过；
- Codex CLI 必需参数预检通过。

仍未完成且不得提前宣称完成：

- 没有自动生成新低模的确定性 Retopology Operator；
- 没有拓扑三行四视图渲染与人工批准写接口；
- 没有将新源码部署到生产或其他节点；
- 没有在生产 Blender 5.1.2 上执行 Golden Asset；
- Codex Agent Runtime 尚未进入生产任务链。
- [ ] 任何数据库迁移、服务重启和生产启用均在无运行任务的安全窗口执行。

## 17. 本轮明确未执行

- 没有将 Skill 安装到 Codex、4090、3090-A 或 3090-B；
- 没有复制或修改 Skill 文件；
- 没有运行 Blender Skill 脚本；
- 没有启动 Codex Agent 任务；
- 没有修改 Asset API、Scheduler、Web UI 或数据库；
- 没有部署、重启或中断任何生产服务；
- 没有修改 ImageClip、ModelViewCreator 及其工作流。

下一步建议只实施 **Phase A：Web UI 真实化与统一只读视图**。它能先把管理体验对齐，同时不给现有
GPU 调度和生产任务引入新的执行风险。
