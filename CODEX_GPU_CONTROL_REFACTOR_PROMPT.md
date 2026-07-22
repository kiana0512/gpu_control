# Codex 总控任务：把现有 `gpu_control` 仓库重构为三台 Ubuntu 服务器的 ComfyUI GPU 调度、API、日志、监控与 Web 运维平台

你现在位于现有仓库根目录。当前仓库可能已有 `apps/`、`configs/`、`deploy/`、`docs/`、`scripts/`、`storage/`、`logs/`、`pyproject.toml`、`README.md` 等内容。请先真实检查全部代码与配置，再进行重构。

## 0. 执行方式与硬性要求

这不是“给方案”任务，而是“实际改仓库”任务。

你必须：

1. 先审计现有仓库，但不要停在审计或计划阶段；审计结束后直接继续实现。
2. 可以完全替换现有架构，不要求向后兼容；有价值的代码可以复用，无价值代码直接删除。
3. 不得删除 `.git`、用户真实模型、用户真实工作流、真实输入输出文件或真实密钥。
4. 不要修改或提交 `.idea/`、运行日志、数据库数据、模型权重、生成图片、缓存等大文件。
5. 所有生产代码、Dockerfile、Compose、迁移、测试、脚本、中文文档必须一起完成。
6. 不允许只创建空目录、接口壳、伪实现、`pass`、大量 `TODO` 或“后续再做”。
7. 目标工作流和模型尚未提供时，不得伪造一个号称可生产运行的局部重绘工作流；应完成通用工作流注册与参数绑定机制、测试夹具、导入工具和明确的 `USER_INPUT_REQUIRED.md`。
8. 没有 GPU 或无法联网时，不得声称 GPU 测试或镜像拉取已经成功。要完成可离线完成的代码和 Mock 测试，并在状态文档中准确列出需要在服务器执行的验证命令。
9. 所有依赖、镜像、ComfyUI、自定义节点都必须固定版本或 commit，不使用不受控的 `latest`。
10. 所有文档使用简体中文，命令必须可以复制执行；代码标识符使用英文，必要注释可使用中文。
11. 每完成一个阶段都更新 `docs/IMPLEMENTATION_STATUS.md`，记录完成项、测试命令、真实结果、未验证项和原因。
12. 最终必须运行仓库内所有可运行的 lint、类型检查、单元测试、集成测试、前端构建、Compose 配置校验，并给出真实结果。
13. 不要向我反复询问非阻塞问题。使用下面给定的默认值并把它们做成可配置项。只有缺少真实工作流、模型文件、飞书 Webhook 等外部材料时，写入 `USER_INPUT_REQUIRED.md`，不要阻塞其他实现。
14. 先创建 `docs/00_REPOSITORY_AUDIT.md`，记录旧代码保留、替换、删除的理由；然后立即继续实施。
15. 使用 Git 友好的小步修改，不要把生成物、密钥、模型和运行数据加入版本控制。

---

## 1. 业务背景

有三台位于同一局域网的 Ubuntu Linux 服务器：

| 节点 | 示例 IP | GPU | 主要职责 |
|---|---|---|---|
| control-4090 | `192.168.10.10` | RTX 4090 | 控制中心、统一 API、数据库、调度器、Web 后台、日志监控；GPU 默认保留给其他任务 |
| worker-3090-a | `192.168.10.11` | RTX 3090 | ComfyUI 主计算节点 |
| worker-3090-b | `192.168.10.12` | RTX 3090 | ComfyUI 主计算节点 |

系统主要运行 ComfyUI 工作流，目前首个业务是局部重绘生图，后续还会增加超分、去背景、商品图等 API。

典型场景是 100 个用户同时提交请求。系统必须快速接收、可靠排队、公平分流、实时显示进度，且不能让调度器成为明显性能瓶颈。

4090 机器还要运行其他任务，因此必须优先把生图任务发给两台 3090。4090 只能在管理员明确启用或达到可配置的溢出条件、并且没有被其他任务占用时参与计算。

运维人员按 Linux 和 Docker 初学者考虑，仓库必须包含一步一步的完整中文安装、部署、升级、回滚、日志排查和灾难恢复文档。

---

## 2. 不可更改的架构决策

请按以下方案实现，不要换成 Kubernetes、Docker Swarm 或复杂集群：

### 2.1 总体技术栈

- Docker Engine + Docker Compose。
- Python 3.11 作为默认应用运行时，除非真实自定义节点明确要求其他版本。
- FastAPI：公开 API、管理 API、WebSocket/SSE、健康检查、飞书告警桥接。
- PostgreSQL：任务、状态、工作流、节点、API 客户、审计日志的唯一持久化真相来源。
- Redis：只用于低延迟唤醒、实时事件广播、分布式限流、短期缓存；Redis 不是任务真相来源。
- 自研轻量异步调度器：使用 `asyncio`、SQLAlchemy async、`httpx` 和 WebSocket 客户端；不要使用 Celery。
- Vue 3 + TypeScript + Vite + Pinia + Vue Router + Element Plus + ECharts：中文 Web 管理后台。
- Prometheus + Grafana：指标与仪表盘。
- Loki + Grafana Alloy：三台机器的集中日志。
- Alertmanager + 飞书 Webhook Bridge：告警与恢复通知。
- Node Exporter + NVIDIA DCGM Exporter：系统和 GPU 指标。
- Alembic：数据库迁移。
- pytest、pytest-asyncio、Ruff、mypy：后端质量。
- Vitest、ESLint、Prettier：前端质量。
- Locust 或等价的 Python 压测工具：100 并发验收。
- Nginx：HTTPS、反向代理、上传大小限制、基础限流和安全头。

### 2.2 GPU 与 ComfyUI 规则

- 一张 GPU 只运行一个 ComfyUI 实例。
- 两台 3090 是主计算池，常驻 ComfyUI，允许保留模型缓存以降低后续任务延迟。
- 4090 的 ComfyUI 使用与 3090 完全相同的镜像，但默认不参与任务。
- 4090 支持 `DISABLED`、`RESERVED`、`OVERFLOW`、`ACTIVE`、`DRAINING` 五种调度模式。
- 4090 默认模式为 `RESERVED` 或等价的“控制中心、不参与计算”状态。
- 4090 在 `OVERFLOW` 模式下，只有满足队列阈值、最长等待阈值、显存阈值、GPU 利用率阈值、无人工预留等条件时才可参与。
- 4090 完成溢出任务并空闲达到配置时长后，调用 ComfyUI 释放模型显存；管理员也可在 Web 后台手动释放。
- 不得把大量任务提前塞进各 ComfyUI 自带队列。调度器只在节点空闲且其 ComfyUI 本地队列为空时提交一个任务。
- 图片二进制不得放入 PostgreSQL 或 Redis。
- 模型权重不得打进 Docker 镜像，统一通过宿主机只读卷挂载。
- ComfyUI 和自定义节点必须通过 Dockerfile 可复现构建，禁止使用 `docker commit` 作为生产镜像制作方式。

### 2.3 调度器规则

