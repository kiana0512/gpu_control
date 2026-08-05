# 统一调度中心（GPU Control）文档总入口

本页是仓库文档导航。今天部署时不要从 30 多份文档逐一翻找，按下面的“现场主线”执行即可；其余文档是遇到具体问题时的细节手册。

当前是分组件生产基线：GPU Control API、Scheduler 和 Web 仍为 `1.5.7`；Asset API 已更新到
`1.5.8`，生产镜像 revision 为 `7f7fd197f86288ffbeeab622cc39199335e22c61`；数据库为
`20260803_0012`。总状态为 `DEPLOYED_NOT_ACCEPTED`，不能写成整套 1.5.8 控制面已部署。Linux
Blender Worker 均使用 tag `1.2.4`；4090 revision 为 `7f7fd197…`，3090-A/3090-B 为
`e2cab4c8…`，但 Worker 相关源码和批准 Skill SHA 已逐节点核对一致；统一 OCI image digest/SBOM
仍待归档。

生产 UV 和 Retopology 都是 `advisory`：几何质量告警不阻断正式制品交付，但身份、manifest、
文件完整性、租约和 SHA 仍为硬门禁。四个 Windows Substance Baker Agent v5 均为
`ONLINE/HEALTHY`。三节点 ComfyUI 使用同一 `projects-0.2.3` 镜像，健康、`RestartCount=0`，本轮未
停止/重启 ComfyUI，也未清理模型缓存。没有注入额外合成流量；现有真实任务已完成 PBR、UV warning、
UV clean 和连续两笔重拓扑 canary。控制面统一、API artifact 三重 SHA、registry/SBOM、固定基准、
完整故障矩阵和七天观察未完成前，禁止标记
`FROZEN / PRODUCTION_ACCEPTED`。断电或重启后仍必须以运行时 API/数据库只读检查为准。

## 1. 当前现场主线

