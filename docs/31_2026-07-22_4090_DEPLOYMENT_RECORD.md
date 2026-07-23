# 2026-07-22 RTX 4090 主控部署记录

本文记录 2026-07-22 在 `lilithgames2` 上完成的实际部署状态，供后续 3090 节点接入、故障排查、镜像分发和验收使用。本文不记录任何密码、API Key、JWT、HMAC 或数据库密钥。

## 0. 2026-07-23 双项目升级记录

本节是当前状态，优先级高于下方 2026-07-22 的 ImageClip-only 基线记录。

> 注意：第 4 节起保留的是 2026-07-22 历史命令，其中的 `imageclip-0.1.0` 不再用于
> 新节点。3090 部署必须使用 `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md`。

- 正式 8188 已切换到 `gpu-control/comfyui:projects-0.2.2`。
- Registry tag 为 `registry.local:5000/gpu-control/comfyui:projects-0.2.2`，两个 tag
  指向同一 Image ID：
  `sha256:bb8c76cfb0bf18c1caff7cfe2a758a9ec1e049543180f117d75af2e94d73a325`。
- 镜像 lock 指纹为
  `5b57a8cba970c41329b5d7a3af0ecf8426c6793c4cdaade4218d38ad0ee41a65`；
  真实 GPU 隔离启动、`pip check` 和两套 API 工作流节点检查均通过。
- ImageClip 当前固定提交为
  `bb243808a6bd43055ad92c1071b2ea949b1d9ea1`；ModelViewCreator 提交为
  `b22bb377d200d10ae1af565494674fdfb53580dc`。
- ModelViewCreator 的 5 个 Git LFS 模型已全部从指针还原，仓库工作树保持干净，
  大小和 SHA-256 全部通过 `scripts/verify_comfy_projects.sh`。
- `ImageClip.json` 共 47 个节点，`flux_fill_inpaint.json` 共 18 个节点；正式容器
  `/object_info` 对比后的缺失节点数量均为 0。
- ModelViewCreator 所需的 Nunchaku、CropAndStitch、KJNodes 公共节点已固定 commit
  写入镜像；内部 `haoze-LiClick` 与 ImageClip 的 `Cherry_lizi` 均由各自仓库只读挂载。
- Nunchaku 6.77GB UNet 已在 RTX 4090 上实际初始化成功，返回 ComfyUI
  `ModelPatcher`；`pip check` 无损坏依赖。
- 生产容器 `comfyui-4090` 已健康运行并加入 `gpu-control_backend`，访问地址仍为
  `http://10.3.34.11:8188`。
- 原 ImageClip-only 容器已停止并保留为
  `comfyui-4090-imageclip-backup-20260723`，没有删除任何输入、输出、用户配置或模型。
- 三机镜像包已导出并再次校验：
  `/srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz`，大小
  `8263311384` 字节，SHA-256
  `97c5e8f73fd189a29b59ac7c6a851f9278fe53bb641c118fd20baec22c027ddc`。
- 切换前的 `projects-0.2.1` 容器保留为
  `comfyui-4090-projects-021-backup-20260723`，可立即回滚；模型、工作流和运行目录未删除。
- 镜像切换前已打开的浏览器标签页曾保留旧缺失节点占位对象，运行时出现
  `Node ID #78 has no class_type`。关闭旧标签、强制刷新并从工作流列表重新打开后，
  全新 Chrome 会话确认缺失节点包、缺失模型和 `class_type` 错误均为 0；仅剩正常的
  “缺少输入图片”提示。

当前双项目核心挂载：

```text
/opt/imageclip/models                         -> /opt/comfyui/models (ro)
/opt/modelviewcreator/model                   -> /opt/modelviewcreator/model (ro)
/opt/imageclip/Cherry_lizi                    -> /opt/comfyui/custom_nodes/Cherry_lizi (ro)
/opt/modelviewcreator/custom_nodes/haoze-LiClick
                                               -> /opt/comfyui/custom_nodes/haoze-LiClick (ro)
/opt/imageclip/ImageClip.json                 -> user/default/workflows/ImageClip.json (ro)
/opt/modelviewcreator/flux_fill_inpaint.json  -> user/default/workflows/ModelViewCreator_flux_fill_inpaint.json (ro)
```