- PostgreSQL 的 `jobs` 表是中央持久队列。
- Redis 只发布“有新任务”“节点空闲”“任务状态变化”等唤醒事件；即使 Redis 丢失，调度器也能从 PostgreSQL 恢复。
- 调度器为单主进程，启动时使用 PostgreSQL advisory lock 保证同一时刻只有一个活跃调度器。
- 调度器是事件驱动的，同时保留可配置的短周期兜底扫描，避免通知丢失后任务长期不调度。
- 调度决策和 ComfyUI 执行监控必须完全异步；不能让一个长任务阻塞其他节点。
- 每个节点最大并发默认为 1。
- 任务先选择主池 3090；4090 只按上述模式参与。
- 需要租户公平性，不能让一个 API 客户一次提交 100 个任务后长期阻塞其他客户。
- 支持优先级、等待时间老化、工作流与节点能力匹配、预计时长、人工置顶。
- 任务领取和状态转换必须放在数据库事务中，并使用行锁或等价机制防止重复领取。
- 调度器必须暴露决策耗时、循环延迟、队列深度、最长等待、各节点忙闲等指标。
- 不得在调度关键路径中同步查询 Loki、Grafana 或执行阻塞 shell 命令。

### 2.4 日志与排障规则

- 所有应用服务输出结构化 JSON 日志。
- 每条关键日志至少包含：
  - `timestamp`
  - `level`
  - `service`
  - `host`
  - `environment`
  - `request_id`
  - `trace_id`
  - `job_id`
  - `tenant_id`
  - `workflow_key`
  - `workflow_version`
  - `node_id`
  - `prompt_id`
  - `attempt`
  - `event`
  - `duration_ms`
  - `error_code`
- 不适用字段可以为空，但字段命名必须统一。
- API Key、密码、Cookie、飞书 Secret、回调签名、完整 Authorization 头不得进入日志。
- 默认不要在集中日志中记录完整用户提示词；记录长度、哈希和可配置的脱敏摘要。原始请求按权限保存到任务目录。
- 每个任务必须保存可诊断的执行快照，包括：
  - 脱敏后的请求 JSON
  - 输入文件元数据和 SHA256
  - 工作流模板版本
  - 实际渲染后的 API 工作流 JSON
  - 上传响应
  - `/prompt` 响应
  - WebSocket 事件摘要
  - `/history/{prompt_id}` 响应
  - 输出文件清单和 SHA256
  - 重试记录
  - 回调记录
- 失败时自动生成诊断信息，并支持从 Web 后台下载单任务诊断包。
- 审计操作必须写入数据库 `audit_logs`，不能只依赖普通日志。
- Docker 本地日志必须配置轮转，Alloy 将 Docker 和 systemd 日志发送到 4090 的 Loki。
- Grafana 中必须提供按 `job_id`、`request_id`、`node_id`、`error_code` 一键检索日志的入口。

---

## 3. 目标仓库结构

可根据现有仓库微调，但最终必须清晰分层，至少包含以下内容：

```text
gpu_control/
├── apps/
│   ├── api/
│   │   ├── src/gpu_control_api/
│   │   ├── tests/
│   │   └── Dockerfile
│   ├── scheduler/
│   │   ├── src/gpu_control_scheduler/
│   │   ├── tests/
│   │   └── Dockerfile
│   ├── node_agent/
│   │   ├── src/gpu_node_agent/
│   │   ├── tests/
│   │   └── systemd/
│   └── web/
│       ├── src/
│       ├── tests/
│       ├── package.json
│       └── Dockerfile
├── packages/
│   ├── common/
│   ├── comfy_client/
│   ├── workflow_engine/
│   └── observability/
├── migrations/
├── docker/
│   └── comfyui/
│       ├── Dockerfile
│       ├── entrypoint.sh
│       ├── healthcheck.py
│       ├── custom_nodes.lock.yaml
│       └── README.md
├── deploy/
│   ├── control-plane/
│   │   ├── compose.yaml
│   │   ├── compose.override.example.yaml
│   │   ├── nginx/
│   │   ├── postgres/
│   │   ├── redis/
│   │   ├── prometheus/
│   │   ├── alertmanager/
│   │   ├── grafana/
│   │   ├── loki/
│   │   └── alloy/
│   ├── gpu-node/
│   │   ├── compose.yaml
│   │   ├── compose.override.example.yaml
│   │   └── alloy/
│   └── registry/
│       └── compose.yaml
├── configs/
│   ├── nodes.example.yaml
│   ├── workflows/
│   ├── models/
│   ├── logging/
│   └── versions.lock.env
├── scripts/
│   ├── gpuctl
│   ├── bootstrap_common_ubuntu.sh
│   ├── bootstrap_control_4090.sh
│   ├── bootstrap_gpu_node.sh
│   ├── install_nvidia_container_runtime.sh
│   ├── preflight.sh
│   ├── build_comfyui_image.sh
│   ├── export_comfyui_image.sh
│   ├── import_comfyui_image.sh
│   ├── push_local_registry.sh
│   ├── sync_models.sh
│   ├── verify_models.sh
│   ├── deploy_control.sh
│   ├── deploy_node.sh
│   ├── backup.sh
│   ├── restore.sh
│   ├── collect_diagnostics.sh
│   └── smoke_test.sh
├── workflows/
│   ├── examples/
│   ├── schemas/
│   └── README.md
├── tests/
│   ├── fake_comfyui/
│   ├── integration/
│   ├── e2e/
│   └── load/
├── docs/
│   ├── 00_REPOSITORY_AUDIT.md
│   ├── 01_BEGINNER_OVERVIEW.md
│   ├── 02_ARCHITECTURE.md
│   ├── 03_NETWORK_AND_PORTS.md
│   ├── 04_PREPARATION_CHECKLIST.md
│   ├── 05_CONTROL_4090_INSTALL.md
│   ├── 06_WORKER_3090_INSTALL.md
│   ├── 07_COMFYUI_IMAGE_BUILD.md
│   ├── 08_IMAGE_DISTRIBUTION.md
│   ├── 09_MODEL_SYNC.md
│   ├── 10_WORKFLOW_ONBOARDING.md
│   ├── 11_FIRST_DEPLOYMENT.md
│   ├── 12_WEB_ADMIN_GUIDE.md
│   ├── 13_PUBLIC_API_GUIDE.md
│   ├── 14_SCHEDULER_DESIGN.md
│   ├── 15_LOGGING_AND_TROUBLESHOOTING.md
│   ├── 16_MONITORING_AND_FEISHU.md
│   ├── 17_BACKUP_AND_RESTORE.md
│   ├── 18_UPGRADE_AND_ROLLBACK.md
│   ├── 19_SECURITY.md
│   ├── 20_FAILURE_RUNBOOK.md
│   ├── 21_LOAD_TEST_AND_CAPACITY.md
│   ├── 22_FAQ.md
│   ├── 23_ACCEPTANCE_CHECKLIST.md
│   ├── IMPLEMENTATION_STATUS.md
│   ├── USER_INPUT_REQUIRED.md
│   └── adr/
├── .env.example
├── .editorconfig
├── .gitattributes
├── .gitignore
├── Makefile
├── pyproject.toml
├── uv.lock
├── README.md
└── CHANGELOG.md
```

不要为了完全匹配目录树而制造无意义文件；但上述能力必须实际存在。

---

## 4. 控制面与节点部署

### 4.1 4090 控制中心 Compose

`deploy/control-plane/compose.yaml` 至少包含：

- `nginx`
- `api`
- `scheduler`
- `web`
- `postgres`
- `redis`
- `prometheus`
- `alertmanager`
- `grafana`
- `loki`
- `alloy`
- `node-exporter`
- `dcgm-exporter`
- `postgres-exporter`
- `redis-exporter`
- `comfyui-4090`，通过 Compose profile 控制是否启动
- 可选的局域网私有 `registry`

要求：

