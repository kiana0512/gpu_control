# 镜像分发

当前三机统一镜像为：

```text
registry.local:5000/gpu-control/comfyui:projects-0.2.2
```

模型和内部项目节点不进入镜像；镜像内固定 ComfyUI、Python、PyTorch/CUDA、
公共自定义节点及其 Python 依赖。无私有仓库时，在 4090：

```bash
mkdir -p /srv/gpu-control/images
scripts/export_comfyui_image.sh \
  --image registry.local:5000/gpu-control/comfyui:projects-0.2.2 \
  --output /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
sha256sum -c /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz.sha256
scp /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz* \
  USER@WORKER_IP:/srv/gpu-control/images/
```

在每台 3090：

```bash
cd /opt/gpu-control
sha256sum -c /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz.sha256
scripts/import_comfyui_image.sh \
  --input /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
sudo docker image inspect registry.local:5000/gpu-control/comfyui:projects-0.2.2
```

检查点：3090 的 Image ID、ComfyUI commit 和 `io.gpu-control.lock-sha256` 必须与
4090 记录一致。不要用 `docker commit`，也不要覆盖旧 tag。

也可启动 `deploy/registry/compose.yaml` 后使用 `scripts/push_local_registry.sh`；registry
只对内网开放。导入失败时保留旧镜像并重传归档。回滚只需把 `.env` 的
`COMFY_IMAGE` 改回旧 tag，再重建 `comfyui` 服务；不要删除 volume 或模型。
