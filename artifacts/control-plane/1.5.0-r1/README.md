# 统一调度中心 1.5.0-r1 离线镜像

本目录通过 Git LFS 分发 2026-07-28 已构建、静态审计并完成禁网 smoke 的候选镜像归档。源码基线为 `e492779`。

包含：

- `gpu-control-api:1.5.0-r1`
- `gpu-control-scheduler:1.5.0-r1`
- `gpu-control-web:1.5.0-r1`
- `unified-scheduler-asset-api:1.5.0-r1`
- `li3d/blender-worker:1.1.0-r1`

镜像 ID：

```text
gpu-control-api              sha256:0a486eaf3b6dd66309e397359984a3460cf1f88fa26044d0cefe2d19dec2fae0
gpu-control-scheduler        sha256:4324b575d504687ffcb609e73a410dddf2c4f98080bea9ba7977804b0b34e74b
gpu-control-web              sha256:a8023030c8999d853fb1e1b6b5d299f4cb2f64b985ad007e922118f46090d757
unified-scheduler-asset-api  sha256:0037f90adf1cbe1184eebb81aba755b75740b46e55268068430d62c6db2bcd72
li3d/blender-worker          sha256:2aab3454556d21815cacd9b046ec7dcd0a416400c9f5af4c246ca36203755a29
```

归档信息：

- 文件：`unified-scheduler-1.5.0-r1-images.tar.gz`
- 大小：`826519963` bytes
- SHA-256：`0c68057f66f2c143f203f54b98533e1fb419a8df0f70ad7704646836b1521ccb`
- Git LFS 分发：`part-00` 至 `part-06`，每个完整分片 `128 MiB`，支持并发上传和断点重试
- 服务器归档：`/srv/gpu-control/images/unified-scheduler-1.5.0-r1-images.tar.gz`

恢复：

```bash
git lfs pull
cd artifacts/control-plane/1.5.0-r1
sha256sum -c SHA256SUMS.txt
cat unified-scheduler-1.5.0-r1-images.tar.gz.part-* \
  > /srv/gpu-control/images/unified-scheduler-1.5.0-r1-images.tar.gz
printf '%s  %s\n' \
  '0c68057f66f2c143f203f54b98533e1fb419a8df0f70ad7704646836b1521ccb' \
  '/srv/gpu-control/images/unified-scheduler-1.5.0-r1-images.tar.gz' \
  | sha256sum -c -
gzip -t /srv/gpu-control/images/unified-scheduler-1.5.0-r1-images.tar.gz
gzip -dc /srv/gpu-control/images/unified-scheduler-1.5.0-r1-images.tar.gz | docker load
```

本归档不包含 `.env`、API Key、HMAC/JWT 密钥、证书私钥、数据库、任务、模型或其他运行时数据。`1.5.0-r1` 当前是已验收构建候选，并未替换在线 API/Scheduler/ComfyUI；生产滚动发布必须先 Drain 并确认在途任务为 0。
