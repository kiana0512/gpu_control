# 2026-07-30 Scheduler 与 Substance 物理 GPU 稳定性热修复

日期：2026-07-30

范围：GPU Control API / Scheduler / Asset API；不修改 ImageClip、ModelViewCreator、Retopology Skill 或任何外部 workflow
当前状态：`SOURCE_TESTED_NOT_DEPLOYED`

## 1. 目标与结论边界

六 API 120 VU 首轮压力暴露了两项控制面稳定性问题：Scheduler 为持有 PostgreSQL session
advisory lock 留下长期 `idle in transaction`，以及 Windows Substance Baker 在持续 ComfyUI 队列下
可能拿不到同一张 `worker-3090-b` 物理 GPU。当前源码候选同时修复这两项问题，并补齐取消、租约过期、
健康写回和生产优先级的并发闭环。

本候选不改变外部业务 workflow、模型、prompt、分辨率、采样、节点图或输出语义；Retopology advisory
仍只放宽几何质量判定，BLEND/FBX 完整性、SHA、manifest 和输入身份继续是硬门禁。

## 2. Scheduler 单主锁

- advisory lock 固定在一条专用 `AUTOCOMMIT` 物理连接上，不再长期持有数据库事务或 vacuum horizon；
- 记录锁连接的 PostgreSQL backend PID，并每两秒在同一连接核对 PID 与精确 `pg_locks` 所有权；
- 连接断开、查询超时、PID 变化或锁丢失均 fail closed：停止领取新任务、唤醒主循环并取消当前执行/组装任务；
- 异常清理有三秒上限并使连接失效，避免连接状态未知时把它放回连接池；
- 正常退出必须得到 `pg_advisory_unlock` 的明确成功结果。

真实 PostgreSQL 17 隔离回归覆盖：长期 idle 不持有事务、第二 Scheduler 被拒绝、终止 backend 后失锁、
idle session timeout 后失锁、异常退出后接管，以及 leader epoch 必须等待旧 claim 提交并拒绝旧 owner
迟到写入；最终源码结果为 `5 passed`。

## 3. Substance 与 ComfyUI 物理 GPU 互斥

`worker-3090-b` 同时承载 ComfyUI 和 Windows Substance Baker。互斥不再只依赖易被其他写者覆盖的
`Node.mode`，而使用数据库持久标签作为硬调度闭锁：

- `substance_bake_pending_reservation`：为下一批真实生产烘焙预留，数量不超过新鲜在线 Baker 的实际空闲槽；
- `substance_bake_fence_job_ids`：已被 Baker 领取并占用物理 GPU 的任务集合；
- `substance_bake_recovery_required`：租约过期后的模糊执行闭锁；
- `substance_bake_drain_owner=asset-api`：只标识 Asset API 自己取得的 DRAINING 所有权。

Scheduler 即使看到节点被误改回 `ACTIVE`，只要上述 pending/fence/recovery 任一有效，仍拒绝给 ComfyUI
分配新任务。Asset API 不覆盖管理员设置的 `DISABLED`、`RESERVED`、手工保留或其他模块拥有的
`DRAINING`；无法取得 drain 所有权时不创建 reservation/fence。

健康探测写回改为“网络探测结束后重新锁 Node 行并合并最新 labels”，不再用探测前的旧 ORM 对象覆盖
并发产生的 fence/recovery/pending 或管理员 mode。

## 4. 恢复、取消与锁顺序

- Substance claim、cancel、lease expiry、complete/fail 统一采用 `Node -> AssetJob` 锁顺序；涉及 Worker
  时继续按固定顺序获取，避免 `Job -> Node` 与 `Node -> Job` 互锁；
- lease 过期不自动假定 Windows 进程已经停止。先持久化“原 Worker 报告 current_jobs=0”的时间，
  再要求之后出现新的、未过期的 ComfyUI ONLINE heartbeat；两阶段证据同时成立才解除 recovery；
- 所有 Asset 完成入口使用短事务验证 lease，再在无 Job/Node 锁时上传和校验；最终按固定锁序重新
  检查 `cancel_requested`，并以唯一不可变 artifact 目录和数据库终态一次发布。取消或 commit 失败
  不会留下阻断重试的固定 `output/`；
