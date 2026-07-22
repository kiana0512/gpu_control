# 核心代码、算法与并发审计附录

本章面向第二轮源码审计人员，描述当前实现实际采用的算法、持久化点、并发假设和失败边界。伪代码用于解释，关键字段和函数名与仓库一致。最终判断必须以源码和实机故障注入为准。

## 1. 全局不变量

系统正确性依赖以下不变量。第二轮测试应围绕“不变量能否被竞态或故障打破”设计，而不是只看正常页面。

| 编号 | 不变量 | 实现位置 |
|---|---|---|
| I-01 | PostgreSQL 是 job 状态、节点占用和交付状态的唯一真相 | models、repository、scheduler |
| I-02 | Redis 消失只增加唤醒延迟，不能丢 job | API notify 降级、scheduler fallback scan |
| I-03 | 一个节点任意时刻最多一个 active lease | node row lock、current_jobs、NodeLease |
| I-04 | PRIMARY 有合格空闲节点时不使用 OVERFLOW | `choose_node` 的候选池优先级 |
| I-05 | 4090 OVERFLOW 必须同时通过全部 Guard | `overflow_exclusion` |
| I-06 | 已拿到 prompt_id 的 job 不自动重提 | `execute(recovering=True)`、`fail_job` |
| I-07 | 终结状态不可回到运行态 | `ALLOWED_TRANSITIONS` |
| I-08 | 每次状态变更同时追加顺序事件 | `transition_job` |
| I-09 | 工作流参数只能写入声明的 node.inputs 字段 | workflow schema + bindings |
| I-10 | Node Agent 不能执行任意 shell | HMAC middleware + fixed argv map |

## 2. 节点选择算法

### 2.1 基础排除

对每个节点依次判断，命中任一条件即排除：

```text
mode in {DISABLED, RESERVED, DRAINING}
OR health != ONLINE
OR heartbeat missing/expired
OR current_jobs >= max_concurrency
OR foreign_queue_detected
OR external_busy
OR manual_reserved
```

当前实现返回明确的 exclusion reason，写入调度日志，方便解释“为什么没派到某节点”。心跳时间统一转为 UTC 后计算过期秒数。

### 2.2 4090 OVERFLOW 布尔条件

设：

- `A`：自动 overflow 已开启。
- `R`：人工 reserve 为 false。
- `S`：`4090.reserved` 哨兵文件不存在。
- `Q`：`queue_depth >= queue_threshold`。
- `W`：`oldest_wait >= wait_threshold`。
- `U`：`gpu_util < max_gpu_util`。
- `V`：`free_vram >= min_free_vram`。
- `T`：当前时间处于允许窗口。
- `B`：基础健康、心跳、槽位和外部占用检查通过。

则 OVERFLOW 合格条件是：

```text
eligible_4090 = B AND A AND R AND S AND (Q OR W) AND U AND V AND T
```

注意比较边界：利用率使用严格小于，达到阈值即拒绝；剩余显存使用大于等于；队列深度或最长等待任一达到阈值即可。跨午夜时间窗通过 `current >= start OR current <= end` 判断。

### 2.3 PRIMARY 优先与负载轮转

```python
for node in nodes:
    if base_exclusion(node):
        continue
    if node.pool == "PRIMARY":
        primary.append(node)
    elif not overflow_exclusion(node):
        overflow.append(node)

candidates = primary if primary else overflow
candidates.sort(key=(last_assigned_at_or_min, node_id))
return candidates[0]
```

这个算法先在池级别做绝对优先，再在同一池内使用最久未分配节点，时间相同时用 node_id 保证确定性。节点数固定为 3，选择复杂度可视为常数；一般化后为 `O(N log N)`。

### 2.4 已识别的调度竞态

`schedule_available` 先读节点和 Guard，再 rollback，然后 `claim_next_job` 在新事务中锁 node 并只复核槽位。两步之间 sentinel、人工 reserve、GPU 利用率或心跳可能变化。因此当前 Guard 不是和领取 job 同一事务内的最终原子检查。

