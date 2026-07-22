# 模型同步与校验

模型不进入镜像。先复制 `configs/models/models.manifest.example.yaml` 为 `/srv/comfyui/models/models.manifest.yaml`，填真实相对路径、字节数和 SHA-256。`scripts/verify_models.sh` 默认读取该路径。

在 4090 先执行 `scripts/verify_models.sh --manifest /srv/comfyui/models/models.manifest.yaml`；全部显示 `OK` 后：

```bash
scripts/sync_models.sh --host WORKER_3090_A --dry-run
scripts/sync_models.sh --host WORKER_3090_A
scripts/sync_models.sh --host WORKER_3090_B
```

脚本使用 rsync partial/append-verify，并在远端再次 SHA-256 校验。默认不删除远端文件；`--delete` 必须交互确认。失败时保留 `.partial`，检查磁盘和 SSH 后重跑。回滚是恢复上一版 manifest 和对应模型文件，不需重建镜像。
