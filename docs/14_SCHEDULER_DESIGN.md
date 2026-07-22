# 调度器设计

PostgreSQL 是队列。候选任务先按 pinned、优先级老化和租户上次调度时间排序，再在事务中用 `FOR UPDATE SKIP LOCKED` 领取，并原子占用 `node_leases`。优先级有效分数随等待时间增加，防止 batch 永久饥饿；租户权重/最近调度时间防止单租户淹没队列。

```text
loop:
  refresh node health and external Comfy queue
  snapshot = queued_count + oldest_wait
  if any healthy idle PRIMARY 3090: choose least-recently-assigned
  else if 4090 ACTIVE: choose it
  else if 4090 OVERFLOW and auto enabled:
      require queue_count >= Q OR oldest_wait >= T
      require !manual_reserved && !sentinel && util < U && free_vram >= V && in_window
  transaction: lock candidate task SKIP LOCKED; create lease; mark CLAIMED
  execute exactly one prompt on that node
```

`RESERVED/DISABLED/DRAINING`、心跳过期、槽位占用、外部队列、外部 GPU 忙、人工预留都会排除节点。PRIMARY 只要有空闲，普通任务绝不发 4090。每个节点 `max_concurrency=1`。

提交前失败可进入 RETRY_WAIT 后重排；`prompt_id` 一经取得先提交数据库，再监听。重启恢复时查 `/history` 和 `/queue`：找到就继续，均找不到则 `COMFY_RECOVERY_UNKNOWN`，不重复提交。Redis 中断只增加扫描延迟，不影响真相。

