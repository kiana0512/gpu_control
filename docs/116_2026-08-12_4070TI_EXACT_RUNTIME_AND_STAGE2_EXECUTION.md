# GPU Control → RTX 4070 Ti 精确运行时交付与第二阶段实施书

> 文档状态：`HOST_RUNTIME_PREPARATION_AUTHORIZED / NODE_REGISTRATION_BLOCKED`
> 文档日期：2026-08-12
> 接收方：`DAC3OZhangqichao` 主机维护方
> 目标节点：`worker-4070ti-animation-host-01`
> 控制中心：`control-4090` / `10.3.34.11`
> 依据回执：`GPU_CONTROL_4070TI_HOST_PREPARATION_RECEIPT_2026-08-12.md`
> 回执 SHA-256：`5c160711e384be8a273ac6cf2ef0c678d83c09b23dd1a6eab7b8b24f226b710d`
> 本文不包含任何 API Key、HMAC 明文、Windows 密码或私钥。

## 1. 本次正式答复

GPU Control 接受 4070 主机预处理回执。以下事实已经成立，不需要重复安装：

- Windows 10 Pro 22H2 build `19045.3803`。
- WSL `2.7.11.0`，Kernel `6.18.33.2-microsoft-standard-WSL2`。
- Ubuntu `22.04.5 LTS`，systemd 正常。
- WSL 资源上限为 32 GiB 内存、12 CPU、16 GiB swap。
- WSL 根盘为 ext4，可用空间约 954 GiB。
- Windows 与 WSL 均识别 RTX 4070 Ti，UUID 为
  `GPU-70c028e4-dd91-4337-8f96-29daa437d1c3`，VRAM `12282 MiB`。
- Windows 路由地址为 `10.3.34.238/24`，物理 MAC 为
  `34:5a:60:47:c6:1d`。
- WSL 已信任 GPU Control LAN CA，无需 `-k` 即可访问
  `https://10.3.34.11/health/live`。
- AssetClaw 与秋叶 ComfyUI 未被修改，当前未安装 Docker、未注册节点、未开放集群端口。

本文件提供此前缺失的具体值和执行顺序。接收方现在可以完成：

1. 安装与 3090-B 完全一致的锁定 Docker/containerd/Compose。
2. 安装锁定 NVIDIA Container Toolkit。
3. 建立正确的 Linux 运维账户与容器目录权限。
4. 接收并校验不可变 ComfyUI 镜像与四个 ImageClip 模型。
5. 完成本地容器 GPU 自检。

接收方现在仍然**不能**：

- 安装 Node Agent 或注入 HMAC。
- 在 4090 数据库注册节点。
- 开放 8188/9201/9100/2222。
- 启动生产 ComfyUI 或接收生产 Job。
- 修改 `imageclip-rgba` 的工作流、模型、分辨率、提示词、参数或输出节点。
- 把 12 GB 节点标记为兼容当前 `min_vram_mb=22000` 的工作流。

原因是 GPU Control 四节点控制面 commit 和独立节点凭证尚未生成。当前 GPU Control 仓库
HEAD 已从回执中的 `a7120a4...` 前进到 `40e2dc95568268c465191fdec62fdb6343bd234e`，
但这些新增提交属于其他资产能力，不是四节点发布 commit。最终 Node Agent 必须绑定后续经过测试的
四节点 release commit，不能把任意实时 HEAD 当成交付版本。

## 2. 对原回执的两项纠正

### 2.1 `gpucontrol` 登录账户不应强制 UID 10001

现网 3090-B 的实际状态为：

```text
gpucontrol:x:1000:1000::/home/gpucontrol:/bin/bash
```

容器内 ComfyUI 用户及 `/srv/comfyui/runtime` 才使用数值 UID/GID `10001:10001`。
两种身份不能混为一谈：

| 身份 | 用途 | UID/GID 规则 |
|---|---|---|
| `gpucontrol` Linux 登录/运维账户 | WSL 登录、Docker Compose、代码和模型同步 | 普通 Linux UID，使用系统自动分配；不得强制 10001 |
| ComfyUI 容器用户 | 写 input/output/temp/user | 固定数值 `10001:10001` |
| `gpuagent` | systemd Node Agent | 独立 non-login 系统账户，由正式安装脚本创建 |

因此，4070 不应执行“把 `gpucontrol` 创建成 UID 10001”的旧要求。GPU Control 当前
`bootstrap_common_ubuntu.sh` 对此存在历史假设，四节点发布前必须修正；4070 现在按第 5 节执行。

### 2.2 WSL 节点默认不要求 DCGM `9400`

3090-B 的生产做法是通过 Node Agent `/v1/gpu-metrics` 获取 WSL GPU 指标；控制面也支持
`wsl_runtime=true`、`dcgm_exporter_enabled=false` 时不为该节点生成 `:9400` target。

4070 的正式必需端口为：

