# 镜像分发

无私有仓库时，在 4090：

```bash
mkdir -p /srv/gpu-control/images
scripts/export_comfyui_image.sh --image "$COMFY_IMAGE" --output /srv/gpu-control/images/comfyui-0.1.0.tar.gz
sha256sum -c /srv/gpu-control/images/*.sha256
scp /srv/gpu-control/images/comfyui-*.tar* gpucontrol@WORKER_IP:/srv/gpu-control/images/
```

在每台 3090：`scripts/import_comfyui_image.sh --input /srv/gpu-control/images/FILE.tar.gz`。检查点：`docker image inspect "$COMFY_IMAGE"` 成功且 digest/标签与 4090 一致。

也可启动 `deploy/registry/compose.yaml` 后用 `scripts/push_local_registry.sh`；registry 只对内网开放。导入失败保留旧镜像，清理不完整文件后重传；回滚把 `.env` 的 `COMFY_IMAGE` 改回旧 tag 并 `docker compose up -d --force-recreate comfyui`。
