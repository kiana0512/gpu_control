# 三台 GPU 主机完整部署、联调与验收手册

版本：1.0.0  
目标系统：Ubuntu 24.04 LTS x86_64  
角色：1 台 RTX 4090 控制中心，2 台 RTX 3090 主计算节点  
执行原则：命令块上方明确写出执行主机；出现占位符时先替换，禁止原样执行。

## 1. 最终运行形态

- 4090 控制中心运行 Nginx、Web、API、asyncio scheduler、PostgreSQL、Redis、Loki、Grafana Alloy、Prometheus、Alertmanager、Grafana、exporter；4090 ComfyUI 默认不启动，数据库模式为 `RESERVED`。
- 3090-A 和 3090-B 各运行一个 ComfyUI、Alloy、node exporter、DCGM exporter，并在宿主机用 systemd 运行受限 Node Agent。
- PostgreSQL 是任务真相来源；Redis 仅做唤醒、通知和限流。scheduler 每个节点只允许一个本地 prompt，不会向 ComfyUI 预堆队列。
- 正常只使用两台 3090。4090 只有管理员切换 `ACTIVE`，或明确启用且满足全部 Guard 的 `OVERFLOW` 时参与推理。
- 工作节点日志通过 3100/TCP 推送到 4090 Loki；Prometheus 从 4090 抓取两台工作节点的 9100/9400。

## 2. 部署前填写表

把下表复制到变更单并填实值。本文示例使用 `192.168.10.0/24`，实际地址不同必须同步修改 `.env`、`.env.node`、`configs/nodes.yaml`、Prometheus targets 和 UFW。

| 项目 | 示例 | 实际值 |
|---|---|---|
| 管理网段 | `192.168.10.0/24` | 待填写 |
| SSH 管理来源网段 | `192.168.10.0/24` | 待填写 |
| 4090 主机名/IP | `gpu-control-4090 / 192.168.10.10` | 待填写 |
| 3090-A 主机名/IP | `gpu-worker-3090-a / 192.168.10.11` | 待填写 |
| 3090-B 主机名/IP | `gpu-worker-3090-b / 192.168.10.12` | 待填写 |
| Web 域名 | `gpu-control.example.internal` | 待填写 |
| 仓库地址和版本 | `REPO_URL / RELEASE_TAG` | 待填写 |
| ComfyUI 镜像 tag | `.../comfyui:0.1.0` | 待填写 |
| TLS 证书来源 | 内网 CA / 正式 CA | 待填写 |
| 模型 manifest 版本 | `YYYYMMDD-N` | 待填写 |
| API 工作流版本 | `workflow_key:version` | 待填写 |
| 飞书 Webhook/Secret | 密钥系统引用 | 待填写 |
| 备份目标 | 独立存储路径 | 待填写 |

上线窗口前还要确认：三机可 SSH、静态 IP 已固定、DNS 已生效、时间同步、`/srv` 容量充足、旧数据已备份、业务方已提供真实 API 工作流和模型 SHA-256。

## 3. 端口矩阵

| 来源 | 目标 | 端口 | 用途 | 防火墙要求 |
|---|---|---:|---|---|
| 管理员/业务网 | 4090 | 443 | Web 和 API | 仅可信网段 |
| 管理员/业务网 | 4090 | 80 | 跳转 HTTPS/健康 | 仅可信网段 |
| 4090 | 两台 3090 | 8188 | ComfyUI HTTP/WS | 仅 4090 |
| 4090 | 两台 3090 | 9201 | HMAC Node Agent | 仅 4090 |
| 4090 | 两台 3090 | 9100 | 主机指标 | 仅 4090 |
| 4090 | 两台 3090 | 9400 | GPU 指标 | 仅 4090 |
| 两台 3090 | 4090 | 3100 | Alloy 推送 Loki | 仅两台 3090 |
| 管理员 | 三台主机 | 22 | SSH | 仅管理网段 |

