# GPU Control 成品功能、核心逻辑与测试报告

版本：1.1.0  
日期：2026-07-22  
结论：代码与无 GPU 环境验证已达到“三机现场部署候选版本”；真实 Ubuntu/NVIDIA/模型/工作流验收必须按《当天部署与联调手册》执行后才能标记生产通过。

## 1. 成品范围

本系统不是 ComfyUI 的替代品，而是三台单 GPU ComfyUI 上方的任务、路由和运维控制层：

- PostgreSQL 持久任务队列，Redis 仅做唤醒和实时通知；
- FastAPI 公共 API、后台 API、API Key、JWT 管理登录、幂等、配额和回调；
- 单主 asyncio Scheduler，事务领取、租约、重试、恢复、取消和超时；
- 3090-A/3090-B 主池，4090 默认 RESERVED，可手工 ACTIVE 或受 Guard 控制的 OVERFLOW；
- ComfyUI HTTP/WebSocket 客户端与 Fake ComfyUI；
- Vue 3 LiClick 风格管理台；
- Dockerfile 固定构建的统一 ComfyUI 镜像，模型目录外置；
- Node Agent、`gpuctl`、镜像导入导出、模型同步、诊断、备份恢复；
- Prometheus、Alertmanager、Grafana、Loki、Alloy 三机日志与指标。

Celery/Flower 未采用。三张 GPU、最多三个并行槽位，不需要通用分布式任务框架；PostgreSQL 是唯一任务真相，调度与恢复语义更直接。

## 2. 主要管理功能

| 页面/接口 | 已实现功能 |
|---|---|
| 总览 | 排队、运行、今日成功/失败、最老等待、预计清空、7 小时趋势、节点、活动告警、最近任务 |
| 任务 | 状态/进度/节点、管理员取消、失败重试、单任务诊断 ZIP、事件与 artifacts |
| 节点 | Drain、Reserve、Release、启动/停止 ComfyUI、中断、释放模型、安全重启、GPU/显存/槽位 |
| 工作流 | 导入注册包、API 格式验证、不可变版本、自动计算三节点兼容性、启停确认 |
| 客户 | 客户配额/并发/权重、创建 API Key、Key 仅显示一次 |
| 调度 | 4090 自动溢出开关、队列/等待/利用率/显存/时段阈值，数据库动态生效 |
| 告警 | Alertmanager 鉴权入库、去重、持久投递状态、异步飞书重试、测试消息 |
| 日志 | 按 job_id/request_id/node_id/error_code 构造 Grafana Loki 查询 |
| 审计 | 操作者、动作、目标、前后值、原因、来源 IP、request_id |

![LiClick 风格 GPU Control 最终总览](../artifacts/screenshots/gpu-control-11-final-dashboard.png)

## 3. 核心数据与状态机

关键表包括 `jobs`、`job_events`、`job_attempts`、`node_leases`、`nodes`、`workflows`、`workflow_versions`、`workflow_node_compatibility`、`api_clients`、`api_keys`、`idempotency_keys`、`job_callbacks`、`alerts` 和 `audit_logs`。

主要状态链：

```text
RECEIVED -> VALIDATING -> QUEUED -> CLAIMED -> UPLOADING
         -> SUBMITTED -> RUNNING -> DOWNLOADING -> SUCCEEDED

异常出口：RETRY_WAIT -> QUEUED、FAILED、TIMED_OUT、CANCELLING -> CANCELLED
```

每次转换同时写 `job_events`；任务行保存 request_id、trace_id、node_id、prompt_id、attempt_count、错误码和目录。Redis 丢失不会丢任务，Scheduler 的周期扫描会重新发现 PostgreSQL 中的工作。

## 4. 事务领取与单节点单槽位

调度器只在发现空闲节点后领取一项任务，不提前向 ComfyUI 堆几十个 prompt。生产 PostgreSQL 的核心语义为：

