# 3090 工作节点空机安装

当前生产目标是两台 3090 使用同一个固定 ComfyUI 镜像，同时只读挂载
`/opt/imageclip` 与 `/opt/modelviewcreator` 两个独立 Git 工程。今天接入请优先按
[3090 节点交接手册](33_3090_NODE_DEPLOYMENT_HANDOFF.md) 执行；本页保留分角色摘要。

当前有效基线为 GPU Control `1.5.4`、ComfyUI `projects-0.2.3`；3090-A/3090-B
均应注册为 `ONLINE/ACTIVE`，4090 主控保持 `ONLINE/OVERFLOW`。

1. 在 3090 克隆或同步当前 GPU Control 源码到 `/opt/gpu-control`。
2. `cd /opt/gpu-control && sudo scripts/bootstrap_gpu_node.sh`。脚本会安装 Docker、Git LFS、rsync，并创建运行目录。
3. `sudo scripts/install_nvidia_container_runtime.sh`。
4. `nvidia-smi`，再运行 `sudo docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`；两步均须显示 RTX 3090。
5. 克隆 ImageClip 和 ModelViewCreator 到 `/opt`，固定到交接手册记录的提交；ModelViewCreator 必须执行 `git lfs pull`，不能保留 LFS 指针。
6. 按 [镜像分发](08_IMAGE_DISTRIBUTION.md) 导入 `projects-0.2.3` 镜像，按 [模型同步](09_MODEL_SYNC.md) 同步并校验两个项目模型。
7. 复制 `.env.node.example` 为 `.env`，填写本机与主控 IP。两台节点分别使用自己的 `NODE_ID/NODE_BIND_IP`；`NODE_AGENT_HMAC_SECRET` 使用主控为该节点生成的值。
8. `sudo scripts/configure_ufw_gpu_node.sh --control-ip 10.3.34.11 --ssh-cidr ADMIN_CIDR`；配置 UFW 时保留第二个 SSH 会话。
9. `sudo scripts/install_node_agent.sh --role node`，检查 `curl -fsS http://127.0.0.1:9201/health/live` 与 `systemctl status gpu-node-agent`。
10. `GPU_CONTROL_ROLE=node scripts/gpuctl doctor`，然后 `GPU_CONTROL_ROLE=node scripts/gpuctl deploy node`。
11. 从 4090 检查 `8188/system_stats`、`9100/metrics`、`9400/metrics` 与 `9201/health/ready`。

失败回滚只停止节点 Compose：

```bash
docker compose --env-file .env -f deploy/gpu-node/compose.yaml down
```

不要使用 `down -v`，不要删除镜像、两个项目、模型或 `/srv/comfyui/runtime`。
