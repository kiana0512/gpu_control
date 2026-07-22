# ComfyUI 镜像构建

镜像由 `docker/comfyui/Dockerfile` 可复现构建：Ubuntu/CUDA、Python、ComfyUI 完整 commit、Python lock 和自定义节点 commit 均固定；模型通过只读 volume 注入。

在 4090 执行：

```bash
scripts/gpuctl comfy build
docker image inspect "$COMFY_IMAGE" --format '{{json .Config.Labels}}'
```

检查点：标签含 ComfyUI commit 和 lock SHA；`docker run --rm --gpus all -v /srv/comfyui/models:/models:ro "$COMFY_IMAGE" --help` 能启动 Python。构建失败先检查 `configs/versions.lock.env` 和网络；修复 lock 后重新构建同一新 tag，不覆盖已部署 tag。

自定义节点必须写入 `custom_nodes.lock.yaml` 的仓库 URL 与完整 commit。禁止容器运行时安装节点、禁止 `docker commit`、禁止把模型 COPY 进镜像。回滚只需恢复旧 `COMFY_IMAGE` tag 并重建容器。

