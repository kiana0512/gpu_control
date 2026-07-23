# 三机当天上线勾选表

本表配合 `docs/28_TODAY_DEPLOYMENT_MANUAL.md` 使用。命令和故障解释以 28 号文档为准；本表负责现场顺序、预期结果和签字记录。

> 2026-07-23 当前 3090 双项目接入以 `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md`
> 为准；本表的三台空机通用步骤和签字项仍可使用。

## 0. 现场信息

- [ ] 日期：`____________`
- [ ] 操作者：`____________`
- [ ] 4090 主控 IP：`____________`
- [ ] 3090-A IP：`____________`
- [ ] 3090-B IP：`____________`
- [ ] SSH 部署用户：`____________`
- [ ] Ubuntu 版本三台均为 24.04 LTS：`是 / 否`
- [ ] 真实 API 工作流路径：`____________`
- [ ] 模型清单已完成 SHA-256：`是 / 否`

任一必填项为空时，不进入生产首单。

## 1. 三台空机准备

三台分别执行并记录：

```bash
cd /opt/gpu-control
sudo scripts/bootstrap_common_ubuntu.sh --role control   # 4090
sudo scripts/bootstrap_common_ubuntu.sh --role node      # 3090
sudo scripts/install_nvidia_driver_ubuntu.sh --gpgpu
sudo reboot
```

重连后安装 Toolkit，并验证：

```bash
sudo scripts/bootstrap_nvidia_runtime.sh
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
docker version
docker compose version
```

- [ ] 4090 裸机与 CUDA 容器都识别 GPU
- [ ] 3090-A 裸机与 CUDA 容器都识别 GPU
- [ ] 3090-B 裸机与 CUDA 容器都识别 GPU
- [ ] 三台 Docker daemon 正常

## 2. 主控一次生成配置

在 4090 仓库根目录执行：

```bash
scripts/gpuctl init \
  --control-ip 192.168.10.10 \
  --worker-a-ip 192.168.10.11 \
  --worker-b-ip 192.168.10.12 \
  --lan-cidr 192.168.10.0/24 \
  --ssh-cidr 192.168.10.0/24 \
  --deploy-user gpuadmin
chmod 600 .env output/deploy/*.env output/deploy/INITIAL_ADMIN_PASSWORD.txt
scripts/gpuctl doctor --role control
```

- [ ] `.env` 已生成且未外发
- [ ] 两份 worker env 已生成
- [ ] `configs/nodes.yaml` 是三个真实 IP
- [ ] `configs/prometheus.yml` 是两个真实 worker IP
- [ ] 初始管理员密码已离线保存

## 3. 统一 ComfyUI 镜像

先补齐 `docker/comfyui/custom_nodes.lock.yaml` 的真实自定义节点完整 commit 与 requirements lock，然后在 4090 执行：

```bash
scripts/gpuctl image build
scripts/gpuctl image export --output /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
sha256sum -c /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz.sha256
source .env
docker image inspect "$COMFY_IMAGE" --format '{{.Id}}'
```

- [ ] 构建无临时 pip/git 操作失败
- [ ] tar.gz SHA 校验通过
- [ ] 4090 image ID：`____________`

## 4. 启动 4090 控制面

```bash
sudo scripts/configure_ufw_control.sh --lan-cidr 192.168.10.0/24 --ssh-cidr 192.168.10.0/24
sudo scripts/install_node_agent.sh --role control
scripts/gpuctl tls init --control-ip 192.168.10.10
scripts/gpuctl deploy control
curl --cacert deploy/control-plane/nginx/certs/lan-ca.crt https://192.168.10.10/health/ready
docker compose -f deploy/control-plane/compose.yaml ps
```

- [ ] `/health/ready` 返回成功
- [ ] Compose 服务均为 running/healthy
- [ ] 管理后台可登录
- [ ] Grafana 可登录
- [ ] 4090 在后台显示 `RESERVED`

## 5. 两台 3090 导入同一镜像

从主控复制 worker env、镜像和 SHA 文件。两台分别执行：