PostgreSQL 5432、Redis 6379、Grafana 3000、Prometheus 9090、Alertmanager 9093 不发布到局域网。不要为了排错临时映射这些端口到 `0.0.0.0`。

## 4. 三台主机共同准备

### 4.1 设置主机名、时间和磁盘

分别执行，并把主机名替换成对应角色：

```bash
sudo hostnamectl set-hostname gpu-control-4090
# 或 gpu-worker-3090-a / gpu-worker-3090-b
sudo timedatectl set-timezone Asia/Shanghai
timedatectl status
ip -br address
ip route
df -h / /srv
free -h
```

检查点：`System clock synchronized: yes`；三台主机互相能解析或直接访问固定 IP；`/srv` 不是临时盘。建议模型盘、PostgreSQL 数据和 Loki 数据有容量告警。

### 4.2 安装 NVIDIA 驱动

三台均执行：

```bash
sudo apt-get update
sudo apt-get install -y ubuntu-drivers-common
ubuntu-drivers devices
sudo ubuntu-drivers install
sudo reboot
```

重连 SSH 后：

```bash
nvidia-smi
cat /proc/driver/nvidia/version
```

检查点：4090 显示 RTX 4090，两台工作机显示 RTX 3090；没有 `No devices were found`。驱动失败时停止部署，不要先启动 ComfyUI。

### 4.3 放置仓库

三台均执行。仓库内容必须来自同一 release/tag：

```bash
sudo install -d -m 0755 /opt/gpu-control
sudo chown "$USER":"$USER" /opt/gpu-control
git clone --branch RELEASE_TAG --depth 1 REPO_URL /opt/gpu-control
cd /opt/gpu-control
chmod +x scripts/*.sh scripts/gpuctl scripts/gpu-node-ctl
```

没有 Git 服务时，可把本交付 ZIP 解压到 `/opt/gpu-control`，再校验交付 SHA-256。三台的 `pyproject.toml`、`configs/versions.lock.env` 和 Compose 文件必须一致。

## 5. 部署 4090 控制中心

以下命令只在 4090 执行。

### 5.1 安装 Docker 与目录

```bash
cd /opt/gpu-control
sudo scripts/bootstrap_control_4090.sh
sudo usermod -aG docker "$USER"
```

退出并重新登录，使 docker 组生效。然后安装 NVIDIA Container Toolkit：

```bash
cd /opt/gpu-control
sudo scripts/install_nvidia_container_runtime.sh
docker version
docker compose version
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

最后一条必须在容器内显示 RTX 4090。失败时检查：

```bash
sudo systemctl status docker --no-pager
sudo journalctl -u docker -n 200 --no-pager
docker info | grep -i runtime
```

### 5.2 创建控制面配置和密钥

```bash
cd /opt/gpu-control
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 24
```

把不同随机值分别用于 `JWT_SECRET`、`API_KEY_PEPPER`、`NODE_AGENT_HMAC_SECRET`、`POSTGRES_PASSWORD/REDIS_PASSWORD` 和 `GRAFANA_ADMIN_PASSWORD`。编辑 `.env`：

```bash
nano .env
```

必须检查：

- `ENVIRONMENT=production`。
- `CONTROL_HOST/WORKER_3090_A_HOST/WORKER_3090_B_HOST` 为真实 IP。
- `DATABASE_URL` 中密码与 `POSTGRES_PASSWORD` 完全一致并做 URL 编码。
- `REDIS_URL` 中密码与 `REDIS_PASSWORD` 完全一致并做 URL 编码。
- 三项应用密钥已替换，不能以 `CHANGE_ME` 开头。
- `PUBLIC_BASE_URL` 和 `GRAFANA_BASE_URL` 使用最终 HTTPS 地址。
- `COMFY_IMAGE` 是本次要分发的固定 tag，不用 `latest`。
- 4090 初次上线保持 `OVERFLOW_4090_AUTO_ENABLED=false`。

确认没有占位符：

```bash
grep -n 'CHANGE_ME\|192.168.10' .env
```

若真实网络就是示例网段，可以只允许预期的 IP 行存在；任何 Secret 占位符都必须为零结果。

### 5.3 配置 TLS

```bash
mkdir -p deploy/control-plane/nginx/certs
install -m 0644 /SECURE_SOURCE/server.crt deploy/control-plane/nginx/certs/server.crt
install -m 0600 /SECURE_SOURCE/server.key deploy/control-plane/nginx/certs/server.key
openssl x509 -in deploy/control-plane/nginx/certs/server.crt -noout -subject -issuer -dates
openssl x509 -noout -modulus -in deploy/control-plane/nginx/certs/server.crt | openssl sha256
openssl rsa -noout -modulus -in deploy/control-plane/nginx/certs/server.key | openssl sha256
```

最后两条摘要必须相同。测试环境可用内网 CA；生产不要长期使用浏览器不信任的自签证书。

### 5.4 配置 4090 防火墙

先保持第二个已登录 SSH 窗口，再执行：

```bash
sudo scripts/configure_ufw_control.sh \
  --lan-cidr 192.168.10.0/24 \
  --worker-a 192.168.10.11 \
  --worker-b 192.168.10.12 \
  --ssh-cidr 192.168.10.0/24
