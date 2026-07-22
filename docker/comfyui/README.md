# ComfyUI 可复现镜像

镜像固定 ComfyUI v0.28.0 的完整 commit、Python 3.11.13、CUDA 12.8.1 和直接 Python 依赖。模型通过 `/models` 只读挂载，不进入镜像。自定义节点只能写入 `custom_nodes.lock.yaml`，并为每个节点提供固定 commit 与单独锁定的 requirements 文件。

构建：`scripts/gpuctl comfy build`。分发见 `docs/08_IMAGE_DISTRIBUTION.md`。生产禁止在容器内安装节点，也禁止使用 `docker commit`。
