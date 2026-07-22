# 第一轮重构交付、功能与测试报告

日期：2026-07-21  
范围：`gpu_control` 全量替换后的第一轮实现  
结论：当前 Windows/无 GPU 环境内可执行验证通过；生产 Ubuntu/GPU/三机联调仍须按本文和部署手册逐项签字。

## 1. 交付物

- 可运行源码、依赖锁、Alembic 迁移、Compose、Dockerfile、监控配置、脚本、测试和 00-25 文档。
- PostgreSQL 持久任务模型和独立 asyncio scheduler；没有采用 Celery/Flower，理由见 ADR 0002。
- Vue 3 管理台，视觉参考 LiClick 的暗色底、紫粉渐变、细边框、紧凑表格和状态胶囊，但不复制品牌资产。
- 10 张来自真实本地运行页面的截图，不是静态设计稿。
- 原始 pytest、前端 test/lint/format/build、Ruff、mypy、Alembic 日志。
- 《三台 GPU 主机完整部署、联调与验收手册》Markdown 与 PDF。
- 本报告 Markdown 与 PDF。
- 排除虚拟环境、node_modules、缓存、运行数据库和日志后的源码 ZIP，以及 SHA-256。

## 2. 架构和关键约束

| 能力 | 实现 |
|---|---|
| 任务真相 | PostgreSQL jobs/events/attempts/leases/artifacts/callbacks |
| 并发领取 | 事务、`FOR UPDATE SKIP LOCKED`、单 scheduler advisory lock |
| 唤醒/实时 | Redis pub/sub 和限流；Redis 失败不丢 PostgreSQL 任务 |
| 节点槽位 | 每节点数据库 lease，`max_concurrency=1` |
| 主池 | 3090-A、3090-B 为 PRIMARY/ACTIVE |
| 4090 | 默认 OVERFLOW/RESERVED；人工 ACTIVE 或全 Guard OVERFLOW |
| ComfyUI | 固定 commit Dockerfile；API 上传/提交/WS/history/下载/interrupt/free |
| 故障恢复 | 已有 prompt_id 先查 queue/history，不盲目重提 |
| 超时/取消 | watchdog 中断超时 prompt；运行中取消观察器完成 CANCELLING -> CANCELLED |
| 日志 | JSON 字段贯穿 request_id/trace_id/job_id/node_id/prompt_id |
| 可观测性 | Alloy -> Loki；Prometheus/Grafana/Alertmanager/飞书 |
| 节点运维 | HMAC、防重放、白名单命令、systemd、最小 sudoers，不挂 Docker Socket |

## 3. 后端功能汇总

### 3.1 公共 API

- API Key 哈希保存、禁用/过期判断、租户隔离。
- multipart 创建任务；支持 API 工作流版本、参数 JSON、优先级、输入图、蒙版和 callback URL。
- `Idempotency-Key` 同请求返回原任务，不同内容返回冲突。
- 队列/运行配额和 Redis 速率限制；Redis 不可用时持久入队仍成功。
- JPEG/PNG/WEBP 解码校验、最大字节、最大像素、输入与蒙版尺寸一致校验。
- 任务状态、事件 SSE、产物列表/下载、取消。
- callback 仅 HTTPS allowlist；拒绝私网解析和重定向；HMAC 签名；六次指数退避和 attempt 记录。
- 稳定错误码与 `X-Request-ID`。

### 3.2 管理 API

- JWT 登录和 admin/operator/viewer RBAC。
- Dashboard、任务筛选、pin、retry、诊断包。
- Node Drain/Reserve/Release/ACTIVE/OVERFLOW、interrupt、free model、安全 restart。
- 工作流导入、不可变版本、启停。
- API 客户、配额/权重/callback host、一次性 Key。
- 动态调度和保留策略参数；所有变更二次确认并写 AuditLog。
- 告警 webhook、飞书测试、Grafana Loki 精确检索链接。

### 3.3 工作流和文件安全

- 只接受 ComfyUI `Export Workflow (API)` 格式；拒绝 UI `nodes/links` 格式。
- JSON Schema 参数白名单、binding 路径限制、class type 白名单。
- 模型/自定义节点/最低显存/节点标签/输出节点/超时声明。
- 原子写入、路径穿越防护、安全文件名、流式 SHA-256。
- 真实模型和工作流未提供，因此只交付机制、验证器和示例 schema，不伪造生产业务材料。