sudo ufw status numbered
```

从新终端再次 SSH 登录成功后再关闭旧会话。若把自己锁在外面，从带外控制台执行 `sudo ufw disable`。

### 5.5 安装 4090 Node Agent

```bash
cd /opt/gpu-control
sudo scripts/install_node_agent.sh --role control
curl -fsS http://127.0.0.1:9201/health/live | jq
sudo systemctl status gpu-node-agent --no-pager
sudo journalctl -u gpu-node-agent -n 100 --no-pager
```

`/v1/operations` 不允许匿名访问，直接 curl 返回 401 是正确结果。Node Agent 不挂 Docker Socket，不接受任意 shell，只能调用 sudoers 中的受限控制器。

### 5.6 构建 ComfyUI 镜像

真实自定义节点必须先写入 `docker/comfyui/custom_nodes.lock.yaml`，每个节点固定完整 commit；Python 包固定在 `requirements.lock.txt`。模型不得 COPY 入镜像。

```bash
cd /opt/gpu-control
scripts/gpuctl doctor
scripts/gpuctl comfy build
docker image inspect "$COMFY_IMAGE" --format '{{json .Config.Labels}}' | jq
docker image inspect "$COMFY_IMAGE" --format '{{.Id}}'
```

记录镜像 ID、ComfyUI commit、lock SHA 和构建时间到变更单。不要运行 `docker commit`，不要在运行容器里 `pip install` 或 git clone 自定义节点。

### 5.7 启动控制面和迁移数据库

```bash
cd /opt/gpu-control
docker compose --env-file .env -f deploy/control-plane/compose.yaml config --quiet
scripts/gpuctl deploy control
docker compose -f deploy/control-plane/compose.yaml ps
docker compose -f deploy/control-plane/compose.yaml logs --no-color --tail 100 api scheduler postgres redis
```

`deploy control` 顺序是 PostgreSQL/Redis -> Alembic -> 全部控制服务。检查：

```bash
curl -kfsS https://127.0.0.1/health/live | jq
curl -kfsS https://127.0.0.1/health/ready | jq
docker compose -f deploy/control-plane/compose.yaml exec -T postgres \
  pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose -f deploy/control-plane/compose.yaml exec -T redis \
  redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping
```

预期 API 为 `live/ready`，数据库接受连接，Redis 返回 `PONG`。不要执行 `docker compose down -v`。

### 5.8 创建管理员与应用三节点清单

```bash
docker compose -f deploy/control-plane/compose.yaml run --rm api \
  python scripts/bootstrap_admin.py --username admin