审计评级：`REVIEW-HIGH`。生产前建议把关键数据库 Guard 条件放进带 node row lock 的最终领取函数，并在提交 prompt 前再次做快速安全检查；sentinel 属于文件系统状态，至少要在领取后、提交前二次读取。第二轮应故障注入证明竞态窗口不会让受保护的 4090 接单。

## 3. 队列公平、优先级老化与配额

### 3.1 有效优先级

基础优先级：BATCH=0、NORMAL=1、CRITICAL=2。等待每跨过 `aging_seconds` 提升一级，最高为 2：

```text
effective_rank = min(2, base_rank + floor(waited_seconds / aging_seconds))
if pinned: effective_rank += 10
```

因此 pinned 形成独立最高频段；普通 batch 最终可老化到 critical 频段，避免永久饥饿。

### 3.2 租户轮转

在最高有效频段内，排序键为：

```text
(tenant.last_scheduled_at, job.created_at, job.id)
```

最久未获得调度的租户先选，同租户内更早 job 先选，ID 用于稳定排序。领取后更新 `last_scheduled_at`。

### 3.3 运行并发配额

领取时统计租户处于 CLAIMED、UPLOADING、SUBMITTED、RUNNING、DOWNLOADING、CANCELLING 的 job 数。如果达到 `max_running`，从当前最多 200 个已锁候选中排除该租户并重新选择。

已识别问题：当前代码只检查第一次选中租户的 `max_running`；重新选择出的第二个租户没有再次循环复核。理论上第二租户也可能已经满额而被领取。审计评级：`REVIEW-HIGH`。建议改为循环弹出不合格租户直到找到满足配额的候选，并增加“多个连续满额租户”测试。

### 3.4 权重字段差异

`ApiClient.weight` 存在于模型和管理 UI，但当前 `choose_fair_job` 没有把 weight 纳入排序或配额。当前实现是无权重的近似轮转，不应宣称加权公平。审计评级：`REVIEW-MEDIUM`。第二轮要么实现 weighted fair queueing，要么删除/冻结该配置并修正文档。

## 4. PostgreSQL 事务领取

核心领取过程：

```text
BEGIN
  SELECT node WHERE id=:node_id FOR UPDATE
  if current_jobs >= max_concurrency: RETURN NONE

  SELECT up to 200 compatible QUEUED jobs
    JOIN enabled workflow_version
    JOIN workflow_node_compatibility
    WHERE not_before IS NULL OR not_before <= now
    ORDER BY pinned DESC, created_at ASC
    FOR UPDATE SKIP LOCKED

  select fair job and enforce tenant max_running
  create 256-bit random lease token
  insert node_lease(active=true, expires_at=now+lease_seconds)
  increment node.current_jobs
  increment job.attempt_count and bind node_id
  transition job -> CLAIMED and append event
  insert job_attempt with lease token
COMMIT
```

`SKIP LOCKED` 的作用：另一个事务已经锁住的候选不会阻塞当前消费者，而是跳过继续找可领取行。它适合队列消费者，但不提供公平性；公平性由候选排序和租户轮转完成。

事务正确性检查点：

- node row、job rows、lease、attempt、event 和计数必须同一 commit。
- 事务失败应整体 rollback，不能出现 current_jobs 增加但 lease 不存在。
- `release_lease` 同时锁 node 和 active lease，将 active=false 并把 current_jobs 减到不小于 0。
- 应在 PostgreSQL 增加或确认数据库级约束：同节点至多一个 active lease、同 job 至多一个 active lease、current_jobs 合法范围。

当前迁移通过 `Base.metadata.create_all` 创建完整 schema。它适合作为首版基线，但第二轮应核对生成的 PostgreSQL DDL、索引和部分唯一约束，不能只凭 ORM 定义判断。`downgrade` 会 drop_all，生产回滚默认应恢复备份而不是直接执行。

## 5. Scheduler 单主与循环

### 5.1 单主锁

启动时执行 PostgreSQL session-level advisory lock：

```sql
SELECT pg_try_advisory_lock(:scheduler_lock_id);
```