完整的构建、Git LFS、三机分发、更新和回滚命令见
`docs/07_COMFYUI_IMAGE_BUILD.md`。

安全记录：ModelViewCreator 的内部节点仓库当前包含明文第三方 API 凭据。本文未记录
任何凭据值；后续必须轮换并改为环境变量/secret 文件注入。在完成该改造前不得公开该
仓库、镜像运行日志或配置文件。

## 1. 记录结论

截至 2026-07-23 复查，以下服务已连续运行约 15 至 16 小时：

- RTX 4090 ComfyUI 已通过 Docker 启动，容器健康，局域网 `8188` 可访问。
- GPU Control 的 PostgreSQL、Redis、API、Scheduler、Web、Nginx 和监控栈已启动并通过健康检查。
- Web 管理台已能显示本机 4090；该节点保持 `RESERVED`，不自动承担普通任务。
- ImageClip 工作流需要的公共自定义节点已固定版本打入镜像，内部 `Cherry_lizi` 从独立 Git 仓库只读挂载。
- ImageClip 使用的四个模型均已下载并通过大小及 SHA-256 校验。
- 可供三台 GPU 服务器复用的 Docker 镜像归档及校验文件已经生成。

这次部署完成了“4090 单机 ComfyUI 可用”和“4090 控制平面可用”，但尚未完成两台 3090 的正式接入及第一笔真实任务的最终出图验收。

## 2. 主机与基础环境

| 项目 | 实际值 |
|---|---|
| 主机名 | `lilithgames2` |
| 操作系统 | Ubuntu 22.04.5 LTS, x86_64 |
| 主控地址 | `10.3.34.11/24` |
| GPU | NVIDIA GeForce RTX 4090, 24564 MiB |
| NVIDIA 驱动 | `580.159.03` |
| Docker Engine | `29.6.2` |
| Docker Compose | `v5.3.1` |
| GPU Control 仓库 | `/opt/gpu-control` |
| GPU Control 基线提交 | `e14311c63f16c77801ae436c38b28d83962e9136` |
| ImageClip 仓库 | `/opt/imageclip` |
| ImageClip 提交 | `abb798dde360fb0ab357ce36fbdfd1581f88d3b5` |

部署前已验证 Docker、NVIDIA Container Toolkit 和 CUDA 容器能够识别 RTX 4090。没有重装 NVIDIA 驱动或操作系统。

## 3. 访问入口

| 服务 | 地址 | 说明 |
|---|---|---|
| ComfyUI | `http://10.3.34.11:8188` | 局域网直接访问 |
| GPU Control Web | `https://10.3.34.11` | 使用内部 CA 签发的证书 |
| API readiness | `https://10.3.34.11/health/ready` | 已返回 database/redis `ok` |
| Node Agent | `http://10.3.34.11:9201` | 只供控制平面调用 |
| Loki | `http://10.3.34.11:3100` | 局域网日志入口 |

内部 CA 文件位于：

```text
/opt/gpu-control/deploy/control-plane/nginx/certs/lan-ca.crt
```

Web 管理员用户名为 `admin`。初始密码仅保存在以下受限文件中，不得复制到文档、聊天或 Git：

```text
/opt/gpu-control/output/deploy/INITIAL_ADMIN_PASSWORD.txt
```

## 4. ComfyUI 镜像

当前运行镜像：

```text
gpu-control/comfyui:imageclip-0.1.0
registry.local:5000/gpu-control/comfyui:imageclip-0.1.0
```

两个 tag 指向同一镜像：

```text
Image ID: sha256:f884d4f9362084b852b348fd3d0b93641b74ee91d71362992139e6d22ab520b8
Image size: 7914326724 bytes
ComfyUI commit: 700821e1364eaab0e8f21c538a2131719fec57bf
ComfyUI version: 0.28.0
Python: 3.11.13
PyTorch: 2.7.1+cu128
```

已固定的公共自定义节点：

