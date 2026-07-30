# GPU Control 三机当天部署与联调手册

> **版本取代提示（2026-07-30）：**本文记录的 `projects-0.2.2` 是历史部署基线，已经被
> `registry.local:5000/gpu-control/comfyui:projects-0.2.3` 取代。新部署、滚动更新和恢复必须使用
> `projects-0.2.3`，并以 `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md`、
> `configs/versions.lock.env` 和 `docs/62_2026-07-30_REPRODUCIBLE_BACKUP_AND_ROLLING_UPDATE.md`
> 的当前锁定值为准。下文保留 0.2.2 命令仅用于历史追溯，不得直接复制到当前生产环境。

版本：1.1.0（2026-07-22）  
适用：Ubuntu 24.04 LTS、RTX 4090 主控一台、RTX 3090 工作机两台  
目标：从三台可 SSH 的 Ubuntu 主机开始，当天完成统一 ComfyUI 镜像、控制面、两台主计算节点、管理后台、日志监控和首个真实任务联调。

> 本文是实际执行顺序。示例 IP 为 `192.168.10.10/11/12`；执行前替换为真实 IP。安全强化不阻塞首次上线，但三机应位于可信局域网，不要把 8188、9201、3100、9100、9400 暴露到公网。

> 2026-07-23 现场已经升级为 ImageClip + ModelViewCreator 双项目结构。当前主控状态、
> 固定提交、`projects-0.2.2` 镜像、Git LFS 和 3090 实际接入命令，以
> `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md` 为准；本文继续作为通用三机全流程参考。

## 1. 部署结果与停止条件

最终角色：

- `192.168.10.10`：4090 主控。运行 Nginx、Web、API、Scheduler、PostgreSQL、Redis、Prometheus、Alertmanager、Grafana、Loki、Alloy；4090 ComfyUI 默认不启动，节点状态为 `RESERVED`。
- `192.168.10.11`：3090-A。运行统一 ComfyUI 镜像、Alloy、主机/GPU exporter、systemd Node Agent，节点状态为 `ACTIVE`。
- `192.168.10.12`：3090-B。配置同 A。
- 三台使用同一 `COMFY_IMAGE` tag 和同一 Docker image ID；模型不在镜像中，通过 `/srv/comfyui/models` 同步。

遇到下列任一情况就先停止，不继续启动业务：

- 宿主机或 CUDA 容器内 `nvidia-smi` 失败；
- 两台 3090 的 `/system_stats` 不通；
- 三机镜像 ID 不一致或模型 manifest 校验失败；
- 真实工作流不是 ComfyUI 的 `Export Workflow (API)` 格式；
- API `/health/ready` 非 200；
- 100 并发或首个真实任务无法到达终态。

## 2. 一次性填写部署变量

在自己的变更记录中填写，不要把密码写进聊天或 Git：

```bash
CONTROL_IP=192.168.10.10
WORKER_A_IP=192.168.10.11
WORKER_B_IP=192.168.10.12
LAN_CIDR=192.168.10.0/24
SSH_CIDR=192.168.10.0/24
DEPLOY_USER=ubuntu
REPO_URL='替换为仓库地址；无 Git 时使用 scp/rsync 复制本目录'
RELEASE_REF='替换为本次交付 tag 或 commit'
COMFY_ARCHIVE=/srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
```

端口：用户到主控 80/443；主控到工作机 8188/9201/9100/9400；工作机到主控 3100；SSH 22。PostgreSQL 和 Redis 不发布宿主端口。

## 3. 三台主机共同准备

三台分别设置唯一主机名：

```bash
sudo hostnamectl set-hostname gpu-control-4090     # 主控
sudo hostnamectl set-hostname gpu-worker-3090-a   # A
sudo hostnamectl set-hostname gpu-worker-3090-b   # B
sudo timedatectl set-timezone Asia/Shanghai
timedatectl status
ip -br address
df -h / /srv
```

三台放置完全相同的仓库：

```bash
sudo install -d -m 0755 /opt/gpu-control
sudo chown "$USER:$USER" /opt/gpu-control
git clone --branch "$RELEASE_REF" --depth 1 "$REPO_URL" /opt/gpu-control
cd /opt/gpu-control
chmod +x scripts/*.sh
sha256sum pyproject.toml configs/versions.lock.env deploy/*/compose.yaml
```