1. 根目录 `GPU_CONTROL_成品部署联调与核心逻辑手册.pdf`：产品结构、核心算法、三机命令、联调、日志、压测和故障定位的单文件版本。
2. `docs/USER_INPUT_REQUIRED.md`：先补齐真实 IP、SSH 用户、模型、API 工作流和业务限制。
3. `docs/28_TODAY_DEPLOYMENT_MANUAL.md`：从三台空 Ubuntu 主机开始的完整命令正文。
4. `docs/30_TODAY_ONSITE_CHECKLIST.md`：现场操作者逐项打勾并记录结果。
5. `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md`：2026-07-23 当前双项目镜像、模型和 3090 接入的唯一最新交接步骤。
6. `docs/56_GPU_CONTROL_MATTING_HANDOFF_V4.md`：动画管家批量抠图当前实施合同；冻结上传完整性、失败隔离与真实取消语义。
7. `docs/41_2026-07-27_GPU_CONTROL_1_3_2_STRESS_AND_PIPELINE_RECORD.md`：1.3.2/1.3.3 管线修复、三节点真实压力和生产优先级证据。
8. `docs/42_2026-07-27_ASSETCLAW_V3_ALIGNMENT_RESPONSE.md`：动画管家 V3 固定格式对齐回执与安全启用门禁。
9. `docs/43_BLENDER_PBR_UV_ASSET_API_CONTRACT_V1.md`：Blender CPU Worker、并发模型、外部 API 和验收合同。
10. `docs/44_2026-07-27_UNIFIED_WEB_AND_BLENDER_WORKER_STAGING_RECORD.md`：统一 Web-only 上线、浏览器 QA 和两台 3090 Blender 镜像验收。
11. `docs/45_MODELVIEW_OPTIONAL_PROMPT_AND_SEEDVR2_ROLLOUT.md`：局部重绘可选提示词、SeedVR2 固定依赖和三节点安全升级方案。
12. `docs/46_2026-07-27_PRODUCTION_DRAIN_AND_ROLLOUT_RUNBOOK.md`：生产任务排空门禁、三节点局部重绘发布、Asset API 启用与回滚执行单。
13. `docs/55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md`：UV/重拓扑当前 API、进度、产物、真实两机验收与 3090-B 迁移唯一交接。
14. `docs/57_2026-07-28_3090_B_WINDOWS_WSL2_GPU_ACCEPTANCE.md`：3090-B Windows/WSL2 混合节点、真实 GPU API 验收、当前 Asset Worker 状态与后续项。
15. `docs/58_2026-07-29_ASSET_V4_CLIENT_HANDOFF_AND_STABILITY.md`：Asset V4 当前唯一客户端合同、自动交付、Codex/RetopoFlow 健康和三机稳定性验收。
16. `docs/59_2026-07-29_RELEASE_AUDIT_STABILITY_AND_IMAGE_RECORD.md`：本轮完整发布审计、三节点压力测试、镜像与机器可读证据。
17. `docs/57_2026-07-29_MODELVIEW_ROUGHNESS_API.md`：ModelView 粗糙度工作流公共 API、输入输出和真实调用说明。
18. `docs/58_2026-07-29_SUBSTANCE_BAKER_API.md`：Windows Substance Baker API、输入包、贴图类型和下载合同。
19. `docs/59_2026-07-29_ROUGHNESS_AND_SUBSTANCE_BAKER_API_HANDOFF_V2.md`：粗糙度与烘焙当前联合交接和验收规则。
20. `docs/60_2026-07-29_UV_AND_RETOPOLOGY_API_HANDOFF_V5.md`：UV/重拓扑当前客户端合同、自动 QA 发布与错误语义。
21. `docs/61_2026-07-29_SUBSTANCE_BAKER_4_SLOT_RELEASE.md`：3090-B Windows Baker 四个独立槽位发布记录。
22. `docs/62_2026-07-30_REPRODUCIBLE_BACKUP_AND_ROLLING_UPDATE.md`：当前可复现镜像、备份状态、滚动更新和回滚手册。
23. `docs/63_2026-07-30_THREE_NODE_RELEASE_AND_RECOVERY_CLOSURE.md`：当前三节点发布、镜像、恢复点和最终 canary 的闭环事实。
24. `docs/64_2026-07-30_ASSETCLAW_GPU_CONTROL_V4_1_RECEIPT.md`：动画管家 V4.1 性能稳定合同的首轮事实回执、缺口和联合验收前置项。
25. `docs/65_2026-07-30_ASSETCLAW_POST_OPTIMIZATION_ALIGNMENT_RECEIPT.md`：动画管家优化后的第二轮代码对齐回执、G-P0-01～07 状态，以及 Docker/Git LFS/联合验收待填证据。
26. `docs/66_2026-07-30_WEBUI_OPERATIONS_REDESIGN.md`：与动画管家对齐的任务中心、性能分析、调度说明、证据规则和安全发布边界。
27. `docs/67_2026-07-30_SIX_API_MIXED_LOAD_TEST_RUNBOOK.md`：六 API、GPU/CPU/Windows Worker 的 100+ 用户综合压测计划、安全门禁、遥测和停止条件；这是执行手册，r5/r7 历史结果见 73 号记录，R8 最终有界压测见 76 号记录。
28. `docs/68_2026-07-30_CONTROL_PLANE_1_5_5_REPRODUCIBLE_PACKAGING.md`：1.5.5 四组件的历史可复现打包门禁；1.5.6 实际镜像、归档和 LFS 证据见 75 号记录。
29. `docs/69_2026-07-30_ASSETCLAW_1_5_5_SPEED_STABILITY_PREJOINT_RECEIPT.md`：动画管家第三轮输入的历史联测前回执；其中“未部署/未压测”状态已被 73～75 号记录取代。
30. `docs/70_2026-07-30_RETOPOLOGY_QA_ADVISORY_HOTFIX_AND_LOAD_READINESS.md`：Retopology advisory 正式 BLEND/FBX 交付、SHA 硬门禁和回滚方式。
31. `docs/73_2026-07-30_SIX_API_120VU_LOAD_RESULT.md`：保留 r5 历史结果及 r7 的业务通过、遥测超界 199 ms 的 fail-closed 事实；附 R8 新结果索引，不反向改写历史失败。
32. `docs/74_2026-07-30_SCHEDULER_AND_SUBSTANCE_STABILITY_HOTFIX.md`：Scheduler 单主锁、leader epoch、Substance 物理 GPU fence/recovery 的 1.5.6 已部署证据。
33. `docs/75_2026-07-30_CONTROL_PLANE_1_5_6_DEPLOYMENT_AND_ARCHIVE.md`：1.5.6 四镜像身份、零任务热更新、回滚标签、离线归档/LFS 和尚未通过的正式验收门禁。
34. `docs/76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md`：R8 退出码 0、六 API/七阈值、120/120 清场、GPU/Asset 峰值、Substance 自动 fence 恢复、379 样本与持久 SHA 证据；不替代 B97、故障矩阵和七天验收。
35. `docs/77_2026-08-03_PBR_NEXT_TURN_AND_COMFY_CACHE_RETENTION_1_5_7.md`：1.5.7 的 PBR 下一轮预约续租修复、Windows Baker v3、ComfyUI 不显式清缓存合同、Web 等待原因、零任务发布、真实 PBR canary 和回滚证据；当前为 `DEPLOYED_NOT_ACCEPTED`。
36. `docs/78_2026-08-03_SUBSTANCE_LONG_LEASE_AND_AGENT_RECOVERY_HOTFIX.md`：Substance 长烘焙租约续期、Windows Agent 计划任务自恢复、3090-B 安全排空与 ComfyUI 连续性证据。
37. `docs/79_2026-08-03_CODEX_PER_NODE_AUTH_AND_PROBE_RECOVERY.md`：三节点独立持久化 Codex 认证、Worker 1.2.3 滚动更新、真实探针恢复、镜像身份限制和回滚证据。
38. `docs/80_2026-08-03_CONTROL_PLANE_1_5_8_CANDIDATE_AND_SAFE_ROLLOUT.md`：1.5.8 分阶段部署记录；Asset API/DB/Worker/Agent 已局部上线，API/Scheduler/Web 仍为 1.5.7，三 Worker 镜像身份待对齐。
39. `docs/81_2026-08-03_ASSET_V4_UV_RETOPOLOGY_LATEST_HANDOFF.md`：Li3D/动画管家当前 UV 与自动重拓扑唯一最新应用端合同，包含 CA、幂等、仅 BLEND、双 advisory、正式制品、SSE、SHA 和当前分组件部署边界。
40. `docs/82_2026-08-03_ASSET_FAILURES_UV_ADVISORY_AND_RELEASE_ACCEPTANCE.md`：PBR 空 ExitCode 假失败、UV advisory 五件套交付、Codex Skill 子链接修复的局部生产事实、剩余 canary 与证据回填表。
41. `docs/83_2026-08-03_CONTROL_PLANE_1_5_9_RELEASE_AND_SIX_API_ACCEPTANCE.md`：1.5.9/Worker 1.2.5 的统一候选、生产优先原子准入、六 API 精确产物、五镜像发布、灰度、压测和动画管家回执入口；所有 `PENDING_*` 回填前仍非生产验收。

