# ComfyUI projects-0.2.3 离线镜像

本目录通过 Git LFS 分发三节点生产验收使用的最终 ComfyUI 镜像。原始归档超过 GitHub
Git LFS 单文件上限，因此按 1900 MiB 拆为 5 个 LFS 对象。

验收值：

- 镜像：`registry.local:5000/gpu-control/comfyui:projects-0.2.3`
- Image ID：`sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea`
- 原归档大小：`8271225047` bytes
- 原归档 SHA-256：`20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586`
- GPU Control 源码提交：`f3888e85cb927314a2cb1da07ea78b3e5d028f6d`

恢复与校验：

```bash
git lfs install
git lfs pull
cd artifacts/comfyui/projects-0.2.3
sha256sum -c SHA256SUMS.txt
cat comfyui-projects-registry-0.2.3.tar.gz.part-* \
  > /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
echo '20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586  /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz' \
  | sha256sum -c -
gzip -dc /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz \
  | docker load
```

归档不包含模型、任务、数据库、`.env`、证书或运行时数据。不要修改任一分片；后续升级使用
新版本目录，避免覆盖旧 LFS 对象和破坏回滚。
