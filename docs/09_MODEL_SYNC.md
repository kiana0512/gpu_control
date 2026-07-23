# 模型同步与校验

模型不进入镜像，两个项目保持各自的目录：

| 项目 | 宿主机模型目录 | manifest |
|---|---|---|
| ImageClip | `/opt/imageclip/models` | `/opt/imageclip/models/models.manifest.yaml` |
| ModelViewCreator | `/opt/modelviewcreator/model` | `/opt/gpu-control/configs/modelviewcreator.models.manifest.yaml` |

先在 4090 校验两套真实模型：

```bash
cd /opt/gpu-control
scripts/verify_comfy_projects.sh
```

全部显示 `OK` 后，先 dry-run，再向每台 3090 同步：

```bash
scripts/sync_models.sh --host WORKER_3090_A --user USER --dry-run
scripts/sync_models.sh --host WORKER_3090_A --user USER
scripts/sync_models.sh --host WORKER_3090_B --user USER --dry-run
scripts/sync_models.sh --host WORKER_3090_B --user USER
```

可用 `--project imageclip` 或 `--project modelview` 只同步一个项目。脚本使用 rsync
`partial/append-verify`，完成后在远端调用 `verify_comfy_projects.sh` 再次计算 SHA-256。
默认绝不删除远端文件；`--delete` 必须交互输入 `DELETE`。失败时检查磁盘和 SSH 后
原命令重跑即可。模型更新不需要重建镜像，但三台服务器必须使用相同 manifest 与哈希。
