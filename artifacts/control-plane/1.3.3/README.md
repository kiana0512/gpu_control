# GPU Control 1.3.3 控制面镜像

本目录通过 Git LFS 分发已完成三节点真实压力与批量序列帧验收的控制面镜像：

- `gpu-control-api:1.3.3`
- `gpu-control-scheduler:1.3.3`
- `gpu-control-web:1.3.3`

当前归档只有一个分片。恢复与校验：

```bash
cat gpu-control-control-plane-1.3.3.tar.gz.part-* > gpu-control-control-plane-1.3.3.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -dc gpu-control-control-plane-1.3.3.tar.gz | docker load
```

归档不包含 `.env`、数据库、任务、模型、证书或其他运行时数据。完整部署和验收事实见
`docs/41_2026-07-27_GPU_CONTROL_1_3_2_STRESS_AND_PIPELINE_RECORD.md`。