| 节点包 | 固定提交 | 用途 |
|---|---|---|
| ComfyUI-GGUF | `6ea2651e7df66d7585f6ffee804b20e92fb38b8a` | `UnetLoaderGGUF` |
| rgthree-comfy | `27b4f4cdcf3b127c29d5d8135ac1536ecbd4c383` | `Image Comparer (rgthree)` |
| ComfyUI-Easy-Use | `54d080bf6a4f52da287e984f305243c10db097f5` | `easy float` |

内部节点没有复制进 GPU Control 仓库，而是从以下路径只读挂载：

```text
/opt/imageclip/Cherry_lizi -> /opt/comfyui/custom_nodes/Cherry_lizi
```

对 `/opt/imageclip` 执行 `git pull` 后，重启 ComfyUI 即可加载新的 Cherry 节点代码。三台服务器必须使用同一 ImageClip 提交。

## 5. 模型与校验

模型没有放入 Docker 镜像，而是只读挂载。兼容旧部署脚本的软链接为：

```text
/srv/comfyui/models -> /opt/imageclip/models
```

真实模型 manifest：

```text
/opt/imageclip/models/models.manifest.yaml
```

| 相对路径 | 大小（字节） | SHA-256 |
|---|---:|---|
| `unet/flux-2-klein-9b-Q6_K.gguf` | 7865424160 | `1cd667293607431e79c9e7e01ecf5c602bd00539c2c0f49d4817a62998b5fe98` |
| `text_encoders/qwen_3_8b_fp8mixed.safetensors` | 8664848742 | `abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6` |
| `vae/flux2-vae.safetensors` | 336213556 | `d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5` |
| `loras/Koutu_Flux2klein_v2_000007250.safetensors` | 165704392 | `79838cfe96bc7508f4d5e6aca6588191eda333ec983a3b202afe694857ccd27d` |

最终验收结果：

```text
OK unet/flux-2-klein-9b-Q6_K.gguf
OK text_encoders/qwen_3_8b_fp8mixed.safetensors
OK vae/flux2-vae.safetensors
OK loras/Koutu_Flux2klein_v2_000007250.safetensors
```

四个文件均已在对应 ComfyUI loader 中出现。`ImageClip.json` 与 `/object_info` 比较后的缺失节点类型数量为 `0`。

## 6. 镜像归档

三机正式分发包：

```text
/srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz
/srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz.sha256
```

归档信息：

```text
Size: 7896136667 bytes
SHA-256: 0b33f3ccc19d9fc68a6fc4f059451f6f1edc76fb27f7000cf56bde4fcf584f51
```

该归档保存的是 `registry.local:5000/gpu-control/comfyui:imageclip-0.1.0` tag，与已生成的主控和 3090 环境配置一致。目标服务器必须同时复制 `.tar.gz` 和 `.sha256`。

3090 导入命令：

```bash
cd /opt/gpu-control
scripts/import_comfyui_image.sh \
  --input /srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz
```

模型不包含在镜像归档中，必须通过 `scripts/sync_models.sh` 或其他受控文件传输方式单独同步，并在每台机器运行 `scripts/verify_models.sh`。

## 7. 已部署的控制平面

以下容器已启动，并在 2026-07-23 复查时保持运行：

- `gpu-control-postgres-1`
- `gpu-control-redis-1`
- `gpu-control-api-1`
- `gpu-control-scheduler-1`
- `gpu-control-web-1`
- `gpu-control-nginx-1`
- `gpu-control-prometheus-1`
- `gpu-control-alertmanager-1`
- `gpu-control-grafana-1`
- `gpu-control-loki-1`
- `gpu-control-alloy-1`
- `gpu-control-node-exporter-1`
- `gpu-control-dcgm-exporter-1`
- `gpu-control-postgres-exporter-1`
- `gpu-control-redis-exporter-1`

本机 Node Agent 已由 systemd 管理：

```bash
systemctl status gpu-node-agent
curl -fsS http://127.0.0.1:9201/health/ready
```

Agent 已修正 Ubuntu 22.04 宿主机 Python 3.10 的兼容安装、缺失的 PyJWT/Argon2 依赖，以及读取主控 `.env` 导致的权限问题。

