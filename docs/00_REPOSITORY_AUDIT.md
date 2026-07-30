# 现有仓库审计

> 当前状态补记（2026-07-31）：本文主体是 2026-07-21 重构前的历史快照，不再代表当前生产状态。
> 当前仓库已有正常 Git 历史并同步 `main`；GPU Control `1.5.6` 已以源码
> `310a44c70c20f7cbfc601d19e19858380a61c20a` 完成四控制面服务的零任务热更新，发布状态为
> `DEPLOYED_NOT_ACCEPTED`。当前版本、归档、回滚及剩余门禁以
> `75_2026-07-30_CONTROL_PLANE_1_5_6_DEPLOYMENT_AND_ARCHIVE.md` 为准；六 API、120 VU 的独立 R8
> 已以退出码 0 完成，机器报告、持久 SHA 和正式边界见
> `76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md`。本文以下“缺口”“无 Git”描述
> 只用于解释最初为何全量重构，不能作为今天的部署判断。

审计日期：2026-07-21  
审计范围：仓库中现有 Python、React/TypeScript、Shell、配置和文档；未读取或修改运行数据、日志、IDE 配置。

## 结论

旧仓库是面向单机一张 GPU 的 ComfyUI 保护代理：SQLite 保存队列，调度器与 API 同进程，直接管理本机 ComfyUI Python 进程，React 后台代理旧管理接口。它不能安全扩展到三台主机，也不满足 PostgreSQL 为真相来源、两台 3090 优先、4090 保留/溢出、故障恢复和集中日志的约束，因此生产路径需要替换。

在 2026-07-21 原始快照中，仓库只有空 `.git` 目录且没有 `HEAD` 等有效元数据；当时
`git status` 返回“not a git repository”，所以该阶段只能以状态文档、文件清单和测试输出为准。
此限制已在后续仓库初始化和远端同步后消除，不适用于当前 1.5.6 版本。

## 保留并迁移的思路

- 旧 `apps/worker` 的 ComfyUI 接口与 NVML 方案只作为重构输入；旧目录已删除。当前实现位于 `apps/scheduler` 的持久连接/WebSocket 生命周期客户端，GPU 指标由 DCGM Exporter 采集，节点 Agent 只提供固定运维与诊断操作。
- 旧前端的任务、节点、日志、运维信息架构可作交互参考；实现改为要求的 Vue 3/Pinia/Element Plus。
- 现有脚本中严格模式、systemd 状态检查等惯例继续采用。

## 替换的生产路径

| 旧实现 | 风险 | 新实现 |
|---|---|---|
| `aiosqlite` 单连接队列 | 无行级锁、租约、跨进程安全 | PostgreSQL + SQLAlchemy async + Alembic |
| API 内启动调度循环 | 调度故障影响 API，无法单主 | 独立 asyncio scheduler + advisory lock |
| FIFO/单机 VRAM 预算 | 无租户公平、节点池或 4090 保护 | 优先级老化、租户轮转、PRIMARY/OVERFLOW 策略 |
| 重启后把执行中任务直接失败 | 丢失可恢复执行，可能重复出图 | 先按 prompt_id 查询 queue/history 再协调 |
| 运行时启动宿主机 Python ComfyUI | 环境不可复现、权限边界过宽 | 固定 commit Dockerfile + 同镜像三节点部署 |
| 弱或默认关闭的管理认证 | 可越权执行运维操作 | 哈希 API Key、JWT/RBAC、审计、二次确认 |
| 普通文本/本地日志 | 无跨机链路与统一检索 | JSON 结构化日志 + Alloy/Loki |
| React 单体后台 | 与目标栈不一致 | Vue 3 + TypeScript + Pinia + Router |

## 全量替换授权与边界

用户随后明确授权“不须保留旧仓库任何内容，直接全量替换”。旧 API、scheduler/worker、React 管理台、旧配置和旧脚本已从生产源码树删除并由目标架构替代。未删除 `.idea/`、空 `.git/`、`logs/`、`storage/` 和本地工具缓存；模型、真实工作流、输入输出和密钥从未作为生产材料加入仓库。

## 基线缺口

缺少 Alembic、PostgreSQL/Redis 数据层、统一领域模型、安全文件存储、工作流注册/绑定、Fake ComfyUI、三节点调度、节点 Agent、Compose、ComfyUI Dockerfile、Loki/Alloy、Prometheus/Grafana/Alertmanager、飞书 Bridge、部署与灾备文档、自动测试和 100 并发测试。

## 迁移原则

新代码位于 `packages/gpu_control_core`、`apps/*/src` 和目标部署目录。所有镜像与依赖固定版本；二进制只落任务目录；Redis 仅唤醒/广播；管理员运维不允许任意 Shell 或 Docker Socket。Celery/Flower 虽获用户允许，但未采用，理由见 ADR 0002。
