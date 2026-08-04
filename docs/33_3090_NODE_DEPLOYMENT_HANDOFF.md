# 2026-07-23 RTX 3090 节点部署交接

这是当前将 4090 已验证环境接入 3090-A/3090-B 的操作页。当前恢复状态、滚动更新和
镜像归档验收还必须同时遵循
`docs/62_2026-07-30_REPRODUCIBLE_BACKUP_AND_ROLLING_UPDATE.md`。旧文档中的
`imageclip-0.1.0`、`comfyui-0.1.0` 和单目录 `/srv/comfyui/models` 命令只属于历史
记录，不用于本次部署。

## 1. 当前固定版本

| 内容 | 当前值 |
|---|---|
| GPU Control 应用 | `1.5.4` |
| GPU Control 主控 | `10.3.34.11` |
| ComfyUI 镜像 | `registry.local:5000/gpu-control/comfyui:projects-0.2.3` |
| GPU 拓扑 | 3090-A/B `ONLINE/ACTIVE`；4090 `ONLINE/OVERFLOW` |
| ComfyUI commit | `700821e1364eaab0e8f21c538a2131719fec57bf` |
| ImageClip commit | `bb243808a6bd43055ad92c1071b2ea949b1d9ea1` |
| ModelViewCreator commit | `b22bb377d200d10ae1af565494674fdfb53580dc` |
| ImageClip 路径 | `/opt/imageclip` |
| ModelViewCreator 路径 | `/opt/modelviewcreator` |

镜像的最终 Image ID、锁文件指纹、归档大小和 SHA-256 已记录在第 8 节。

## 2. 开始前必须拿到的信息

每台 3090 分别确认：当前 IP、MAC、GPU UUID、SSH 用户、Ubuntu 版本、磁盘空间、NVIDIA
驱动、Docker 状态。工作节点允许使用 DHCP：节点代理每 10 秒主动向主控上报当前地址，
主控按独立 `NODE_ID + HMAC + MAC + GPU UUID` 验证并更新 ComfyUI、Agent 和监控地址。
仍建议网络管理员做 DHCP 保留，以减少地址漂移。建议至少保留 80 GB 可用空间。主控地址
目前来自 DHCP，网络管理员应按
MAC `58:11:22:c1:66:63` 为 `10.3.34.11` 做 DHCP 保留。

以下命令中的变量只在当前终端使用：

```bash
WORKER_IP=10.3.34.X
WORKER_USER=你的3090登录用户
NODE_ID=worker-3090-a
```

第二台将 `NODE_ID` 改为 `worker-3090-b`。

## 3. 3090 安装基础环境

先在 3090 克隆仓库基线，然后安装公共依赖。当前 4090 有尚未推送的部署改进，第 4 节
会用受控脚本覆盖为主控当前工作树；脚本明确排除 `.env`、密钥、输出和缓存。

```bash
sudo mkdir -p /opt/gpu-control
sudo chown "$USER:$(id -gn)" /opt/gpu-control
git clone --branch main --single-branch \
  https://github.com/kiana0512/gpu_control.git /opt/gpu-control
cd /opt/gpu-control
chmod +x scripts/*.sh scripts/gpuctl scripts/gpu-node-ctl docker/comfyui/entrypoint.sh
sudo scripts/bootstrap_gpu_node.sh
sudo scripts/install_nvidia_container_runtime.sh
```

重新登录一次，让 Docker 用户组生效。不要重装一个已经通过测试的 NVIDIA 驱动。

```bash
nvidia-smi
sudo docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

两条命令都必须看到 RTX 3090。

## 4. 从 4090 同步当前 GPU Control 源码

在 4090 执行：

```bash
cd /opt/gpu-control
scripts/sync_node_source.sh --host "$WORKER_IP" --user "$WORKER_USER" --dry-run
scripts/sync_node_source.sh --host "$WORKER_IP" --user "$WORKER_USER"
```

该脚本不会复制生产 `.env`、数据库、任务、TLS 证书/私钥、密钥、模型和构建缓存；结束时会比较
本机与远端关键文件指纹。

## 5. 准备两个独立项目

在 3090 使用有权限访问内部 GitLab 的 SSH 身份：

```bash
git clone git@gitlab.lilithgame.com:rd_center/ai_art/imageclip.git /opt/imageclip
git -C /opt/imageclip checkout bb243808a6bd43055ad92c1071b2ea949b1d9ea1