- `8188`：4090 → Windows portproxy → WSL ComfyUI。
- `9201`：4090 → Windows portproxy → WSL Node Agent。
- `9100`：4090 → Windows portproxy → WSL Node Exporter。
- `2222`：可选，4090 运维来源 → Windows portproxy → WSL SSH 22。

`9400` 在初始 WSL2 方案中关闭，避免为了 DCGM 给容器增加不必要的 `SYS_ADMIN`。若未来单独完成
WSL DCGM 验收，再通过新变更单启用。

## 3. 从 3090-B 实机核验的锁定运行时

以下值于 2026-08-12 从在线 `worker-3090-b` 只读采集，不是根据 latest 猜测：

| 组件 | 锁定值 |
|---|---|
| Ubuntu | `22.04.5 LTS` |
| WSL Kernel 参考 | `6.18.33.2-microsoft-standard-WSL2` |
| Docker Engine | `29.6.2` |
| `docker-ce` | `5:29.6.2-1~ubuntu.22.04~jammy` |
| `docker-ce-cli` | `5:29.6.2-1~ubuntu.22.04~jammy` |
| containerd | `2.2.6` |
| `containerd.io` | `2.2.6-1~ubuntu.22.04~jammy` |
| Docker Buildx | `0.35.0-1~ubuntu.22.04~jammy` |
| Docker Compose plugin | `5.3.1-1~ubuntu.22.04~jammy` |
| NVIDIA Container Toolkit | `1.19.1-1` |
| NVIDIA Container Toolkit Base | `1.19.1-1` |
| libnvidia-container tools/runtime | `1.19.1-1` |
| ComfyUI | `0.28.0` |
| Python | `3.11.13` |
| PyTorch | `2.7.1+cu128` |
| CUDA runtime family | `12.8` / locked build `12.8.1` |

### 3.1 Docker 包 SHA-256

| 包 | 文件大小 | SHA-256 |
|---|---:|---|
| `docker-ce_29.6.2-1~ubuntu.22.04~jammy_amd64.deb` | 23,312,180 | `abda813589be3a9953c72181d2d1fa6064eb64966f917d70fe8996d9af485fc6` |
| `docker-ce-cli_29.6.2-1~ubuntu.22.04~jammy_amd64.deb` | 16,889,272 | `5ad09e85f123841a0ced843f748e4ec52209f1773a770bdb39eb64f24eff6ba5` |
| `containerd.io_2.2.6-1~ubuntu.22.04~jammy_amd64.deb` | 23,621,096 | `a5fd776785cf8482d1a342479d5eed53cccd6daf534ef129012797b6e817dee6` |
| `docker-buildx-plugin_0.35.0-1~ubuntu.22.04~jammy_amd64.deb` | 17,205,924 | `62b77b009803ebea4f9bc3cdecd00e3bf6c88266a3525046105c4449ceea94c7` |
| `docker-compose-plugin_5.3.1-1~ubuntu.22.04~jammy_amd64.deb` | 8,099,832 | `00784bd434f1fadde20cc047f5c88d97c9f2d17c82cef88ac69160421c553f2b` |

### 3.2 NVIDIA 包 SHA-256

| 包 | 文件大小 | SHA-256 |
|---|---:|---|
| `nvidia-container-toolkit_1.19.1-1_amd64.deb` | 1,334,076 | `e66acb5b33420a8417429cd217abc8400b4a409a2ae17a3852cf6feb34b5c8e6` |
| `nvidia-container-toolkit-base_1.19.1-1_amd64.deb` | 5,608,524 | `b6c5b4e77a28cde0197cc0e64edf75538604775d9f8aea502cef667e7e5b2132` |
| `libnvidia-container-tools_1.19.1-1_amd64.deb` | 20,816 | `5642763d51961a2295dff09990048a5dcee81edbea2a8c5084e47b09ccf17268` |
| `libnvidia-container1_1.19.1-1_amd64.deb` | 1,191,204 | `d73bb582af893135198ef81cb22135c790a75d2ad72910446477c6c4430f3e6b` |

以上 SHA 来自 3090-B 当前 APT metadata，并与其已安装版本对应。下载后必须本地重新计算；任一不符立即停止。

## 4. 第二阶段 A：安装锁定容器运行时

本节可立即执行，只改变 WSL Ubuntu，不改 Windows AssetClaw、秋叶 ComfyUI、端口转发或生产流量。

### 4.1 安装基础工具

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg jq openssl rsync openssh-client
```

### 4.2 配置 Docker Jammy 官方仓库

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
test "$VERSION_CODENAME" = "jammy"

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
```

确认候选版本必须逐项等于第 3 节：

```bash
apt-cache policy docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

### 4.3 下载并验证 Docker 包

```bash
gpu_pkg_dir=$(mktemp -d)
cd "$gpu_pkg_dir"

apt-get download \
  'docker-ce=5:29.6.2-1~ubuntu.22.04~jammy' \
  'docker-ce-cli=5:29.6.2-1~ubuntu.22.04~jammy' \
  'containerd.io=2.2.6-1~ubuntu.22.04~jammy' \
  'docker-buildx-plugin=0.35.0-1~ubuntu.22.04~jammy' \
  'docker-compose-plugin=5.3.1-1~ubuntu.22.04~jammy'