拿不到锁即退出，避免两个 scheduler 同时派发。进程结束时调用 unlock；异常断开数据库连接时 PostgreSQL 会释放 session lock。第二轮应确认连接池不会在主循环期间归还持锁连接，并以两个进程实测只有一个存活。

### 5.2 唤醒模型

```text
while running:
    schedule_available()
    record loop lag
    clear wakeup event
    wait for Redis pub/sub wakeup OR fallback_scan_timeout
```

API 入队后向 Redis 发布 wakeup；Redis listener 失败只记录 degraded 日志并重试。fallback scan 保证最终仍扫描 PostgreSQL。核心正确性不依赖消息恰好送达。

### 5.3 一次调度循环

```text
snapshot queued count + oldest wait
while queue not empty:
    read all nodes
    choose eligible node
    transactionally claim one compatible job
    spawn execute(job_id)
    repeat until no queue or no node
observe decision duration
```

循环没有向 ComfyUI 批量预提交；并行度来自最多三个独立 execute task，每个受数据库 node slot 限制。

## 6. Job 状态机

主要合法转换：

| 当前状态 | 允许目标 |
|---|---|
| RECEIVED | VALIDATING、CANCELLED、FAILED |
| VALIDATING | QUEUED、CANCELLED、FAILED |
| QUEUED | CLAIMED、CANCELLED |
| CLAIMED | UPLOADING、RETRY_WAIT、CANCELLING、TIMED_OUT、FAILED |
| UPLOADING | SUBMITTED、RETRY_WAIT、CANCELLING、TIMED_OUT、FAILED |
| SUBMITTED | RUNNING、DOWNLOADING、CANCELLING、TIMED_OUT、RETRY_WAIT、FAILED |
| RUNNING | DOWNLOADING、CANCELLING、TIMED_OUT、RETRY_WAIT、FAILED |
| DOWNLOADING | SUCCEEDED、RETRY_WAIT、TIMED_OUT、FAILED |
| RETRY_WAIT | QUEUED、CANCELLED、FAILED |
| CANCELLING | CANCELLED、TIMED_OUT、FAILED |
| SUCCEEDED/CANCELLED | 无 |
| TIMED_OUT | RETRY_WAIT、FAILED |
| FAILED | RETRY_WAIT |

每次 `transition_job`：先调用 `require_transition`；查询该 job 最大 event sequence；更新 job.status/updated_at；首次 RUNNING 写 started_at；终结状态写 finished_at；追加 JobEvent(previous_status, status, event, details)。非法边直接抛出异常。

并发风险：event sequence 通过 `max(sequence)+1` 计算。只有 job row 已被锁或单执行者假设成立时才安全。第二轮应确认所有可能并发变更同一 job 的 API、watchdog 和 executor 都有一致锁策略，并考虑数据库唯一约束 `(job_id, sequence)`。

## 7. 执行主链与持久化点

### 7.1 新任务

```text
load job/node/workflow
bind log context(job_id, trace_id, tenant_id, workflow, node_id, attempt)
start timeout watchdog

transition UPLOADING -> COMMIT
upload input/mask to node ComfyUI
persist upload.responses.json atomically
load rendered.api.json
POST /prompt
save prompt_id
transition SUBMITTED -> persist submit.response.json -> COMMIT

if cancel requested: interrupt -> CANCELLING -> CANCELLED -> release lease
transition RUNNING -> COMMIT
start cancellation watcher
consume WebSocket progress; clamp to <100; commit and publish
GET history
transition DOWNLOADING -> COMMIT
download each output; safe basename; stream SHA-256
insert artifact(size, sha256, relative path, confirmed)
progress=100; transition SUCCEEDED; release lease -> COMMIT
```

最重要的“去重边界”是 prompt_id 获取后立即写入数据库并提交。外部 `/prompt` 成功与本地 commit 之间仍存在不可消除的分布式双写窗口：若进程在 ComfyUI 接收 prompt 后、保存 prompt_id 前崩溃，本地不知道远端 ID。当前会把 UPLOADING 恢复为重试，可能重复出图。

