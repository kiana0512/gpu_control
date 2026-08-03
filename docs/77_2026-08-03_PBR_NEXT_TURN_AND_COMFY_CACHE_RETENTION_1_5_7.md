# 2026-08-03 PBR 下一轮优先权与 ComfyUI 缓存保持（1.5.7 候选）

记录日期：2026-08-03

当前状态：`CANDIDATE_NOT_DEPLOYED`

范围：GPU Control Asset API、API/Web 可观测性、Windows Substance Baker Agent、控制面版本与部署；
不修改 ImageClip、ModelViewCreator、Retopology Skill、任何外部 workflow、模型、prompt、采样参数、
图拓扑或输出语义。

## 1. 结论与事实边界

本候选解决的是 `worker-3090-b` 上 Windows Substance Baker 与 WSL2 ComfyUI 共享同一张物理 GPU 时，
生产 PBR 已进入队列却可能持续等待的问题。目标调度语义为：

1. 已经运行的 ComfyUI 当前帧安全完成，不中断真实任务；
2. 生产 PBR 取得 3090-B 的下一轮优先权；
3. pending reservation 有效期间 Scheduler 不再向 3090-B 分配新的 ComfyUI 帧；
4. Windows Baker 完成并安全释放后，3090-B 才重新回到 ComfyUI 调度；
5. UV、重拓扑等纯 CPU Asset Worker 使用独立队列和槽位，不因 3090-B 的 GPU 互斥而停止领取。

仓库版本字段已调整为 `1.5.7`，但本文编写时改动仍在工作树中，最终 source revision、四镜像
image ID/digest、Windows Agent 上线身份和生产运行证据均未形成。因此当前生产基线仍是 `1.5.6` /
`310a44c70c20f7cbfc601d19e19858380a61c20a`，不得把本候选写成“已部署”、`FROZEN` 或
`PRODUCTION_ACCEPTED`。

| 项目 | 当前事实 |
| --- | --- |
| 候选版本 | `1.5.7` |
| 候选 source revision | `PENDING_FINAL_COMMIT` |
| 候选基于的当前 HEAD | `a98175ff33c16546cd97ff547f1d74327a683865` |
| 数据库迁移 | 本候选代码差异中无新增 migration |
| 四组件镜像身份 | `PENDING_REPRODUCIBLE_BUILD` |
| Windows Agent 生产 v3 证据 | `PENDING_SAFE_INSTALL_AND_HEARTBEAT` |
| 生产发布状态 | `NOT_DEPLOYED` |

## 2. 根因

Asset API 为排队中的生产 `SUBSTANCE_BAKE_V1` 创建
`substance_bake_pending_reservation`，并把 3090-B 置于 Asset API 自有的受控 drain。Windows Baker
轮询 claim 时会运行 reservation reconciliation；如果 3090-B 上仍有一个 ComfyUI 当前帧，reconcile
会把 reservation 的过期时间向后续租，然后返回“暂时无可领取任务”。

缺陷在于该提前返回路径原来没有 `commit`。请求 session 结束时，刚续租的过期时间随事务回滚；原始
60 秒 TTL 到期后，Scheduler 可能再次把 3090-B 当成可分配节点并派入新的 ComfyUI 帧。于是 PBR
虽然已经排队，却会在连续 ComfyUI 帧之间反复错过执行窗口，Web 只看到“排队中”。

这不是 PBR 应改为 CPU 烘焙，也不是 CPU Asset 队列被 ComfyUI 阻塞。Substance 的 SAL/SoRa 路径
需要使用 3090-B 的物理 GPU；真正的问题是“下一轮预约续租未持久化”。

## 3. 1.5.7 候选修复

### 3.1 下一轮预约持久化

- Baker claim 在“当前 ComfyUI 帧仍运行”等安全阻塞分支返回前显式提交 reservation reconciliation；
- 续租后的 `expires_at` 由数据库持久化，不再随请求结束回滚；
- Scheduler 即使在当前帧结束后看到 GPU 槽位空闲，仍会依据持久 pending reservation 以
  `substance_reserved` 排除 3090-B；
