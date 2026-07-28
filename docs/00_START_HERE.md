# 统一调度中心（GPU Control）文档总入口

本页是仓库文档导航。今天部署时不要从 30 多份文档逐一翻找，按下面的“现场主线”执行即可；其余文档是遇到具体问题时的细节手册。

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
| Asset V3 | `55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md` | UV、重拓扑、多视角、进度/ETA、审核与两机真实验收 |
| 3090-B 混合节点验收 | `57_2026-07-28_3090_B_WINDOWS_WSL2_GPU_ACCEPTANCE.md` | Windows/WSL2 网络、节点身份、GPU 真实任务、Asset Worker 与回滚 |
| 分角色安装 | `03`—`11` | 网络、准备、4090、3090、镜像、模型、工作流、首次部署 |
| 使用手册 | `12_WEB_ADMIN_GUIDE.md`、`13_PUBLIC_API_GUIDE.md` | 管理后台和业务 API |
| 运维 | `15`—`22` | 日志、告警、备份、升级、故障、容量和 FAQ |
| 验收 | `23`—`26`、`IMPLEMENTATION_STATUS.md` | 验收清单、三机验收、交付报告和单文件审计材料 |
| 决策记录 | `docs/adr/` | 为什么选择 asyncio、PostgreSQL、Redis 通知、Loki/Alloy 等 |

## 3. 三台主机职责

| 主机 | 默认职责 | GPU 状态 | 关键服务 |
|---|---|---|---|
| RTX 4090 主控 | 控制面、数据、监控、日志，也作为第三个受控 ComfyUI 槽位 | `ACTIVE`（池为 `OVERFLOW`） | Nginx、Web、API、Scheduler、PostgreSQL、Redis、Loki、Grafana、Alloy、Prometheus、Alertmanager、ComfyUI |
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

三机 CUDA 容器通过；三机镜像 ID 和模型 SHA 一致；两台 3090 显示 `ONLINE/ACTIVE`；4090 显示 `RESERVED`；PostgreSQL/Redis/API/Nginx/Loki/Grafana/Prometheus 正常；真实 API 工作流完成 1 单、3 单、10 单；Fake ComfyUI 100 并发无丢单；管理后台能够 Drain、Reserve、Release、中断、释放模型、启停/重启和下载诊断；Grafana 可按 `job_id`、`request_id`、`node_id`、`prompt_id` 查询。