cp configs/nodes.example.yaml configs/nodes.yaml
nano configs/nodes.yaml
/opt/gpu-control/.venv/bin/python scripts/bootstrap_nodes.py --config configs/nodes.yaml
```

`configs/nodes.yaml` 必须满足：两个 3090 为 `PRIMARY/ACTIVE`；4090 为 `OVERFLOW/RESERVED`；每节点 `max_concurrency: 1`。4090 的 `base_url` 保持容器网络地址 `http://comfyui-4090:8188`，`agent_url` 使用 4090 的真实主机 IP。

验证数据库：

```bash
docker compose -f deploy/control-plane/compose.yaml exec -T postgres psql \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c 'select id,pool,mode,health,base_url,agent_url,max_concurrency from nodes order by id;'
```

此时两台工作节点尚未启动，显示 `OFFLINE` 正常；4090 必须仍是 `RESERVED`。

## 6. 分发镜像和模型

### 6.1 导出镜像（4090）

```bash
mkdir -p /srv/gpu-control/images
scripts/export_comfyui_image.sh \
  --image "$COMFY_IMAGE" \
  --output /srv/gpu-control/images/comfyui-0.1.0.tar.gz
cd /srv/gpu-control/images
sha256sum --check comfyui-0.1.0.tar.gz.sha256
scp comfyui-0.1.0.tar.gz* gpucontrol@192.168.10.11:/srv/gpu-control/images/
scp comfyui-0.1.0.tar.gz* gpucontrol@192.168.10.12:/srv/gpu-control/images/
```

传输前先在两台工作机创建 `/srv/gpu-control/images` 并确保 `gpucontrol` 可写；第 7 节的 bootstrap 已完成这一步。

### 6.2 准备模型清单（4090）

```bash
cp configs/models/models.manifest.example.yaml /srv/comfyui/models/models.manifest.yaml
nano /srv/comfyui/models/models.manifest.yaml
find /srv/comfyui/models -type f -not -name models.manifest.yaml -printf '%P\n'
sha256sum /srv/comfyui/models/checkpoints/REAL_MODEL.safetensors
stat -c '%s' /srv/comfyui/models/checkpoints/REAL_MODEL.safetensors
scripts/verify_models.sh
```

manifest 中每项路径相对 `/srv/comfyui/models`，`size_bytes` 和 SHA-256 必须来自实际文件。全部显示 `OK` 才能同步。

### 6.3 同步模型（4090）

```bash
scripts/sync_models.sh --host 192.168.10.11 --dry-run
scripts/sync_models.sh --host 192.168.10.11
scripts/sync_models.sh --host 192.168.10.12 --dry-run
scripts/sync_models.sh --host 192.168.10.12
```

脚本默认不删除远端文件，使用 partial/append-verify，并在远端重新运行 SHA 校验。`--delete` 只能在已备份、明确清理旧模型时使用。

## 7. 部署 3090-A

以下命令只在 3090-A 执行。

### 7.1 基础安装

```bash
cd /opt/gpu-control
sudo scripts/bootstrap_gpu_node.sh
sudo install -d -o gpucontrol -g gpucontrol /srv/gpu-control/images
sudo usermod -aG docker "$USER"
```

重新登录后：

```bash
sudo scripts/install_nvidia_container_runtime.sh
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

必须显示 RTX 3090。

### 7.2 节点配置

```bash
cd /opt/gpu-control
cp .env.node.example .env
chmod 600 .env
nano .env
```

设置：

```dotenv
CONTROL_HOST=192.168.10.10
NODE_ID=worker-3090-a
NODE_BIND_IP=192.168.10.11
COMFY_IMAGE=与4090完全一致的tag
NODE_AGENT_HMAC_SECRET=与4090完全一致
```

工作节点 `.env` 不应包含 PostgreSQL、Redis、JWT、Grafana 或 API Key 密钥。

### 7.3 防火墙、Node Agent、镜像

```bash
sudo scripts/configure_ufw_gpu_node.sh \
  --control-ip 192.168.10.10 \
  --ssh-cidr 192.168.10.0/24
