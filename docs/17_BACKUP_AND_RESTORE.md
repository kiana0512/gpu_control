# 备份与恢复

本章只覆盖 GPU Control 所拥有的控制面、调度、任务/资产存储、部署配置和运行时。
ImageClip、ModelViewCreator 的仓库、工作流、模型和业务语义不由本脚本修改；它们必须另外通过
“批准的上游 commit + 三节点 SHA-256 清单 + 模型清单”重建和校验。

## 1. 两种备份模式

| 模式 | 内容 | 典型用途 |
| --- | --- | --- |
| `small`（默认） | PostgreSQL custom dump 与角色、Git bundle/LFS 清单、未提交 diff、部署配置、Docker 镜像/容器/卷清单、完整 `SHA256SUMS` | 每次发布前、日常快速恢复点 |
| `full` | `small` 的全部内容，外加当前 Git 工作树、根 `.env`、`/srv/gpu-control/secrets`、`/etc/gpu-control`、任务、资产、离线镜像、Asset/Codex runtime、4090 ComfyUI 运行数据 | 裸机重建、重大迁移、完整演练 |

`full` 默认归档这些路径；可在命令环境中覆盖：

```text
JOB_ROOT=/srv/gpu-control/jobs
ASSET_ROOT=/srv/gpu-control/assets
IMAGE_ARCHIVE_ROOT=/srv/gpu-control/images
CONTROL_RUNTIME_ROOT=/opt/gpu-control/runtime
COMFY_DATA_ROOT=/srv/comfyui/4090
```

`full` **不包含** `/srv/comfyui/models`、`/opt/imageclip` 和 `/opt/modelviewcreator`。这些是外部
业务管线/模型资产，禁止由 GPU Control 备份脚本擅自改写；恢复时应从批准的镜像、上游 commit
和经过校验的模型副本重新同步。

所有备份都应视为敏感数据；`postgres-globals.sql` 可能含角色口令哈希。`full` 还明确包含生产
密钥、API 密钥和 TLS 私钥。脚本强制 `umask 077`、备份目录 `0700`、文件 `0600`，但离机复制
时仍必须使用加密介质并限制访问。

## 2. 一致性门禁

生产 `full` 备份前必须满足：

1. 将 4090、3090-A、3090-B GPU 节点切到 `DRAINING`；
2. `jobs`、`job_batches`、`asset_jobs` 中没有非终态任务（历史
   `WAITING_REVIEW`/`REVIEW_REJECTED` 视为已停止写入的终态）；
3. 所有节点 `current_jobs=0`；
4. 停止新的 API 提交；需要文件级强一致时，再停止 API、asset-api、scheduler 和 Asset Worker；
5. 不执行 `docker compose down -v`，不删除任务、模型或命名卷。

`full` 模式会在**开始归档前**和**全部 full 载荷写完后**各从 PostgreSQL 检查一次“零任务 +
在线 GPU 节点全部 DRAINING”。任一门禁不通过都会失败且不发布 `BACKUP_COMPLETE`。这两个门禁
只能发现快照时刻的任务，不能锁住外部 API 写入；因此操作员仍必须先停止提交/接单。只有明确
接受 crash-consistent 候选时才可使用 `--skip-quiesce-check`；恢复脚本默认拒绝这种 full 候选，
必须再次显式传入 `--allow-crash-consistent`，且不得把它登记为强一致生产恢复基线。

`small` 的 `pg_dump -Fc` 自身提供数据库一致性快照，但仍建议在发布前排空任务。

## 3. 创建和校验

先看计划：

```bash
cd /opt/gpu-control
scripts/backup.sh --mode small --dry-run
scripts/backup.sh --mode full --dry-run
```

创建快速恢复点：

```bash
sudo -n scripts/backup.sh \
  --mode small \
  --output /srv/gpu-control/backups
```

创建完整恢复点（在第 2 节门禁通过后）：

```bash
sudo -n scripts/backup.sh \
  --mode full \
  --output /srv/gpu-control/backups
```

目录名形如 `20260730T022631Z-full`。只有同时存在 `BACKUP_COMPLETE` 和通过校验的
`SHA256SUMS` 才是完整备份。清单覆盖全部 payload；完成标记在校验全部通过后才原子发布，并
固定 `SHA256SUMS` 自身的 SHA-256。任一 `tar`、`pg_dump`、Git 或 SHA 操作失败都会使脚本非零
退出，不会隐藏错误，也不会生成完成标记。

脚本拒绝把备份输出目录放进 GPU Control 工作树、任一 full 数据源或敏感配置源中，也拒绝让
输出目录包含这些源目录。这避免归档递归读入自身、磁盘耗尽或产生不可恢复的自引用快照。
`SECRETS_ROOT` 和 `SYSTEM_CONFIG_ROOT` 可覆盖两个敏感配置根的默认位置；存在时同样执行重叠
检查。完成标记先写入同目录临时文件，设为 `0600`，再通过原子 `rename` 发布。

独立复核：

```bash
scripts/restore.sh \
  --from /srv/gpu-control/backups/20260730T022631Z-full \
  --verify-only
```

将完成目录加密复制到第二存储，并记录目录自身的 SHA-256/介质编号。只保留在 4090 同一块磁盘
上的文件不算灾难恢复备份。

## 4. 恢复演练

恢复脚本要求 `BACKUP_MANIFEST`、`BACKUP_FORMAT=2`、manifest/marker 的 `MODE` 一致；再校验
完成标记固定的 `SHA256SUMS`。SHA 清单只能引用无斜杠的安全相对顶层文件名，必须精确覆盖全部
payload，不能包含额外、重复、缺失或目录外目标。备份顶层只允许普通文件，归档内部只允许普通
文件和目录；符号链接、硬链接、设备、FIFO、绝对路径和 `..` 路径穿越都会被拒绝。

