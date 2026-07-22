# 日志与排错

API、scheduler、ComfyUI、Docker 和 systemd 日志由每台 Alloy 发往 4090 Loki。应用日志为 JSON，并统一携带 `request_id/trace_id/job_id/tenant_id/workflow_key/workflow_version/node_id/prompt_id/attempt/event/duration_ms/error_code`；Authorization、Key、密码、Cookie 和 Secret 字段会脱敏。

Grafana Explore 示例：

```logql
{job=~".+"} | json | job_id="JOB_ID"
{job=~".+"} | json | node_id="worker-3090-a"
{job=~".+"} | json | error_code!=""
{job="gpu-control-scheduler"} | json | prompt_id="PROMPT_ID"
```

```mermaid
flowchart TD
  S["用户报告失败"] --> J["取得 job_id/request_id"]
  J --> D["查 jobs/job_events/job_attempts"]
  D --> L["Loki 按 job_id"]
  L --> N{"是否节点/Comfy 错误"}
  N -->|是| C["查 system_stats/queue/history 与节点日志"]
  N -->|否| A["查 API/工作流/存储"]
  C & A --> Z["下载去敏诊断包"]
```

CLI：`scripts/gpuctl diagnostics job JOB_ID`。诊断包只含白名单 JSON/状态，不含 Key 与私有参数。按时间排查先确认三机 NTP。Loki 不通时本地 `docker compose logs --tail 200 SERVICE` 和 `journalctl -u gpu-node-agent --since -30min` 仍可用。

