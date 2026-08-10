# 监控与飞书

Prometheus 抓取 API、scheduler、node exporter、DCGM、PostgreSQL、Redis。Grafana 已 provision Loki/Prometheus 和 GPU Control 总览。核心指标：队列深度/最老等待、任务吞吐与失败、API 延迟、调度决策与 loop lag、节点槽位、4090 overflow 次数、回调尝试/失败、GPU 利用率/显存/温度。

3090-B WSL2 另有两层保护：

- “状态层”通过 Node Agent 的签名接口 `/v1/system-metrics` 直接读取 WSL 内核 boot ID/uptime、
  `load1 / CPU`、可用内存比例、交换分区和 Linux PSI。对应指标包括
  `gpu_control_wsl_system_probe_up`、`gpu_control_wsl_boot_uptime_seconds`、
  `gpu_control_wsl_memory_available_ratio`、`gpu_control_wsl_swap_used_ratio`、
  `gpu_control_wsl_load1_per_cpu`、`gpu_control_wsl_pressure_avg10` 和
  `gpu_control_wsl_boot_changes_total`。
- WSL2 无 DCGM 时由签名 GPU 查询补位：`gpu_control_node_gpu_utilization_percent`、
  `gpu_control_node_gpu_free_vram_mb`、`gpu_control_node_gpu_temperature_c`、
  `gpu_control_node_gpu_power_w` 和 `gpu_control_node_gpu_power_limit_w`。温度持续 5 分钟超过 85°C
  触发 `WSLGPUHot`。
- “性能层”使用实际成功任务做同机型、同分辨率对照。核心指标
  `gpu_control_wsl_imageclip_slowdown_ratio` 是同分辨率 ImageClip 最近 5 个成功 GPU 样本的中位数
  除以原生 3090-A 中位数；至少各 3 个样本才输出有效值。
- `gpu_control_wsl_imageclip_performance_anomaly`：上述比值达到 `2.0` 时为 1；持续 2 分钟触发
  `WSLImageClipSevereSlowdown`。单帧冷启动不会触发。
- Scheduler 健康连续 30 秒为 0 或 10 分钟内上下线至少 4 次，分别触发 `WSLComfyUnhealthy` 和
  `WSLNodeHealthFlapping`。

状态层还会触发 `WSLSystemProbeMissing`、`WSLMemoryPressure`、`WSLSustainedLoad` 和
`WSLBootChanged` 和 `WSLGPUHot`。其中 boot ID 只在 Scheduler 建立初始基线后发生变化才计数，Scheduler 自己重启
不会误报 WSL 重启。所有 WSL 深度查询均为只读、小响应、HMAC 签名请求，不读取凭据，不访问 Docker
Socket，也不执行通用命令。

性能告警只能提示并要求先 Drain，不自动重启 Windows/WSL2 或 ComfyUI；生产任务运行时不得通过
自动重启“自愈”，避免中断帧。样本不足会输出 `NaN` 比值和 0 异常，不得解释为性能通过。

Alertmanager 规则覆盖 API/节点离线、队列堆积、调度延迟、GPU 高温/低显存、任务和回调持续失败。将 `FEISHU_WEBHOOK_URL` 与 `FEISHU_SIGNING_SECRET` 写入权限 600 的 `.env`，在后台点击“飞书测试”。检查点：飞书收到测试卡片，告警解除后收到恢复通知。

失败时检查 `http://127.0.0.1:9090/targets`、Alertmanager alerts、API 日志中的 `feishu` 事件。回滚为清空两项环境变量并重建 API/Alertmanager，不影响任务执行。未配置飞书时，告警仍会经
Alertmanager webhook 持久化并显示在 Web“告警”页，但不会产生外部飞书通知。