sudo scripts/install_node_agent.sh --role node
curl -fsS http://127.0.0.1:9201/health/live | jq
scripts/import_comfyui_image.sh --input /srv/gpu-control/images/comfyui-0.1.0.tar.gz
docker image inspect "$COMFY_IMAGE" --format '{{.Id}}'
scripts/verify_models.sh
```

镜像 ID 应与 4090 记录一致，模型全部 `OK`。

### 7.4 启动节点

```bash
GPU_CONTROL_ROLE=node scripts/gpuctl doctor
GPU_CONTROL_ROLE=node scripts/gpuctl deploy node
docker compose -f deploy/gpu-node/compose.yaml ps
docker compose -f deploy/gpu-node/compose.yaml logs --no-color --tail 200 comfyui
curl -fsS http://192.168.10.11:8188/system_stats | jq
curl -fsS http://192.168.10.11:8188/queue | jq
```

预期 ComfyUI healthy、`queue_running` 和 `queue_pending` 均为空。若存在外来 prompt，先清理来源，不要让 scheduler 接管。

## 8. 部署 3090-B

完全重复第 7 节，但节点变量改为：

```dotenv
NODE_ID=worker-3090-b
NODE_BIND_IP=192.168.10.12
```

所有验证命令中的 IP 改为 `192.168.10.12`。不要复制 3090-A 的 `.env` 后忘记修改 `NODE_ID/NODE_BIND_IP`；否则 Loki 标签和端口绑定会冲突或误标。

## 9. 三机网络联调

### 9.1 4090 到两台 3090

在 4090 执行：

```bash
for host in 192.168.10.11 192.168.10.12; do
  echo "checking $host"
  curl -fsS "http://$host:8188/system_stats" | jq '.system // .'
  curl -fsS "http://$host:9100/metrics" | head
  curl -fsS "http://$host:9400/metrics" | grep -m1 DCGM_FI_DEV_GPU_UTIL
  curl -fsS "http://$host:9201/health/live" | jq
done
```

9201 健康端点可匿名读取，运维操作仍需 HMAC。若失败，依次检查 `ip route`、`ss -lntp`、`ufw status`、容器端口绑定和服务日志。

### 9.2 两台 3090 到 4090 Loki

分别在两台 3090 执行：

```bash
curl -fsS http://192.168.10.10:3100/ready
docker compose -f deploy/gpu-node/compose.yaml logs --no-color --tail 100 alloy
```

预期 `/ready` 返回 ready，Alloy 没有持续重试或 4xx。若 3100 不通，检查 4090 Compose 中 Loki 端口、4090 UFW 是否只允许两台工作机 IP。

### 9.3 控制面确认节点上线

在 4090 执行并等待两个心跳周期：

```bash
docker compose -f deploy/control-plane/compose.yaml logs --no-color --tail 200 scheduler
docker compose -f deploy/control-plane/compose.yaml exec -T postgres psql \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c 'select id,pool,mode,health,current_jobs,gpu_util_percent,free_vram_mb,last_heartbeat_at from nodes order by id;'
```

验收：两个 3090 为 `ONLINE/ACTIVE/current_jobs=0`；4090 为 `RESERVED`。如果 4090 ComfyUI 没启动，health 可为 OFFLINE，但 mode 必须 RESERVED，且不能被调度。

## 10. 日志、指标和 Grafana 验证

### 10.1 本地日志

4090：

```bash
docker compose -f deploy/control-plane/compose.yaml logs --no-color --tail 200 api
docker compose -f deploy/control-plane/compose.yaml logs --no-color --tail 200 scheduler
docker compose -f deploy/control-plane/compose.yaml logs --no-color --tail 200 loki prometheus grafana alloy
sudo journalctl -u gpu-node-agent --since -30min --no-pager
```

3090：

```bash
docker compose -f deploy/gpu-node/compose.yaml logs --no-color --tail 200 comfyui alloy dcgm-exporter
sudo journalctl -u gpu-node-agent --since -30min --no-pager
```

### 10.2 Prometheus targets

在 4090 宿主机通过临时只读查询或 Grafana 数据源查看 Prometheus targets。若真实 IP 不同，修改 `deploy/control-plane/prometheus/prometheus.yml` 的 workers targets 后：

```bash
docker compose -f deploy/control-plane/compose.yaml config --quiet
docker compose -f deploy/control-plane/compose.yaml restart prometheus
docker compose -f deploy/control-plane/compose.yaml logs --tail 100 prometheus
```

必须为 UP：API、scheduler、control node/DCGM、PostgreSQL、Redis、两个 worker node exporter 和两个 DCGM exporter。

### 10.3 Loki 查询

登录 `https://CONTROL/grafana/`，在 Explore 选择 Loki：