- 数据卷、日志、任务文件、数据库、Grafana、Loki 都有明确宿主机目录。
- 所有服务都有健康检查和合理的 `restart` 策略。
- 生产服务不暴露无关公网端口。
- PostgreSQL、Redis、Prometheus、Loki、ComfyUI 只允许内部网络或指定局域网访问。
- Nginx 只对外开放 80/443，80 默认跳转 443。
- 支持自签名证书的初始部署，并文档化如何替换正式证书。
- API 上传必须流式写入中央 NVMe，不把大文件整体读入内存。
- 任务目录默认：
  `/srv/gpu-control/jobs/YYYY/MM/DD/<job_id>/`
- 4090 模型目录默认：
  `/srv/comfyui/models`
- 4090 ComfyUI 输入输出临时目录与中央任务目录分离。
- `comfyui-4090` 默认不自动参与调度；即使容器已启动，数据库节点模式也必须控制是否可用。
- 当 4090 长时间空闲时支持调用 `/free` 释放模型显存。

### 4.2 每台 3090 Compose

`deploy/gpu-node/compose.yaml` 至少包含：

- `comfyui`
- `node-exporter`
- `dcgm-exporter`
- `alloy`

要求：

- 使用与 4090 完全相同的 ComfyUI 镜像。
- GPU 显式绑定。
- 模型目录只读挂载。
- 输入、输出、临时目录可写并有清理策略。
- 8188 只允许 4090 IP 访问。
- Exporter 端口只允许 4090 访问。
- Alloy 将 ComfyUI 容器日志、Docker 日志、系统关键 journal 发送到中央 Loki。
- 提供 UFW 配置脚本。
- 提供磁盘清理脚本，但不得删除尚未被中央确认下载完成的输出。
- 在节点启动时运行环境与模型清单校验。
- ComfyUI 本地队列出现非本系统任务时，调度器必须将节点标记为 `DEGRADED`，避免混用。

### 4.3 安全节点运维 Agent

实现一个最小化 `node_agent`，用于 Web 后台执行受限运维操作。

要求：

- 它不是任务调度关键路径；Agent 故障不能影响 ComfyUI 正常执行和调度。
- 推荐作为 systemd 服务运行在宿主机，而不是挂载 Docker Socket 的高权限容器。
- 只监听局域网指定地址，UFW 仅允许 4090 访问。
- 使用带时间戳、nonce 和 HMAC 的请求签名，防重放。
- 只支持固定白名单操作：
  - 查看状态
  - 启动/停止/重启指定 ComfyUI Compose 服务
  - 获取受限行数的日志
  - 获取 `nvidia-smi` 诊断快照
  - 获取磁盘、内存、Docker Compose 状态
  - 运行预定义的诊断脚本
- 不允许任意 shell、任意路径、任意 Docker 操作。
- 提供 root 所有的 `/usr/local/sbin/gpu-node-ctl` 白名单包装脚本和最小 sudoers 配置。
- 每次操作都写审计日志。
- Web 后台执行重启等破坏性操作时必须二次确认。

---

## 5. ComfyUI 可复现镜像

实现 `docker/comfyui/Dockerfile` 与相关构建脚本。

要求：

1. 使用明确版本的 NVIDIA CUDA runtime 基础镜像。
2. 固定 Ubuntu、Python、PyTorch、ComfyUI commit、自定义节点 commit。
3. 将版本集中写入 `configs/versions.lock.env` 和 `docker/comfyui/custom_nodes.lock.yaml`。
4. 构建时克隆 ComfyUI 到固定 commit。
5. 自定义节点通过锁文件构建，不允许生产容器启动后在线随意升级。
6. 对每个自定义节点记录：
   - 名称
   - 仓库地址
   - commit
   - 是否启用
   - 依赖安装方式
   - 安全备注
7. 处理自定义节点 Python 依赖冲突，并将最终依赖锁定。
8. 容器默认启动：
   - `--listen 0.0.0.0`
   - `--port 8188`
   - `--disable-auto-launch`
9. 增加 `/system_stats` 健康检查。
10. 增加 OCI labels，记录镜像构建时间、Git commit、ComfyUI commit、依赖锁摘要。
11. 模型、LoRA、VAE、ControlNet、输出图片不得进入镜像。
12. 用户数据、输入、输出、临时目录通过卷挂载。
13. 提供以下分发方式并分别写文档：
   - 方案 A：`docker save` + gzip + `scp` + `docker load`，作为最简单首部署方式。
   - 方案 B：4090 部署局域网私有 Registry，两台 3090 拉取镜像，作为后续迭代推荐方式。
14. 不要使用 `docker commit`。
15. 提供 `scripts/build_comfyui_image.sh`、`export_comfyui_image.sh`、`import_comfyui_image.sh`、`push_local_registry.sh`。
16. 脚本必须 `set -Eeuo pipefail`、支持 `--help`、失败时给出可理解提示、不得输出密钥。
17. 构建完成后生成镜像元数据 JSON 和 SHA256。
18. 提供 `scripts/gpuctl`，作为 Linux 下类似“环境管理启动器”的统一入口，至少支持：
   - `gpuctl doctor`
   - `gpuctl comfy build`
   - `gpuctl comfy start`
   - `gpuctl comfy stop`
   - `gpuctl comfy restart`
   - `gpuctl comfy status`
   - `gpuctl comfy logs`
   - `gpuctl comfy shell`
   - `gpuctl image export`
   - `gpuctl image import`
   - `gpuctl models sync`
   - `gpuctl models verify`
   - `gpuctl deploy control`
   - `gpuctl deploy node`
   - `gpuctl diagnostics`
19. `gpuctl` 必须对初学者友好，显示当前主机、角色、配置文件、即将执行的命令，并对危险操作确认。

---

## 6. 模型与工作流管理

### 6.1 模型

- 每台服务器本地 NVMe 保存同一份模型，不使用运行时跨网络读取模型。
- 默认目录 `/srv/comfyui/models`。
- 实现 `models.manifest.yaml`，记录相对路径、大小、SHA256、所属工作流、是否必需。
- 实现 `sync_models.sh`，从 4090 使用 `rsync` 同步到 3090。
- 实现断点续传、校验、dry-run、只同步清单文件、同步后重新校验。
- 默认不得自动删除远端多余模型；删除必须显式参数并二次确认。
- Web 后台显示每个节点模型清单版本、缺失项、哈希不一致项。
- 模型不进入 Git。

### 6.2 工作流注册中心

实现通用工作流注册和版本机制：

- 工作流必须是 ComfyUI API 格式 JSON。
- 公共用户不能上传任意工作流 JSON。
- 每个工作流版本包含：
  - `workflow_key`
  - `version`
  - API 工作流模板文件
  - 参数 JSON Schema
  - 参数到节点输入的绑定映射
  - 允许的节点 `class_type` 白名单
  - 必需模型清单
  - 必需自定义节点及版本
  - 最小显存
  - 超时时间
  - 可运行节点标签
  - 输出节点定义
  - 是否启用
- 实现安全的参数绑定引擎，只允许修改 manifest 中明确声明的 JSON 路径。
- 不允许客户端控制服务器文件路径、输出目录、任意节点类型或任意 Python 表达式。
- 在启用工作流前，对三台节点执行兼容性检查：
  - `/object_info`
  - 模型清单
  - 自定义节点清单
  - 最小显存
- 提供 CLI：
  - `workflow import`
  - `workflow validate`
  - `workflow diff`
  - `workflow enable`
  - `workflow disable`
  - `workflow test`