3090-A 的已完成部署、动态心跳、Web 修复和真实任务证据见
`docs/34_2026-07-23_3090_A_DEPLOYMENT_RECORD.md`。
3090-B、动态热缓存、OOM/重试修复、GPU 指标与三卡 10 客户实测见
`docs/35_2026-07-23_3090_B_AND_THREE_NODE_ACCEPTANCE.md`。

动画管家批量序列帧抠图请只按 `docs/56_GPU_CONTROL_MATTING_HANDOFF_V4.md` 联调；V1～V3 只保留
为历史记录，不能继续作为当前接口合同。1.2.0 初版批处理记录仍保留在
`docs/39_2026-07-24_BATCH_MATTING_DEPLOYMENT_RECORD.md`，1.3.3 的当前事实以 41 号记录为准。

部署完成后看 `docs/IMPLEMENTATION_STATUS.md`，把“现场待测”项改成实际日期、主机和结果，不要覆盖本机验证记录。

## 2. 文档结构

| 层级 | 文档 | 用途 |
|---|---|---|
| 总入口 | `00_START_HERE.md` | 当前页面，告诉操作者先做什么 |
| 产品与审计 | `00_REPOSITORY_AUDIT.md`、`29_PRODUCT_RELEASE_AND_TEST_REPORT.md` | 仓库现状、功能范围、实现证据和真实边界 |
| 入门与架构 | `01_BEGINNER_OVERVIEW.md`、`02_ARCHITECTURE.md`、`14_SCHEDULER_DESIGN.md`、`27_CORE_LOGIC_AND_ALGORITHM_AUDIT.md` | 系统如何工作、任务状态机、3090/4090 算法 |
| 当天部署主线 | `28_TODAY_DEPLOYMENT_MANUAL.md`、`30_TODAY_ONSITE_CHECKLIST.md` | 三机从空机到首单和 100 并发验收 |
| 当前 3090 交接 | `33_3090_NODE_DEPLOYMENT_HANDOFF.md` | 将本机已验证的双项目环境复制到两台 3090 |
| 3090-A 记录 | `34_2026-07-23_3090_A_DEPLOYMENT_RECORD.md` | A 的实机身份、部署结果、断电恢复与真实任务证据 |
| B 与三卡验收 | `35_2026-07-23_3090_B_AND_THREE_NODE_ACCEPTANCE.md` | B 的完整部署、性能优化、故障修复和 10 客户三卡实测 |
| 动画管家 V4 | `56_GPU_CONTROL_MATTING_HANDOFF_V4.md` | 批量抠图当前接口、上传完整性、失败/取消语义和联调清单 |
| 批处理部署记录 | `39_2026-07-24_BATCH_MATTING_DEPLOYMENT_RECORD.md` | 1.2.0 生产变更、真实三卡证据和回滚点 |
| 1.3.2/1.3.3 压测记录 | `41_2026-07-27_GPU_CONTROL_1_3_2_STRESS_AND_PIPELINE_RECORD.md` | 最新管线修复、真实 7:3 压力和生产优先级证据 |
| Asset V3 | `55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md` | UV、重拓扑、多视角、进度/ETA 与两机真实验收；人工审核描述已被 V4/V5 自动 QA 合同取代 |
| 3090-B 混合节点验收 | `57_2026-07-28_3090_B_WINDOWS_WSL2_GPU_ACCEPTANCE.md` | Windows/WSL2 网络、节点身份、GPU 真实任务、Asset Worker 与回滚 |
| Asset V4 客户端合同 | `58_2026-07-29_ASSET_V4_CLIENT_HANDOFF_AND_STABILITY.md` | UV/重拓扑当前 API、自动交付、确定性算法、Codex/RetopoFlow 健康与稳定性验收 |
| 1.5.4 发布审计 | `59_2026-07-29_RELEASE_AUDIT_STABILITY_AND_IMAGE_RECORD.md`、`62_2026-07-30_REPRODUCIBLE_BACKUP_AND_ROLLING_UPDATE.md` | 三节点真实压力、代码审计、镜像归档、恢复状态与机器可读证据 |
| 粗糙度与烘焙 | `57_2026-07-29_MODELVIEW_ROUGHNESS_API.md`、`58_2026-07-29_SUBSTANCE_BAKER_API.md`、`59_2026-07-29_ROUGHNESS_AND_SUBSTANCE_BAKER_API_HANDOFF_V2.md`、`61_2026-07-29_SUBSTANCE_BAKER_4_SLOT_RELEASE.md` | 粗糙度 GPU API、Windows Baker API、四槽并发与客户端交接 |
| UV / 重拓扑 V5 | `60_2026-07-29_UV_AND_RETOPOLOGY_API_HANDOFF_V5.md` | 历史 V5 合同；当前几何 QA 已切为 advisory，正式 BLEND/FBX 交付以 70 号记录为准 |
| 动画管家 V4.1 回执 | `64_2026-07-30_ASSETCLAW_GPU_CONTROL_V4_1_RECEIPT.md` | 逐项实现状态、版本证据、身份冲突、P0/P1 缺口与联合验收输入 |
| 动画管家优化后第二轮回执 | `65_2026-07-30_ASSETCLAW_POST_OPTIMIZATION_ALIGNMENT_RECEIPT.md` | 批准的 691 身份、G-P0-01～07 候选代码状态、取消状态映射、发布与联合验收待填证据 |
| WebUI 运行中心重构 | `66_2026-07-30_WEBUI_OPERATIONS_REDESIGN.md` | 任务/API 分类、真实时间、性能分析、调度解释、视觉验证与 Web-only 回滚边界 |
| 六 API 综合压测 | `67_2026-07-30_SIX_API_MIXED_LOAD_TEST_RUNBOOK.md`、`73_2026-07-30_SIX_API_120VU_LOAD_RESULT.md`、`76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md` | 100+ 用户真实请求组合、GPU/CPU/Windows 槽位遥测、生产让路和精确清场；73 保留 r5/r7 历史，R8 在 76 号记录中以退出码 0 通过有界压力门禁 |
| 1.5.5 候选打包 | `68_2026-07-30_CONTROL_PLANE_1_5_5_REPRODUCIBLE_PACKAGING.md` | 1.5.5 历史打包门禁；当前 1.5.6 部署/归档身份以 75 号记录为准 |
| 动画管家 1.5.5 联测前回执 | `69_2026-07-30_ASSETCLAW_1_5_5_SPEED_STABILITY_PREJOINT_RECEIPT.md` | 本轮最终源码、性能稳定修复、WebUI、素材输入阻断和发布/压测未执行边界；联合测试前按此对账 |
| Retopology / 六 API / 稳定性热修复 | `70_2026-07-30_RETOPOLOGY_QA_ADVISORY_HOTFIX_AND_LOAD_READINESS.md`、`73_2026-07-30_SIX_API_120VU_LOAD_RESULT.md`、`74_2026-07-30_SCHEDULER_AND_SUBSTANCE_STABILITY_HOTFIX.md`、`76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md` | 正式 BLEND/FBX advisory 交付、r5/r7 历史、R8 最终有界压力结果，以及已随 1.5.6 部署的 Scheduler/Substance 并发修复 |
| 1.5.6 部署与归档 | `75_2026-07-30_CONTROL_PLANE_1_5_6_DEPLOYMENT_AND_ARCHIVE.md` | 四镜像 source identity、热更新、回滚、离线归档/LFS 和 `DEPLOYED_NOT_ACCEPTED` 边界 |
| 1.5.7 PBR 下一轮与缓存保持 | `77_2026-08-03_PBR_NEXT_TURN_AND_COMFY_CACHE_RETENTION_1_5_7.md` | 修复 3090-B 预约续租未提交造成的 PBR 饥饿；v3 不 stop/start/free、连续性证据、Web 解释、零任务发布与真实 canary 已完成；当前 `DEPLOYED_NOT_ACCEPTED` |
| Substance 长租约与 Agent 恢复 | `78_2026-08-03_SUBSTANCE_LONG_LEASE_AND_AGENT_RECOVERY_HOTFIX.md` | 长烘焙持续续租、四个 Windows Agent 自恢复、安全排空和不重启 ComfyUI 的生产热修复证据 |
| Codex 三节点探针恢复 | `79_2026-08-03_CODEX_PER_NODE_AUTH_AND_PROBE_RECOVERY.md` | 每节点独立可写认证、Worker 1.2.3、三机真实探针、构建来源限制与回滚；当前 `DEPLOYED_NOT_ACCEPTED` |
| 1.5.8 分阶段发布 | `80_2026-08-03_CONTROL_PLANE_1_5_8_CANDIDATE_AND_SAFE_ROLLOUT.md` | Asset API/DB/Worker/Agent 局部部署、ComfyUI 连续性、三 Worker 身份差异与剩余验收门禁 |
| Asset V4 UV/自动拓扑最新合同 | `81_2026-08-03_ASSET_V4_UV_RETOPOLOGY_LATEST_HANDOFF.md` | 当前分组件基线：CA、幂等、仅 BLEND、UV/拓扑双 advisory、正式制品、SSE、SHA 和真实 canary |
| Asset 失败修复与发布验收 | `82_2026-08-03_ASSET_FAILURES_UV_ADVISORY_AND_RELEASE_ACCEPTANCE.md` | PBR 假失败、UV advisory 五件套、Codex Skill 子链接与真实 canary；统一 OCI/SBOM、API 三重 SHA 与观察待回填 |
| 1.5.9 统一发布与六 API 验收 | `83_2026-08-03_CONTROL_PLANE_1_5_9_RELEASE_AND_SIX_API_ACCEPTANCE.md` | 生产优先全局准入、精确 artifact 合同、五镜像身份、三节点灰度、浏览器 QA、120 用户压测及动画管家回执的唯一回填入口 |
| 1.5.10 部分成功与失败帧补算 | `84_2026-08-05_PARTIAL_SUCCESS_AND_FAILED_FRAME_REPAIR_HANDOFF.md` | PARTIAL_SUCCESS、跨节点帧级重试、成功子集归档、failed_items、OOM 证据与动画管家补算合同 |
| 分角色安装 | `03`—`11` | 网络、准备、4090、3090、镜像、模型、工作流、首次部署 |
| 使用手册 | `12_WEB_ADMIN_GUIDE.md`、`13_PUBLIC_API_GUIDE.md` | 管理后台和业务 API |
| 运维 | `15`—`22` | 日志、告警、备份、升级、故障、容量和 FAQ |
| 验收 | `23`—`26`、`IMPLEMENTATION_STATUS.md` | 验收清单、三机验收、交付报告和单文件审计材料 |
| 决策记录 | `docs/adr/` | 为什么选择 asyncio、PostgreSQL、Redis 通知、Loki/Alloy 等 |