- 只有 Windows Baker 建立正式 fence、任务取消、任务完成，或按既有恢复规则安全释放后，预约才结束；
- 该预约只拥有 3090-B 的物理 GPU 下一轮，不拥有 CPU Asset Worker 队列。

### 3.2 Agent v3 身份与旧进程隔离

Asset API 只允许以下精确身份领取 Substance 任务：

```text
blender_version=substance-15.1.0
skill_version=substance-baker-2026.08.03-v3
```

旧 v2 Agent 可以继续上报心跳，但会被标为 `DRAINING` 且不能 claim，避免旧 Agent 继续执行会停止并
重启 ComfyUI 的历史行为。安装器新增 `-ConfirmNoActiveBakes`：发现已有计划任务时，没有显式空闲确认
就拒绝覆盖；确认后先停止全部 legacy/indexed Baker 计划任务，等待退出，再替换脚本并启动四个 v3
实例，防止 Windows `MultipleInstances IgnoreNew` 让旧进程继续常驻。

### 3.3 ComfyUI 进程连续性与结果硬门禁

每个 v3 Baker attempt 在原生烘焙前后都通过 WSL 的绝对路径 `/usr/bin/docker inspect` 核对：

```text
container Id + State.StartedAt + RestartCount + State.Status + Health.Status
```

前后必须是同一身份，且状态始终为 `running/healthy`。成功回执还必须同时提供：

```json
{
  "comfyui_cache_policy": "no_explicit_eviction_process_preserved",
  "comfyui_container_restarted": false,
  "comfyui_process_continuity_verified": true
}
```

缺失、为假或不匹配时，Asset API 以 `SUBSTANCE_RESULT_INVALID` 拒绝完成，不发布烘焙结果。运行中发现
容器身份、启动时间、重启计数或健康状态变化时，Agent 以
`SUBSTANCE_COMFYUI_CONTINUITY_FAILED` 非重试失败上报；Asset API 不执行普通 fence 释放，而把
3090-B 保持在 `recovery_required` drain，直到原 Baker 空闲和其后的新鲜健康 ComfyUI 心跳共同满足
既有两阶段恢复证据。

## 4. `no-explicit-eviction` 的精确含义

v3 Agent 的合同是“保持 ComfyUI 进程，不主动清缓存”，具体保证：

- 不调用 ComfyUI 模型释放接口，包括 `/free`；
- 不执行 `docker stop`、`docker start` 或 `docker restart`；
- 不通过共享本地 marker 猜测多 Baker 实例的 fence 状态；持久 fence 仍由 Asset API/PostgreSQL 管理；
- 每个 attempt 单独记录并核对 ComfyUI 容器身份、`StartedAt` 和 `RestartCount`；
- 安装 v3 只替换 Windows Baker 计划任务，不重启 WSL2 ComfyUI 容器。

这里的“缓存保持”不等于承诺所有模型字节始终驻留显存。Windows Substance 与 WSL2 ComfyUI 共享
物理 GPU，驱动、显存压力或框架自身策略仍可能让部分模型从 VRAM 移出。当前候选能够证明的是：GPU
Control 没有显式释放模型，且 ComfyUI 容器进程没有被停止或重启；它只能保留热缓存复用的机会，不能
伪造显存驻留事实。

现有 warm-workflow affinity 继续生效。真实冷/热差异应由下一次自然到达的、同一批准 workflow 身份的
业务任务记录加载日志和阶段耗时来证明。1.5.7 不会偷偷提交业务 prompt 充当预热，也不会为追求速度
修改外部 workflow。若以后增加主动预热，必须是可审计、可取消、低优先级、使用已批准
workflow/version 的正常 GPU Control 任务，并在真实生产任务到达时立即让路。

## 5. Web/API 可观测性

`/admin/asset-processing` 候选响应新增真实的 `substance_gpu` 和逐 PBR `resource_wait`：

