# GPU Control 1.3.4 控制面离线镜像

本目录通过 Git LFS 分发最终控制面镜像：

- `gpu-control-api:1.3.4`
  - Image ID：`sha256:39212e3422ab254d1ad08f4fd7ca08221ac4582cbebacfe3c1286b6453bf3942`
- `gpu-control-scheduler:1.3.4`
  - Image ID：`sha256:38427bca133cdbfd883577642e6d241fc6c793b4d1fb9fc911767353c8a06ee4`
- `gpu-control-web:1.3.4`
  - Image ID：`sha256:b6c9dbdf7dc7dd399ca07c6f3e4bdd76f5e94037515e6f0e4cd7f1a76f4623d9`

归档信息：

- 大小：`149836214` bytes
- SHA-256：`462ab55f9775d4818b97f713a383b640d484f1a6a3a40d34d4204b13d21e1b36`
- GPU Control 源码提交：`f3888e85cb927314a2cb1da07ea78b3e5d028f6d`

恢复与校验：

```bash
git lfs install
git lfs pull
cd artifacts/control-plane/1.3.4
sha256sum -c SHA256SUMS.txt
cat gpu-control-control-plane-1.3.4.tar.gz.part-* \
  > /srv/gpu-control/images/gpu-control-control-plane-1.3.4.tar.gz
echo '462ab55f9775d4818b97f713a383b640d484f1a6a3a40d34d4204b13d21e1b36  /srv/gpu-control/images/gpu-control-control-plane-1.3.4.tar.gz' \
  | sha256sum -c -
gzip -dc /srv/gpu-control/images/gpu-control-control-plane-1.3.4.tar.gz \
  | docker load
```

归档不包含 `.env`、数据库、任务、模型、证书或其他运行时数据。