## 3. 三台主机职责

| 主机 | 默认职责 | GPU 状态 | 关键服务 |
|---|---|---|---|
| RTX 4090 主控 | 控制面、数据、监控、日志，也作为第三个受控 ComfyUI 槽位 | `OVERFLOW` | Nginx、Web、API、Scheduler、PostgreSQL、Redis、Loki、Grafana、Alloy、Prometheus、Alertmanager、ComfyUI |
| RTX 3090-A | 主推理节点 | `ACTIVE` | ComfyUI、Alloy、GPU exporter、Node Agent |
| RTX 3090-B | 主推理节点 | `ACTIVE` | ComfyUI、Alloy、GPU exporter、Node Agent |

当前三节点压力与批量抠图模式中三卡均可接单；4090 位于 `OVERFLOW` 池，是否参与由运行时策略、
队列阈值、等待时间、哨兵文件、GPU 利用率、剩余显存和允许时段共同决定。需要纯两卡生产时可把
4090 设为保留，但文档和 Web 必须展示实际运行模式，不能沿用旧“单机模式”文案。

## 4. 代码结构

| 路径 | 内容 |
|---|---|
| `apps/api` | FastAPI 公共接口、后台接口、鉴权、幂等、审计、告警入口 |
| `apps/scheduler` | asyncio 调度循环、数据库领取、ComfyUI 生命周期与恢复 |
| `apps/node_agent` | HMAC 防重放的固定运维操作代理 |
| `apps/asset_api` | 与 GPU Scheduler 隔离的 Asset API、CPU 作业队列、租约和最终产物发布 |
| `apps/blender_worker` | 可并发的 Blender 5.1.2 CPU Worker |
| `apps/web` | LiClick 风格 Vue 管理后台 |
| `packages/gpu_control_core` | 数据模型、状态机、设置、日志和通用核心逻辑 |
| `migrations` | PostgreSQL/SQLite Alembic 迁移 |
| `docker/comfyui` | 可复现统一 ComfyUI 镜像、自定义节点锁和依赖锁 |
| `deploy` | 主控与 GPU 节点 Compose、Nginx、Loki、Alloy、Grafana、Prometheus |
| `scripts` | `gpuctl`、初始化、驱动/Toolkit、镜像、模型、备份、诊断和联通脚本 |
| `tests` | 单元、集成、Fake ComfyUI 和 100 并发回归测试；这是源码，不是缓存 |
| `workflows` | API 工作流注册格式、示例和校验 schema |

## 5. 生成文件与源码的区别

- 必须保留：`apps/`、`packages/`、`deploy/`、`docker/`、`scripts/`、`migrations/`、`tests/`、`docs/`、`workflows/`、锁文件和示例配置。
- 现场生成：`.env`、`configs/nodes.yaml`、`configs/prometheus.yml`、`output/deploy/`、证书、诊断包和数据目录。
- 不应提交或打包：`.venv`、`node_modules`、`dist`、`.pytest-*`、`.mypy_cache`、`.ruff_cache`、IDE 配置、临时数据库和 PDF 渲染图片。

## 6. 上线成功的最低标准

三机 CUDA 容器通过；三机 ComfyUI `projects-0.2.3` 镜像 ID 和批准模型 SHA 一致；两台 3090 显示 `ONLINE/ACTIVE`；4090 显示 `ONLINE/OVERFLOW`；PostgreSQL/Redis/API/Nginx/Loki/Grafana/Prometheus 正常；真实 API 工作流完成 1 单、3 单、10 单；Fake ComfyUI 100 并发无丢单；管理后台能够 Drain、Reserve、Release、中断、释放模型、启停/重启和下载诊断；Grafana 可按 `job_id`、`request_id`、`node_id`、`prompt_id` 查询。