- 创建 Mock 用 API 格式工作流夹具用于自动测试。
- 创建局部重绘配置模板和参数 Schema，但不得伪造真实模型名。所有需要用户替换的字段放入 `USER_INPUT_REQUIRED.md`。
- `docs/10_WORKFLOW_ONBOARDING.md` 必须一步一步说明如何从 ComfyUI 前端导出 API 工作流、如何填写绑定、如何验证、如何灰度启用。

---

## 7. 数据库模型与任务状态机

使用 SQLAlchemy 2 async 和 Alembic，至少实现以下表：

- `jobs`
- `job_events`
- `job_attempts`
- `job_artifacts`
- `job_callbacks`
- `callback_attempts`
- `nodes`
- `node_leases`
- `workflows`
- `workflow_versions`
- `workflow_node_compatibility`
- `api_clients`
- `api_keys`
- `rate_limit_policies`
- `idempotency_keys`
- `system_settings`
- `audit_logs`
- `alerts` 或告警事件记录

任务状态至少包括：

```text
RECEIVED
VALIDATING
QUEUED
CLAIMED
UPLOADING
SUBMITTED
RUNNING
DOWNLOADING
SUCCEEDED
RETRY_WAIT
CANCELLING
CANCELLED
TIMED_OUT
FAILED
```

要求：

- 建立明确的合法状态转换表，并在代码中强制验证。
- 每次转换写入 `job_events`。
- 分配任务时在同一事务中：
  - 锁定任务
  - 创建 attempt
  - 获取节点 lease
  - 更新 job 和 node
- 使用唯一约束和幂等键防止重复提交。
- `Idempotency-Key` 相同且请求内容相同时返回原任务；内容不同时返回冲突。
- 所有时间使用 UTC 存储，前端按浏览器时区显示。
- 为队列查询、节点查询、租户查询、状态查询建立合适索引。
- 数据库迁移必须可从空库完整建立，也要有回滚策略说明。
- 审计日志至少记录管理员、动作、目标、前后值摘要、IP、request_id 和结果。

---

## 8. 调度算法

在 `docs/14_SCHEDULER_DESIGN.md` 写清数学规则、伪代码和失败恢复，并在代码中实现。

### 8.1 节点健康与可选条件

节点可调度必须同时满足：

- 节点模式允许。
- 最近心跳未超时。
- ComfyUI `/system_stats` 正常。
- ComfyUI `/queue` 显示无本系统外的运行或排队任务。
- 节点没有有效 lease。
- 工作流兼容性为通过。
- 模型清单匹配。
- 自定义节点匹配。
- 可用显存满足工作流要求。
- 节点不处于冷却、维护或人工预留状态。

### 8.2 3090 优先与 4090 溢出

节点分池：

- `PRIMARY`：两台 3090。
- `OVERFLOW`：4090。
- 可扩展其他标签池。

始终先尝试空闲 PRIMARY 节点。

4090 只有以下情况之一可参与：

1. 管理员把模式设置为 `ACTIVE`。
2. 模式是 `OVERFLOW`，且同时满足：
   - `queue_depth >= OVERFLOW_QUEUE_THRESHOLD`，或
   - `oldest_wait_seconds >= OVERFLOW_WAIT_THRESHOLD`
   - 没有人工预留
   - 没有外部任务占用标志
   - GPU 利用率低于阈值
   - 可用显存高于阈值
   - 当前时间在允许窗口内，若配置了窗口

提供两种 4090 保护方式：

- 管理后台/API 人工 `reserve` 与 `release`。
- 4090 宿主机哨兵文件，例如 `/run/gpu-control/4090.reserved`；存在时调度器绝不使用该 GPU。

不要仅依赖 GPU 利用率猜测外部任务，人工预留是最高优先级。

### 8.3 公平排队

实现简单、可解释、可测试的公平规则：

1. 先按优先级带选择：`critical`、`normal`、`batch`。
2. 任务等待达到配置时长后进行老化，避免低优先级永远饿死。
3. 同一优先级带内，以租户轮转方式选择：
   - 优先选择 `last_scheduled_at` 最早的租户。
   - 再选择该租户最老的兼容任务。
4. 支持租户权重；高权重租户可以获得更高频率，但不能完全压制其他租户。
5. 支持单租户最大排队数和最大运行数。
6. 人工置顶必须有审计记录和上限，不能无限插队。

### 8.4 节点选择

对于同池多个可用节点，按以下顺序：

1. 工作流兼容。
2. 节点健康。
3. 节点空闲。
4. 预计完成时间最短；预计时长使用 `(workflow_key, workflow_version, node_id)` 的 EWMA。
5. 最近最少分配作为最终平局条件。

记录每次调度决策的候选节点、排除原因、最终选择和耗时。正常日志使用摘要，Debug 日志可记录完整候选详情。

### 8.5 事件驱动与领取

伪代码目标：

```python
async def scheduler_loop():
    acquire_singleton_advisory_lock()
    await reconcile_on_startup()

    while not stopping:
        await wait_for_wakeup_or_timeout()

        while True:
            idle_nodes = await load_eligible_idle_nodes()
            if not idle_nodes:
                break

            assignment = await claim_next_assignment(idle_nodes)
            if assignment is None:
                break

            start_background_execution(assignment)
```

`claim_next_assignment` 必须在数据库事务中完成，必要时使用 `FOR UPDATE SKIP LOCKED`。

### 8.6 调度性能

- 提交 API 不等待 GPU 调度完成，成功落盘和入库后返回 202。
- 调度器不传递图片二进制，只处理路径、元数据和小型 JSON。
- 使用持久化 HTTP 客户端连接池。
- 上传、WebSocket、下载全部异步流式处理。
- API 与调度器是独立进程，调度器故障不影响查询已有任务。
- 记录 `scheduler_decision_duration_seconds`、`scheduler_loop_lag_seconds`。
- Mock 压测目标：
  - 100 个并发提交时 API p95 小于 500ms。
  - 调度决策 p95 小于 100ms。
  - 不发生一张 GPU 同时执行两个任务。
  - 调度器重启后无任务永久丢失。
- 如果当前开发机性能不足以满足目标，真实记录测试环境与结果，不伪造。

---

## 9. ComfyUI 客户端与任务执行器

在 `packages/comfy_client` 实现类型化异步客户端。

至少支持：

- `/system_stats`
- `/object_info`
- `/models/{folder}`
- `/upload/image`
- `/upload/mask`
- `/prompt`
- `/queue`
- `/history/{prompt_id}`
- `/view`
- `/interrupt`
- `/free`
- `/ws`

要求：

- 每个提交使用唯一 `client_id`。
- 保存 `prompt_id` 后才进入 `SUBMITTED`。
- WebSocket 消息必须按 `prompt_id` 过滤。
- 支持 `execution_start`、`executing`、`progress`、`executed`、`execution_error`、`execution_success`、状态变化等事件。
- WebSocket 断开时自动重连，并以 `/history` 和 `/queue` 做状态核对。
- 所有 HTTP 调用配置连接、读取、总超时和有限重试。
- 上传前后记录文件大小、SHA256、耗时，不记录图片二进制。
- 输出使用流式下载到临时文件，校验后原子重命名。
- 支持一个工作流多个输出。
- 每个远端临时文件使用 job 唯一目录或前缀，防止任务互相覆盖。
- 结果确认下载后才允许清理远端临时文件。
- 清理失败不应把已成功任务改成失败，但要告警和记录。
- ComfyUI 校验失败时保存 `node_errors`。
- 执行异常保存完整 history 和错误摘要。
- 支持任务取消：
  - QUEUED：数据库直接取消。
  - 上传或提交前：执行器检查取消标记并停止。
  - RUNNING：调用 `/interrupt`，随后核对状态。