```bash
cd /opt/gpu-control
chmod 600 .env
sudo scripts/configure_ufw_gpu_node.sh --control-ip 192.168.10.10 --ssh-cidr 192.168.10.0/24
sudo scripts/install_node_agent.sh --role node
scripts/gpuctl image import --input /srv/gpu-control/images/comfyui-projects-registry-0.2.2.tar.gz
source .env
docker image inspect "$COMFY_IMAGE" --format '{{.Id}}'
```

- [ ] 3090-A image ID 与 4090 相同
- [ ] 3090-B image ID 与 4090 相同
- [ ] 此阶段尚未启动 ComfyUI

## 6. 模型同步后再启动工作机

在 4090 执行 manifest 校验、dry-run 和正式同步；两台 3090 分别执行：

```bash
scripts/verify_comfy_projects.sh
GPU_CONTROL_ROLE=node scripts/gpuctl deploy node
curl -fsS http://$(hostname -I | awk '{print $1}'):8188/system_stats | jq
```

- [ ] 4090 模型 manifest 全部 OK
- [ ] 3090-A 模型 manifest 全部 OK，`system_stats` 返回 JSON
- [ ] 3090-B 模型 manifest 全部 OK，`system_stats` 返回 JSON

## 7. 三机联通与日志

```bash
# 两台工作机
GPU_CONTROL_ROLE=node scripts/gpuctl connectivity
systemctl is-active gpu-node-agent
docker compose -f deploy/gpu-node/compose.yaml ps

# 4090 主控
scripts/gpuctl connectivity --ca deploy/control-plane/nginx/certs/lan-ca.crt
docker compose -f deploy/control-plane/compose.yaml logs --tail 200 api scheduler
```

- [ ] 两台 3090 在 20 秒内变为 `ONLINE/ACTIVE`
- [ ] PostgreSQL、Redis、Loki、API、两台 ComfyUI 和三个 Agent 都连通
- [ ] Prometheus targets 全部 UP
- [ ] Loki 可看到三台主机日志

## 8. 导入真实工作流与首单

- [ ] 使用 ComfyUI `Export Workflow (API)`，不是普通 UI 保存 JSON
- [ ] 工作流注册包包含 `workflow_key`、版本、API JSON、bindings、allowed class types
- [ ] 后台导入后显示三个节点兼容性
- [ ] 启用工作流后提交真实输入图和蒙版
- [ ] 状态完整经过 `RECEIVED → VALIDATING → QUEUED → CLAIMED → UPLOADING → SUBMITTED → RUNNING → DOWNLOADING → SUCCEEDED`
- [ ] 输出文件可下载且 SHA/尺寸校验通过
- [ ] Grafana 可用四类 ID 找到同一任务全链路

首单 `job_id`：`____________`；`prompt_id`：`____________`；耗时：`____________`。

## 9. 管理操作实测

- [ ] Drain 3090-A：不接新任务，当前任务结束后进入维护
- [ ] Release 3090-A：恢复接单
- [ ] Reserve 4090：绝不接单
- [ ] 4090 `OVERFLOW`：未达到阈值不接单，达到阈值且 Guard 全通过才接单
- [ ] 中断任务
- [ ] 释放模型
- [ ] 停止、启动和安全重启 ComfyUI
- [ ] 下载诊断 ZIP

## 10. 容量与收尾

```bash
python3 -m venv .load-venv
source .load-venv/bin/activate
pip install -e '.[load]'
locust -f tests/load/locustfile.py --headless -u 100 -r 20 -t 2m --host https://192.168.10.10
```

- [ ] Fake ComfyUI 100 并发无丢单和重复领取
- [ ] 真实工作流完成 1、3、10 单阶梯验证
- [ ] 备份脚本成功并记录文件 SHA
- [ ] 4090 恢复 `RESERVED`
- [ ] 两台 3090 保持 `ACTIVE`
- [ ] 现场未通过项已写入 `IMPLEMENTATION_STATUS.md`

现场结论：`通过 / 有条件通过 / 不通过`。操作者签字：`____________`；复核人：`____________`。