审计评级：`REVIEW-HIGH`。可缓解方案包括使用稳定 `client_id/job_id` 查询远端队列、保存提交请求指纹、让 ComfyUI 侧可按业务 ID 查询，或引入提交协调记录。第二轮必须在该精确窗口做故障注入并决定业务是否接受极低概率重复。

### 7.2 输出完整性

history 必须包含 prompt_id，状态不能为 error，并且至少有一个 output。下载到 job 隔离目录，文件名取 basename，边下载边计算 SHA-256；只有写入 artifact 并完成 commit 后任务才 SUCCEEDED。

第二轮应补充：下载中断、同名输出、超大输出、磁盘满、history 输出路径异常和 SHA 不一致测试。

## 8. 重启恢复决策表

Scheduler 启动先把卡在 DELIVERING 的 callback 置回 RETRY，再扫描活动 job。

| 本地状态 | prompt_id | 远端 history | 远端 queue | 当前动作 |
|---|---|---|---|---|
| CLAIMED/UPLOADING | 无 | 不适用 | 不适用 | release lease -> RETRY_WAIT -> QUEUED |
| SUBMITTED/RUNNING | 有 | 找到 | 任意 | 从 history 下载并结束 |
| SUBMITTED/RUNNING | 有 | 未找到 | 找到 | 继续监听/等待 |
| SUBMITTED/RUNNING | 有 | 未找到 | 未找到 | FAILED: COMFY_RECOVERY_UNKNOWN，不盲重提 |
| DOWNLOADING | 有 | 找到 | 任意 | 重新校验并下载 |

这套策略优先避免重复生成，因此未知状态会失败而不是自动 retry。管理员可检查远端历史、日志和产物后决定是否人工重试。

## 9. 取消、超时与重试

### 9.1 取消

- QUEUED：API 可直接转 CANCELLED，不生成 prompt。
- 活动态：API 设置 `cancel_requested=true`，需要时转 CANCELLING，并发布 wakeup。
- executor 每 0.5 秒查询 cancel flag；发现后调用 `/interrupt`。
- WebSocket 结束后再次 refresh job，保证取消与完成竞态时按持久 flag 收敛为 CANCELLED。
- 最终释放 lease 并发布 job.cancelled。

ComfyUI `/interrupt` 通常影响当前执行，不天然绑定 prompt_id。由于每节点限制一个本系统 prompt，风险被降低；若检测到 foreign queue 或外部占用节点必须排除。第二轮必须验证外部用户直接向 ComfyUI 提交时不会误中断无关工作。

### 9.2 超时

watchdog 等待 workflow.timeout_seconds，随后最多 5 秒调用 interrupt；锁 job；若未终结则写 JOB_TIMEOUT、转 TIMED_OUT、release lease、commit、发布事件，再 cancel executor parent task。

executor 区分 watchdog 引起的 CancelledError 与服务关闭引起的取消，避免覆盖已落库的 TIMED_OUT。

### 9.3 重试

`fail_job` 只有在 attempts 未耗尽且尚无 prompt_id 时自动 RETRY_WAIT -> QUEUED。已有 prompt_id 的失败不自动重提，以重复生成安全优先。callback 重试独立于 job，最多 6 次，延迟为 10、60、300、1800、7200 秒阶梯。

## 10. 工作流验证与渲染

Manifest 声明：workflow_key、version、API template、JSON Schema、bindings、allowed class types、required models/custom nodes、最低显存、timeout、node labels、output nodes 和 enabled。

输入模板验证：

```text
template must be non-empty object
reject if top-level contains UI-format "nodes" or "links"
for each node_id -> node:
    node_id must be string
    node must be object
    class_type must be in explicit allowlist
    inputs must be object
```

Binding 只接受精确三段路径：

```text
<node_id>.inputs.<input_name>
```

拒绝空段、以下划线开头的段和更深路径。参数先通过 JSON Schema，再确认每个参数都有 binding；对模板 deep copy 后只改目标 inputs，最后再次执行 class allowlist 验证。模板 canonical JSON 计算 SHA-256 用于版本完整性。

限制：class_type allowlist 不能证明自定义节点内部安全；自定义节点本质上是镜像内代码，必须固定 commit、构建时审计且不能运行时安装。

