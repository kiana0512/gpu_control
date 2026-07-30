# ComfyUI 中断终态与压测止损恢复

日期：2026-07-30  
状态：`DEPLOYED_NOT_ACCEPTED`

## 现场结论

六 API r4 阶梯压测在 25 VU 暴露了两个相互独立的问题：

1. 旧 Nginx 将业务请求、节点心跳和 capacity 查询放在同一个 `10 r/s`、`burst 20`
   的来源 IP 限流区。单 NAT 压测流量产生 429 后，控制流也被拒绝，生产看板短暂出现节点
   `DRAINING/OFFLINE`；控制流隔离修复见
   `71_2026-07-30_NGINX_GATEWAY_CAPACITY_AND_CONTROL_ISOLATION.md`。
2. 父批次取消已经通过认证、审计和幂等接口持久化，三台 ComfyUI 也都记录了
   `execution_interrupted`，但客户端事件迭代器只把 `execution_success` 和
   `execution_error` 当作终态。结果是三个测试子任务继续占用 Scheduler lease，直到
   Scheduler 恢复对账。

现场只读核对确认三台 GPU 利用率均已归零，ComfyUI history 中三个 prompt 均为
`status_str=error` 且最后一条消息为 `execution_interrupted`。当时没有真实生产 GPU 或
Asset 任务。重启现有 Scheduler 后，父批次全部收敛为 `CANCELLED`、lease 全部释放、三节点
`current_jobs=0`。旧 1.5.4 将内部子任务记录为 `FAILED/COMFY_EXECUTION_ERROR`；当前源码的
“取消意图优先”规则会把相同竞态收敛为 `CANCELLED`。

## 部署身份与现状

本修复已按受控窗口上线，当前属于“已部署、未验收”：

- 应用源码 revision：`7656aa68ebde9c95f5a41c52db3f066cae00e249`
- 发布归档 commit：`40d5d1c911953adedf4016073e240152f028ddd6`
- API 镜像：`sha256:762dc15ebc72ba8825906a0716e781f9a8d9ec29f0e81793b820489faba3ec43`
- Scheduler 镜像：`sha256:6abbaa1ed6a9238109dfa2d6f6fb3804804f73366d5944bd3562331511cf206d`
- Asset API 镜像：`sha256:52c8c96e79074b086884afd4b72a10c4fe6a79479f0a6552721a042fdd96aec6`
- Web 镜像：`sha256:80f8651621d2264ce00500180a19fbf6ceaad9887ef4adc44983b67a4341f0bf`
- 数据库 migration：`20260730_0011`
- 上线后网关观察窗口：`429=0`、`5xx=0`
- GPU 节点：`3/3 ACTIVE/ONLINE`
- 六 API r5 阶梯压测：`进行中`；本文不声明尚未完成的吞吐、延迟或稳定性结果。

## 修复合同

- `execution_interrupted` 是 ComfyUI WebSocket 的终态事件；事件流收到后必须立即返回，
  不得继续读取或等待完整 workflow timeout。
- Scheduler 收到该事件时记录 `gpu_finished_at`，随后重新读取持久化取消意图。
- 取消意图存在时，Job 与 JobAttempt 最终均为 `CANCELLED`，释放 NodeLease 并把
  `node.current_jobs` 归零。
- 遥测失败或发现外来真实任务时，遥测 greenlet 只发幂等 stop signal；只有 Locust Shape
  负责结束运行，禁止并发调用 `runner.quit()`。
- wrapper 同时使用环境变量和 CLI 固定 `30s` Locust stop timeout，调用方不能用旧的
  `21600s` 覆盖。
- teardown 只处理当前 session 注册的任务；同一 cancel key 对 429/5xx 最多重试三次，
  使用有界退避和请求节流，失败保持 fail closed 并写入原始结果。

## 回归证据

- 真实异步 WebSocket 行为测试：跳过其他 prompt，目标 prompt 收到
  `execution_interrupted` 后立即结束，不读第三条消息且不重连。
- 调度竞态集成测试：`Job=CANCELLED`、`JobAttempt=CANCELLED`、`gpu_finished_at` 非空、
  lease inactive、节点槽位为 0。
- 压测止损测试：stop signal 幂等、Shape 单点结束、遥测函数 AST 中没有直接 `quit()`。
- teardown 测试：429→503→200 时执行三次并按 `0.25s/0.5s` 退避；连续 5xx 三次后明确失败。
- wrapper 测试：外部 `LOCUST_STOP_TIMEOUT=21600` 被固定 `30s` 覆盖，CLI 也只出现一次
  `--stop-timeout 30`。

本文件不授权修改外部 ImageClip/ModelViewCreator workflow、模型、prompt、图结构或输出节点。
四个服务已使用上述同一 source revision 构建并部署，数据库迁移、备份、健康检查和生产
优先级门禁已执行。但 r5 压测、故障注入、持续观察与联合签收仍未完成，因此状态必须保持
`DEPLOYED_NOT_ACCEPTED`，不得标记为 `FROZEN` 或 `PRODUCTION_ACCEPTED`。