```logql
{host="worker-3090-a"}
{host="worker-3090-b"}
{service="scheduler"} | json
{job=~".+"} | json | job_id="REAL_JOB_ID"
{job=~".+"} | json | request_id="REAL_REQUEST_ID"
{job=~".+"} | json | node_id="worker-3090-a"
{job=~".+"} | json | prompt_id="REAL_PROMPT_ID"
```

验收：三台主机都有新日志；任务执行后可用 `job_id/request_id/node_id/prompt_id` 串起 API、scheduler 和 ComfyUI。日志不得出现 Authorization、API Key、密码、Cookie 或回调 Secret。

### 10.4 飞书

在 4090 `.env` 填 `FEISHU_WEBHOOK_URL/FEISHU_SIGNING_SECRET`，重建 API/Alertmanager：

```bash
docker compose -f deploy/control-plane/compose.yaml up -d --force-recreate api alertmanager
```

在管理台“告警与飞书”点击测试。验收：飞书收到测试消息；构造并恢复一个测试告警时分别收到触发和恢复通知。未提供 Webhook 时必须记录为“未验证”，不能写成通过。

## 11. 注册真实工作流

1. 用与生产镜像相同版本的 ComfyUI 验证工作流。
2. 通过 **Export Workflow (API)** 导出；顶层含 `nodes/links` 的普通 UI JSON 禁止提交。
3. 在 `workflows/REAL_NAME/` 保存 API JSON 和 manifest。manifest 必须声明参数 JSON Schema、bindings、允许的 class type、模型、自定义节点、最低显存、超时和输出节点。
4. 在 4090 验证并导入：

```bash
/opt/gpu-control/.venv/bin/python -m packages.gpu_control_core.workflow_cli \
  validate workflows/REAL_NAME/manifest.yaml
/opt/gpu-control/.venv/bin/python -m packages.gpu_control_core.workflow_cli \
  import workflows/REAL_NAME/manifest.yaml
```

5. 确认两台 3090 的 `/object_info` 具备所需节点、模型 manifest 有全部文件，再启用：

```bash
/opt/gpu-control/.venv/bin/python -m packages.gpu_control_core.workflow_cli \
  enable WORKFLOW_KEY VERSION
```

工作流版本不可原地覆盖。回滚时禁用新版本并重新启用旧版本。

## 12. 创建客户和提交第一单

登录 `https://CONTROL/`，依次：API 客户 -> 新建客户 -> 生成 Key。Key 只显示一次，立即放入密钥系统。

在安全客户端设置，不写入 shell history 或仓库：