Alloy 配置也已修正，容器日志和 systemd journal 能够发送至 Loki。

## 8. 日常操作

ComfyUI：

```bash
cd /opt/gpu-control
scripts/comfyui-server.sh status
scripts/comfyui-server.sh restart
scripts/comfyui-server.sh logs
scripts/comfyui-server.sh stop
scripts/comfyui-server.sh start
```

控制平面：

```bash
cd /opt/gpu-control
docker compose --env-file .env \
  -f deploy/control-plane/compose.yaml ps
```

模型校验：

```bash
cd /opt/gpu-control
MODEL_ROOT=/opt/imageclip/models \
scripts/verify_models.sh \
  --manifest /opt/imageclip/models/models.manifest.yaml \
  --root /opt/imageclip/models
```

不要执行以下操作：

- 不要执行 `docker compose down -v`。
- 不要删除 PostgreSQL、任务、模型或 `/srv/gpu-control` 数据目录。
- 不要把大模型加入 Git。
- 不要在三台机器分别运行时安装不同版本的自定义节点。
- 不要重装当前已验证通过的 NVIDIA 驱动。

## 9. 回滚点

旧基础镜像仍保留：

```text
gpu-control/comfyui:0.1.0
```

旧容器已停止并保留：

```text
comfyui-4090-base-backup
```

当前输入、输出、临时目录、用户配置和模型均为宿主机挂载，不在容器可写层中。需要回滚时应先停止当前容器，核对挂载路径，再恢复旧容器；禁止删除数据目录。

## 10. 尚未完成与风险

1. `10.3.34.11` 当前来自 DHCP。需要网络管理员按 MAC `58:11:22:c1:66:63` 建立 DHCP 保留。
2. UFW 当前状态为 `inactive`。正式开放防火墙前必须保留第二个 SSH 会话，并按部署文档只开放必要端口。
3. 两台 3090 尚未正式接入。当前规划节点保持 `DISABLED/OFFLINE`，不能当作三机验收完成。
4. 4090 应继续保持 `RESERVED`，今天不启用自动 `OVERFLOW`。
5. 尚未提交一笔包含真实输入图片的 ImageClip 任务。工作流默认 `LoadImage` 文件名为 `0010.png`；需要上传真实图片后执行，并确认最终输出可下载。
6. `comfy-angle` 未安装，因此可选 GLSL 节点不可用；ImageClip 工作流不依赖该节点。
7. GPU Control 工作区当前包含本次部署修复和原有脚本执行权限变更，尚未提交。提交前必须审查 `git diff`，不得把 `.env`、密码文件、证书私钥或生成密钥加入 Git。
8. ImageClip 仓库当前修改了 `.gitignore` 以忽略大模型断点文件和 Qwen 模型，并新增未跟踪的 `models/models.manifest.yaml`。建议仅提交这两个小文件；不得提交 GGUF、Qwen、VAE 等大模型。

## 11. 下一步顺序

1. 获取两台 3090 的真实固定 IP、MAC、GPU、驱动、Docker 和 NVIDIA Runtime 状态。
2. 在两台 3090 克隆与主控相同提交的 `/opt/gpu-control` 和 `/opt/imageclip`。
3. 导入本记录中的 registry-tag 镜像归档，确认三台镜像 ID 完全一致。
4. 同步模型和 manifest，在两台 3090 上执行完整 SHA-256 校验。
5. 安装并验证两台 3090 的 Node Agent、ComfyUI、exporters 和 Alloy。
6. 从 4090 完成到两台 3090 的网络联通检查。
7. 在 Web 中导入 ComfyUI `Export Workflow (API)` 格式的正式工作流。
8. 上传真实输入图片，提交第一笔任务，确认最终状态为 `SUCCEEDED` 并下载结果。

## 12. Docker 镜像、外挂资源和三机更新规则

### 12.1 管理边界

当前采用“干净镜像 + 外挂业务资源”的方案。大模型不打进镜像，也不重复保存在容器可写层中。

