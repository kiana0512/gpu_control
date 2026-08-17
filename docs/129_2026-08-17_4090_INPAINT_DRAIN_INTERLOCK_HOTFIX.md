# 4090 局部重绘 drain interlock 热修记录

## 故障与影响

局部重绘任务 `53bd82b3-c15d-4122-9c27-cf3950c589bf` 到达时，4090 正在执行
ImageClip。Scheduler 向 ComfyUI `/interrupt` 发出抢占请求，服务返回 HTTP 200，
但响应 body 为空或不是 JSON。旧客户端把成功响应误判成
`COMFY_INVALID_RESPONSE`，随后在 4090 写入 `gpu_cache_drain_failed`。该标签是显存
安全硬互锁，因此 4090 被排除，任务错误落到 3090-B：端到端 103.1 秒，GPU 阶段
85.4 秒。

这不是 10 分钟优先窗口的正常行为。优先窗口只决定局部重绘到达时的调度优先级，
不应在无局部重绘时预留空闲 GPU。

## 修复

1. 在确认 4090 ComfyUI `queue_running/queue_pending` 都为空、`current_jobs=0`、无外部
   队列后，仅清除失效的 `gpu_cache_drain_failed` 标签；保留局部重绘优先策略。
2. `ComfyClient.interrupt()` 兼容 HTTP 2xx 的空 body、纯文本和 JSON 响应。
3. HTTP 2xx 只代表中断请求已受理；Scheduler 仍必须轮询 `/queue` 到零并调用
   `/free`、校验显存恢复。队列或显存未在期限内恢复时仍 fail closed。
4. WebUI 将“局部重绘保护中”改成“局部重绘优先窗口”，明确没有局部重绘时继续
   接普通任务，不预留空闲 GPU。
5. 故障时被错误标记失败的 ImageClip 第 104 帧通过正式 Admin Retry 恢复成功；
   批次聚合器在 `failed_items=0` 后清除已经解决的逐帧错误，避免界面继续显示旧故障。

## 生产回归

- Scheduler 镜像：`gpu-control-scheduler:1.5.18-drainfix-r1`
- 健康：`healthy`
- 回归任务：`5eff92b0-dbb4-4884-af4d-0a2538680249`
- 结果节点：`control-4090`
- 提交到领取：2.14 秒
- GPU 阶段：37.923 秒
- 端到端：41.219 秒
- 输出：2048×2048 RGB PNG，564,971 bytes
- SHA-256：`360e91a0dcfaa998cc39b92ed9025b8f8ca005d5e87d8643a669c817569c3bab`
- OOM：否
- `gpu_cache_drain_failed`：未产生

回归发生在三台其它 GPU 均占用、4090 正在跑 ImageClip 的真实争用场景。日志确认
`POST /interrupt` 返回 200、普通帧被改派、局部重绘分配到 4090。调度缺陷已关闭。
本轮 41.219 秒仍高于 30 秒性能目标，后续性能优化必须独立进行，不能通过改低工作流
默认参数或虚报热启动基线来验收。
