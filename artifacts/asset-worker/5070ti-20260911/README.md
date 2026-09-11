# 5070 Ti Blender Worker / Codex 探针归档

已部署镜像为 `li3d/blender-worker:1.4.75-uv-overlap-v28-pins1-codex-auth2`，Docker Engine 本机身份
`sha256:0c6ea65709566114544710dcac3255b94ac71f53138f158e4a678c6abcacb992`。
详见 [Codex 探针验收](../../../docs/5070TI_CODEX_AUTH_PROBE_20260911.md)。

探针区分未授权、授权失效及本地运行失败。auth2 修复已安装包的 `RECORD`，不改动 auth1 的运行代码、模型、工作流或技能。
`source/patched/main.py` 为实际导入的模块；`source/worker-main.patch` 与 `patch-proof.json` 记录基础模块到已部署模块的变化。
`Dockerfile` 创建 auth1 层，`Dockerfile.metadata` 再创建 auth2 元数据层。既有基础镜像和 1.5.23 Python 包版本仍是其原身份，不将整个包声称为新重构源码。

```bash
cd artifacts/asset-worker/5070ti-20260911/image-parts
sha256sum -c SHA256SUMS.txt
cat runtime-images.tar.gz.part-* | sha256sum
cat runtime-images.tar.gz.part-* | gzip -dc | docker load
```

镜像归档总 SHA-256：`d911f9bd18649e67e5b21f714033cc1fd6f66c0ff63174a457ad9fb236b44b2c`，689900572 字节，6 片。
完整层、标签与每片摘要见 [`image-parts/manifest.json`](image-parts/manifest.json)。

`asset-runtime.tar.gz.part-000` 为独立的 125320171 字节运行资源归档，共 465 个文件，摘要
`6a6345e62c94cdff59d79367e14db22c61d7cfcd26fafe26ac020115c2008cfd`。
其内容为已批准的技能、Codex 可执行程序、RetopoFlow 和公开 LAN CA；不包含 CODEX_HOME、auth.json 或其他凭据。
核验 [`asset-runtime-manifest.json`](asset-runtime-manifest.json) 后先查看 `tar -tzf`，按新节点 Compose 的挂载目录提取到独立暂存目录再安装；不要盲目覆盖已有节点运行目录。

5070 Ti 的 Codex CLI 已安装，但实际用户授权仍为 `MISSING/BLOCKED/AUTH_MISSING`。已过期的设备码不可复用；登录应在此节点独立完成，不复制其他节点的 OAuth 刷新令牌。
完整 Windows 原生 MOF/Substance 安装与许可不包含在此归档。