```bash
read -rsp 'API Key: ' GPU_CONTROL_API_KEY; export GPU_CONTROL_API_KEY; echo
curl -kfsS https://CONTROL/api/v1/workflows \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" | jq
curl -kfsS -X POST https://CONTROL/api/v1/jobs \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" \
  -H "Idempotency-Key: acceptance-$(date +%s)" \
  -F workflow_key=WORKFLOW_KEY \
  -F workflow_version=VERSION \
  -F 'parameters={"REPLACE":"WITH_SCHEMA_VALUES"}' \
  -F input_image=@/SAFE_PATH/input.png \
  -F mask=@/SAFE_PATH/mask.png | tee /tmp/gpu-control-first-job.json
JOB_ID="$(jq -r .job_id /tmp/gpu-control-first-job.json)"
curl -kfsS "https://CONTROL/api/v1/jobs/$JOB_ID" \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" | jq
```

验收：创建立即返回 202；状态按实际路径到 `SUCCEEDED`；只使用一个 3090；另一个空闲时 4090 不参与。结果下载后核对 SHA-256、尺寸和内容。

## 13. 调度策略验证

### 13.1 稳态

- 提交至少三项可运行任务。
- 两台 3090 最多各运行一项，其余留在 PostgreSQL `QUEUED`。
- 4090 保持 `RESERVED`，不能出现 prompt。

查询：

```bash
docker compose -f deploy/control-plane/compose.yaml exec -T postgres psql \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "select id,status,node_id,prompt_id,attempt_count from jobs order by created_at desc limit 20;"
curl -fsS http://192.168.10.11:8188/queue | jq
curl -fsS http://192.168.10.12:8188/queue | jq
```

### 13.2 Drain 和恢复

- 在管理台将 3090-A 设为 `DRAINING`。
- 已运行任务可完成，但不再领取新任务。
- `current_jobs=0` 后执行维护，再切回 `ACTIVE`。

### 13.3 4090 人工 ACTIVE

- 先确认控制面资源正常、没有人工预留文件、显存充足。
- 启动 4090 ComfyUI：`scripts/gpuctl comfy start`。
- 管理台将 4090 切为 `ACTIVE`，提交测试任务，确认可运行。
- 测试结束先 Drain，等待空闲，再改回 `RESERVED` 并执行 `scripts/gpuctl comfy stop`。

### 13.4 OVERFLOW

只在业务批准后设置 `OVERFLOW_4090_AUTO_ENABLED=true`、允许时段、队列阈值、最长等待、GPU 利用率和最小剩余显存。验证以下任一 Guard 不满足都不能调度 4090：人工 Reserve、`/run/gpu-control/4090.reserved` 存在、利用率过高、显存不足、时段不允许、队列未达到阈值。

## 14. 取消、超时、重启和恢复验证

在非生产测试工作流执行：

1. 取消 QUEUED：应直接 `CANCELLED`，不产生 prompt。
2. 取消 RUNNING：API 标记 `CANCELLING`，scheduler 观察器调用 `/interrupt`，最终 `CANCELLED` 并释放 lease。
3. 强制超时：超过工作流 timeout 后 `/interrupt`，最终 `TIMED_OUT`，节点槽位释放。
4. scheduler 重启：`sudo`/Compose 重启后，对已有 `prompt_id` 先查 queue/history，不盲目重提。
5. Redis 暂停：新任务仍进入 PostgreSQL；scheduler 依靠 fallback scan，恢复 Redis 后不丢任务。

每次记录 job_id、prompt_id、状态事件、日志和数据库 lease。测试生产模型前先得到业务方批准。

## 15. 100 并发和容量测试

先用 Fake ComfyUI/测试客户：

```bash
export LOAD_TEST_API_KEY='gpc_REPLACE'
export LOAD_TEST_WORKFLOW='fake'
make load-test
```

默认 100 用户、25 用户/秒、20 秒。验收同时观察：API p95、5xx、429、队列深度、最老等待、scheduler decision/loop lag、PostgreSQL 锁等待、每节点 `current_jobs<=1`。429 只有在配置的配额/限流生效时可接受；其他非 200/202 必须解释。

