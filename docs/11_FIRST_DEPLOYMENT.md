# 首次部署

执行位置：4090 控制中心，前提是三台机器已按 05/06 文档完成 GPU 容器验证。

```bash
cp .env.example .env
chmod 600 .env
scripts/gpuctl doctor
scripts/gpuctl deploy control --build-only
# build-only 不激活服务；按当前发布手册逐个指定 service 完成首次激活后再继续：
docker compose -f deploy/control-plane/compose.yaml run --rm --no-deps api python scripts/bootstrap_admin.py
cp configs/nodes.example.yaml configs/nodes.yaml
# 编辑为真实 IP 后：
/opt/gpu-control/.venv/bin/python scripts/bootstrap_nodes.py --config configs/nodes.yaml
```

随后分发镜像/模型并在两台 3090 执行
`GPU_CONTROL_ROLE=node scripts/gpuctl deploy node --build-worker-only`。该命令仍不激活服务；确认节点
`DRAINING`、任务/租约为 0 后，只更新明确的 `blender-worker` service，不运行无 service 范围的
`compose up/down`。完成受控激活后登录 Web，确认两台 3090 为 `ONLINE/ACTIVE`、4090 为
`RESERVED`，再导入并启用真实 API 工作流、创建 API 客户和一次性 Key，并按
[公共 API](13_PUBLIC_API_GUIDE.md) 提交首个任务。

检查点：API 返回 202；状态依次到 SUCCEEDED；任务目录有 rendered workflow/history/output；Grafana 可按 job_id 查日志。任一步失败先 Drain 节点并按 [故障手册](20_FAILURE_RUNBOOK.md) 排查。回滚：禁用工作流、停止接单、备份、恢复旧镜像 tag；不要删除数据库卷。