正式 root 恢复要求备份目录/文件由 root 所有；普通用户演练要求由当前用户所有。两种情况下都
严格要求目录 `0700`、顶层文件 `0600`，并且所有者一致。`--dry-run` 不 source `.env`，也不依赖调用者预先设置
`POSTGRES_DB`/`POSTGRES_USER`；它从 `BACKUP_MANIFEST` 读取非敏感数据库身份并只打印计划。

仅验证数据库恢复计划（兼容旧用法，未选组件时默认 `--database`）：

```bash
scripts/restore.sh --from BACKUP_DIR --database --dry-run
```

将文件恢复到临时根目录做演练，而不是覆盖生产：

```bash
sudo install -d -m 0700 /tmp/gpu-control-restore-test
scripts/restore.sh \
  --from BACKUP_DIR \
  --secrets --data \
  --host-root /tmp/gpu-control-restore-test \
  --dry-run
```

仅在事故处置人员已经接受“备份期间可能仍有外部写入”的风险时，才允许验证或恢复跳过双门禁的
full 候选：

```bash
scripts/restore.sh \
  --from BACKUP_DIR \
  --allow-crash-consistent \
  --verify-only
```

去掉 `--dry-run` 后，脚本仍会分别要求输入 `RESTORE SECRETS` 和 `RESTORE DATA`。临时根中的
路径保持为 `opt/...`、`srv/...`、`etc/...`，便于逐项核对。

Git 提交历史可从 `repository.bundle` 独立克隆；`full` 的 `repository-worktree.tar` 还保留了
备份瞬间的未提交工作树。LFS 大对象是否可用必须结合 `git-lfs-files.txt`、离线镜像归档和 LFS
远端共同验证。

每次修改备份/恢复脚本后必须运行隔离安全回归；测试只使用 `/tmp` 中的合成 Git 仓库、假
Docker 和假载荷，不连接生产数据库，也不改变生产服务：

```bash
tests/scripts/test_backup_restore_security.sh
```

该测试覆盖合法 small/full 备份、前后双门禁、五项门禁输出完整性、完成标记发布，以及
目录/载荷符号链接、SHA 路径越界、未登记文件、危险权限、错误格式/模式、crash-consistent
默认拒绝、tar 链接与 `..` 穿越、敏感源目录重叠，并验证后门禁失败时绝不生成
`BACKUP_COMPLETE`。

## 5. 正式恢复

正式恢复前再次完成以下门禁：Drain 三节点、停止接单、确认所有 GPU/资产任务为 0、停止
API/scheduler/asset-api/Asset Worker，并先为当前故障现场再做一个恢复点。恢复数据库和文件会
覆盖数据；撤销只能恢复操作前的备份。

文件恢复是**覆盖式叠加**：只写入备份中存在的路径，不会删除目标上后来新增的旧文件。因此它
不是目录镜像同步；正式切换前必须在隔离目标演练并自行审计/清理陈旧文件。数据库恢复会依次
执行断开连接、删除数据库、重建和 `pg_restore`，不是一个跨步骤事务，也没有自动回滚；任何
中途失败都可能留下空库或部分恢复库，必须用恢复前快照重新执行。

### 5.1 数据库

```bash
scripts/restore.sh --from BACKUP_DIR --database --dry-run
scripts/restore.sh --from BACKUP_DIR --database
# 按提示输入：RESTORE DATABASE
```

只有裸机上尚无数据库角色时才增加 `--globals`，并先审计 `postgres-globals.sql`：

```bash
scripts/restore.sh --from BACKUP_DIR --globals --database
```

### 5.2 配置和代码

配置恢复绝不隐式发生，必须显式选择并输入确认短语：

```bash
scripts/restore.sh --from BACKUP_DIR --config --dry-run
scripts/restore.sh --from BACKUP_DIR --config
# 输入：RESTORE CONFIG
```

`--config` 仅覆盖 `configs`、`workflows`、`docker/comfyui`、`deploy/control-plane`。恢复整个备份
工作树需要 `full` 备份并显式使用 `--worktree` / `RESTORE WORKTREE`。优先从 Git 的固定 commit
部署；只有必须复原未提交现场时才恢复工作树。

### 5.3 密钥和运行数据

```bash
scripts/restore.sh --from BACKUP_DIR --secrets --data --dry-run
sudo -n scripts/restore.sh --from BACKUP_DIR --secrets --data
# 分别输入：RESTORE SECRETS、RESTORE DATA
```

恢复完成后复核所有者和权限，尤其是容器 uid/gid 10001 的任务/ComfyUI 目录。不要把
`sensitive-config.tar.gz`、解压后的 `.env` 或私钥提交到 Git。

## 6. 恢复后的固定验收顺序

1. 运行 `alembic upgrade head`；
2. 启动 PostgreSQL/Redis，再启动 API、asset-api、scheduler、Web/监控；
3. 运行 `scripts/smoke_test.sh`；
4. 核对数据库 revision、应用 image ID、离线镜像 SHA-256；
5. 在三个节点分别核对 ComfyUI image ID、外部管线 commit、自定义节点和模型 SHA-256；
6. 先恢复一个节点为 `ACTIVE` 做真实 canary，确认输入、最终产物和任务追溯正确；
7. 再依次恢复其余节点；4090 按既定策略回到 `OVERFLOW`，3090 节点回到 `ACTIVE`；
8. 观察至少一个告警周期，确认心跳、队列、GPU/CPU Asset Worker 和日志均正常。

监控历史 Docker volume 不是业务真源；若合规要求保留长期监控数据，应在对应服务停止后使用
存储后端原生快照另行备份。禁止直接打包正在写入的 `/var/lib/docker/volumes` 后宣称强一致。