无 Git 时，从管理电脑把整个仓库复制到三机 `/opt/gpu-control`；不要只复制 PDF。三机上述 SHA-256 必须一致。

### 3.1 安装 NVIDIA 驱动（三台）

```bash
cd /opt/gpu-control
sudo scripts/install_nvidia_driver_ubuntu.sh
sudo reboot
```

重连后：

```bash
nvidia-smi
cat /proc/driver/nvidia/version
```

脚本使用 Ubuntu 官方 `ubuntu-drivers install --gpgpu` 自动选择计算驱动。如现场已安装可用驱动，不重复安装；直接验证 `nvidia-smi`。

### 3.2 安装 Docker、目录与 NVIDIA Container Toolkit（三台）

主控执行：

```bash
cd /opt/gpu-control
sudo scripts/bootstrap_control_4090.sh
sudo usermod -aG docker "$USER"
sudo scripts/install_nvidia_container_runtime.sh
```

两台 3090 各执行：

```bash
cd /opt/gpu-control
sudo scripts/bootstrap_gpu_node.sh
sudo usermod -aG docker "$USER"
sudo scripts/install_nvidia_container_runtime.sh
```

退出 SSH 后重新登录，再在三台验证：

```bash
docker version
docker compose version
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

容器内必须识别本机 GPU。Toolkit 固定为 `1.19.1-1`，安装脚本执行 `nvidia-ctk runtime configure --runtime=docker` 并重启 Docker。

## 4. 在 4090 一次生成全部配置

只在主控执行：

```bash
cd /opt/gpu-control
scripts/gpuctl init control \
  --control-ip 192.168.10.10 \
  --worker-a-ip 192.168.10.11 \
  --worker-b-ip 192.168.10.12
