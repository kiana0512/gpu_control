# ComfyUI projects-0.2.2 离线镜像

原始镜像归档超过 GitHub Git LFS 的单文件上限，因此按 1900 MiB 拆为 5 个 LFS 对象。
检出仓库前需安装 Git LFS：

```bash
git lfs install
git lfs pull
cd artifacts/comfyui/projects-0.2.2
sha256sum -c SHA256SUMS.txt
cat comfyui-projects-registry-0.2.2.tar.gz.part-* \
  > /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
echo '97c5e8f73fd189a29b59ac7c6a851f9278fe53bb641c118fd20baec22c027ddc  /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz' \
  | sha256sum -c -
```

验收值：

- 镜像：`registry.local:5000/gpu-control/comfyui:projects-0.2.2`
- Image ID：`sha256:bb8c76cfb0bf18c1caff7cfe2a758a9ec1e049543180f117d75af2e94d73a325`
- 原归档大小：`8263311384` bytes
- 原归档 SHA-256：`97c5e8f73fd189a29b59ac7c6a851f9278fe53bb641c118fd20baec22c027ddc`

不要修改任一分片；镜像升级时使用新版本目录，避免覆盖旧 LFS 对象和破坏回滚。
