# 2026-07-30 WebUI 运行中心重构记录

> 候选版本：GPU Control `1.5.5`
> 设计基准：动画管家“任务中心 / 性能分析”的信息层级与可读性
> 生产边界：本文记录的是候选 WebUI 与只读管理数据的变化；没有因此重启 API、Scheduler、
> PostgreSQL、Redis、GPU/CPU Worker，也没有修改任务领取、优先级、租约、重试或工作流语义。
> 当前生产仍为 GPU Control `1.5.4` / 数据库 `20260729_0010`；Web 候选属于 `1.5.5` 工作树，
> 完整 V4.1 字段依赖尚未部署的候选迁移 `20260730_0011`。

## 1. 结果

本轮把原先以“尽可能多显示内部字段”为主的后台，改成以运维决策为主的运行中心：

- 侧栏按“运行工作台、计算能力、接入与治理、诊断与系统”分组；
- 任务中心可同时按业务功能、工作流、公开 API、状态和关键词筛选；
- 任务首行直接展示提交、开始、结束、端到端、真实排队、GPU wall time、进度和节点；
- 任务详情展示九个权威阶段时间、父批次身份、Pipeline SHA 和逐帧明细；
- 新增 `/analysis` 性能分析页，展示中位数、P90、阶段基线、字段覆盖率和逐任务关键路径；
- `/scheduling` 改为“调度运行说明”，先解释任务如何分配、当前容量和风险，再折叠展示可编辑的
  4090 备用条件；
- 总览先展示排队、运行、成功、失败和最老等待，再展示 GPU/CPU 平面与节点状态。

WebUI 没有增加旁路调度器，也不会在浏览器内计算任务分配结果。所有任务状态、节点容量和设置仍
来自现有管理 API；保存调度条件仍调用原来的设置接口，并明确只影响后续新任务。

## 2. 页面与操作路径

| 页面 | 路径 | 主要问题的修复 |
|---|---|---|
| 生产运行总览 | `/` | 把最重要的队列、失败、等待和可用容量放在首屏，不再先展示装饰性指标 |
| GPU 任务中心 | `/jobs` | 按功能/API/工作流/状态组合筛选；父批次一行；关键时间与耗时直接可见 |
| 性能分析 | `/analysis` | 只用服务端已上报字段计算阶段分布、P50/P90、吞吐和证据覆盖 |
| GPU 节点 | `/nodes` | 保留原节点运维能力，入口归入“计算能力” |
| CPU 资产处理 | `/asset-processing` | UV、重拓扑、Baker 继续使用独立 CPU/围栏队列，不与 GPU 领取 SQL 混合 |
| 调度运行说明 | `/scheduling` | 用四步流程、实时容量、风险与影响范围解释原调度设置 |

GPU 与 CPU 任务使用不同后端状态机，WebUI 因此保留两个领域工作台。任务中心提供“CPU 资产任务”
直达入口，而不是在前端强行合并成一个会掩盖调度边界的伪统一列表。

## 3. 任务分类

GPU 任务按服务端真实 `workflow_key` 和任务类型映射：

| 业务功能 | 判断依据 | 展示 API |
|---|---|---|
| 动画序列帧抠图 | `kind=batch` 且 `workflow_key=imageclip-rgba` | `/api/v1/batches/imageclip-rgba` |
| ImageClip RGBA 抠图 | 独立 `imageclip-rgba` job | `/api/v1/services/imageclip-rgba` |
| ModelView 局部重绘 | `workflow_key=modelview-inpaint` | `/api/v1/services/modelview-inpaint` |
| PBR 粗糙度 | `workflow_key=modelview-roughness` | `/api/v1/services/modelview-roughness` |
| 其它 GPU 工作流 | 未知但已上报的 `workflow_key` | `/api/v1/jobs`，标为“自定义工作流” |

筛选维度彼此独立，可形成交集；搜索同时覆盖任务/父批次 ID、租户、工作流、版本、API 和节点。
真实业务与压力测试仍按 `client_kind` 隔离，不允许把压力流量伪装成生产任务。

## 4. 时间与性能证据规则

界面遵循“缺失即缺失”，不会用当前时间、最后进度时间或其它阶段代替未上报的权威字段。

