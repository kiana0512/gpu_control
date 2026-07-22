# 监控与飞书

Prometheus 抓取 API、scheduler、node exporter、DCGM、PostgreSQL、Redis。Grafana 已 provision Loki/Prometheus 和 GPU Control 总览。核心指标：队列深度/最老等待、任务吞吐与失败、API 延迟、调度决策与 loop lag、节点槽位、4090 overflow 次数、回调尝试/失败、GPU 利用率/显存/温度。

Alertmanager 规则覆盖 API/节点离线、队列堆积、调度延迟、GPU 高温/低显存、任务和回调持续失败。将 `FEISHU_WEBHOOK_URL` 与 `FEISHU_SIGNING_SECRET` 写入权限 600 的 `.env`，在后台点击“飞书测试”。检查点：飞书收到测试卡片，告警解除后收到恢复通知。

失败时检查 `http://127.0.0.1:9090/targets`、Alertmanager alerts、API 日志中的 `feishu` 事件。回滚为清空两项环境变量并重建 API/Alertmanager，不影响任务执行。