- `WAITING_FOR_COMFYUI_FRAME`：已获下一轮预约，等待当前帧安全结束；
- `WAITING_FOR_BAKER_CLAIM`：GPU 已预约，等待 Windows Baker 领取；
- `SUBSTANCE_GPU_ACTIVE`：Baker 已建立 fence 并执行；
- 3090-B 离线、恢复闭锁、管理员保留、外部 GPU 活动、foreign queue 等分别显示独立原因；
- 只有 reservation 的数据库 owner 精确为 `asset-api` 时，Web 才能显示“下一轮已预约”。

Asset 页面文案改为“队列独立；3090-B 物理 GPU 按任务互斥”。四个 Windows Baker 数字表示进程槽位，
不是四张 GPU。页面会展示提交、开始、结束、耗时、执行 Worker 和具体等待原因，不再把 0/4 Baker
槽或显存缓存误写成 GPU 空闲/占用结论。

## 6. 候选验证记录

截至本文创建时，已得到以下候选验证；这些结果证明受影响代码路径，不等于生产发布：

| 验证 | 结果 |
| --- | --- |
| PBR 预约/连续性定向回归 | `7 passed` |
| Asset API + Admin API 受影响集成回归 | `56 passed in 96.88s` |
| Web production Docker build | 通过；仅保留既有 `504.46 kB` 非阻断分包提示 |
| Windows 实机 `docker inspect` 格式探测 | `Id~StartedAt~RestartCount~running~healthy` 可解析 |

自动回归覆盖以下关键语义：

1. 原 reservation 精确过期后，Baker 再次轮询会持久续租；在新的数据库 session 中仍可见；
2. 当前 ComfyUI 帧结束后，Scheduler 仍以 `substance_reserved` 排除 3090-B，不派下一帧；
3. 同一时段 CPU UV Worker 仍能领取独立任务；
4. v2 Agent 心跳被置为 `DRAINING` 且不能 claim；
5. 缺失或伪造 ComfyUI 连续性证据的 completion 以 422 拒绝；
6. 运行时连续性失败会保留 recovery drain；
7. Web 只在真实、有效且由 Asset API 拥有的 reservation 下显示“下一轮优先权”；
8. Agent 静态合同中不存在 `Set-ComfyUiRunning`、`docker stop/start`、`/free` 或旧共享 fence marker。

最终发布前还必须对最终工作树重新执行并记录：Ruff、mypy、`git diff --check`、Compose render、最新 Web
Vitest/ESLint/Prettier、四镜像可复现构建，以及最终两份 PowerShell 脚本在目标 Windows PowerShell 5.1
上的 parser 检查。不能用较早候选副本的解析结果替代最终字节检查。

## 7. 生产安全发布门禁

### 7.1 发布前只读门禁

以下条件必须在同一短窗口重新查询，历史截图不算实时证据：

1. GPU 子任务和父批次没有任何非终态记录；`TIMED_OUT` 等终态不得误计为活动任务；
2. Asset job 没有 `QUEUED/CLAIMED/RUNNING` 等非终态任务，四个 Baker `current_jobs=0`；
3. 三个 GPU 节点在线，3090-B 没有 current ComfyUI job、foreign queue、external busy、manual reserve、
   pending reservation、active fence 或 recovery；
4. 没有真实用户任务刚进入接纳/校验窗口；若出现，停止本次发布并优先真实任务；
5. 保存 1.5.6 四镜像可定位 rollback tag，记录当前 3090-B ComfyUI 的完整 Id、`StartedAt`、
   `RestartCount`、状态和健康；
6. 最终源码已提交，四镜像的 version/revision OCI label 一致，API/Asset API provenance 完整；
7. 最终 Windows v3 Agent 与 Installer 在目标 PowerShell 5.1 解析通过。

### 7.2 发布顺序

1. 在零任务窗口先更新 Asset API，使旧 v2 Agent 立即 fail closed，健康后再继续；
2. 逐服务更新 API、Scheduler、Web，每一步等待健康；不重启 PostgreSQL、Redis、ComfyUI、GPU Worker、
   CPU Asset Worker，也不修改外部 pipeline；