| 内容 | 真相源 | 三机同步方式 |
|---|---|---|
| ComfyUI、Python、PyTorch、公共自定义节点 | Docker 版本镜像 | 新 tag + 镜像归档，以后可换内网 Registry |
| 镜像 Dockerfile、lock、启停脚本和部署文档 | `/opt/gpu-control` Git 仓库 | `git pull --ff-only` |
| `Cherry_lizi`、`ImageClip.json`和已纳入版本管理的小 LoRA | `/opt/imageclip` Git 仓库 | `git pull --ff-only` |
| GGUF、Qwen text encoder、VAE 等大模型 | `/opt/imageclip/models` + manifest | `rsync` 或受控下载，然后 SHA-256 校验 |
| 输入、输出、临时文件、用户配置 | `/srv/comfyui/<节点>/` | 宿主机持久化，不进镜像 |

这样更换容器或升级镜像时，模型、输出和用户配置不会丢失。大模型不能因为“迁移方便”而打进镜像；否则每次小节点修改都要重新分发十几 GB 的重复数据。

Docker 镜像由 Docker Engine 管理，不应直接复制 `/var/lib/docker`。查看本机镜像应使用：

```bash
docker image ls 'gpu-control/comfyui*'
docker image ls 'registry.local:5000/gpu-control/comfyui*'
docker image inspect \
  registry.local:5000/gpu-control/comfyui:imageclip-0.1.0 \
  --format '{{.Id}} {{.Size}}'
```

可移动的“安装包”不是 Docker 内部目录，而是已导出的两个文件：

```text
/srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz
/srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz.sha256
```

### 12.2 容器实际挂载关系

4090 当前的核心挂载是：

```text
/opt/imageclip/models
  -> /opt/comfyui/models                         (read-only)
/opt/imageclip/Cherry_lizi
  -> /opt/comfyui/custom_nodes/Cherry_lizi       (read-only)
/srv/comfyui/4090/input
  -> /opt/comfyui/input
/srv/comfyui/4090/output
  -> /opt/comfyui/output
/srv/comfyui/4090/temp
  -> /opt/comfyui/temp
/srv/comfyui/4090/user
  -> /opt/comfyui/user
```

`/srv/comfyui/models -> /opt/imageclip/models` 是为了兼容 GPU Control 旧脚本的宿主机软链接。容器内使用的是明确的只读 bind mount，不依赖容器内能解析该软链接。

`ImageClip.json` 的版本真相源是 `/opt/imageclip/ImageClip.json`。修改工作流后，应导出并提交到 ImageClip Git 仓库；浏览器里的临时副本或 `/srv/comfyui/4090/user` 不应当作唯一源文件。GPU Control 正式调度使用的工作流仍必须是 ComfyUI `Export Workflow (API)` 格式。

### 12.3 把同一镜像安装到两台 3090

以 `3090-a` 为例。先在目标机准备同路径的两个仓库和数据目录，再从 4090 传输镜像与模型。两台 3090 重复同样的流程。

注意：4090 上的 GPU Control 部署修复目前仍是未提交工作树。在 3090 上执行普通 `git clone` 之前，必须先完成差异审查、提交和推送；否则 3090 只会得到旧的 `e14311c` 代码，无法复现当前环境。审查完成前不得为了省事将 `.env`、密钥或密码文件加入 Git。

4090 上传输镜像归档：

```bash
rsync -avP \
  /srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz \
  /srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz.sha256 \
  lilithgames@3090_A_IP:/srv/gpu-control/images/
```

3090 上校验并导入：

```bash
cd /opt/gpu-control
scripts/import_comfyui_image.sh \
  --input /srv/gpu-control/images/comfyui-imageclip-registry-0.1.0.tar.gz

docker image inspect \
  registry.local:5000/gpu-control/comfyui:imageclip-0.1.0 \
  --format '{{.Id}}'
```

期望两台 3090 与 4090 都返回：

```text
sha256:f884d4f9362084b852b348fd3d0b93641b74ee91d71362992139e6d22ab520b8
```

同步外挂模型后必须验证 manifest：

