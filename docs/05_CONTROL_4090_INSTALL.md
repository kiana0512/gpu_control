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
9. `scripts/gpuctl deploy control --build-only`。该命令只构建五个第一方镜像，不启动或重建服务；
   首次激活或生产滚动必须按当前发布手册逐个指定 service，不能使用全栈 `compose up`。
10. 完成受控激活后，执行
    `docker compose -f deploy/control-plane/compose.yaml run --rm --no-deps api python scripts/bootstrap_admin.py`
    创建管理员。
11. 复制并修改 `configs/nodes.example.yaml` 为 `configs/nodes.yaml`，随后执行 `/opt/gpu-control/.venv/bin/python scripts/bootstrap_nodes.py --config configs/nodes.yaml` 应用真实三机 IP。

失败回滚：仅停止或恢复本次明确激活失败的应用 service，保留 `/srv/gpu-control` 数据后修正 `.env`；
禁止执行无 service 范围的 `compose down` 或 `down -v`，不得停止、重建或清理 ComfyUI。