## 4. Scheduler 功能汇总

1. 单主锁和周期扫描；Redis 消息只缩短唤醒延迟。
2. 对空闲、健康、模式允许、兼容的节点计算候选。
3. 租户轮转、优先级和 aging，避免大客户长期占用。
4. 先选 3090 PRIMARY；4090 OVERFLOW 必须同时通过：自动开关、队列或最长等待阈值、非人工预留、无 sentinel、利用率、剩余显存、允许时段。
5. 事务领取一个 job 并创建 lease/attempt；每节点最多一个 active lease。
6. 上传 input/mask、渲染模板、提交一个 prompt、记录 prompt_id。
7. 监听 WebSocket，持久化进度，完成后拉 history 和输出并校验。
8. 运行中取消由独立观察器调用 `/interrupt`；工作流超时由 watchdog 处理。
9. 可重试错误按 attempts 和 not_before 退避；不可重试错误直接失败。
10. 重启恢复先查询 ComfyUI queue/history；未知状态保守保留，不直接复制 prompt。
11. callback 与任务执行分离，stuck delivery 会恢复为 retry。
12. 指标区分 decision duration、loop lag、队列和 overflow。

## 5. 管理台页面

| 页面 | 可见能力 |
|---|---|
| 登录 | 独立暗色登录页、错误反馈 |
| 系统总览 | 六项指标、三节点表、队列趋势、告警、最近任务 |
| 任务中心 | 状态/工作流/节点/进度、失败与超时重试入口 |
| GPU 节点 | 三节点指标、Drain/Reserve/Release/interrupt/free/restart |
| 工作流 | 版本、启用、显存、超时、模型依赖 |
| API 客户 | 配额、并发、权重、创建客户和一次性 Key |
| 调度策略 | overflow/保留/阈值与 retention 配置 |
| 告警 | 告警列表和飞书测试 |
| 审计日志 | actor/action/target/before/after/result |
| 日志中心 | 按四类关联 ID 跳转 Grafana Loki |
| 系统设置 | 受边界校验的动态设置 |

响应式验证覆盖默认桌面视口和 390x844 移动视口；移动端折叠导航为图标栏，内容无水平页面溢出。宽表在自身容器内滚动，不撑破页面。

## 6. 部署与运维能力

- 控制面和工作节点 Compose；全部基础镜像固定版本。
- ComfyUI 多阶段 Dockerfile，固定 repository commit、自定义节点 commit 和 Python lock；模型只读挂载。
- `gpuctl` 统一 doctor、build、start/stop/restart/status/logs、image export/import、model sync/verify、deploy、diagnostics、workflow。
- 4090/3090 bootstrap、NVIDIA Container Toolkit、控制面/节点 UFW、Node Agent 安装、节点 inventory 导入。
- save/load 镜像带 SHA；rsync 模型支持 partial/append-verify，远端再次 SHA。
- PostgreSQL/配置备份、SHA 校验、恢复二次确认、升级和回滚手册。
- Loki 3100 只绑定控制 IP，由 UFW 仅允许两台工作机；PostgreSQL/Redis 不发布主机端口。

本轮部署复查修复了：控制面 internal 网络阻断 LAN 出站、Loki 未发布给 Alloy、Node Agent 无完整安装、3090 UFW 文档参数错误、非默认 IP 缺少节点清单应用、模型 manifest 默认路径错误、镜像导入导出文档参数错误。

## 7. 已执行测试和结果

| 检查 | 实际结果 |
|---|---|
| Ruff format/check | 通过 |
| mypy strict | 22 个源文件无问题 |
| Python pytest | 44 passed；包含 100 并发、API/RBAC/幂等、Redis 降级、Fake ComfyUI、调度、状态机、Node Agent 独立最小配置/防重放、回调、图片、超时 |
| Vitest | 1 passed |
| ESLint | 通过，0 warning |
| Prettier | 全部匹配 |
| Vue TypeScript/Vite build | 2038 modules transformed，生产构建成功 |
| Alembic | 空 SQLite 到 `20260721_0001 (head)` |
| YAML/JSON | 仓库配置可解析 |
| Markdown 链接 | 本地链接可解析 |
| 浏览器 | 真实登录/多页面/刷新交互；无 console error/warn；移动端无页面横向溢出 |

