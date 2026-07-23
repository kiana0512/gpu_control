# GPU Control 文档总入口

本页是仓库文档导航。今天部署时不要从 30 多份文档逐一翻找，按下面的“现场主线”执行即可；其余文档是遇到具体问题时的细节手册。

## 1. 今天上线只看这五项

1. 根目录 `GPU_CONTROL_成品部署联调与核心逻辑手册.pdf`：产品结构、核心算法、三机命令、联调、日志、压测和故障定位的单文件版本。
2. `docs/USER_INPUT_REQUIRED.md`：先补齐真实 IP、SSH 用户、模型、API 工作流和业务限制。
3. `docs/28_TODAY_DEPLOYMENT_MANUAL.md`：从三台空 Ubuntu 主机开始的完整命令正文。
4. `docs/30_TODAY_ONSITE_CHECKLIST.md`：现场操作者逐项打勾并记录结果。
5. `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md`：2026-07-23 当前双项目镜像、模型和 3090 接入的唯一最新交接步骤。

3090-A 的已完成部署、动态心跳、Web 修复和真实任务证据见
`docs/34_2026-07-23_3090_A_DEPLOYMENT_RECORD.md`。
3090-B、动态热缓存、OOM/重试修复、GPU 指标与三卡 10 客户实测见
`docs/35_2026-07-23_3090_B_AND_THREE_NODE_ACCEPTANCE.md`。

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
| 分角色安装 | `03`—`11` | 网络、准备、4090、3090、镜像、模型、工作流、首次部署 |
| 使用手册 | `12_WEB_ADMIN_GUIDE.md`、`13_PUBLIC_API_GUIDE.md` | 管理后台和业务 API |
| 运维 | `15`—`22` | 日志、告警、备份、升级、故障、容量和 FAQ |
| 验收 | `23`—`26`、`IMPLEMENTATION_STATUS.md` | 验收清单、三机验收、交付报告和单文件审计材料 |
| 决策记录 | `docs/adr/` | 为什么选择 asyncio、PostgreSQL、Redis 通知、Loki/Alloy 等 |

## 3. 三台主机职责

| 主机 | 默认职责 | GPU 状态 | 关键服务 |
|---|---|---|---|
| RTX 4090 主控 | 控制面、数据、监控、日志，也保留一个 ComfyUI | `RESERVED` | Nginx、Web、API、Scheduler、PostgreSQL、Redis、Loki、Grafana、Alloy、Prometheus、Alertmanager、ComfyUI |
| RTX 3090-A | 主推理节点 | `ACTIVE` | ComfyUI、Alloy、GPU exporter、Node Agent |
| RTX 3090-B | 主推理节点 | `ACTIVE` | ComfyUI、Alloy、GPU exporter、Node Agent |

正常任务只进入两台 3090。管理员把 4090 设为 `ACTIVE` 时三机都可用；设为 `OVERFLOW` 时还必须同时满足排队阈值、等待时间、哨兵文件、GPU 利用率、剩余显存和允许时段条件。

## 4. 代码结构

| 路径 | 内容 |
|---|---|
| `apps/api` | FastAPI 公共接口、后台接口、鉴权、幂等、审计、告警入口 |
| `apps/scheduler` | asyncio 调度循环、数据库领取、ComfyUI 生命周期与恢复 |
| `apps/node_agent` | HMAC 防重放的固定运维操作代理 |
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
