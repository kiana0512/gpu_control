# 统一调度中心 1.5.0 离线镜像

本目录通过 Git LFS 分发 2026-07-28 已部署并验收的正式镜像归档，包含：

- `gpu-control-api:1.5.0`
- `gpu-control-scheduler:1.5.0`
- `gpu-control-web:1.5.0`（包含分页、字号及首页任务表布局修复）
- `unified-scheduler-asset-api:1.5.0`
- `li3d/blender-worker:1.1.0`

归档信息：

- 文件：`unified-scheduler-1.5.0-images.tar.gz`
- 大小：`826500078` bytes
- SHA-256：`598f25a0a9100b9ebd1e87d084eb3be31e2168ac1b624768260619abc3fbfac8`
- GPU Control 源码基线：`f38f583`

恢复与校验：

```bash
git lfs install
git lfs pull
cd artifacts/control-plane/1.5.0
sha256sum -c SHA256SUMS.txt
cat unified-scheduler-1.5.0-images.tar.gz.part-* \
  > /srv/gpu-control/images/unified-scheduler-1.5.0-images.tar.gz
echo '598f25a0a9100b9ebd1e87d084eb3be31e2168ac1b624768260619abc3fbfac8  /srv/gpu-control/images/unified-scheduler-1.5.0-images.tar.gz' \
  | sha256sum -c -
gzip -dc /srv/gpu-control/images/unified-scheduler-1.5.0-images.tar.gz \
  | docker load
```

归档不包含 `.env`、密钥、证书、数据库、任务、模型或其他运行时数据。