原始日志位于：

- `artifacts/pytest-2026-07-21.txt`
- `artifacts/frontend-2026-07-21.txt`
- `artifacts/static-and-migration-2026-07-21.txt`

## 8. 浏览器 QA 证据

测试流：`/login` -> 输入本地演示管理员 -> `/` -> 刷新数据 -> 任务/节点/工作流/客户/策略/审计/日志 -> 390x844 总览。

| 检查 | 结果 |
|---|---|
| 页面 identity/title | `GPU Control`，路由正确 |
| 非空/无框架错误层 | 通过 |
| 真实 API 数据 | 3 节点、12 任务、客户、工作流、设置、审计均可见 |
| 登录交互 | 错误账号显示错误；正确账号进入总览 |
| 刷新交互 | 唯一按钮可操作，数据仍一致 |
| 控制台 | error/warn 均为空 |
| 桌面 overflow | `scrollWidth == clientWidth` |
| 移动 overflow | 375 == 375（设备内容宽度） |

演示库和演示密码仅在本地截图期间使用，数据库位于 gitignore 范围，不在交付 ZIP 中。截图中的 workflow/model 名明确带 `demo`，不能作为生产材料。

## 9. 最终截图

旧迭代过程截图已在交付清理时删除，只保留通过最终浏览器检查的 [LiClick 风格系统总览](../artifacts/screenshots/gpu-control-11-final-dashboard.png)。节点、任务、工作流、客户、调度、审计和日志页面的功能证据以自动测试结果、API 路由和 `docs/29_PRODUCT_RELEASE_AND_TEST_REPORT.md` 为准。

## 10. 未验证和不得误报的项目

- 当前主机没有 Docker daemon、NVIDIA GPU、PostgreSQL/Redis 服务和 Ubuntu systemd/UFW。
- 未执行三台真实主机的驱动、NVIDIA Container Toolkit、Compose、DCGM、Node Agent、网络与权限验证。
- PostgreSQL `SKIP LOCKED` 的真实竞争和 advisory lock 只实现并静态/逻辑测试；SQLite 不替代 PostgreSQL 压测。
- 未提供真实 ComfyUI API workflow、模型、自定义节点、三机 IP、TLS、飞书密钥和 callback endpoint。
- 未验证真实推理、输出质量、显存阈值、吞吐、4090 OVERFLOW 和进程级故障恢复。
- 未验证三机 Alloy/Loki、Prometheus targets、飞书触发/恢复、备份恢复演练。
- 当前环境没有 Docker/Make/shellcheck；WSL 无可用发行版，因此 shell 脚本未做真实 Linux 执行。
- `npm install` 报 4 个依赖公告；精确 `npm audit` 因外部元数据传输权限被拒，不能声称依赖安全审计通过。
- 目录只有不完整 `.git`，没有 HEAD，无法提供可靠 git diff。

## 11. 第二轮审计建议顺序

1. 不信任本报告，先解压 ZIP、核对 SHA、检查文件清单和 Secret/大文件。
2. 运行 Ruff、mypy、pytest、Vitest/lint/format/build。
3. 新 PostgreSQL 实例运行 Alembic，检查约束、索引、`SKIP LOCKED` 和 advisory lock。
4. 用三个 Fake ComfyUI 运行服务级 100 并发、取消、超时和 scheduler kill/restart。
5. 审查 callback SSRF/DNS rebinding/HMAC、Node Agent replay/sudoers、文件路径和图片炸弹。
6. `docker compose config` 检查全部 profile、volume、network、port 和 healthcheck。
7. 三台 Ubuntu 按 `docs/24_THREE_HOST_DEPLOYMENT_AND_ACCEPTANCE.md` 从空机演练。
8. 用真实但非敏感工作流验证首单、日志关联、备份恢复和告警恢复。
9. 将所有发现直接修复并补测试，再更新 `IMPLEMENTATION_STATUS` 和验收表。

## 12. 最终界面证据

![最终系统总览](../artifacts/screenshots/gpu-control-11-final-dashboard.png)

最终浏览器验收同时检查了节点、调度页面以及 390×844 无横向溢出；为保持仓库干净，不再保留每次迭代的重复 PNG。
