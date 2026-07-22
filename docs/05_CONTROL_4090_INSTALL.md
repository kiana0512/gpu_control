# 4090 控制中心空机安装

目标：在 **4090 Ubuntu 24.04** 安装控制面；首次部署保持 GPU `RESERVED`。

1. 克隆仓库到 `/opt/gpu-control`，进入目录：`cd /opt/gpu-control`。
2. 执行 `sudo scripts/bootstrap_control_4090.sh`。预期输出“控制中心目录已创建”。失败时查看 `journalctl -xe`；脚本只创建用户/目录和基础包，可修复后重跑。
3. 执行 `sudo scripts/install_nvidia_container_runtime.sh`，然后重启 Docker。检查点：`nvidia-smi` 能识别 4090；`docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi` 成功。
4. `cp .env.example .env && chmod 600 .env`，替换全部 `CHANGE_ME`、IP、域名。Redis URL 中密码必须与 `REDIS_PASSWORD` 一致。
5. 保留一个已登录 SSH 会话，再执行 `sudo scripts/configure_ufw_control.sh --lan-cidr LAN_CIDR --worker-a WORKER_A_IP --worker-b WORKER_B_IP --ssh-cidr ADMIN_CIDR`。
6. `sudo scripts/install_node_agent.sh --role control`，再检查 `curl http://127.0.0.1:9201/health/live` 与 `systemctl status gpu-node-agent`。
7. `scripts/gpuctl doctor`。检查点：磁盘、端口、Docker、GPU 均为 OK。
8. `scripts/gpuctl comfy build`，不得用 `docker commit`。
9. `scripts/gpuctl deploy control`。预期 `docker compose ps` 全部 healthy。
10. `docker compose -f deploy/control-plane/compose.yaml run --rm api python scripts/bootstrap_admin.py` 创建管理员。
11. 复制并修改 `configs/nodes.example.yaml` 为 `configs/nodes.yaml`，随后执行 `/opt/gpu-control/.venv/bin/python scripts/bootstrap_nodes.py --config configs/nodes.yaml` 应用真实三机 IP。

失败回滚：先 `docker compose -f deploy/control-plane/compose.yaml down`；保留 `/srv/gpu-control` 数据，修正 `.env` 后重跑。不要使用 `down -v`。
