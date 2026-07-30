# ComfyUI 双项目镜像构建与三机分发

当前采用“固定 Docker 镜像 + 两个独立业务仓库只读外挂”的结构。镜像包含
ComfyUI、CUDA、PyTorch 和公共自定义节点；ImageClip、ModelViewCreator 的工作流、
内部节点和模型仍以各自仓库为真相源，不复制到 GPU Control，也不写入容器层。

当前正式镜像：

```text
gpu-control/comfyui:projects-0.2.3
registry.local:5000/gpu-control/comfyui:projects-0.2.3
Image ID: sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea
Image size: 8292205258 bytes
lock SHA-256: 5ef4ba8cc88fd24a0fc81c997420bcbbf5cbae96fb96aff1276b7c3c5d60648d
ComfyUI commit: 700821e1364eaab0e8f21c538a2131719fec57bf
```

两个 tag 必须指向同一个 Image ID。三台 GPU 机器都必须使用这个固定 tag 和相同
Image ID，禁止使用 `latest`，也禁止运行后通过 `docker commit` 制作差异镜像。

## 1. 宿主机资源和容器映射

| 宿主机真相源 | 容器路径 | 方式 |
|---|---|---|
| `/opt/imageclip/models` | `/opt/comfyui/models` | 只读目录挂载 |
| `/opt/modelviewcreator/model` | `/opt/modelviewcreator/model` | 只读目录挂载，并由 extra model paths 注册 |
| `/opt/imageclip/Cherry_lizi` | `/opt/comfyui/custom_nodes/Cherry_lizi` | 只读目录挂载 |
| `/opt/modelviewcreator/custom_nodes/haoze-LiClick` | `/opt/comfyui/custom_nodes/haoze-LiClick` | 只读目录挂载 |
| `/opt/imageclip/ImageClip.json` | `user/default/workflows/ImageClip.json` | 只读文件挂载 |
| `/opt/modelviewcreator/flux_fill_inpaint.json` | `user/default/workflows/ModelViewCreator_flux_fill_inpaint.json` | 只读文件挂载 |
| `/srv/comfyui/<节点>/input` | `/opt/comfyui/input` | 可写持久化 |
| `/srv/comfyui/<节点>/output` | `/opt/comfyui/output` | 可写持久化 |
| `/srv/comfyui/<节点>/user` | `/opt/comfyui/user` | 可写持久化 |

`configs/comfyui-extra-model-paths.yaml` 把 ModelViewCreator 的 `clip`、`unet`、
`vae` 和 `lora` 映射到 ComfyUI 对应模型类别。两个工程重名模型会同时出现在下拉框，
原始文件不会被移动。

## 2. ModelViewCreator Git LFS

ModelViewCreator 的五个模型由 Git LFS 管理。普通 `git clone` 后如果文件只有约
130 字节，它只是指针，必须先拉取真实对象：

```bash
sudo apt-get install -y git-lfs
cd /opt/modelviewcreator
git lfs install --local
git lfs pull
git lfs status
git status --short --branch
```

`git lfs status` 不应列出待处理文件，工作树应保持干净。随后统一校验两个项目：

```bash
cd /opt/gpu-control
scripts/verify_comfy_projects.sh
```

该命令按 manifest 同时校验文件大小和 SHA-256。任何一项失败都不得启动正式任务。

## 3. 构建与验证

公共节点在 `docker/comfyui/custom_nodes.lock.yaml` 中固定完整 commit。当前包含：

- ComfyUI-GGUF、rgthree-comfy、ComfyUI-Easy-Use；
- ComfyUI-nunchaku 1.0.1 与 Nunchaku 1.0.0/torch2.7 后端；
- ComfyUI-Inpaint-CropAndStitch；
- ComfyUI-KJNodes 1.4.7。

构建新版本必须使用新 tag：

```bash
cd /opt/gpu-control
scripts/build_comfyui_image.sh \
  --tag registry.local:5000/gpu-control/comfyui:projects-0.2.3

docker image inspect \
  registry.local:5000/gpu-control/comfyui:projects-0.2.3 \
  --format '{{.Id}} {{.Size}} {{json .Config.Labels}}'
```

启动前运行模型校验，启动后检查状态：

```bash
scripts/verify_comfy_projects.sh
scripts/comfyui-server.sh start
scripts/comfyui-server.sh status
```

浏览器访问 `http://服务器IP:8188`。常用维护命令：