3. 再次确认 Asset/PBR 活动数、pending/fence/recovery 和四个 Baker current_jobs 全为 0；
4. 仅在此时使用 `-ConfirmNoActiveBakes` 安装 Windows v3 Agent；不得运行任何 ComfyUI stop/start/free；
5. 确认四个 Baker 心跳均为精确 v3 且 `ONLINE`，旧 v2 进程和 legacy 计划任务均不存在；
6. 对比发布前后的 ComfyUI Id、`StartedAt`、`RestartCount`，必须完全不变并保持 healthy；
7. 在没有真实任务等待时只运行一个受控 PBR canary，验证下一轮预约、fence、正式产物 SHA 和安全释放；
8. 不用隐藏 prompt 伪造预热。由下一次自然到达或明确批准的同 workflow canary 核对 warm affinity、
   模型加载日志和耗时；随后观察真实队列是否仍发生 PBR 饥饿。

### 7.3 发布后必须回填的证据

| 项目 | 发布后值 |
| --- | --- |
| source revision | `PENDING_DEPLOYMENT` |
| API image ID / registry digest | `PENDING_DEPLOYMENT` |
| Asset API image ID / registry digest | `PENDING_DEPLOYMENT` |
| Scheduler image ID / registry digest | `PENDING_DEPLOYMENT` |
| Web image ID / registry digest | `PENDING_DEPLOYMENT` |
| 1.5.6 rollback tags | `PENDING_DEPLOYMENT` |
| v3 四 Worker 心跳与安装时间 | `PENDING_DEPLOYMENT` |
| ComfyUI 发布前/后身份 | `PENDING_DEPLOYMENT` |
| PBR canary job/request/trace/artifact SHA | `PENDING_DEPLOYMENT` |
| 下一次同 workflow cold/hot 计时 | `PENDING_RUNTIME_EVIDENCE` |

上述字段没有真实值之前，本文状态保持 `CANDIDATE_NOT_DEPLOYED`。

## 8. 回滚门禁

控制面回滚必须在与发布相同的零任务条件下执行：停止新测试流量，等待真实任务完成，确认 GPU/Asset/
Windows Baker 全部空闲，再处理 3090-B 的标签。只能清理由
`substance_bake_drain_owner=asset-api` 明确拥有的 pending/fence/recovery；遇到身份不明或两阶段恢复证据
不完整时保持 `DRAINING`，禁止强制改回 `ACTIVE`。

默认只把 API、Asset API、Scheduler、Web 回滚到已记录的 1.5.6 镜像，并保留 v3 Windows Agent；1.5.6
Asset API 可以接收 v3 的兼容心跳，而 v3 继续满足“不停止 ComfyUI、不主动释放模型”的安全合同。不得
为了回滚控制面而静默恢复会 stop/start ComfyUI 的旧 Agent。

若故障根因确实位于 v3 Agent，应停止新的 PBR 接纳并保持 3090-B 安全 drain；只有在四个 Baker 空闲、
ComfyUI 身份已记录、用户明确批准会改变缓存合同的降级方案后，才允许更换 Windows Agent。任何回滚
均不得清理 ComfyUI 模型缓存、替换业务 workflow，或用预览/中间产物代替正式 PBR 输出。

## 9. 验收边界

本修复关闭的是“生产 PBR 下一轮预约可能因续租回滚而饥饿”和“Baker 主动重启 ComfyUI”两个控制面
问题。正式生产验收仍需：

- 真实用户任务与 PBR 混合流量下连续观察下一轮优先权、生产让路和无饥饿；
- 3090-B ComfyUI/Baker 故障注入及 recovery 两阶段恢复；
- 同批准 workflow 的冷/热阶段计时和模型加载证据；
- 六 API 正式压力、固定 B 系列、完整故障矩阵和七天观察；
- 镜像 digest、SBOM、回滚执行记录与动画管家联合签字。

未完成这些门禁时，即使 1.5.7 后续上线，也最多只能标记 `DEPLOYED_NOT_ACCEPTED`。