```sql
SELECT ... FROM jobs
WHERE status = 'QUEUED'
ORDER BY effective_priority, tenant_fairness, created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

领取事务中同时：

1. 锁定候选节点行；
2. 再检查 mode、health、心跳、外部队列、当前槽位；
3. 检查工作流兼容性和租户 `max_running`；
4. 建立唯一活动 lease；
5. `current_jobs + 1`；
6. 任务转 `CLAIMED` 并提交。

结束、失败、取消或恢复时释放 lease 并减少槽位。即使多个 Scheduler 实例误启动，单主 advisory lock 和行锁也阻止同一任务/槽位被重复领取。

## 5. 公平、优先级与 3090/4090 算法

候选任务按基础优先级和等待老化形成有效优先级；同优先级下选择当前未达到运行上限、最近最少被调度的租户，再按创建时间 FIFO。若第一个租户已满，查询会继续排除它寻找其他可运行租户，避免队首阻塞。

节点选择顺序：

```text
可用 PRIMARY 3090 -> 最久未分配节点
否则 4090 ACTIVE -> 可立即使用
否则 4090 OVERFLOW -> 必须通过全部 Guard
否则不领取，任务继续留在 PostgreSQL
```

OVERFLOW Guard：自动开关打开；队列数量或最长等待超过阈值；4090 未人工保留；无 sentinel；健康在线；无外来队列；GPU 利用率不高于阈值；空闲显存不低于阈值；在允许时段内。选择节点之后、事务锁定节点之后会再次检查，减少检查与领取之间的竞争窗口。

## 6. 幂等、上传与真实工作流

同一租户和 `Idempotency-Key` 在事务中串行检查。请求摘要包含工作流、参数和上传文件 SHA-256：

- 相同 key + 相同摘要返回原 job；
- 相同 key + 不同摘要返回 409；
- 并发竞争由租户事务锁和数据库唯一约束收口；
- 输入先写 `.staging`，完整校验后原子移动到正式任务目录；
- 失败或竞争落败会清理 staging 和孤儿目录。

图片执行安全文件名、大小、真实解码、像素上限和 image/mask 尺寸一致性检查。真实工作流必须是 `Export Workflow (API)`，模板在入队时按白名单 bindings 注入参数，未知字段和非允许 class type 被拒绝。导入后自动生成每个节点的显存/标签兼容记录，没有兼容节点不能启用。

## 7. 维护操作协议

释放模型、停止和重启前先事务锁定节点并切为 `DRAINING`。若仍有活动 lease/任务，操作返回 409，节点保持 DRAINING，等待任务结束后管理员再次执行。中断会先阻止新领取、标记活动任务取消并落库，再调用 ComfyUI `/interrupt`。启动/停止/重启通过每节点独立 HMAC 密钥调用 systemd Node Agent；Agent 只能执行固定白名单命令。

## 8. 镜像一致性

统一 Dockerfile 固定：Ubuntu CUDA runtime、Python、PyTorch CUDA wheel、ComfyUI 完整 commit、自定义节点完整 commit、Python requirements。运行容器没有临时安装步骤，不使用 `docker commit`。构建后导出 `tar.gz + SHA256`，两台 3090 导入同一归档并核对 Docker image ID。

当前生产模型独立保存在 `/opt/imageclip/models` 与
`/opt/modelviewcreator/model`，以只读方式挂载到容器。ImageClip 自带
`models.manifest.yaml`，ModelViewCreator 使用
`configs/modelviewcreator.models.manifest.yaml`；`scripts/verify_comfy_projects.sh`
统一校验 9 个实际文件的字节数和 SHA-256。空清单或 Git LFS 指针都会失败，避免
“没有真实模型却显示校验通过”。

## 9. 日志与诊断链路

API 中间件只生成一次 request_id，并用于响应头、任务记录、审计和结构化日志；Scheduler 继续绑定 job_id、node_id、prompt_id。三台 Alloy 采集 Docker 与 systemd journal，推送到主控 Loki；Grafana 可以用四类 ID 串联一次任务。诊断包括主机资源、GPU、Compose 状态和任务标识，敏感键按规则脱敏。

## 10. 本机真实测试结果

截至 2026-07-22 已执行：

- Ruff：通过；
- mypy strict：22 个源文件无问题；
- pytest：51 passed（34.20 秒），包含 100 并发入队、100 个同幂等 key 只产生一个 job/目录、跨租户隔离、稳定 request_id、告警鉴权去重、工作流兼容、动态溢出设置、节点单槽位和恢复逻辑；
- 前端 ESLint：通过；
- Prettier：通过；
- Vitest：1 passed；
- Vue TypeScript + Vite production build：通过，2038 modules transformed；
- 浏览器：管理员真实登录演示 API；总览、节点、调度页面可读；桌面无 console warning/error；390×844 无横向溢出；最终 UI 截图已保存；
- 配置：部署目录与 configs 下 12 个 YAML 文件全部解析通过；
- Alembic：空 SQLite 升级到 `20260722_0002 (head)` 通过。

没有 Docker daemon、Linux、NVIDIA GPU、真实模型和业务 API 工作流，因此没有声称以下项目已通过：三机 Compose、systemd/UFW、NVIDIA Container Toolkit、真实推理、PostgreSQL 的真实并发锁、Loki 跨机推送、飞书和备份恢复演练。

## 11. 部署前必须提供

1. 三台固定 IP 和部署 SSH 用户；
2. 真实 ComfyUI API 工作流注册包；
3. checkpoint/VAE/LoRA/ControlNet 清单、大小和 SHA-256；
4. 自定义节点仓库、完整 commit 和 requirements lock；
5. 首单输入图、蒙版和期望业务参数；
6. 是否启用 4090 自动 OVERFLOW 及阈值；
7. 可选的飞书 Webhook/Secret、域名/正式证书。

这些外部材料不影响控制面部署，但没有真实工作流和模型就不能宣称业务出图完成。

## 12. 交付路径

- 当天执行手册：`docs/28_TODAY_DEPLOYMENT_MANUAL.md`
- 本报告：`docs/29_PRODUCT_RELEASE_AND_TEST_REPORT.md`
- 单文件 PDF：仓库根目录 `GPU_CONTROL_成品部署联调与核心逻辑手册.pdf`
- 最终界面截图：`artifacts/screenshots/gpu-control-11-final-dashboard.png`
- 现场状态记录：`docs/IMPLEMENTATION_STATUS.md`