- 支持 `/free`，用于 4090 空闲释放模型和管理员操作。
- 对“已提交但调度器崩溃”的任务，启动恢复时先查询 ComfyUI，不得盲目重复提交。

---

## 10. 文件存储

中央存储在 4090：

```text
/srv/gpu-control/jobs/YYYY/MM/DD/<job_id>/
├── request.sanitized.json
├── request.private.json
├── input/
├── workflow/
│   ├── manifest.snapshot.yaml
│   ├── template.snapshot.json
│   └── rendered.api.json
├── comfy/
│   ├── upload.responses.json
│   ├── submit.response.json
│   ├── websocket.events.jsonl
│   ├── history.json
│   └── queue.snapshot.json
├── output/
├── callback/
└── diagnostics/
```

要求：

- 私有请求文件权限严格限制。
- 写文件使用临时文件 + 原子 rename。
- 路径完全由服务端生成，拒绝路径穿越。
- 提供保留策略：
  - 成功任务输入
  - 成功任务输出
  - 失败任务诊断
  - 临时文件
  - Loki 日志
- 提供 dry-run 清理命令和 Web 后台预览。
- 清理操作写审计日志。
- 支持未来切换 S3 的抽象接口，但 V1 只实现本地文件存储，不部署 MinIO。

---

## 11. 公开 API 与管理 API

### 11.1 公共 API

至少实现：