git clone git@gitlab.lilithgame.com:rd_center/ai_art/modelviewcreator.git /opt/modelviewcreator
git -C /opt/modelviewcreator checkout b22bb377d200d10ae1af565494674fdfb53580dc
git -C /opt/modelviewcreator lfs install --local
git -C /opt/modelviewcreator lfs pull
git -C /opt/modelviewcreator lfs status
```

若 3090 暂时没有内部 GitLab SSH 权限，从 4090 复制两个完整 Git 工作树（包含 `.git`，
排除大模型目录和 LFS 对象缓存），然后在远端执行 `git fsck`、`git rev-parse HEAD` 和
`git status --short`。A 已用此方式验证通过；不要移动或复制内部节点到 GPU Control 仓库。

## 6. 导入统一镜像

从 4090 复制镜像包：

```bash
scp /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz* \
  "$WORKER_USER@$WORKER_IP:/srv/gpu-control/images/"
```

在 3090 校验并导入：

```bash
cd /opt/gpu-control
sha256sum -c /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz.sha256
scripts/import_comfyui_image.sh \
  --input /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
sudo docker image inspect registry.local:5000/gpu-control/comfyui:projects-0.2.3
```

## 7. 同步两个项目的模型

在 4090 执行：

```bash
cd /opt/gpu-control
scripts/verify_comfy_projects.sh
scripts/sync_models.sh --host "$WORKER_IP" --user "$WORKER_USER" --dry-run
scripts/sync_models.sh --host "$WORKER_IP" --user "$WORKER_USER"
```

脚本默认不删除远端模型，并会在远端重新校验 9 个文件的大小与 SHA-256。不得在模型未
全部 `OK` 时启动正式节点。

## 8. 当前镜像验收值

当前部署必须以 `projects-0.2.3` 的以下值为准：

```text
Image ID: sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea
Image size: 8292205258 bytes
lock SHA-256: 5ef4ba8cc88fd24a0fc81c997420bcbbf5cbae96fb96aff1276b7c3c5d60648d
archive: /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
archive SHA-256: 20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586
```

三台节点导入后必须逐项相同。该镜像的三节点一致性和恢复证据见 62 号手册。

### 8.1 历史 0.2.2 验收值（已被 0.2.3 取代）

以下现场值保留用于审计和回滚，不能用于新节点上线：

```text
Image ID: sha256:bb8c76cfb0bf18c1caff7cfe2a758a9ec1e049543180f117d75af2e94d73a325
Image size: 8284294954 bytes
lock SHA-256: 5b57a8cba970c41329b5d7a3af0ecf8426c6793c4cdaade4218d38ad0ee41a65
archive: /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
archive size: 8263311384 bytes
archive SHA-256: 97c5e8f73fd189a29b59ac7c6a851f9278fe53bb641c118fd20baec22c027ddc
```

该历史镜像曾在 RTX 4090 上真实启动，`pip check` 通过；
ImageClip 23 种 API 节点、ModelViewCreator 18 种 API 节点的缺失数量均为 0。

## 9. 节点配置与启动

在 4090 使用该节点自己的 HMAC 密钥生成环境文件，不要手工复制另一台节点的 `.env`：

```bash
cd /opt/gpu-control
set -a; source .env; set +a
python3 scripts/generate_env.py node \
  --node-id "$NODE_ID" \
  --node-ip "$WORKER_IP" \
  --control-ip 10.3.34.11 \
  --agent-secret "$NODE_AGENT_HMAC_SECRET_WORKER_3090_A" \
  --output "/tmp/${NODE_ID}.env"
scp "/tmp/${NODE_ID}.env" "$WORKER_USER@$WORKER_IP:/tmp/gpu-control-node.env"
scp deploy/control-plane/nginx/certs/lan-ca.crt \
  "$WORKER_USER@$WORKER_IP:/tmp/gpu-control-lan-ca.crt"
```

部署 B 时必须改用 `NODE_AGENT_HMAC_SECRET_WORKER_3090_B`。在 3090 安装文件并只构建 Worker：

```bash
sudo install -d -o root -g root -m 0755 /etc/gpu-control
sudo install -o root -g root -m 0644 /tmp/gpu-control-lan-ca.crt \
  /etc/gpu-control/lan-ca.crt