```bash
# 4090 上执行，不要加 --delete
rsync -avP /opt/imageclip/models/ \
  lilithgames@3090_A_IP:/opt/imageclip/models/

# 3090 上执行
cd /opt/gpu-control
MODEL_ROOT=/opt/imageclip/models \
scripts/verify_models.sh \
  --manifest /opt/imageclip/models/models.manifest.yaml \
  --root /opt/imageclip/models
```

单独验证 ComfyUI 时，3090 可用下列命令启动并通过浏览器访问 `http://3090_A_IP:8188`：

```bash
cd /opt/gpu-control
COMFY_IMAGE=registry.local:5000/gpu-control/comfyui:imageclip-0.1.0 \
COMFY_CONTAINER=comfyui-3090-a \
COMFY_DATA_ROOT=/srv/comfyui/3090-a \
scripts/comfyui-server.sh start
```

正式纳入 GPU Control 时，应改用节点 `.env` 和 `scripts/deploy_node.sh`，不要同时保留一个占用同一 GPU/端口的手工测试容器。

### 12.4 像 Git 一样的版本升级方法

Docker 镜像本身不使用 `git pull` 更新。正确的对应关系是：

- Git commit 固定构建源码和 lock。
- Docker tag 固定可运行的二进制环境。
- manifest + SHA-256 固定外挂模型集合。

每次镜像改动都必须使用新 tag，例如从 `imageclip-0.1.0` 升级到 `imageclip-0.1.1`；不得覆盖已部署的旧 tag。

4090 上构建和导出新版本：

```bash
cd /opt/gpu-control
scripts/build_comfyui_image.sh \
  --tag registry.local:5000/gpu-control/comfyui:imageclip-0.1.1

scripts/export_comfyui_image.sh \
  --image registry.local:5000/gpu-control/comfyui:imageclip-0.1.1 \
  --output /srv/gpu-control/images/comfyui-imageclip-registry-0.1.1.tar.gz
```

然后将新归档和 `.sha256` 分发到两台 3090，执行与 12.3 相同的导入、镜像 ID 核对和真实工作流验证。三台机器都通过后，再将各节点 `.env` 中的 `COMFY_IMAGE` 切换到新 tag 并只重建 ComfyUI 服务。

对当前由 `scripts/comfyui-server.sh` 创建的单机容器，安全切换时先保留旧容器作为回滚点：

```bash
docker stop comfyui-4090
docker rename comfyui-4090 comfyui-4090-backup-0.1.0

cd /opt/gpu-control
COMFY_IMAGE=registry.local:5000/gpu-control/comfyui:imageclip-0.1.1 \
scripts/comfyui-server.sh start
```

新版本验证内容至少包括：容器 health、`nvidia-smi`、`/object_info`中缺失节点为 0、4 个模型可选、ImageClip 真实输入能够出图。验证前不删除旧 tag 或 backup 容器。

### 12.5 日常更新分类

只修改 Cherry 节点、工作流或 Git 管理的小 LoRA 时，不需要重构建 Docker 镜像：

```bash
cd /opt/imageclip
git pull --ff-only

cd /opt/gpu-control
scripts/comfyui-server.sh restart
```

修改 ComfyUI 版本、Python 依赖或公共自定义节点时，必须构建新镜像 tag 并走完三机分发流程。

只增加或替换大模型时，不需要重构建镜像；应更新 `models.manifest.yaml`、同步模型并在三台机器上重新校验 SHA-256，然后重启 ComfyUI 让 loader 刷新。

当前内网 Registry 的 Compose 仍绑定旧地址 `192.168.10.10:5000`，尚未按 `10.3.34.11` 完成 TLS 和网络配置。因此现阶段以 `tar.gz + .sha256` 为正式分发方式，不要在 3090 上盲目执行 `docker pull registry.local:5000/...`。

相关文档：

- `docs/07_COMFYUI_IMAGE_BUILD.md`
- `docs/08_IMAGE_DISTRIBUTION.md`
- `docs/09_MODEL_SYNC.md`
- `docs/24_THREE_HOST_DEPLOYMENT_AND_ACCEPTANCE.md`
- `docs/28_TODAY_DEPLOYMENT_MANUAL.md`