## 11. API 幂等、配额与文件

请求摘要由 workflow、version、参数和上传内容相关信息形成。`IdempotencyKey` 以 `(client_id, key)` 唯一：

```text
existing key + same request hash -> return original job
existing key + different hash -> 409 conflict
no key -> validate, persist job + key in transaction
```

创建前检查 client enabled、Key hash/过期、日配额、max_queued、max_running 和速率限制。Redis 限流不可用时允许持久入队并记录降级，业务容量主要仍由 PostgreSQL 配额和 scheduler 控制。

文件存储：job 目录按日期和 UUID 隔离；安全文件名去除路径；临时文件写完后原子 replace；流式计算 SHA-256；Pillow 解码确认格式和像素数；inpaint 输入与 mask 尺寸必须一致。

第二轮应检查临时文件清理、Windows/Linux 路径差异、符号链接、磁盘配额和并发写同一 job 的行为。

## 12. API Key、JWT 与密钥算法

- 管理员密码：Argon2，time_cost=3、memory_cost=65536、parallelism=4。
- API Key：返回格式 `gpc_<prefix>_<secret>`；数据库只存 prefix 和 `HMAC-SHA256(pepper, secret)`。
- 验证：prefix 定位记录，使用 constant-time compare 验 HMAC。
- JWT：HS256，包含 sub、role、iat、exp，默认 900 秒。
- callback secret：`HMAC-SHA256(master, "callback:<callback_id>")` 派生，不保存明文；创建时仅返回一次。
- callback 签名：`HMAC-SHA256(secret, timestamp + "." + body)`。
- Agent 签名正文：method、path、timestamp、nonce、SHA256(body) 用换行连接后做 HMAC-SHA256。

生产密钥必须各自随机且分离。API pepper 轮换会使现有 API Key 和派生 callback secret 失效，需要双轨迁移方案。

## 13. Callback SSRF 与交付

入口只接受 HTTPS、无用户名密码、host 同时位于客户 allowlist 和全局 allowlist。字面 IP 必须非 private/loopback/link-local/reserved。投递前 `getaddrinfo`，所有结果必须 `is_global`；httpx 禁止跟随重定向。

已识别问题：代码先解析 DNS 做检查，随后 httpx 再独立解析并连接，存在 DNS rebinding/TOCTOU 窗口。当前不能宣称完全防止 DNS rebinding。审计评级：`REVIEW-HIGH`。建议使用固定解析结果建立连接并校验证书 hostname，或经受控 egress proxy 投递；同时阻断代理环境变量、IPv4/IPv6 特殊地址和解析变化。

交付状态由 PENDING/RETRY -> DELIVERING -> SUCCEEDED/FAILED；领取使用 `FOR UPDATE SKIP LOCKED`。每次记录响应状态、error code 和 duration；进程重启把 DELIVERING 恢复为 RETRY。

## 14. Node Agent 安全算法

Agent 除 `/health/live` 外的每个请求都验证：

```text
timestamp parses as int
abs(now - timestamp) <= 30 seconds
nonce exists and length <= 128
nonce not seen in last 60 seconds
signature == HMAC(method, path, timestamp, nonce, SHA256(body))
action exists in fixed COMMANDS map
execute_subprocess_exec(*argv), no shell=True
fixed minimal PATH
timeout 60 seconds
truncate stdout/stderr before response
```

动作映射到 `/usr/local/sbin/gpu-node-ctl` 的 status/start/stop/restart/nvidia-smi/system/diagnostics/logs，不接收任意 argv。sudoers 只授权该 wrapper；Agent 不挂 Docker Socket。

已知边界：nonce cache 在进程内存，重启后清空；30 秒时间窗仍限制旧请求，但严格场景可迁移到共享持久 nonce。三台主机时间同步是签名可用性的前提。

## 15. 日志与可观测性算法

请求和执行上下文使用 contextvars 绑定字段。结构化日志至少应包含：

```text
timestamp, level, service, environment, event, message
request_id, trace_id, job_id, tenant_id
workflow_key, workflow_version, node_id, prompt_id, attempt
error_code, duration_ms
```