```bash
scripts/comfyui-server.sh logs
scripts/comfyui-server.sh restart
scripts/comfyui-server.sh stop
scripts/comfyui-server.sh start
```

正式验收必须确认两个挂载工作流的缺失节点数量均为 `0`，并确认 Nunchaku、CLIP、
VAE 和 LoRA loader 能看到 manifest 中的文件。4090 上还应实际加载一次 Nunchaku
UNet；只看到文件名不算模型兼容性验收。

## 4. 日常更新规则

内部节点或工作流更新不需要重建镜像：

```bash
cd /opt/imageclip
git pull --ff-only

cd /opt/modelviewcreator
git pull --ff-only
git lfs pull

cd /opt/gpu-control
scripts/verify_comfy_projects.sh
scripts/comfyui-server.sh restart
```

公共自定义节点、ComfyUI、Python 或 PyTorch 变化时必须修改 lock，构建新的不可变
tag，在 18188 等隔离端口验证后再切换 8188。禁止在运行容器内使用 Manager 或 pip
临时安装，否则三台机器会产生不可追踪差异。

模型仍保留在两个业务仓库路径下。ImageClip 的大模型通过受控下载或 rsync 同步；
ModelViewCreator 使用 Git LFS。每台机器同步后都运行 `verify_comfy_projects.sh`。

## 5. 导出和安装到 3090

4090 导出：

```bash
cd /opt/gpu-control
scripts/export_comfyui_image.sh \
  --image registry.local:5000/gpu-control/comfyui:projects-0.2.3 \
  --output /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
```

把 `.tar.gz` 和同名 `.sha256` 一起复制到 3090。目标机必须克隆三个仓库的固定
提交，执行 ModelViewCreator 的 `git lfs pull`，再导入镜像：

当前 `0.2.3` 归档验收值：

```text
文件：/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
SHA-256：20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586
```

```bash
cd /opt/gpu-control
scripts/import_comfyui_image.sh \
  --input /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz

docker image inspect \
  registry.local:5000/gpu-control/comfyui:projects-0.2.3 \
  --format '{{.Id}} {{.Size}}'

scripts/verify_comfy_projects.sh
COMFY_DATA_ROOT=/srv/comfyui/runtime scripts/comfyui-server.sh start
```

4090 和两台 3090 的 `docker image inspect` 必须返回相同 Image ID。Docker 镜像不是
Git 仓库，不能 `git pull` 同步：小代码由三个 Git 仓库同步，镜像由新 tag 的归档或
内网 Registry 同步，大模型由 LFS/rsync 同步。

### 5.1 历史 0.2.2 验收值（已被 0.2.3 取代）

以下值只保留用于审计和回滚，不能再作为新节点部署基线：

```text
Image ID: sha256:bb8c76cfb0bf18c1caff7cfe2a758a9ec1e049543180f117d75af2e94d73a325
Image size: 8284294954 bytes
lock SHA-256: 5b57a8cba970c41329b5d7a3af0ecf8426c6793c4cdaade4218d38ad0ee41a65
archive: /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
archive size: 8263311384 bytes
archive SHA-256: 97c5e8f73fd189a29b59ac7c6a851f9278fe53bb641c118fd20baec22c027ddc
```

## 6. 回滚

升级前停止旧容器并改名保留，不删除 `/srv/comfyui`。新镜像失败时停止新容器，
把旧容器名恢复后启动，或指定旧 `COMFY_IMAGE` 重建容器。禁止执行
`docker compose down -v`。

4090 当前保留的直接回滚容器：

```text
comfyui-4090-imageclip-backup-20260723
comfyui-4090-base-backup
```

## 7. 已知非阻塞项与安全要求

- 两个正式工作流依赖已经按 lock 固定；额外可选节点是否可用不作为生产验收标准。
- Nunchaku 的可选 PuLID 节点未安装 `insightface`；ModelViewCreator 工作流不使用
  PuLID，`NunchakuFluxDiTLoader` 已在 4090 实际加载成功。
- 镜像切换时已经打开的浏览器标签页会保留旧的缺失节点占位对象，运行时可能报告
  `has no class_type`。关闭该工作流标签页，按 `Ctrl+Shift+R` 强制刷新，再从左侧
  “工作流”重新打开挂载文件；不要保存旧标签页覆盖源文件。
- `haoze-LiClick` 当前仓库源码/配置中存在明文第三方 API 凭据。不得在文档或日志
  中复制该值；应尽快轮换，并改成仅从环境变量或受限 secret 文件读取。完成改造前，
  只允许在受控内网服务器使用该仓库。
