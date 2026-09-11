# 5070 Ti 五节点增量发布 · 2026-09-11

本目录归档实际部署镜像、公开配置和脱敏验收记录。完整状态见
[部署验收](../../../docs/5070TI_DEPLOYMENT_ACCEPTANCE_20260911.md)及
[发布审计](../../../docs/5070TI_RELEASE_AUDIT_20260911.md)。

| 组件 | 已部署镜像标签 | 源码定位 |
|---|---|---|
| API | `gpu-control-api:1.5.23.post3-5070-c61648f` | `runtime/5070-control-c61648f` |
| Scheduler | `gpu-control-scheduler:1.5.23.post3-5070-c61648f` | 同上 |
| Web | `gpu-control-web:1.5.23-5070-ui-350c388` | `runtime/5070-web-350c388` |
| 新节点 Blender Worker | `li3d/blender-worker:1.4.75-uv-overlap-v28-pins1-codex-auth2` | [Worker 归档](../../asset-worker/5070ti-20260911/README.md) |

源码标签是独立的运行版本快照：control 为完整仓库布局，web 为 Web 项目根目录布局。
[`source/`](source/README.md) 保存各次控制层的基础文件摘要与重建脚本，从锁定 Git 提交复原完整构建输入，避免重复提交 API/Scheduler 源码。发布提交中的业务管理源码包含这些变更。
主仓原有未提交 UV/MOF 迭代保留在工作区，未混入本次控制平面提交；新 Worker 的已部署模块原字节另附在 Worker 归档中。

## 镜像校验与恢复

先通过 Git LFS 下载本次目录。以下步骤仅导入镜像；生产替换须先将目标节点 Drain 并确认任务结束。

```bash
git lfs pull --include='artifacts/control-plane/5070ti-20260911/**,artifacts/asset-worker/5070ti-20260911/**'
cd artifacts/control-plane/5070ti-20260911/release-parts
sha256sum -c SHA256SUMS.txt
cat runtime-images.tar.gz.part-* | sha256sum
cat runtime-images.tar.gz.part-* | gzip -dc | docker load
```

组合归档 SHA-256：`d648239a8f31ca0c16d45703c8cbb6fb896aea7b3a24e8928bf97bfce74d7161`，155435934 字节。
[`release-parts/manifest.json`](release-parts/manifest.json) 记录每片摘要、标签、实际 Docker image ID、层和 OCI 标签；这些是本机 Engine 身份，不声明 registry digest 或 SBOM。

采用现有私有 `.env` 中独立保存的 `APP_IMAGE_TAG`、`SCHEDULER_IMAGE_TAG`、`WEB_IMAGE_TAG` 选择上表版本。
`REALESRGAN_NODES` 必须保留已验收的五节点清单；Compose 已取消写死四节点清单，缺少变量将直接报错。
新增节点 HMAC 放入 `NODE_AGENT_HMAC_SECRETS`，从受控秘密存储恢复；不得使用示例值覆盖现网。
旧节点的环境、挂载、网络身份及回滚容器必须保留。不要对全仓运行未经审查的 Compose 重建来覆盖既有 UV/MOF 服务。

本次为增量包，未重复上传已批准的 ComfyUI、RealESRGAN 基础镜像和 20 个模型；其锁定配置、版本与模型清单见
[`deploy/gpu-node/5070ti`](../../../deploy/gpu-node/5070ti)。外部 ImageClip/ModelViewCreator 的源码和工作流未修改。

## 更新后的 Windows 预处理包

`release-parts/windows-prep-20260911.zip.part-000` 是完整单片 ZIP，下载后重命名为 `.zip` 即可解压。
先按 [`windows-prep-archive.json`](windows-prep-archive.json) 校验整包摘要，解压后再验证内部 `SHA256SUMS.txt`。
[`windows-prep/`](windows-prep) 保留与归档一致的原始字节，不执行 Git 换行转换。
包只含公开管理公钥和公开 LAN CA，不含私钥、登录凭据或集群密钥。

此包替代 20260910 的初始预处理包，包含现场已验证的单地址解析、精确防火墙规则兼容修复，以及经 Windows PowerShell 5.1 测试的 Inspect 容错。
已部署的 5070 Ti 无需重新初始化。Windows 完整重启后仍需 `lilithgames` 登录；此次只完成 WSL 发行版终止后的自动恢复验收。

## 证据

`evidence/` 保留完整 GPU/CPU 功能验收、最终 HTTPS 静态资源摘要与浏览器路由、生产任务计数、Codex 真值状态、镜像包元数据和 Windows 恢复记录。
文件名称含 `final-` 的 Web 验证对应最终 ui.2；早期全导航验收对应同功能的前一版 UI，最终版仅更正页脚标签与版本。
运行日志与原始传输包不作为源码入库；经复核的重复副本清理结果见发布审计。