- `POST /api/v1/jobs`
- `POST /api/v1/jobs/inpaint` 便捷接口
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/events`，SSE
- `GET /api/v1/jobs/{job_id}/artifacts`
- `GET /api/v1/jobs/{job_id}/artifacts/{artifact_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `GET /api/v1/workflows`
- `GET /health/live`
- `GET /health/ready`

要求：

- 使用 API Key。
- 支持 `Idempotency-Key`。
- Multipart 文件上传流式落盘。
- 参数基于工作流 JSON Schema 校验。
- 返回结构化错误码。
- 返回 202、任务 ID、状态 URL、事件 URL、近似队列位置和 ETA。
- 支持可选回调 URL，但必须来自管理员预先允许的域名或客户配置，防止 SSRF。
- 回调使用 HMAC 签名，指数退避，保存每次尝试。
- OpenAPI 文档完整，提供 curl 和 Python 示例。
- API Key 只存哈希，支持启用、禁用、轮换。
- Redis token bucket 或等价原子限流。
- 限制单客户每秒请求、总排队数、运行数、每日配额。
- 队列满时明确返回 429 或 503，不无限接收。

### 11.2 管理 API

至少支持：

- 管理员登录和刷新令牌。
- RBAC：管理员、运维、只读。
- 节点列表、详情、健康、模式切换、Drain、Reserve、Release。
- 中断当前任务、释放模型、调用受限 Agent 重启 ComfyUI。
- 任务查询、取消、重试、置顶、下载诊断包。
- 工作流导入、版本、验证、启停和兼容性。
- API 客户、Key、配额、回调配置。
- 调度阈值、保留策略、告警配置。
- 飞书测试消息。
- 审计日志查询。
- 仪表盘汇总。
- 日志跳转链接。
- 所有破坏性操作二次确认并写审计。

---

## 12. Web 管理后台

实现中文后台，不只做静态页面。

页面至少包括：

1. 登录。
2. 总览：
   - 排队数
   - 运行数
   - 今日成功/失败
   - P50/P95 等待和执行时间
   - 最老任务等待
   - 预计清空队列时间
   - 三张 GPU 状态
   - 最近告警
3. 实时任务中心：
   - WebSocket 实时更新
   - 状态、进度、节点、租户、工作流、耗时
   - 搜索、分页、筛选
4. 任务详情：
   - 完整状态时间线
   - 输入缩略图、蒙版、输出
   - 工作流版本
   - attempt
   - ComfyUI prompt_id
   - 错误代码
   - 日志链接
   - 取消、重试、诊断包
5. GPU 节点：
   - ONLINE/OFFLINE/DEGRADED/DRAINING/RESERVED
   - GPU 利用率、显存、温度、功耗
   - 当前任务
   - ComfyUI 队列
   - 模型清单
   - 自定义节点兼容性
   - Drain、Reserve、Release、Interrupt、Free、Restart
6. 工作流管理。
7. API 客户和 Key 管理。
8. 调度策略设置。
9. 告警和飞书。
10. 审计日志。
11. 日志中心：
    - 按 job_id/request_id/node_id/error_code 构造 Grafana/Loki 查询
    - 不需要自己重新实现完整日志搜索引擎
12. 系统设置和保留策略。

要求：

- 对实时事件断线重连。
- 所有表格有加载、空状态、错误状态。
- 破坏性操作确认。
- 不在前端保存明文 API Key；新 Key 只显示一次。
- 提供生产构建和 Nginx 静态部署。
- 提供基础单元测试。
- UI 不需要华丽，但必须清晰、可用、响应式。

---

## 13. 可观测性

### 13.1 指标

API 和调度器至少暴露：

- `gpu_control_http_requests_total`
- `gpu_control_http_request_duration_seconds`
- `gpu_control_jobs_queued`
- `gpu_control_jobs_running`
- `gpu_control_jobs_completed_total`
- `gpu_control_jobs_failed_total`
- `gpu_control_job_wait_seconds`
- `gpu_control_job_execution_seconds`
- `gpu_control_scheduler_decision_duration_seconds`
- `gpu_control_scheduler_loop_lag_seconds`
- `gpu_control_oldest_queued_job_seconds`
- `gpu_control_node_health`
- `gpu_control_node_current_jobs`
- `gpu_control_comfy_request_duration_seconds`
- `gpu_control_comfy_errors_total`
- `gpu_control_callback_attempts_total`
- `gpu_control_callback_failures_total`
- `gpu_control_4090_overflow_assignments_total`

所有高基数字段如 `job_id` 不得作为 Prometheus label。

### 13.2 Grafana

按代码 provisioning：

- 数据源：Prometheus、Loki。
- 仪表盘：
  - 系统总览
  - GPU 节点
  - API
  - 调度器
  - 工作流性能
  - PostgreSQL/Redis
  - 日志排障
- 从任务页面生成带时间范围和过滤条件的 Grafana 链接。

### 13.3 告警

Alertmanager 规则至少包括：

- 节点心跳中断。
- ComfyUI 不可用。
- 队列深度持续过高。
- 最老任务等待超过阈值。
- 失败率异常。
- CUDA OOM 或 ComfyUI execution_error 增加。
- GPU 温度过高。
- GPU 高显存但长时间无进度。
- 4090 在 RESERVED 状态却被调度，作为严重逻辑告警。
- 磁盘不足。
- PostgreSQL/Redis 不可用。
- API 5xx。
- Scheduler loop lag。
- Loki/Alloy 日志采集中断。
- 回调持续失败。
- 备份失败。

### 13.4 飞书 Bridge

实现告警 Webhook 转换服务：

- 接收 Alertmanager Webhook。
- 支持飞书自定义机器人 Webhook 和可选签名 Secret。
- 告警、恢复消息样式不同。
- 去重、分组、静默窗口。
- 严重级别映射。
- 消息包含节点、任务、时间、摘要、建议动作、后台链接。
- 脱敏后再发送。
- 提供 `/admin/alerts/test-feishu`。
- 失败重试并记录指标与日志。
- 飞书配置缺失时系统仍可运行，只显示未配置状态。

---

## 14. 完整日志设计

实现统一日志库，禁止各服务自行随意定义格式。

要求：

- 使用 `structlog` 或等价方案。
- 自动注入 request/trace/job/node 上下文。
- API 中间件生成或透传 `X-Request-ID`。
- Job 创建时生成 `trace_id`。
- 调度、上传、提交、WS、下载、回调均沿用相同 trace。
- 错误必须使用稳定 `error_code`，至少包括：
  - `AUTH_FAILED`
  - `RATE_LIMITED`
  - `INPUT_INVALID`
  - `WORKFLOW_NOT_FOUND`
  - `WORKFLOW_RENDER_FAILED`
  - `WORKFLOW_INCOMPATIBLE`
  - `NODE_UNAVAILABLE`
  - `NODE_RESERVED`
  - `COMFY_HEALTH_FAILED`
  - `COMFY_UPLOAD_FAILED`
  - `COMFY_VALIDATION_FAILED`
  - `COMFY_SUBMIT_FAILED`
  - `COMFY_WS_DISCONNECTED`
  - `COMFY_EXECUTION_ERROR`
  - `COMFY_OUTPUT_MISSING`
  - `OUTPUT_DOWNLOAD_FAILED`
  - `JOB_TIMEOUT`
  - `JOB_CANCELLED`
  - `CALLBACK_FAILED`
  - `STORAGE_ERROR`
  - `DATABASE_ERROR`
  - `INTERNAL_ERROR`
- 所有异常保留堆栈，但返回客户端的错误信息不得泄露内部路径和密钥。
- Docker 日志设置 `max-size` 和 `max-file`。
- Alloy 配置必须给日志增加 `host`、`service`、`container`、`environment` 标签，避免使用高基数 job_id 作为 Loki label；job_id 保留在 JSON 正文中。
- Loki 设置可配置保留时间和磁盘上限。
- 实现 `gpuctl diagnostics job <job_id>` 和后台诊断包。
- 诊断包必须脱敏，不包含 API Key、密码、Secret。
- `docs/15_LOGGING_AND_TROUBLESHOOTING.md` 提供按错误代码、按节点、按任务、按时间排查的命令和图示流程。

---

## 15. 初学者部署脚本与文档

所有 Bash 脚本必须幂等、明确显示当前步骤、检查前置条件，并在失败时告诉用户下一步。

文档每条命令前必须标明在哪台机器执行，例如：

```text
【在 4090 控制中心执行】
【在 3090-A 执行】
【在两台 3090 都执行】
【在管理员电脑执行】
```

### 15.1 公共准备

文档和脚本包含：

- 确认 Ubuntu 版本。
- 主机名。
- 静态 IP 或 DHCP 保留。
- DNS。
- NTP/时区。
- SSH Key。
- 磁盘空间和挂载。
- NVIDIA 驱动检查。
- `nvidia-smi`。
- Docker Engine。
- Docker Compose plugin。
- NVIDIA Container Toolkit。
- 容器 GPU 验证。
- UFW。
- 创建 `/srv` 目录和专用用户。
- 权限。
- Git。
- rsync。
- curl、jq 等工具。

### 15.2 4090 安装文档

`docs/05_CONTROL_4090_INSTALL.md` 必须从空机开始：

1. 填写 IP 和主机名。
2. 安装前置软件。
3. 克隆仓库。
4. 复制 `.env.example`。
5. 生成强密码和密钥。
6. 解释每个必须修改的变量。
7. 执行 preflight。
8. 构建 ComfyUI 镜像。
9. 启动数据库与 Redis。
10. 执行迁移。
11. 创建首个管理员。
12. 启动 API、调度器、Web。
13. 启动 Prometheus、Grafana、Loki、Alertmanager。
14. 配置飞书。
15. 选择是否启动 4090 ComfyUI。
16. 检查每个容器。
17. 打开后台。
18. 验证健康接口。
19. 查看日志。
20. 备份初始配置。

每一步都给出预期输出和常见错误。

### 15.3 3090 安装文档

`docs/06_WORKER_3090_INSTALL.md` 必须从空机开始：

1. 主机名和静态 IP。
2. NVIDIA 驱动。
3. Docker 和 GPU runtime。
4. 创建目录。
5. 获取仓库部署文件。
6. 从 tar 导入镜像，或从局域网 Registry 拉取。
7. 同步模型。
8. 校验模型。
9. 配置中央 IP、节点 ID、日志端点。
10. 配置 UFW。
11. 启动 ComfyUI、Exporter、Alloy。
12. 安装 node_agent systemd。
13. 检查 `/system_stats`。
14. 在 4090 后台发现并批准节点。
15. 运行节点测试工作流。
16. 两台 3090 重复执行。

### 15.4 升级和回滚

- 使用不可变镜像 tag。
- 先 Drain 一个 3090。
- 拉取新镜像。
- 运行自检。
- 灰度一个节点。
- 验证后升级第二节点。
- 最后按需升级 4090。
- 失败一键回滚旧 tag。
- 数据库迁移前备份。
- 迁移兼容策略和回滚说明。
- 工作流版本灰度。
- 自定义节点更新必须重新构建镜像，不能在生产 UI 随意更新。

### 15.5 Windows/WSL 开发

由于仓库可能在 Windows + PyCharm + WSL 中开发，增加文档：

- 推荐在 WSL2 中运行后端开发工具。
- Git 使用 LF。
- 不把 Windows 绝对路径写进部署文件。
- 没有 NVIDIA Linux GPU 时使用 Fake ComfyUI。
- 前端可以在 Windows 或 WSL 启动。
- 生产部署仍只面向 Ubuntu 服务器。

---

## 16. Fake ComfyUI 与自动测试

实现一个可在 CI 和无 GPU 开发机运行的 Fake ComfyUI 服务。

至少模拟：

- `/system_stats`
- `/object_info`
- `/models/{folder}`
- `/upload/image`
- `/upload/mask`
- `/prompt`
- `/queue`
- `/history/{prompt_id}`
- `/view`
- `/interrupt`
- `/free`
- `/ws`

可配置行为：

- 正常执行时长。
- 进度事件。
- 上传失败。
- prompt 校验失败。
- execution_error。
- WebSocket 中断和重连。
- 输出缺失。
- 超时。
- 节点离线。
- 队列被外部任务占用。
- 不同节点速度。
- 4090 外部忙。
- 中断成功或失败。

自动测试至少包括：

1. API 参数校验。
2. API Key 和 RBAC。
3. Idempotency-Key。
4. 状态机合法/非法转换。
5. 参数绑定安全性。
6. 路径穿越防护。
7. 任务写入和文件落盘。
8. 两台 3090 优先。
9. 4090 RESERVED 时永不调度。
10. 4090 OVERFLOW 阈值触发。
11. 人工 Reserve 立即阻止新调度。
12. 每节点并发不超过 1。
13. 租户公平性。
14. 优先级和老化。
15. 节点掉线重试。
16. 提交前失败可安全重试。
17. 提交后调度器崩溃恢复时不盲目重复提交。
18. WebSocket 断线恢复。
19. 任务取消。
20. 多输出下载。
21. 回调签名和重试。
22. 日志脱敏。
23. 诊断包脱敏。
24. 数据库迁移。
25. 100 并发模拟压测。
26. Redis 短暂不可用后仍可从 PostgreSQL 调度。
27. 调度器 singleton lock。
28. Drain 节点不再接新任务。
29. 工作流兼容性不通过时不调度。
30. 清理任务不删除未确认输出。

提供：

- `make test`
- `make lint`
- `make typecheck`
- `make frontend-test`
- `make frontend-build`
- `make integration-test`
- `make load-test`
- `make compose-validate`
- `make verify`

`make verify` 应运行所有不需要真实 GPU 的检查。

---

## 17. 安全要求

- ComfyUI 8188 不暴露公网。
- Redis、PostgreSQL、Prometheus、Loki、Alertmanager、Exporter 不暴露公网。
- Nginx 只开放必要端口。
- API Key 和密码只存安全哈希。
- 管理员密码使用可靠密码哈希。
- JWT/Session Secret 来自环境变量。
- 上传文件检查 MIME、魔数、尺寸、像素数和大小。
- 服务端重新生成文件名。
- 防路径穿越。
- 回调 URL 预配置域名白名单、防内网 SSRF。
- 自定义节点只来自锁文件中的可信来源。
- 生产 ComfyUI Manager 禁止在线任意安装和更新。
- 容器尽量非 root。
- 模型目录只读。
- Node Agent 不挂载 Docker Socket，不支持任意命令。
- `.env`、证书私钥、Webhook、数据库备份不进入 Git。
- 默认安全响应头。
- 管理 API 有权限校验、CSRF/Token 策略和审计。
- 日志脱敏。
- 依赖和镜像版本锁定。
- 提供安全检查清单和网络端口表。

---

## 18. 备份、恢复与灾难恢复

实现并文档化：

- PostgreSQL 定期备份。
- 工作流、配置、锁文件备份。
- 中央任务元数据和重要输出备份。
- 密钥备份说明，但脚本不得明文打印。
- 恢复到新 4090 控制中心。
- Redis 丢失后的恢复。
- 单台 3090 重装后的恢复。
- 两台 3090 都不可用时队列保持。
- 4090 故障时服务不可用的现实边界与恢复步骤。
- 备份校验。
- 定期恢复演练。
- 旧备份清理。
- `backup.sh`、`restore.sh` 必须支持 `--help` 和 dry-run。
- `docs/20_FAILURE_RUNBOOK.md` 按症状给出排查：
  - API 打不开
  - 任务一直排队
  - 单节点不接任务
  - ComfyUI OOM
  - WebSocket 断开
  - 输出找不到
  - Redis 故障
  - PostgreSQL 故障
  - Loki 没日志
  - 飞书没告警
  - 模型不一致
  - 4090 被误用
  - 磁盘满
  - 调度器重启
  - Docker 容器反复重启

---

## 19. 配置与默认值

所有默认值都放到 `.env.example`、数据库系统设置或 YAML 中，不硬编码。

示例默认值：

```dotenv
ENVIRONMENT=production

CONTROL_HOST=192.168.10.10
WORKER_3090_A_HOST=192.168.10.11
WORKER_3090_B_HOST=192.168.10.12

COMFY_PORT=8188
NODE_AGENT_PORT=9201
NODE_EXPORTER_PORT=9100
DCGM_EXPORTER_PORT=9400

JOB_ROOT=/srv/gpu-control/jobs
MODEL_ROOT=/srv/comfyui/models

NODE_HEARTBEAT_INTERVAL_SECONDS=5
NODE_HEARTBEAT_TIMEOUT_SECONDS=20
SCHEDULER_FALLBACK_SCAN_MS=500
NODE_MAX_CONCURRENCY=1

DEFAULT_TENANT_MAX_QUEUED=20
DEFAULT_TENANT_MAX_RUNNING=1
SYSTEM_MAX_QUEUED=500

OVERFLOW_QUEUE_THRESHOLD=20
OVERFLOW_WAIT_THRESHOLD_SECONDS=120
OVERFLOW_4090_MAX_GPU_UTIL_PERCENT=20
OVERFLOW_4090_MIN_FREE_VRAM_MB=20000
OVERFLOW_4090_AUTO_ENABLED=false
OVERFLOW_4090_FREE_MODELS_AFTER_IDLE_SECONDS=120

JOB_DEFAULT_TIMEOUT_SECONDS=900
JOB_MAX_ATTEMPTS=3

SUCCESS_INPUT_RETENTION_DAYS=7
SUCCESS_OUTPUT_RETENTION_DAYS=30
FAILED_DIAGNOSTIC_RETENTION_DAYS=30
LOKI_RETENTION_DAYS=30
```

这些是初始值，必须可在后台调整并有合理边界校验。

---

## 20. README 与文档质量

根 `README.md` 必须面向第一次接触项目的人，包含：

- 一句话说明。
- 三台机器架构图。
- 组件表。
- 3090 优先、4090 保留的说明。
- 5 分钟本地 Fake ComfyUI 开发启动。
- 从空服务器开始应该先读哪篇文档。
- 常用 `gpuctl` 和 `make` 命令。
- 当前版本和状态。
- 不包含真实密钥。
- 文档索引。

每篇部署文档必须：

- 明确目标。
- 明确在哪台机器执行。
- 列出前置条件。
- 一步一条命令。
- 给出预期输出。
- 给出“检查点”。
- 给出失败分支。
- 给出回滚方式。
- 不使用“自行配置”“略”“按需处理”这类对初学者无帮助的表述。
- 对需要用户提供的 IP、域名、模型、工作流、飞书 Webhook 使用醒目标记。
- 提供 Mermaid 架构图、时序图、状态机图和网络图。
- 所有内部相对链接有效。

必须增加 ADR：

- 为什么不用 Kubernetes。
- 为什么不用 Celery。
- 为什么 PostgreSQL 是任务真相来源。
- 为什么 Redis 只做通知和实时事件。
- 为什么一张 GPU 一个 ComfyUI。
- 为什么 3090 是主池、4090 是溢出池。
- 为什么不用 `docker commit`。
- 为什么模型不进入镜像。
- 为什么使用 Loki/Alloy。
- 为什么 Node Agent 不挂载 Docker Socket。

---

## 21. 代码质量与实现约束

后端：

- 全面类型注解。
- Pydantic Settings。
- SQLAlchemy async。
- 数据库事务边界清晰。
- 不使用裸 `except Exception: pass`。
- 所有异常转换成稳定领域错误。
- 资源使用 async context manager。
- HTTP 客户端连接复用。
- 数据库连接池可配置。
- 关闭流程支持 graceful shutdown。
- 调度器停止时不再领取新任务，并保存当前任务状态。
- 测试不依赖执行顺序。
- 对时间、UUID、随机数可注入，便于测试。
- 禁止把 ORM 对象直接返回 API。
- 对所有外部输入做边界验证。

前端：

- TypeScript strict。
- API 类型集中定义。
- 错误统一处理。
- WebSocket 重连。
- 权限路由。
- 不把 Secret 放 localStorage。
- 页面组件合理拆分。
- 基础可访问性。

Docker 与脚本：

- 镜像多阶段构建。
- 不使用 `latest`。
- 健康检查。
- 非 root 可行处使用非 root。
- Bash 使用严格模式。
- ShellCheck 友好。
- Compose 中不写真实密码。
- 生成数据目录在 `.gitignore`。
- `.gitattributes` 强制 shell 脚本 LF。

---

## 22. 具体实施顺序

按下列顺序实际工作，不要只输出计划：

### 阶段 A：审计与骨架

- 阅读全部现有文件。
- 写 `docs/00_REPOSITORY_AUDIT.md`。
- 确定保留/删除。
- 创建最终目录、配置系统、统一开发命令。
- 更新 `.gitignore`、`.editorconfig`、`.gitattributes`。

### 阶段 B：数据库、领域模型、API 基础

- 数据库模型。
- Alembic。
- 状态机。
- API Key、管理员认证、RBAC。
- 文件存储。
- 工作流注册和绑定。
- 公共/管理 API。
- 结构化日志。

### 阶段 C：Fake ComfyUI、客户端与调度器

- Fake ComfyUI。
- ComfyUI async 客户端。
- 节点健康。
- 3090 优先调度。
- 4090 Reserve/Overflow。
- 公平队列。
- 执行、取消、恢复、重试。
- 集成测试。

### 阶段 D：Web 后台

- 登录。
- 总览。
- 任务。
- 节点。
- 工作流。
- API 客户。
- 调度配置。
- 告警。
- 审计。
- 实时更新。

### 阶段 E：Docker、ComfyUI 镜像和部署

- 可复现 ComfyUI Dockerfile。
- 控制中心 Compose。
- 3090 Compose。
- Node Agent。
- `gpuctl`。
- bootstrap、部署、镜像分发、模型同步脚本。
- UFW。

### 阶段 F：日志、监控、告警

- Prometheus。
- Grafana provisioning。
- Loki。
- Alloy。
- Alertmanager。
- 飞书 Bridge。
- Dashboard 和规则。
- 诊断包。

### 阶段 G：完整文档

- 按文档清单逐篇完成。
- 所有命令、预期输出、检查点和排错。
- 文档链接校验。

### 阶段 H：验收

运行并记录：

```bash
make lint
make typecheck
make test
make frontend-test
make frontend-build
make integration-test
make compose-validate
make load-test
make verify
```

若某命令因环境限制不能运行，记录真实错误和服务器验证命令，不得写成通过。

---

## 23. 最终验收标准

最终仓库必须满足：

1. 空数据库可一键迁移。
2. 无 GPU 环境可启动 Fake ComfyUI 完成全链路任务。
3. 100 个模拟并发请求可入队。
4. 同时最多运行两个 3090 任务；4090 默认不运行。
5. 3090 空闲时绝不把普通任务发给 4090。
6. 4090 RESERVED 时绝不调度。
7. 4090 OVERFLOW 仅在阈值和 GPU Guard 都通过时调度。
8. 单节点并发不超过 1。
9. 单租户大量任务不会阻塞其他租户。
10. API 快速返回 202。
11. 调度器重启后可从 PostgreSQL 恢复。
12. Redis 短暂不可用不会永久丢任务。
13. 已向 ComfyUI 提交的任务恢复时不会盲目重复提交。
14. 任务进度能实时显示。
15. 取消、重试、Drain、Reserve、Release 可用。
16. 4090 可手动释放模型。
17. 所有关键日志可按 job_id 串联。
18. 三台机器日志集中到 Loki。
19. Grafana 有任务、GPU、API、调度仪表盘。
20. 告警可发飞书并有恢复通知。
21. Web 可管理节点、任务、工作流、客户和调度设置。
22. 镜像可通过 save/load 部署到两台 3090。
23. 模型可 rsync 并校验。
24. 初学者文档可从空机走到首个成功任务。
25. 升级、回滚、备份、恢复和常见故障有明确步骤。
26. 无真实 Secret、模型、图片、日志进入 Git。
27. 所有已运行测试结果真实记录。
28. 不存在关键功能的空实现。
29. `README.md` 和 `docs/IMPLEMENTATION_STATUS.md` 与代码实际状态一致。
30. `docs/USER_INPUT_REQUIRED.md` 只列真正需要用户提供的外部材料。

---

## 24. 结束时的输出格式

实际修改完成后，在终端回复中给出：

1. 架构变更摘要。
2. 创建、重写、删除的主要文件。
3. 已实现功能清单。
4. 测试和构建命令及真实结果。
5. 未执行或失败的验证及原因。
6. 用户接下来在 4090 上执行的第一条命令。
7. 用户需要提供的真实工作流、模型、自定义节点、IP、域名和飞书配置清单。
8. 安全风险或部署注意事项。
9. 不要只说“已完成”，必须指向具体文件。

现在开始：先检查仓库，再写审计文档，然后直接实施全部阶段。


---

# Codex 第二轮：对 `gpu_control` 重构结果做严格验收并修复

请不要相信上一轮的完成声明。重新从仓库实际文件、测试和 Compose 配置出发做独立验收，并直接修复发现的问题。

重点检查：

1. 是否真的实现了 PostgreSQL 持久队列、自研 async 调度器，而不是隐藏地依赖 Celery。
2. 两台 3090 是否是 PRIMARY，4090 是否默认 RESERVED，OVERFLOW 条件是否全部生效。
3. 是否存在 4090 被误调度、每节点并发超过 1、任务重复领取、租户饥饿等并发漏洞。
4. 调度器重启、Redis 中断、WebSocket 断开、ComfyUI 节点掉线后是否能恢复。
5. 已提交到 ComfyUI 的任务是否会被盲目重试造成重复出图。
6. API 是否流式上传、快速返回 202，图片是否错误地进入 Redis/数据库。
7. ComfyUI 镜像是否由 Dockerfile 可复现构建，是否固定 commit，是否误用 docker commit 或 latest，模型是否误入镜像。
8. 三台机器的 Compose、GPU 绑定、健康检查、卷、端口和 UFW 是否一致可用。
9. Node Agent 是否存在任意命令执行、Docker Socket、签名重放或越权风险。
10. 日志字段是否统一，是否能按 request_id/trace_id/job_id/node_id/prompt_id 串联，是否泄露 Secret。
11. Loki/Alloy、Prometheus/Grafana、Alertmanager/飞书是否有真实配置而不是占位文件。
12. Web 后台是否调用真实 API，实时任务、节点控制、工作流、Key、审计是否可用。
13. 数据库迁移是否能从空库执行，状态机和唯一约束是否完整。
14. Fake ComfyUI 是否覆盖成功、失败、断线、超时、取消、外部占队列、多输出。
15. 100 并发、3090 优先、4090 溢出、公平性等测试是否真实存在并可运行。
16. 文档是否真能让 Linux/Docker 初学者按步骤从空机部署，是否标明机器、预期输出、失败分支和回滚。
17. 所有脚本是否严格模式、幂等、可 `--help`、不会打印 Secret。
18. README、IMPLEMENTATION_STATUS、USER_INPUT_REQUIRED 是否与代码真实状态一致。
19. 是否存在 `pass`、伪实现、关键 TODO、未引用文件、死代码、失效文档链接和未使用配置。
20. 是否有任何测试结果被夸大或伪造。

执行并记录：

```bash
make lint
make typecheck
make test
make frontend-test
make frontend-build
make integration-test
make compose-validate
make load-test
make verify
```

增加必要的缺失测试；发现问题直接修复，不要只写审计报告。最终更新：

- `docs/IMPLEMENTATION_STATUS.md`
- `docs/23_ACCEPTANCE_CHECKLIST.md`
- `CHANGELOG.md`

最后输出真实的通过项、失败项、环境限制、剩余用户输入，以及从 4090 空机开始的第一条部署命令。

