# 3090 工作节点空机安装

目标：分别在 **3090-A、3090-B Ubuntu 24.04** 安装相同 GPU 运行环境。

1. `cd /opt/gpu-control && sudo scripts/bootstrap_gpu_node.sh`。
2. `sudo scripts/install_nvidia_container_runtime.sh`。
3. `nvidia-smi`，再运行 `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`。两步均须显示 RTX 3090。
4. 复制 `.env.node.example` 为 `.env`，填写本机与控制中心 IP；两台节点分别设置自己的 `NODE_ID/NODE_BIND_IP`。`NODE_AGENT_HMAC_SECRET` 必须与控制中心一致，工作节点不保存数据库、JWT 或 Redis 密钥。
5. `sudo scripts/configure_ufw_gpu_node.sh --control-ip CONTROL_4090_IP --ssh-cidr ADMIN_CIDR`；检查 `sudo ufw status` 只有控制中心能访问管理端口。
6. `sudo scripts/install_node_agent.sh --role node`，再检查 `curl http://127.0.0.1:9201/health/live` 与 `systemctl status gpu-node-agent`。
7. 按 [镜像分发](08_IMAGE_DISTRIBUTION.md) 导入镜像，按 [模型同步](09_MODEL_SYNC.md) 校验模型。
8. `GPU_CONTROL_ROLE=node scripts/gpuctl doctor`，然后 `GPU_CONTROL_ROLE=node scripts/gpuctl deploy node`。
9. 从 4090 执行 `curl http://WORKER_IP:8188/system_stats`、`curl http://WORKER_IP:9100/metrics` 和 `curl http://WORKER_IP:9400/metrics`；预期均成功。

失败回滚：`docker compose -f deploy/gpu-node/compose.yaml down`；镜像、模型和 `/srv/comfyui/runtime` 不删除。驱动或 toolkit 验证失败时不得继续。