- Scheduler 的上传、持久 prompt intent、提交恢复、事件、下载和 timeout 都在写入前按
  `epoch -> Job` 重新锁定并刷新；取消优先于迟到 progress/success/timeout，下载只写唯一私有 staging，
  复核后才原子发布正式路径；
- callback 使用 30 秒持久投递租约和稳定 attempt 级 `Idempotency-Key`；传输结果不明确时不消耗
  attempt，旧 leader 的迟到结果不能覆盖新 leader；
- 联表领取查询只执行 `FOR UPDATE OF asset_jobs`，不锁 `api_clients`；
- test tenant 的 Substance 只使用真正空闲容量；任一非 test GPU Job/Batch 非终态时不领取新的 test bake。

## 5. 发布前门禁

只有以下条件同时满足才允许热更新：

1. GPU job、父批次、Asset job 活动数全部为 0；
2. 三个 GPU 节点 `ONLINE`，没有 foreign queue、manual reserve、fence 或 recovery；
3. 全量 Python 回归、Ruff、compileall、Compose render 和真实 PostgreSQL 锁测试通过；
4. 新镜像带完整 source revision，旧镜像保留可定位的 rollback tag；
5. 只重建 API、Scheduler、Asset API 和 Web；不重启 PostgreSQL、Redis、ComfyUI 或业务 Worker；
6. 上线后确认 Scheduler lock connection 的 `state=idle`、`xact_start=NULL`、`backend_xmin=NULL`。

## 6. 旧版本回滚兼容门禁

旧 Asset API/Scheduler 不认识本候选新增的 pending/recovery 标签，因此不能在这些标签存在时直接回滚。
回滚必须先由当前版本执行受控清场，再替换容器：

1. 停止新的测试流量，等待或精确取消本次 test session；不得取消其他 tenant；
2. 确认所有 `SUBSTANCE_BAKE_V1` 均为终态，`worker-3090-b` 没有活动 GPU job/batch，所有 Windows
   Baker `current_jobs=0`；
3. 在一个数据库事务内 `FOR UPDATE` 锁定 `worker-3090-b`，再次核对第 2 项；
4. 只有 `substance_bake_drain_owner` 精确等于 `asset-api` 时，才可删除 Asset API 自有的
   pending/fence/recovery/owner 标签；不得删除未知标签；
5. 只有 mode 仍为 Asset API 自有的 `DRAINING`、且 `manual_reserved=false` 时才恢复 `ACTIVE`；
   管理员的 `DISABLED/RESERVED` 或其他模块的 drain 保持原样；
6. 验证标签、mode、GPU/Asset 活动数和 Worker 心跳后，按 Asset API → Scheduler → API → Web 顺序
   回滚到已记录镜像；任一步失败立即停止，不做数据库 restore。

禁止在旧代码运行后再猜测或批量删除这些标签。若 recovery 证据不完整，保持节点 DRAINING 并人工核对
Windows Baker 与 ComfyUI，而不是强行解锁物理 GPU。

## 7. 当前验证记录

最终源码已完成完整禁网回归 `272 passed / 5 skipped / 0 failed`，其中 Asset 完整专项 `28 passed`、
Scheduler 受影响专项 `58 passed`、load harness 回归 `41 passed`；Scheduler 真实 PostgreSQL 17
锁与接管回归 `5 passed`。Ruff、全项目 mypy（34 个源码文件）、`git diff --check`、两份 Compose render、
Web ESLint/Prettier、Vitest `10/10`、Vue 类型检查和生产构建均通过。Web 构建只有一个非阻断的
`504.46 kB` 分包提示。

r6 六 API 计划已离线验证为 `EXECUTION_ELIGIBLE`：六类固定素材、120 VU、31 分钟、完整备份
`VERIFIED_FULL_PRE_WINDOW`，无执行阻断；该结论只是计划门禁，不表示已发送生产请求。镜像构建、
空闲热更新和 r6 真实运行仍以后续记录为准；在完成前状态保持 `SOURCE_TESTED_NOT_DEPLOYED`，不得写成
`PRODUCTION_ACCEPTED`。