检索路径：request_id 找入口；job_id 串 API/scheduler；node_id 看宿主机和 ComfyUI；prompt_id 对齐 Comfy history。Alloy 添加 host/service/container 标签发送 Loki。Prometheus 单独记录队列深度、最老等待、decision duration、loop lag、完成/失败、4090 overflow 次数和 callback 结果。

第二轮应随机抽一个真实 job，证明四类 ID 从 Nginx/API 到 Scheduler/ComfyUI/Agent 全链路可检索，并检查日志脱敏处理是否覆盖 password、secret、token、authorization、webhook 和 query string。

## 16. 容量模型与算法复杂度

稳定推理吞吐不由 API QPS 决定。两台 3090、单节点单任务时：

```text
steady_throughput ~= 2 / mean_inference_seconds jobs/sec
estimated_wait ~= queued_jobs / steady_throughput
```

4090 默认不计基础容量，只降低突发尾延迟。若平均推理 60 秒，两台 3090 理论约 2 jobs/min；100 个同时请求大约形成 50 分钟级尾部等待，实际还要加上传、下载和失败重试。

主要计算复杂度：节点选择 `O(N log N)`；候选 job 固定最多 200，排序约 `O(K log K)`；每个进度事件一次小事务；输出下载为 `O(bytes)` 并流式 hash。真正的瓶颈应是 GPU 推理、模型切换、存储 I/O 和 PostgreSQL 事件写入，不是 Python 节点选择。

## 17. 为什么当前没有使用 Celery/Flower

Celery 能提供通用 broker、worker、retry 和 Flower UI，但不会自动解决本系统最关键的语义：PostgreSQL 真相、ComfyUI prompt_id 恢复、每 GPU 单 lease、PRIMARY/OVERFLOW Guard、外部队列检测和四类 ID 生命周期。

当前只有三个固定节点，专用 asyncio scheduler 让派发决策和数据库事务处于一个可审计边界。Redis 也不承担不可恢复的 broker 真相。代价是恢复、retry、监控和运维由本项目自己维护。

重新考虑 Celery 的触发条件：节点规模显著增长、出现多类非 GPU 作业、需要跨地域 worker、专用 scheduler 维护成本高于框架适配成本。即便改用 Celery，GPU lease 和 PostgreSQL 状态机仍应保留，不能把 broker 状态当任务真相。

## 18. 第二轮优先修复清单

| 优先级 | 发现 | 推荐动作 | 验证 |
|---|---|---|---|
| HIGH | 4090 Guard 与 job claim 非同一原子边界 | node lock 内复核数据库条件，提交前复核 sentinel/指标 | 竞态注入不误派 4090 |
| HIGH | 满额租户 fallback 只复核一次 | 循环过滤所有满额租户 | 连续多个满额租户测试 |
| HIGH | `/prompt` 成功到 prompt_id commit 有双写窗口 | 稳定业务 ID/远端查询或明确补偿策略 | 精确 kill 点不重复出图 |
| HIGH | callback DNS 检查与连接分离 | 固定解析连接或 egress proxy | DNS rebinding 测试 |
| MEDIUM | client weight 未参与公平算法 | 实现加权轮转或移除承诺 | 不同权重统计分布 |
| MEDIUM | event sequence 依赖并发假设 | 统一 job row lock + 唯一约束 | 取消/超时/完成并发测试 |
| MEDIUM | ORM create_all 迁移难以逐项审计 | 固化显式 Alembic DDL 和约束 | 新 PostgreSQL schema diff |
| MEDIUM | Agent nonce cache 非持久 | 评估 Redis/本地持久 replay cache | Agent 重启后重放测试 |
| MEDIUM | npm 有 4 个未归因公告 | 获准后执行 audit，升级并回归 | 报告无未接受 critical |

这些条目是第一轮深入阅读中主动暴露的审计点。它们意味着“可进入第二轮”，不意味着“已具备生产安全签字”。第二轮应直接修复、补回归测试，并在实机证据完成后更新最终结论。
