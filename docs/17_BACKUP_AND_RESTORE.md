# 备份与恢复

在 4090 每次迁移/升级前：

```bash
scripts/backup.sh --dry-run
scripts/backup.sh --output /srv/gpu-control/backups
```

备份含 PostgreSQL custom dump、配置/工作流/镜像锁文件和 `SHA256SUMS`；不复制可重新同步的模型和大体积任务输出。将备份离机复制并定期做恢复演练。

恢复前 Drain 三节点、停止 API/scheduler，确认备份路径后：`scripts/restore.sh --from BACKUP_DIR --dry-run`，再执行正式命令并输入 `RESTORE`。随后运行 `alembic upgrade head`、启动服务和 smoke test。恢复会覆盖数据库；撤销只能再次恢复操作前备份，因此脚本先校验 SHA 并要求确认。