真实 GPU 压测按 1 -> 10 -> 25 -> 50 -> 100 逐级增加，任何 GPU 温度、显存、输出错误率或等待时间超阈值立即停止。记录真实平均推理时间，稳态理论吞吐近似 `2 / 平均推理秒数` 任务/秒，不把 4090 计入基础容量。

## 16. 备份、升级和回滚

### 16.1 备份

```bash
scripts/backup.sh --dry-run
scripts/backup.sh --output /srv/gpu-control/backups
sha256sum -c /srv/gpu-control/backups/LATEST/SHA256SUMS
```

把备份复制到另一台主机或对象存储。模型通过 manifest 和源库恢复，不依赖数据库备份。

### 16.2 升级

1. 记录当前 `APP_IMAGE_TAG`、`COMFY_IMAGE`、镜像 ID、Alembic revision。
2. 备份并做 Fake 测试、Compose config 和镜像验证。
3. Web Drain 三节点，等待 `current_jobs=0`。
4. 先迁移数据库，再滚动更新控制面和工作节点。
5. 执行 smoke、首单、Loki、Prometheus 和告警测试。

### 16.3 回滚

应用/ComfyUI 回滚只切回旧固定 tag 并 `up -d --force-recreate`。数据库默认恢复升级前备份，除非迁移明确提供且验证过安全 downgrade。运行中 prompt 状态未知时先查 history，禁止直接 retry。

## 17. 常用故障定位顺序

1. 时间：`timedatectl status`。
2. 路由：`ip route get TARGET_IP`。
3. 监听：`sudo ss -lntp`。
4. 防火墙：`sudo ufw status numbered`。
5. Docker：`docker compose ps`、`docker inspect`、`docker logs`。
6. GPU：宿主机与 CUDA 容器内各跑一次 `nvidia-smi`。
7. ComfyUI：`/system_stats`、`/queue`、`/history/PROMPT_ID`。
8. 数据库：jobs、job_events、job_attempts、node_leases。
9. Loki：按 job_id -> prompt_id -> node_id 关联。
10. 诊断包：`scripts/gpuctl diagnostics job JOB_ID`。

不要在排错中删除 volume、任务目录或 ComfyUI history。保留证据后再执行中断、重试或恢复。

## 18. 最终验收签字表

| 验收项 | 证据 | 结果/日期/人员 |
|---|---|---|
| 三机 NVIDIA 宿主机和容器检查 | `nvidia-smi` 输出 | 待填写 |
| 三机版本一致 | release、镜像 ID、lock SHA | 待填写 |
| 4090 控制面 healthy | Compose ps、ready | 待填写 |
| PostgreSQL 空库迁移 | Alembic head | 待填写 |
| Redis 降级不丢任务 | 故障注入记录 | 待填写 |
| 两台 3090 ONLINE/ACTIVE | DB/Web 截图 | 待填写 |
| 4090 RESERVED | DB/Web/无 prompt | 待填写 |
| 单节点并发恒为 1 | DB 与 ComfyUI queue | 待填写 |
| 真实 API 工作流首单成功 | job_id、artifact SHA | 待填写 |
| 取消/超时释放槽位 | 状态事件和 lease | 待填写 |
| scheduler 重启不盲重提 | prompt history | 待填写 |
| 三机日志进入 Loki | 三 host 查询截图 | 待填写 |
| 四类关联 ID 可检索 | LogQL 结果 | 待填写 |
| Prometheus targets 全 UP | targets 截图 | 待填写 |
| 飞书触发和恢复 | 两条消息 | 待填写 |
| 100 并发 | Locust/服务指标 | 待填写 |
| 备份恢复演练 | 新库恢复记录 | 待填写 |
| UFW 最小开放 | 三机规则输出 | 待填写 |
| 未提交 Secret/模型/日志 | Git/制品扫描 | 待填写 |

只有所有生产相关行有真实证据后才能签字。当前 Windows/Fake 测试结果不能替代 Ubuntu、NVIDIA、PostgreSQL 并发、真实工作流、三机日志和飞书验收。
