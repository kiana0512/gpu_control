# 首次部署

执行位置：4090 控制中心，前提是三台机器已按 05/06 文档完成 GPU 容器验证。

```bash
cp .env.example .env
chmod 600 .env
scripts/gpuctl doctor
scripts/gpuctl deploy control
docker compose -f deploy/control-plane/compose.yaml run --rm api python scripts/bootstrap_admin.py
cp configs/nodes.example.yaml configs/nodes.yaml
# 编辑为真实 IP 后：
/opt/gpu-control/.venv/bin/python scripts/bootstrap_nodes.py --config configs/nodes.yaml
```

随后分发镜像/模型并在两台 3090 执行 `GPU_CONTROL_ROLE=node scripts/gpuctl deploy node`。登录 Web，确认两台 3090 为 `ONLINE/ACTIVE`，4090 为 `RESERVED`。导入并启用真实 API 工作流，创建 API 客户和一次性 Key，再按 [公共 API](13_PUBLIC_API_GUIDE.md) 提交首个任务。

检查点：API 返回 202；状态依次到 SUCCEEDED；任务目录有 rendered workflow/history/output；Grafana 可按 job_id 查日志。任一步失败先 Drain 节点并按 [故障手册](20_FAILURE_RUNBOOK.md) 排查。回滚：禁用工作流、停止接单、备份、恢复旧镜像 tag；不要删除数据库卷。
