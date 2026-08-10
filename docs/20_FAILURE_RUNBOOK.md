# 故障手册

| 症状 | 先检查 | 处置 | 恢复检查 |
|---|---|---|---|
| API 503 | PostgreSQL health、磁盘 | 保持 scheduler 停止领取；恢复 DB | `/health/ready` 200 |
| Redis 不通 | Redis health/密码 | 重启 Redis；任务仍在 DB，扫描会继续 | scheduler 无 publish/listen 告警 |
| 队列不动 | scheduler 单主、loop lag、节点排除理由 | 启动唯一 scheduler；修复 health/模式 | QUEUED 减少 |
| 3090 离线 | `nvidia-smi`、8188、容器日志 | Drain，重启 ComfyUI；驱动问题重启宿主机 | system_stats + ONLINE |
| 3090-B WSL2 深度探针丢失 | 9201、Node Agent、HMAC、WSL uptime/boot ID | 保持业务健康判断独立；先查 9201，确认无任务后再处理 WSL | `gpu_control_wsl_system_probe_up=1` |
| 3090-B WSL2 内存/CPU/PSI 异常 | 可用内存、swap、load/CPU、memory full PSI、Windows 后台进程 | 先 Drain 并等当前帧结束；停止异常后台负载或在授权后重启 | 状态指标连续恢复且 8188/9201 正常 |
| 3090-B WSL2 GPU 持续高温 | Node Agent 温度、功耗/上限、显存、Windows 后台负载 | 先 Drain 并等待当前任务结束；检查散热与宿主负载 | 温度持续低于 85°C 且 GPU 查询正常 |
| 3090-B WSL2 速度超过 A 两倍 | WSL 性能比、健康抖动、GPU 温度/功耗/驱动、Windows 后台进程 | 先 Drain 并等当前帧结束；确认无任务后检查或重启 WSL/宿主，禁止直接改工作流 | 最近 5 帧比值低于 2 且无健康抖动 |
| 4090 意外出图 | mode、manual reserve、哨兵、阈值 | 立即 Reserve 并创建 sentinel | overflow counter 不再增长 |
| prompt 状态未知 | DB prompt_id、Comfy queue/history | 不重试；保留诊断，人工协调 | 状态明确后 retry/close |
| 显存不足 | DCGM、模型、工作流 min_vram | Drain、释放模型、校正兼容性 | 冒烟成功 |
| 输出缺失/损坏 | history/output、下载 hash、磁盘 | 保留历史；修复存储后人工 retry | artifact SHA 正确 |
| Loki 无日志 | Alloy 状态、3100、时间 | 查本地日志并恢复 Alloy | Explore 出现新事件 |
| 回调失败 | callback_attempts、DNS/HTTPS/签名 | 修复客户 endpoint；等待退避或建新任务 | SUCCEEDED attempt |

重启顺序：PostgreSQL → Redis → Loki/Prometheus → API → scheduler → 节点。重启 ComfyUI 前先 Drain；若节点运行任务，先等待或明确中断。3090-B 重启后依次确认 Node Agent、ComfyUI、Node
Exporter、Linux Worker/Codex 和四个 Windows Baker，并确认新 boot ID、uptime、内存/PSI 指标可读，
再恢复 `ACTIVE`。紧急诊断：
`scripts/collect_diagnostics.sh`。