| 指标 | 权威字段 |
|---|---|
| 端到端 | `created_at → finished_at` |
| 真实排队 | `performance.queue_ms`，否则 `queued_at → started_at` |
| GPU 执行 | `performance.execution_ms`，否则 `started_at → execution_finished_at` |
| 结果组装 | `performance.assembly_ms`，否则 `assembling_at → artifact_ready_at` |
| 产物发布 | `performance.artifact_publish_ms`，否则 `artifact_ready_at → finished_at` |
| GPU 吞吐 | 服务端 `performance.frames_per_gpu_minute` |

任何一端缺失、时间非法或倒退时显示“未上报”，并从相应 P50/P90 样本中排除。分析页同时展示实际
样本数和字段覆盖，避免少量完整样本被误读为全量结论。当前分析数据来自 `/admin/jobs` 最近最多
500 条记录，不等同长期时序数据库或正式 B97 基准报告。

## 5. 调度页含义

“调度运行说明”的四步对应现有后端事实：

1. 任务进入真实业务或压力测试隔离队列；
2. 调度器按工作流版本、模型、插件、标签、显存和节点状态筛选兼容候选；
3. 按现有排序依次尝试候选，首节点不兼容时继续尝试其它节点；
4. 成功领取后由数据库租约、状态机和审计保存执行证据；无容量时继续安全排队。

页面展示的 GPU/CPU 槽位、节点心跳、外部队列、启用工作流和风险提示都是观测值。4090 自动备用
阈值仍是原设置键，默认折叠；保存前显示确认，且不会迁移或取消已经运行的任务。

## 6. 自动验证与视觉验证

候选代码已设置以下门禁，最终发布回执应补上归档报告和 SHA：

| 门禁 | 覆盖内容 |
|---|---|
| Vitest | 状态组件、父批次单行、压力任务标记、API 分类、阶段耗时和缺失字段不推断 |
| TypeScript/Vite build | 新路由、Vue 模板、Element Plus 组件和生产 bundle |
| ESLint / Prettier | 前端静态质量和稳定格式 |
| Playwright 桌面 | 总览、任务、分析、调度、筛选、详情和高级条件展开 |
| Playwright 移动 | 390 × 844 导航、卡片流、横向表格容器和无页面级溢出 |

当前环境没有 Browser 插件，视觉测试使用本机已有 Playwright Chromium 和完全隔离的 mock 管理 API；
不得为了截图读取或修改生产任务。正式热更新前仍需在候选 Web 镜像中复跑同一套检查。

## 7. 发布与回滚

正在运行任务不允许成为 WebUI 改版的代价。发布顺序固定为：

1. 构建带 source commit 的独立 `gpu-control-web:1.5.5` 候选镜像；
2. 隔离启动并完成登录、任务、分析、调度和移动端检查；
3. 只读确认当前活动任务和四个控制面容器健康；
4. Web-only 更新时只替换 Web 容器，不重启 API、Scheduler、数据库、Redis 或 Worker；
5. 若浏览器健康或关键页面失败，立即恢复 `gpu-control-web:1.5.4`；
6. 包含迁移与 Scheduler 的完整 `1.5.5` 发布必须另走 drain、迁移、滚动和回滚门禁。

本轮用户已明确当前仍有任务运行，因此在候选镜像、归档和测试完成前不会抢先热更新生产 Web；
后端完整发布更不会在活动任务期间执行。

Web-only 候选对生产 `1.5.4` 保持只读降级兼容：旧 API 没有返回的 V4.1 时间和性能字段显示
“未上报”，不会由浏览器补值，也不要求提前执行 `20260730_0011`。但这只能用于界面候选验收，
不代表 1.5.5 后端合同已上线。断电恢复后必须先重新核验活动任务、容器健康和当前版本，不能沿用
断电前截图作为发布门禁。

## 8. 已知边界

- 普通独立 GPU job 目前主要只有 `created_at / started_at / finished_at`，细阶段缺失时会如实显示
  “未上报”；完整阶段分析优先来自 V4.1 父批次。
- `/analysis` 是当前任务数据的前端聚合，不替代 Prometheus/Grafana 长期趋势。
- WebUI 说明调度行为，但不重新实现或预测 Scheduler 的领取 SQL。
- 真实 B1/B6/B30/B64/B97/B300、`3 × B97` 和六 API 100+ 用户综合压测尚未执行；必须等用户
  指定窗口、生产活动任务为零并通过独立压测安全门禁；执行合同见
  `docs/67_2026-07-30_SIX_API_MIXED_LOAD_TEST_RUNBOOK.md`。