chmod 600 .env output/deploy/*.env output/deploy/INITIAL_ADMIN_PASSWORD.txt
grep -n 'CHANGE_ME' .env && echo '发现未替换占位符，停止部署' || true
```

该命令自动生成并保持一致：

- 主控 `.env`；
- 三节点清单 `configs/nodes.yaml`；
- 使用实际工作机 IP 的 `configs/prometheus.yml`；
- 两个工作机 `.env`；
- 三个互不相同的 Node Agent 密钥；
- PostgreSQL、Redis、JWT、API、Alertmanager、Grafana 密钥；
- 初始管理员密码 `output/deploy/INITIAL_ADMIN_PASSWORD.txt`。

检查：

```bash
grep -E '^(CONTROL_HOST|WORKER_|COMFY_IMAGE|PUBLIC_BASE_URL)=' .env
sed -n '1,80p' configs/nodes.yaml
grep -n '9100\|9400' configs/prometheus.yml
```

### 4.1 生成内网 TLS

```bash
scripts/gpuctl tls init --control-ip 192.168.10.10
openssl x509 -in deploy/control-plane/nginx/certs/server.crt -noout -subject -dates -ext subjectAltName
```

把 `deploy/control-plane/nginx/certs/lan-ca.crt` 导入管理电脑信任库。首次命令行验收可使用 `--cacert`，不要习惯性关闭校验。

### 4.2 防火墙和主控 Node Agent

先保持第二个 SSH 会话，再执行：

```bash
sudo scripts/configure_ufw_control.sh \
  --lan-cidr 192.168.10.0/24 \
  --worker-a 192.168.10.11 \
  --worker-b 192.168.10.12 \
  --ssh-cidr 192.168.10.0/24
sudo scripts/install_node_agent.sh --role control
curl -fsS http://127.0.0.1:9201/health/ready | jq
```

## 5. 构建唯一 ComfyUI 镜像

构建前把真实自定义节点固定到 `docker/comfyui/custom_nodes.lock.yaml`；每个节点必须有完整 commit 和 requirements lock。不要把模型 COPY 进 Dockerfile，不使用 `docker commit`。

主控执行：

```bash
cd /opt/gpu-control
scripts/gpuctl doctor --role control
scripts/gpuctl comfy build
docker image inspect "$COMFY_IMAGE" --format '{{.Id}} {{json .Config.Labels}}'
scripts/gpuctl image export --image "$COMFY_IMAGE" --output "$COMFY_ARCHIVE"
sha256sum --check "$COMFY_ARCHIVE.sha256"
```

镜像可能需要较长时间构建 Python、PyTorch 和 ComfyUI；看到 `构建完成` 才继续。镜像内固定 ComfyUI commit、自定义节点和 Python 依赖；三机模型目录统一挂载到 `/opt/comfyui/models`。

## 6. 启动 4090 控制面

```bash
cd /opt/gpu-control
docker compose --env-file .env -f deploy/control-plane/compose.yaml config --quiet
scripts/gpuctl deploy control
docker compose -f deploy/control-plane/compose.yaml ps
scripts/smoke_test.sh "https://192.168.10.10" \
  --ca deploy/control-plane/nginx/certs/lan-ca.crt
```

`deploy control` 会按顺序构建应用镜像、启动 PostgreSQL/Redis、升级 Alembic、写入三节点 inventory、幂等创建管理员、再启动完整控制面。初始管理员：

```bash
cat output/deploy/INITIAL_ADMIN_PASSWORD.txt
```

浏览器打开 `https://192.168.10.10`，用户名 `admin`。Grafana 位于 `https://192.168.10.10/grafana/`，密码从主控 `.env` 的 `GRAFANA_ADMIN_PASSWORD` 读取。

此时 4090 ComfyUI 不启动，属于预期；控制面和 DCGM exporter 可使用 4090，数据库中节点保持 `RESERVED`。

## 7. 配置两台 3090

主控把配置和同一个镜像发送到两台工作机：

```bash
scp output/deploy/worker-3090-a.env "$DEPLOY_USER@192.168.10.11:/opt/gpu-control/.env"
scp output/deploy/worker-3090-b.env "$DEPLOY_USER@192.168.10.12:/opt/gpu-control/.env"
scp "$COMFY_ARCHIVE" "$COMFY_ARCHIVE.sha256" "$DEPLOY_USER@192.168.10.11:/srv/gpu-control/images/"
scp "$COMFY_ARCHIVE" "$COMFY_ARCHIVE.sha256" "$DEPLOY_USER@192.168.10.12:/srv/gpu-control/images/"
```

3090-A 执行：

```bash
cd /opt/gpu-control
chmod 600 .env
sudo scripts/configure_ufw_gpu_node.sh --control-ip 192.168.10.10 --ssh-cidr 192.168.10.0/24
sudo scripts/install_node_agent.sh --role node
scripts/gpuctl image import --input /srv/gpu-control/images/comfyui-0.1.0.tar.gz
source .env
docker image inspect "$COMFY_IMAGE" --format '{{.Id}}'
```

3090-B 执行相同命令。此时只安装管理代理并导入镜像，不启动 ComfyUI；先完成第 8 节模型同步，避免服务在模型不完整时上线。

两台记录镜像 ID：

```bash
source .env
docker image inspect "$COMFY_IMAGE" --format '{{.Id}}'
```

结果必须与主控一致。

## 8. 模型同步与校验

把真实模型放在主控 `/srv/comfyui/models`，按实际相对路径填写 `/srv/comfyui/models/models.manifest.yaml`。每项需要 `path`、`size_bytes`、64 位小写 SHA-256。

```bash
cd /opt/gpu-control
find /srv/comfyui/models -type f -not -name models.manifest.yaml -printf '%P\n'
sha256sum /srv/comfyui/models/checkpoints/真实模型.safetensors
stat -c '%s' /srv/comfyui/models/checkpoints/真实模型.safetensors
scripts/verify_models.sh
scripts/sync_models.sh --host 192.168.10.11 --user "$DEPLOY_USER" --dry-run
scripts/sync_models.sh --host 192.168.10.11 --user "$DEPLOY_USER"
scripts/sync_models.sh --host 192.168.10.12 --user "$DEPLOY_USER" --dry-run
scripts/sync_models.sh --host 192.168.10.12 --user "$DEPLOY_USER"
```

同步脚本默认不删除远端模型，并在远端执行同一 SHA 校验。若 SSH 用户不能写 `/srv/comfyui/models`，在两台工作机执行：

```bash
sudo chown -R "$USER:$USER" /srv/comfyui/models
```

然后重新同步。主控校验通过后，在两台 3090 分别执行：

```bash
cd /opt/gpu-control
scripts/gpuctl models verify
GPU_CONTROL_ROLE=node scripts/gpuctl deploy node
curl -fsS http://$(hostname -I | awk '{print $1}'):8188/system_stats | jq
```

三机 `scripts/verify_models.sh` 全部显示 `OK`、两台工作机 `system_stats` 都返回 JSON 后才继续第 9 节联通测试。

## 9. 三机联通检查

两台工作机各执行：

```bash
cd /opt/gpu-control
GPU_CONTROL_ROLE=node scripts/gpuctl connectivity
systemctl is-active gpu-node-agent
docker compose -f deploy/gpu-node/compose.yaml ps
```

主控执行：

```bash
cd /opt/gpu-control
scripts/gpuctl connectivity --ca deploy/control-plane/nginx/certs/lan-ca.crt
curl -fsS http://192.168.10.11:8188/queue | jq
curl -fsS http://192.168.10.12:8188/queue | jq
docker compose -f deploy/control-plane/compose.yaml logs --no-color --tail 200 api scheduler
```

联通脚本检查公网入口、PostgreSQL、Redis、Loki、两台 ComfyUI、Node Agent 和 exporters。后台“GPU 节点”页应在约 20 秒内显示两台 3090 `ONLINE/ACTIVE`。

## 10. 导入真实工作流并提交首单

在 ComfyUI 中打开已经跑通的业务工作流，使用 **Export Workflow (API)**。普通 UI 保存 JSON 不能提交。把 API JSON 放入工作流注册包的 `template`，同时填写：

- `workflow_key`、不可变 `version`、显示名；
- JSON Schema 参数；
- 参数到 `节点号.inputs.字段` 的 bindings；
- `allowed_class_types`；
- 模型、自定义节点、最低显存、超时、输出节点。

登录管理台进入“工作流” -> “导入工作流包”。系统自动计算三节点兼容性；确认至少一台节点兼容后再“启用”。如果显示无兼容节点，先修模型、显存或标签，不要强行伪造兼容结果。

进入“客户”创建客户，再创建 API Key；Key 只显示一次。提交局部重绘示例（字段名固定为 `input_image` 和 `mask`）：

```bash
export GPU_API_KEY='gpc_复制刚创建的完整Key'
curl --cacert deploy/control-plane/nginx/certs/lan-ca.crt \
  -H "X-API-Key: $GPU_API_KEY" \
  -H "Idempotency-Key: first-production-001" \
  -F workflow_key=你的工作流key \
  -F workflow_version=你的版本 \
  -F 'parameters={"prompt":"首单联调","seed":12345}' \
  -F input_image=@/path/input.png \
  -F mask=@/path/mask.png \
  https://192.168.10.10/api/v1/jobs
```

保存返回的 `job_id`：

```bash
JOB_ID='替换'
curl --cacert deploy/control-plane/nginx/certs/lan-ca.crt \
  -H "X-API-Key: $GPU_API_KEY" \
  "https://192.168.10.10/api/v1/jobs/$JOB_ID" | jq
curl --cacert deploy/control-plane/nginx/certs/lan-ca.crt \
  -H "X-API-Key: $GPU_API_KEY" \
  "https://192.168.10.10/api/v1/jobs/$JOB_ID/events" | jq
```

预期链路：`RECEIVED -> VALIDATING -> QUEUED -> CLAIMED -> UPLOADING -> SUBMITTED -> RUNNING -> DOWNLOADING -> SUCCEEDED`。结果可从 artifacts API 或管理台任务诊断包确认。

## 11. 4090 联动策略实际操作

默认只跑两台 3090。需要 4090 参与时，先在主控启动其 ComfyUI：

```bash
cd /opt/gpu-control
scripts/gpuctl comfy start
docker compose -f deploy/control-plane/compose.yaml --profile 4090-gpu ps comfyui-4090
```

然后在管理台“GPU 节点”对 4090：

- 点“启动服务”；
- `Release` 将它放入 `OVERFLOW`；或直接切换 `ACTIVE` 让三卡都可立即执行；
- 调度设置可打开 `overflow_4090_auto_enabled`，填写队列阈值、最长等待、最低空闲显存、最高 GPU 利用率、允许时段；
- `Reserve` 立即禁止新任务；`Drain` 等当前任务完成；之后可“停止服务”。

OVERFLOW 只有在两台 3090 无空闲槽且阈值满足，同时 4090 未人工保留、无 `/run/gpu-control/4090.reserved`、利用率/显存/时段均通过时才领取任务。每个 ComfyUI 始终只保留一个本系统任务。

## 12. 日志、监控和诊断

常用检查：

```bash
# 主控服务
docker compose -f deploy/control-plane/compose.yaml ps
docker compose -f deploy/control-plane/compose.yaml logs --since 15m api scheduler nginx

# 工作机
docker compose -f deploy/gpu-node/compose.yaml logs --since 15m comfyui alloy
sudo journalctl -u gpu-node-agent --since '15 minutes ago' --no-pager
nvidia-smi

# 集中日志
curl -fsS http://192.168.10.10:3100/ready
scripts/gpuctl diagnostics job "$JOB_ID"
```

Grafana 的 Loki 用 `job_id`、`request_id`、`node_id`、`prompt_id` 检索。管理台“日志”页会生成 Grafana 查询链接。飞书为空不影响推理；配置 `.env` 的 Webhook/Secret 后，在“告警”页发送测试消息。

## 13. 100 并发和上线验收

无 GPU 调度逻辑已由 Fake ComfyUI 测试。生产三机先用真实工作流做 1 单，再逐步 3 单、10 单，不直接并发轰炸大模型。API 入队压测：

```bash
python3 -m venv .load-venv
source .load-venv/bin/activate
pip install -e '.[load]'
locust -f tests/load/locustfile.py --headless -u 100 -r 20 -t 2m \
  --host https://192.168.10.10
```

上线签字的最小条件：

1. 三机宿主与 CUDA 容器 `nvidia-smi` 通过；
2. 三机 image ID 和模型 SHA 一致；
3. 两台 3090 为 `ONLINE/ACTIVE`，4090 为 `RESERVED`；
4. PostgreSQL/Redis/API/Nginx 就绪；
5. 首个真实任务成功且下载结果可打开；
6. 管理台 Drain/Reserve/Release/中断/释放模型/启动/停止/安全重启可操作；
7. Loki 能查到三台日志，Prometheus targets 为 UP；
8. 备份命令成功：`scripts/backup.sh`；
9. 100 并发入队无重复 job、无 5xx；
10. 未验证项如实记录，不把 Fake 测试写成真实 GPU 通过。

## 14. 快速故障定位

| 现象 | 先执行 | 常见处理 |
|---|---|---|
| GPU 容器启动失败 | `docker run --rm --gpus all ... nvidia-smi` | 重装 Toolkit、执行 `nvidia-ctk`、重启 Docker |
| ComfyUI unhealthy | `docker compose ... logs comfyui` | 检查模型挂载、镜像 tag、驱动、磁盘权限 |
| 节点 OFFLINE | 主控 curl `:8188/system_stats` 与 `:9201/health/ready` | 检查 IP、UFW、Agent、Compose |
| 任务一直 QUEUED | 查节点 mode/health、兼容性和 scheduler 日志 | Release 节点、启用工作流、修 manifest |
| 后台按钮 502 | `journalctl -u gpu-node-agent` | 重装 Agent，检查 per-node secret 与 sudoers |
| 工作机无集中日志 | 看 worker Alloy 日志、主控 `:3100/ready` | 检查 CONTROL_HOST 与 3100 入站 |
| 4090 不接单 | 检查是否启动、OVERFLOW 开关和全部 Guard | 启动服务后 Release，或临时切 ACTIVE |
| TLS 不受信任 | `curl --cacert ... /health/ready` | 导入 LAN CA 或换正式证书 |

恢复原则：先 Drain/Reserve，保留 PostgreSQL、任务目录和日志，再修服务。禁止为了排错执行 `docker compose down -v`、删除 `/srv/gpu-control/jobs` 或清空模型目录。

## 15. 当前实测边界

本交付在 Windows 无 GPU 环境已完成 Python lint、严格类型检查、51 项测试（含 100 并发和幂等竞争）、前端测试与构建。无法在本机宣称 Ubuntu、Docker daemon、NVIDIA、真实模型、真实工作流或三机网络已通过；这些必须按本文在现场执行。代码已提供全部脚本、固定镜像构建、Fake ComfyUI、检查命令和失败出口。

官方参考：

- Docker Engine Ubuntu 安装：<https://docs.docker.com/engine/install/ubuntu/>
- Ubuntu Server NVIDIA 驱动：<https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/>
- NVIDIA Container Toolkit：<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>
- ComfyUI CLI 参数：<https://github.com/Comfy-Org/ComfyUI/blob/700821e1364eaab0e8f21c538a2131719fec57bf/comfy/cli_args.py>