sha256sum ./*.deb
```

将输出按“包名”与第 3.1 节逐项比较。APT 下载文件名可能编码 epoch，但文件内容 SHA 必须完全一致。
全部一致后安装：

```bash
sudo apt-get install -y --no-install-recommends ./*.deb
sudo systemctl enable --now containerd docker
```

### 4.4 配置 NVIDIA Container Toolkit 仓库

WSL 中不要安装 Linux NVIDIA kernel driver；GPU driver 来自 Windows。只安装容器工具链：

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --batch --yes --dearmor \
      -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

sudo apt-get update
```

### 4.5 下载并验证 NVIDIA 包

```bash
gpu_nvidia_pkg_dir=$(mktemp -d)
cd "$gpu_nvidia_pkg_dir"

apt-get download \
  'nvidia-container-toolkit=1.19.1-1' \
  'nvidia-container-toolkit-base=1.19.1-1' \
  'libnvidia-container-tools=1.19.1-1' \
  'libnvidia-container1=1.19.1-1'

sha256sum ./*.deb
```

将输出与第 3.2 节逐项比较。全部一致后：

```bash
sudo apt-get install -y --no-install-recommends ./*.deb
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 4.6 版本验收

```bash
docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
docker compose version
containerd --version
dpkg-query -W docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin \
  nvidia-container-toolkit nvidia-container-toolkit-base \
  libnvidia-container-tools libnvidia-container1
systemctl is-active docker containerd
```

期望 Docker Server `29.6.2`、Compose `v5.3.1`、containerd `v2.2.6`，两个服务均为 `active`。

不得配置 `/etc/docker/daemon.json` 的 TCP hosts，不得监听 `2375/2376`：

```bash
ss -lntp | grep -E ':(2375|2376)\b' && exit 1 || true
docker info --format '{{json .SecurityOptions}}'
```

## 5. 第二阶段 B：账户、主机名和目录权限

### 5.1 创建或确认 Linux 运维账户

如果已经有批准的普通 WSL 管理用户，可以继续使用，不必强制改名。若需要与 3090-B 保持命名一致，使用：

```bash
if getent passwd gpucontrol >/dev/null; then
  getent passwd gpucontrol
else
  sudo adduser --disabled-password --gecos '' gpucontrol
fi
sudo usermod -aG docker gpucontrol
```

检查：

```bash
id gpucontrol
getent passwd gpucontrol
```

通过条件：登录 shell 为 `/bin/bash`，属于 `docker` 组；UID 是普通 Linux UID，**不得是强制写死的 10001**。

### 5.2 固定 WSL 运行时 hostname

`/etc/wsl.conf` 最终建议为：

```ini
[boot]
systemd=true

[automount]
enabled=true
mountFsTab=true
options=metadata,umask=022,fmask=011

[network]
hostname=worker-4070ti-wsl
generateHosts=true
generateResolvConf=true

[interop]
enabled=true
appendWindowsPath=false

[user]
default=gpucontrol
```

如果选择保留现有批准的 WSL 管理用户，把最后一行替换成该用户名，不影响 GPU Control 节点 ID。
修改后由 Windows 执行：

```powershell
wsl.exe --shutdown
wsl.exe -d Ubuntu -- hostname
```

期望 hostname 为 `worker-4070ti-wsl`。Windows hostname 仍是 `DAC3OZhangqichao`。

### 5.3 目录所有权

代码、模型和镜像归 Linux 运维账户；ComfyUI 热目录归数值 UID/GID 10001：

```bash
gpu_operator=gpucontrol
gpu_group=$(id -gn "$gpu_operator")

sudo install -d -m 0755 -o "$gpu_operator" -g "$gpu_group" \
  /opt/gpu-control /opt/imageclip /opt/imageclip/models \
  /srv/gpu-control/images

sudo install -d -m 0775 -o 10001 -g 10001 \
  /srv/comfyui/runtime \
  /srv/comfyui/runtime/input \
  /srv/comfyui/runtime/output \
  /srv/comfyui/runtime/temp \
  /srv/comfyui/runtime/user \
  /srv/comfyui/runtime/user/default \
  /srv/comfyui/runtime/user/default/workflows
```

验收：

```bash
stat -c '%A %a %u:%g %U:%G %n' \
  /opt/gpu-control /opt/imageclip /opt/imageclip/models \
  /srv/gpu-control/images /srv/comfyui/runtime \
  /srv/comfyui/runtime/input /srv/comfyui/runtime/output \
  /srv/comfyui/runtime/temp /srv/comfyui/runtime/user
```

不要递归 chown Windows `/mnt/c`，不要修改 AssetClaw 或秋叶目录。

## 6. 不可变 ComfyUI 镜像交付

### 6.1 精确身份

| 字段 | 批准值 |
|---|---|
| tag | `registry.local:5000/gpu-control/comfyui:projects-0.2.3` |
| image ID | `sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea` |
| repo digest | `registry.local:5000/gpu-control/comfyui@sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea` |
| 离线归档大小 | `8271225047` bytes |
| 离线归档 SHA-256 | `20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586` |

该归档已存在于 4090：

```text
/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
```

它不包含模型、任务、secret 或数据库。

### 6.2 传输原则

GPU Control 维护方通过批准的 WSL SSH/SFTP 或企业文件分发通道把归档放到：

```text
/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
```

不得从未知网盘重新下载同名文件。4070 侧验收：

```bash
stat -c '%s %n' /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
echo '20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586  /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz' \
  | sha256sum -c -
```

### 6.3 导入与 GPU 自检

```bash
gzip -dc /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz \
  | docker load

docker image inspect \
  registry.local:5000/gpu-control/comfyui:projects-0.2.3 \
  --format 'id={{.Id}} digests={{json .RepoDigests}}'
```

输出必须包含第 6.1 节 image ID/digest。然后使用该不可变镜像做 GPU 自检，不再依赖一个未锁定的 CUDA 测试 tag：

```bash
docker run --rm --gpus all \
  --entrypoint /usr/bin/nvidia-smi \
  'registry.local:5000/gpu-control/comfyui@sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea' \
  -L
```

必须只看到：

```text
GPU-70c028e4-dd91-4337-8f96-29daa437d1c3
```

再验证 PyTorch/CUDA，但不加载业务模型：

```bash
docker run --rm --gpus all \
  --entrypoint python3.11 \
  'registry.local:5000/gpu-control/comfyui@sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea' \
  -c 'import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.get_device_name(0)); print(torch.cuda.get_device_properties(0).total_memory)'
```

期望 PyTorch `2.7.1+cu128`、CUDA `12.8`、GPU 名称为 RTX 4070 Ti、总显存约 12,882,624,512 bytes。

本节通过后仍不启动常驻 ComfyUI。

## 7. ImageClip 精确交付

### 7.1 管线身份

| 字段 | 批准值 |
|---|---|
| workflow key | `imageclip-rgba` |
| workflow version | `2026.07.30-691770c-r1` |
| ImageClip commit | `691770cd6a59fd7c51391456fe900dc57a313233` |
| combined pipeline SHA | `00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b` |
| API template SHA-256 | `cfe65f832f831f8003c3b7d9d4406f84af1ab53a5cedd108046e0d356ba8a94a` |
| workflow manifest SHA-256 | `a9456442829bb1fcc77f82e8c2e5006228f1dde02563d77c2022bed5b01e53c0` |
| final output | `SaveImage #25` / node ID `25` |
| 当前调度显存门槛 | `22000 MiB` |

`combined pipeline SHA` 不是单独 `ImageClip.json` 的文件 SHA；它按相对路径排序，将
`ImageClip.json` 与 `Cherry_lizi` 中 64 个批准文件的各自 SHA 合并后再次 SHA-256。4070 Node Agent
必须使用 GPU Control 现有算法计算，不能拿单文件 SHA 冒充。

### 7.2 模型清单

模型 manifest 自身 SHA-256：

```text
4932d81a5a73ba8ea9c4afe5cf04a5dc48c8a506845a79d2a73460d360a540ee
```

模型精确值：

| 相对 `/opt/imageclip/models` 路径 | 字节数 | SHA-256 |
|---|---:|---|
| `unet/flux-2-klein-9b-Q6_K.gguf` | 7,865,424,160 | `1cd667293607431e79c9e7e01ecf5c602bd00539c2c0f49d4817a62998b5fe98` |
| `text_encoders/qwen_3_8b_fp8mixed.safetensors` | 8,664,848,742 | `abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6` |
| `vae/flux2-vae.safetensors` | 336,213,556 | `d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5` |
| `loras/Koutu_Flux2klein_v2_000007250.safetensors` | 165,704,392 | `79838cfe96bc7508f4d5e6aca6588191eda333ec983a3b202afe694857ccd27d` |

总模型字节数约 17.03 GB（十进制）。这些文件可以从批准的 4090/3090 节点逐文件 rsync；不要求重压缩。

### 7.3 本机验证命令

接收完成后，在 4070 WSL：

```bash
cd /opt/imageclip/models

stat -c '%s %n' \
  unet/flux-2-klein-9b-Q6_K.gguf \
  text_encoders/qwen_3_8b_fp8mixed.safetensors \
  vae/flux2-vae.safetensors \
  loras/Koutu_Flux2klein_v2_000007250.safetensors

sha256sum \
  unet/flux-2-klein-9b-Q6_K.gguf \
  text_encoders/qwen_3_8b_fp8mixed.safetensors \
  vae/flux2-vae.safetensors \
  loras/Koutu_Flux2klein_v2_000007250.safetensors
```

必须逐项等于第 7.2 节。只验证文件名或大小不算通过。

### 7.4 外部管线边界

4070 侧只接收 GPU Control 提供的批准副本，不自行从另一个分支拼装 `Cherry_lizi`，不编辑：

- `ImageClip.json`
- `Cherry_lizi` 自定义节点
- 模型文件
- prompt、采样参数、分辨率、输出格式
- SaveImage #25 及其上游图拓扑

如果 12 GB 加载失败，结果是 canary 不通过，不是获得修改这些内容的权限。

## 8. 最终节点环境文件规范

本节说明精确字段，但只有 GPU Control 四节点 release commit 和专用 HMAC 就绪后才能生成实际文件。

`/opt/gpu-control/.env` 的 GPU 部分应为：

```dotenv
ENVIRONMENT=production
GPU_CONTROL_ROLE=node
CONTROL_HOST=10.3.34.11
NODE_ID=worker-4070ti-animation-host-01
NODE_BIND_IP=0.0.0.0
NODE_ADVERTISE_IP=10.3.34.238
NODE_MAC_ADDRESS=34:5a:60:47:c6:1d

COMFY_IMAGE=registry.local:5000/gpu-control/comfyui@sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea
MODEL_ROOT=/opt/imageclip/models
IMAGECLIP_ROOT=/opt/imageclip
NODE_AGENT_PORT=9201
NODE_HEARTBEAT_INTERVAL_SECONDS=5
NODE_CONTROL_CA_CERT=/etc/gpu-control/lan-ca.crt
NODE_EXPORTER_PORT=9100

NODE_AGENT_HMAC_SECRET=[仅通过安全通道写入，不出现在MD/聊天/日志]
GPU_CONTROL_REVISION=[四节点发布的完整40位commit；当前尚未生成]
```

注意：

- `NODE_BIND_IP` 必须是 WSL 内可绑定的 `0.0.0.0`，不能写 Windows 地址 `.238`。
- `NODE_ADVERTISE_IP` 必须是 Windows 稳定地址 `10.3.34.238`，不能写 WSL NAT 地址。
- 不启用 `asset-plane` profile；4070 本次不注册 Blender/Asset Worker。
- 不配置 AssetClaw API Key。
- 文件权限必须是 `0600`，所有者为安装 Node Agent 的受控账户/root。

Node Agent 的 `/etc/gpu-control/node-agent.env` 应包含同样的身份、CA、心跳和 revision；HMAC 只存在于
该 `0600` 文件及 4090 secret 环境，不写数据库 label。

## 9. Node Agent 现有协议精确说明

### 9.1 心跳请求

```text
POST https://10.3.34.11/api/v1/nodes/heartbeat
Content-Type: application/json
X-GPU-Timestamp: <Unix seconds>
X-GPU-Nonce: <32 hex chars>
X-GPU-Signature: <64 lowercase hex chars>
```

Body 使用 UTF-8、key 排序、紧凑 JSON，至少包含：

```json
{
  "gpu_model": "NVIDIA GeForce RTX 4070 Ti",
  "gpu_uuid": "GPU-70c028e4-dd91-4337-8f96-29daa437d1c3",
  "hostname": "worker-4070ti-wsl",
  "imageclip_commit": "691770cd6a59fd7c51391456fe900dc57a313233",
  "imageclip_pipeline_sha256": "00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b",
  "ip": "10.3.34.238",
  "mac": "34:5a:60:47:c6:1d",
  "node_agent_version": "<四节点发布版本>",
  "node_id": "worker-4070ti-animation-host-01",
  "source_revision": "<四节点发布commit>"
}
```

实际 Agent 还可能报告 Codex CLI 诊断字段；4070 GPU-only 节点没有 Codex 不影响 GPU 心跳。

### 9.2 签名 canonical string

```text
POST
/api/v1/nodes/heartbeat
<X-GPU-Timestamp>
<X-GPU-Nonce>
<sha256(raw_body).hexdigest()>
```

签名算法：

```text
hex(HMAC-SHA256(node_secret, canonical_string_utf8))
```

主控规则：

- 时间偏差最多 `±30` 秒。
- nonce 不得为空，最大 128 字符；正式 Agent 使用 16 bytes 随机数的 32 位 hex。
- replay key 是 `node_id:nonce`，服务内保留 60 秒；重复 nonce 返回 409。
- 主控按 node ID 选择独立 secret。
- Nginx 观察到的来源地址必须等于 body 的 `10.3.34.238`。
- 数据库必须预批准 node ID；否则返回 `NODE_NOT_APPROVED`。
- MAC 或 GPU UUID 不符返回 `NODE_IDENTITY_MISMATCH`。
- 首次心跳只更新地址和身份，不改变 `DRAINING` 为 `ACTIVE`。

成功响应：

```json
{
  "status": "accepted",
  "node_id": "worker-4070ti-animation-host-01",
  "base_url": "http://10.3.34.238:8188"
}
```

这不是 Worker Pull、WSS 或双向租约协议。

### 9.3 4090 主动访问接口

| 地址 | 鉴权 | 用途 |
|---|---|---|
| `http://10.3.34.238:8188/system_stats` | Windows 防火墙限定 4090 | ComfyUI/GPU 健康 |
| `http://10.3.34.238:8188/queue` | 同上 | 外部队列与占用检查 |
| `http://10.3.34.238:8188/prompt` | 同上 | Scheduler 推送 prompt |
| `ws://10.3.34.238:8188/ws` | 同上 | 执行进度 |
| `http://10.3.34.238:8188/history/{prompt_id}` | 同上 | 恢复与结果核验 |
| `http://10.3.34.238:9201/v1/identity` | HMAC | 节点身份 |
| `http://10.3.34.238:9201/v1/gpu-metrics` | HMAC | GPU 指标 |
| `http://10.3.34.238:9201/v1/system-metrics` | HMAC | WSL 系统指标 |
| `http://10.3.34.238:9201/v1/operations` | HMAC + 白名单 | status/start/stop/restart/logs/diagnostics |

`/health/live` 与 `/health/ready` 可不带 HMAC访问，但仍受 Windows 来源 IP 防火墙限制。

## 10. 网络与端口转发的精确目标

### 10.1 最终连接矩阵

| 来源 | 目标 | 端口 | 用途 |
|---|---|---:|---|
| 4070 Windows AssetClaw | `10.3.34.11` | 443/TCP 出站 | 批次 API |
| 4070 WSL Node Agent | `10.3.34.11` | 443/TCP 出站 | 签名心跳 |
| 4070 WSL Alloy | `10.3.34.11` | 3100/TCP 出站 | Loki 日志；启用 Alloy 后需要 |
| 4090 | `10.3.34.238` | 8188/TCP 入站 | ComfyUI |
| 4090 | `10.3.34.238` | 9201/TCP 入站 | Node Agent |
| 4090 | `10.3.34.238` | 9100/TCP 入站 | Node Exporter |
| 4090（可选运维） | `10.3.34.238` | 2222/TCP 入站 | WSL SSH 22 |

`9400`、`2375`、秋叶 8188 和 Windows AssetClaw loopback 端口均不在集群开放范围。

### 10.2 Windows portproxy 目标

Windows 维护方应把下列脚本保存为：

```text
C:\ProgramData\GPUControl\Update-4070WslProxy.ps1
```

脚本必须由 Windows 管理员本地审核和执行；4090 不获得 Windows 凭据。

```powershell
$ErrorActionPreference = 'Stop'
$Distribution = 'Ubuntu'
$ListenAddress = '10.3.34.238'
$ControllerAddress = '10.3.34.11'
$Ports = @(8188, 9201, 9100)
$WslExe = "$env:SystemRoot\System32\wsl.exe"

$RawAddresses = (& $WslExe -d $Distribution -- /bin/sh -lc 'hostname -I').Trim()
$WslAddress = $RawAddresses -split '\s+' |
    Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } |
    Select-Object -First 1

if (-not $WslAddress) {
    throw 'Unable to discover WSL IPv4 address'
}

$Parsed = $null
if (-not [System.Net.IPAddress]::TryParse($WslAddress, [ref]$Parsed)) {
    throw "Invalid WSL IPv4 address: $WslAddress"
}
if ($WslAddress -eq $ListenAddress -or $WslAddress.StartsWith('127.')) {
    throw "Unsafe WSL target address: $WslAddress"
}

foreach ($Port in $Ports) {
    & netsh interface portproxy delete v4tov4 `
        listenaddress=$ListenAddress listenport=$Port | Out-Null
    & netsh interface portproxy add v4tov4 `
        listenaddress=$ListenAddress listenport=$Port `
        connectaddress=$WslAddress connectport=$Port
}

$RuleName = 'GPUControl-4070-From-4090'
$Existing = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
if ($Existing) {
    Set-NetFirewallRule -Name $RuleName -Enabled True `
        -Direction Inbound -Action Allow -Profile Domain,Private `
        -RemoteAddress $ControllerAddress
    Set-NetFirewallPortFilter -AssociatedNetFirewallRule $Existing `
        -Protocol TCP -LocalPort ($Ports -join ',')
    Set-NetFirewallAddressFilter -AssociatedNetFirewallRule $Existing `
        -LocalAddress $ListenAddress -RemoteAddress $ControllerAddress
} else {
    New-NetFirewallRule -Name $RuleName `
        -DisplayName 'GPU Control 4070 WSL services from 4090 only' `
        -Enabled True -Direction Inbound -Action Allow `
        -Profile Domain,Private -Protocol TCP `
        -LocalAddress $ListenAddress -RemoteAddress $ControllerAddress `
        -LocalPort ($Ports -join ',') | Out-Null
}

netsh interface portproxy show v4tov4
Get-NetFirewallRule -Name $RuleName |
    Get-NetFirewallAddressFilter
```

脚本只管理特定 listen address 和三个特定端口，不删除其他 portproxy。正式执行条件：

- DHCP Reservation 已确认。
- WSL 中 8188/9201/9100 服务已经安装。
- GPU Control 已预创建 DRAINING 节点。

### 10.3 WSL 重启自愈任务

WSL 发行版属于安装它的 Windows 用户。计划任务不能随意改成 `SYSTEM`，否则可能看不到该用户注册的
`Ubuntu`。创建两个任务：

| 任务 | 触发 | 运行账户 | 动作 |
|---|---|---|---|
| `GPUControl-4070-WSL-Start` | Windows 启动后/该用户登录时 | 拥有 Ubuntu 发行版的 Windows 用户 | 启动 WSL systemd、Docker、Node Agent和批准容器 |
| `GPUControl-4070-WSL-Watchdog` | 每 1 分钟 | 同一用户，最高权限 | 执行 `Update-4070WslProxy.ps1`，仅在映射变化时修复 |

若要求无人登录即可恢复，任务密码必须由主机管理员在 Windows Task Scheduler 本地保存；不得提供给
4090 或写入脚本。上线验收必须包含一次真实 Windows 重启，而不是只运行脚本。

## 11. GPU Control 控制面在注册前必须完成的改动

截至本文生成时，以下状态是具体的 `NOT_IMPLEMENTED`，不是让 4070 猜值：

| 项目 | 当前问题 | 必须结果 |
|---|---|---|
| `Settings.node_agent_secret()` | 仅专门列出 3090-A、3090-B、4090；未知节点回退共享 secret | 增加 4070 独立字段；生产未知节点不得回退共享 secret |
| `bootstrap_nodes.py` | `EXPECTED_IDS` 恰好三节点 | 接受审核后的四节点 inventory，且不删除已有节点 |
| `generate_env.py` | 只生成三台 env 和两个 Worker targets | 生成 4070 env、inventory、监控配置 |
| Scheduler WSL system probe | 只探测 `worker-3090-b`，cache/retry/boot ID 是单实例变量 | 改为按 node ID 的字典，可同时探测 B 与 4070 |
| WSL 性能基线 | 写死 3090-B 对 3090-A | 4070 使用独立 canary 基线，不把型号差异误报为 WSL 衰退 |
| Prometheus 告警 | WSL 规则写死 3090-B | 增加 4070 node ID 或泛化标签 |
| Web 节点排序 | 写死三节点 | 支持第四节点和未知节点稳定排序 |
| connectivity/smoke/load | 部分写死三节点 | 新增四节点场景，保留三节点降级回归 |
| 部分运行时文案 | 只列三节点 | 动态从节点表生成 |

四节点 release 必须通过测试并提交后，才能把它的 40 位 SHA 写入 4070 `GPU_CONTROL_REVISION`。

### 11.1 数据库目标记录

正式 bootstrap 应创建：

```yaml
id: worker-4070ti-animation-host-01
display_name: 4070 Ti 动画主机 Worker
base_url: http://10.3.34.238:8188
agent_url: http://10.3.34.238:9201
pool: PRIMARY
mode: DRAINING
health: OFFLINE
max_concurrency: 1
labels:
  host: 10.3.34.238
  windows_hostname: DAC3OZhangqichao
  hostname: worker-4070ti-wsl
  mac: 34:5a:60:47:c6:1d
  gpu_uuid: GPU-70c028e4-dd91-4337-8f96-29daa437d1c3
  gpu: RTX4070Ti
  wsl_runtime: true
  dcgm_exporter_enabled: false
  imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
  imageclip_pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
```

使用 `DRAINING` 是为了允许心跳、健康和监控验收，同时保证普通 Scheduler 不派新任务。首次心跳不得改 mode。

## 12. 12 GB 节点的分层 canary

当前生产 manifest 的 `min_vram_mb=22000` 必须保持不变。4070 的 `12282 MiB` 会被兼容性计算正确
标记为不兼容。canary 分三层，不能直接删除这条门禁。

### C0：容器与 GPU，不加载模型

- 执行第 6.3 节。
- 通过标准：镜像、PyTorch、CUDA、GPU UUID/VRAM 全部正确。

### C1：DRAINING 节点直连 ComfyUI 隔离测试

- 节点不进入普通调度。
- 由 4090 维护方使用生产 API 模板和完全相同管线，直接向隔离 ComfyUI 提交 1 帧。
- 只验证原工作流能否在 12 GB 上加载和生成批准输出。
- 记录峰值显存、host RAM/swap、耗时、最终 RGBA/alpha和所有错误。
- C1 不计为 GPU Control Scheduler/Lease 验收。

### C2：GPU Control 内部 canary 调度

四节点代码必须提供一个可审计、只对 test/canary tenant 开放的单节点例外，要求：

- 生产 workflow manifest 的 `min_vram_mb=22000` 不变。
- 例外只允许 `worker-4070ti-animation-host-01` 和指定 canary client。
- 请求必须 pinned，不能从普通 AssetClaw API 进入。
- 节点在 canary 窗口临时 ACTIVE，测试结束自动/人工恢复 DRAINING。
- 审计记录操作人、时间、batch/job IDs 和理由。
- 普通生产 Job 在例外存在时仍不能选中 4070。

依次执行 1、6、30 帧，再进行 WSL 重启、Windows 重启、网络中断、OOM/失败和取消测试。

### C3：生产准入决策

只有 C0-C2 全部通过，才可单独评审：

- 是否为该 workflow/node 建立正式 12 GB capability override；或
- 是否保持 4070 DRAINING/DISABLED。

不能根据“容器能看到 GPU”或“1 帧偶然成功”直接把全局 `min_vram_mb` 从 22000 改为 12000。

## 13. 4070 侧本轮应返回的证据

完成第 4-6 节后，返回一个不含 secret 的 Markdown/YAML，至少包括：

```yaml
stage2_runtime_receipt:
  collected_at_utc: "<实际UTC>"
  node_id: "worker-4070ti-animation-host-01"
  status: "RUNTIME_READY_NOT_REGISTERED"

docker:
  server_version: "29.6.2"
  compose_version: "5.3.1"
  containerd_version: "2.2.6"
  package_versions_match: true
  package_sha256_match: true
  tcp_2375_listening: false
  tcp_2376_listening: false

nvidia_container:
  toolkit_version: "1.19.1-1"
  package_sha256_match: true
  gpu_uuid_in_container: "GPU-70c028e4-dd91-4337-8f96-29daa437d1c3"
  pytorch_version: "2.7.1+cu128"
  cuda_version: "12.8"

image:
  archive_size_bytes: 8271225047
  archive_sha256: "20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586"
  image_id: "sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea"
  repo_digest: "registry.local:5000/gpu-control/comfyui@sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea"

filesystem:
  operator_account: "gpucontrol"
  operator_uid: "<实际普通UID>"
  runtime_uid_gid: "10001:10001"
  docker_data_root_filesystem: "ext4"
  free_bytes_after_image_and_models: 0

models:
  manifest_sha256: "4932d81a5a73ba8ea9c4afe5cf04a5dc48c8a506845a79d2a73460d360a540ee"
  all_sizes_match: false
  all_sha256_match: false

unchanged_boundaries:
  assetclaw_unchanged: true
  qiuyue_comfyui_unchanged: true
  imageclip_workflow_unchanged: true
  windows_cluster_ports_opened: []
  node_agent_installed: false
  node_registered: false
  production_traffic_enabled: false

blockers:
  - "DHCP reservation must be confirmed"
  - "GPU Control four-node release commit must be delivered"
  - "Dedicated node HMAC must be securely injected"
  - "C0/C1/C2 canary must pass"
```

如果模型尚未传输，`all_sizes_match/all_sha256_match` 保持 false 是正确事实；不要为了填满回执伪造 true。

## 14. 停止条件

出现以下任一情况立即停止，不继续安装后续阶段：

- APT 候选版本与第 3 节不一致。
- 任一 `.deb` SHA-256 不一致。
- Docker 打开 2375/2376。
- 容器看到的 GPU UUID 与 Windows/WSL 不一致。
- ComfyUI image ID 或 archive SHA 不一致。
- 任一模型 SHA 不一致。
- 需要使用 `--insecure` 才能访问 4090。
- DHCP Reservation 未确认却准备写入 portproxy/防火墙。
- 被要求复用 3090-B、共享 secret 或 AssetClaw API Key。
- 被要求把 WSL NAT 地址 `172.24.3.33` 写入节点记录。
- 首次心跳导致节点自动 ACTIVE。
- 被要求修改外部工作流来绕过 12 GB 门槛。

## 15. 当前可执行边界总结

| 动作 | 当前是否可执行 |
|---|---|
| 安装第 3 节锁定 Docker/containerd/Compose | 可以 |
| 安装 NVIDIA Container Toolkit 1.19.1-1 | 可以 |
| 创建普通 `gpucontrol` 运维账户 | 可以；不要使用 UID 10001 |
| 设置 `/srv/comfyui/runtime` 为 10001:10001 | 可以 |
| 接收并校验 ComfyUI 离线镜像 | 可以 |
| 用不可变 Comfy 镜像做一次性 GPU/PyTorch 自检 | 可以 |
| 接收并校验四个 ImageClip 模型 | 可以 |
| 安装任意 latest | 不可以 |
| 安装 Node Agent | 暂不可以，等待四节点 release commit/HMAC |
| 创建 Windows portproxy/firewall | 暂不可以，等待 DHCP + 服务 + DRAINING 节点 |
| 启动常驻 ComfyUI | 暂不可以 |
| 注册/激活节点 | 暂不可以 |
| 切换 AssetClaw 生产路由 | 暂不可以 |

完成当前允许项后，正确终态是 `RUNTIME_READY_NOT_REGISTERED`，不是 `ONLINE` 或 `ACTIVE`。