sudo install -o "$USER" -g "$(id -gn)" -m 0600 /tmp/gpu-control-node.env \
  /opt/gpu-control/.env
rm -f /tmp/gpu-control-node.env /tmp/gpu-control-lan-ca.crt
sudo scripts/configure_ufw_gpu_node.sh \
  --control-ip 10.3.34.11 --ssh-cidr 10.3.34.0/24
sudo scripts/install_node_agent.sh --role node
GPU_CONTROL_ROLE=node scripts/gpuctl doctor
GPU_CONTROL_ROLE=node scripts/gpuctl deploy node --build-worker-only
```

`--build-worker-only` 不启动或重建 Worker/ComfyUI。确认该节点已由控制面置为 `DRAINING`、
`current_jobs=0`、无活动租约，并记录 ComfyUI container ID/StartedAt/RestartCount 后，才按当前发布手册
单独更新 `blender-worker` service。禁止执行无 service 范围的 `compose up/down`。

生成结果必须包含 `NODE_BIND_IP=0.0.0.0`、当前 `NODE_ADVERTISE_IP`、独立 `NODE_ID` 和
`CONTROL_HOST=10.3.34.11`。业务端口虽然监听所有本机地址，但 UFW 只允许主控访问。

## 10. 主控验收

回到 4090：

```bash
curl -fsS "http://$WORKER_IP:8188/system_stats"
curl -fsS "http://$WORKER_IP:9100/metrics" >/dev/null
curl -fsS "http://$WORKER_IP:9400/metrics" >/dev/null
curl -fsS "http://$WORKER_IP:9201/health/ready"
```

还必须确认节点日志持续出现 `node.heartbeat_accepted`，调度器访问 `/v1/identity` 持续
返回 200，数据库 labels 中的 MAC/GPU UUID 与实机一致，Prometheus 的 9100/9400 动态
目标为 `up`。管理台出现真实心跳后再将节点投入调度。未接入节点保持 `DISABLED/OFFLINE`，
不在主界面冒充真实设备。

## 11. A/B 已验收状态

3090-A 已于 2026-07-23 完成，当前地址 `10.3.34.13`，MAC
`18:c0:4d:9f:13:13`，GPU UUID
`GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c`，状态 `ONLINE / ACTIVE`。真实 ImageClip
请求已只在 A 执行并返回 RGBA PNG；详细证据见
`docs/34_2026-07-23_3090_A_DEPLOYMENT_RECORD.md`。

B 已完成部署并验收：`10.3.34.14`、`lilithgames3`、`enp69s0`、MAC
`2c:f0:5d:76:7b:70`、GPU UUID `GPU-092a5184-5857-d196-5df2-efa9503368aa`，状态
`ONLINE / ACTIVE`。A/B 均使用各自 HMAC 密钥，未复制另一台的 `.env`。B 的固定镜像、
9 个模型、两个 Git HEAD、四个远端端口、Prometheus 目标、真实冷/热任务和三卡 10 客户
验收证据见 `docs/35_2026-07-23_3090_B_AND_THREE_NODE_ACCEPTANCE.md`。

当前运行拓扑已升级为 A/B `ONLINE/ACTIVE`、4090 `ONLINE/OVERFLOW`。早期文档中
4090 `RESERVED` 或三节点全部 `ACTIVE` 的描述均为当时部署事实，已被当前拓扑取代。

两台节点均已安装开机 NVIDIA persistence systemd 服务；Node Agent 提供 HMAC 保护的实时
GPU 利用率/显存接口，Scheduler 每 5 秒写回数据库。工作流相同时复用热缓存，切换工作流
时先释放旧模型，避免 24 GiB 显存同时容纳两套项目导致 OOM。

## 12. 禁止事项与回滚

- 不执行 `docker compose down -v`。
- 不执行 `docker system prune -a`。
- 不删除 `/opt/imageclip`、`/opt/modelviewcreator`、`/srv/comfyui/runtime`、任务或数据库。
- 不用 `latest` tag，不用 `docker commit`。
- 回滚时仅把 `.env` 的 `COMFY_IMAGE` 改回上一固定 tag，然后重建 `comfyui` 服务。
