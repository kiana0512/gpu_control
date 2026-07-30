# 2026-07-30 可复现备份、三节点基线与滚动更新手册

> 审计时间：2026-07-30 11:29:31 +08（Asia/Singapore）<br>
> 审计范围：GPU Control 源码与 Git/LFS、控制面与 Worker Docker 镜像、4090/3090-A/3090-B 运行基线、数据与恢复点、滚动更新和回滚路径<br>
> 生产边界：本次审计没有修改 ImageClip、ModelViewCreator 的工作流 JSON、节点、模型、提示词、参数或输出语义，也没有为了审计重启生产服务。

## 1. 当前结论

运行面已经具备统一基线。11:29 原始审计发现的备份自引用、规范归档缺口和版本默认值漂移
均已在后续验收中关闭；截至本文最新增量核对，**文件级恢复、异机副本、数据库隔离恢复、镜像
离线载入、Git/LFS 远端发布和绑定发布提交的最终增量恢复点均已闭环**。

- 三台 GPU 节点使用同一份 ComfyUI `projects-0.2.3` 镜像，镜像 ID 一致，容器健康且队列为空。
- 三台 Linux/WSL2 资产 Worker 使用同一份 `li3d/blender-worker:1.2.2` 镜像，镜像 ID 一致。
- 控制面实际运行版本为 `1.5.4`，Git 本地 `HEAD`、`origin/main` 和远端 `main` 一致。
- 运行发布提交 `50f1d7b95e038fc5f313843dd9725c12a6b5e099` 已推送到 GitHub `main`；
  Asset Worker `1.2.2` 的 685,495,065 字节对象已作为标准 LFS pointer 提交并完成远端上传。
- ComfyUI `0.2.3` 的本地完整归档和 SHA-256 已存在。
- 11:29 审计时，候选全量备份 `full-20260730T023838Z-pre-rollout` 的校验文件生成过程发生自引用且没有最终完成标记；该历史问题已在审计后修复并完成 LOCAL/A/B 异机验收，当前状态见 5.4 节。
- 当前运行的控制面 `1.5.4`、资产 Worker `1.2.2` 和 ComfyUI `0.2.3` 均已有本地规范归档、
  SHA-256 和离线载入证据；Worker LFS OID 与规范归档摘要均为 `7bb6c067...c09c72f`。
- 最终小恢复点 `20260730T045705Z-small` 已绑定上述 40 位运行发布提交，并在 LOCAL、3090-A、
  3090-B 三地通过 14/14 载荷校验。

因此当前状态应表述为：

> 生产运行与镜像基线一致；全量恢复点、异机副本、隔离数据库恢复、三类镜像归档、Git/LFS
> 发布和发布后最终增量恢复点均已验收。任何恢复演练仍不得把没有有效
> `BACKUP_COMPLETE` 的目录当作恢复点。

## 2. 稳定身份与节点基线

节点身份必须以 `node_id + MAC + GPU UUID` 为主，IP 仅是当前路由地址，不能作为唯一身份。

| 逻辑节点 | 当前/最近验证地址 | 稳定身份 | 角色 | GPU 运行基线 | 资产运行基线 |
| --- | --- | --- | --- | --- | --- |
| `control-4090` | `10.3.34.11` | 主控固定 MAC `58:11:22:c1:66:63` | 控制面、4090 备用/溢出算力、CPU Asset Worker | ComfyUI `projects-0.2.3` | `li3d/blender-worker:1.2.2`，并发槽位 2 |
| `worker-3090-a` | `10.3.34.12`（以实时心跳为准） | MAC `18:c0:4d:9f:13:13` | Linux GPU/CPU Worker | ComfyUI `projects-0.2.3` | `li3d/blender-worker:1.2.2`，并发槽位 3 |
| `worker-3090-b` | Windows `10.3.34.14`，WSL SSH `gpucontrol@10.3.34.14:2222` | MAC `2c:f0:5d:76:7b:70` | WSL2 GPU/CPU Worker、Windows Substance Baker | ComfyUI `projects-0.2.3` | Linux Worker 并发槽位 4；Windows Baker 四个独立槽位 |

3090-B 还必须同时满足以下两条链路：

1. `4090 -> gpucontrol@10.3.34.14:2222 -> WSL2 sshd` 稳定；历史账户 `lilithgames` 不再用于该链路；
2. `4090 -> Windows 原生 Baker Worker` 心跳与任务调用稳定。

DHCP 地址变化后，主控应通过 MAC、节点注册信息和心跳重新绑定地址；不得新建一个同名“新节点”留下幽灵记录。

## 3. 精确软件与镜像基线

### 3.1 Git 基线

| 项目 | 已验证值 |
| --- | --- |
| GPU Control 仓库 | `https://github.com/kiana0512/gpu_control.git` |
| 分支 | `main` |
| 审计起点 `HEAD` | `1a912bbce56b744ca668ddd4ee8e149d46d939d2` |
| 运行发布提交 | `50f1d7b95e038fc5f313843dd9725c12a6b5e099` |
| 发布时 `origin/main` | `50f1d7b95e038fc5f313843dd9725c12a6b5e099` |
| 发布时远端 `main` | `50f1d7b95e038fc5f313843dd9725c12a6b5e099` |
| Git 完整性 | `git fsck --full --no-dangling` 通过 |
| LFS 完整性 | `git lfs fsck --pointers` 与 `--objects` 通过；`git lfs push --dry-run origin main` 无待推送对象 |

11:29 原始审计时工作树至少存在以下未提交修改：

- `docs/17_BACKUP_AND_RESTORE.md`
- `scripts/backup.sh`
- `scripts/restore.sh`

这些变更的静态检查结果为：

- `bash -n scripts/backup.sh scripts/restore.sh`：通过；
- `scripts/backup.sh --mode full --dry-run`：通过；
- `git diff --check`：通过。

后续收尾又加入了经授权的控制面、Web、版本锁、测试、清单、兼容性元数据和镜像 LFS 分片。
正式提交前已依据 `git status --short` 与暂存区完成范围审计；这些变更已经随运行发布提交
`50f1d7b95e038fc5f313843dd9725c12a6b5e099` 推送，形成新的可复现 Git 基线。本文件后续的
收口提交只补充审计事实，不改变运行代码、部署配置、镜像或业务管线；最终恢复点因此刻意绑定
上述运行发布提交，而不是形成自引用备份。

### 3.2 控制面镜像

| 镜像 | 生产镜像 ID | 审计状态 |
| --- | --- | --- |
| `gpu-control-api:1.5.4` | `sha256:06147d527d4a146141c9cf3c56b62c474096543cbdbde2050b2d1a652e478cb3` | healthy |
| `unified-scheduler-asset-api:1.5.4` | `sha256:827053b49248ea22296fb3b78fb3012f1a158577f34921b30dcf140567ce0c3d` | healthy |
| `gpu-control-scheduler:1.5.4` | `sha256:f9569a39438bbbc63a9b3f8c6ff3991e1bce67efddc69167467549c16f4a227b` | healthy |
| `gpu-control-web:1.5.4` | `sha256:8f9558646a306600a24c2898355901a85b0e3b4fd94c3e807b7d2fa27cf408ae` | healthy |

Git LFS 中的控制面 `1.5.4` 归档位于：

```text
artifacts/control-plane/1.5.4/
├── unified-scheduler-1.5.4-images.tar.gz.part-00
├── unified-scheduler-1.5.4-images.tar.gz.part-01
├── SHA256SUMS.txt
└── README.md
```

分卷已验证：

| 对象 | SHA-256 |
| --- | --- |
| `part-00` | `2dedbcec84b9c3e427b2f1c3189770d97a9eac1c5a462aed624abfe5546214e1` |
| `part-01` | `5bc3d4e733ad79bde40485763419fb4e68d3fd993ae7e882350731d85187394d` |
| 重组后的 `unified-scheduler-1.5.4-images.tar.gz` | `b3afe81e660f899f737819deabd46bd5c9dba847097df806a87b66ca79a94d51` |

重组与校验：

```bash
cd /opt/gpu-control
cat artifacts/control-plane/1.5.4/unified-scheduler-1.5.4-images.tar.gz.part-* \
  > /tmp/unified-scheduler-1.5.4-images.tar.gz
sha256sum /tmp/unified-scheduler-1.5.4-images.tar.gz
```

### 3.3 ComfyUI 与资产 Worker

| 镜像 | 生产镜像 ID | 三节点状态 | 规范归档状态 |
| --- | --- | --- | --- |
| `registry.local:5000/gpu-control/comfyui:projects-0.2.3` | `sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea` | 4090/A/B 一致，healthy，重启次数 0 | 完整归档存在 |
| `li3d/blender-worker:1.2.2` | `sha256:9bf4344503041abec7dd67067ccbbb0946223af53b06d1a4a67a27acfeaab6ad` | 4090/A/B 一致，运行中，重启次数 0 | 规范归档、SHA、离线载入及远端 LFS 对象已验证 |

ComfyUI `0.2.3` 归档：

```text
/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz.sha256
```

已记录归档 SHA-256：

```text
20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586
```

三台 ComfyUI 运行时一致性：

- ComfyUI `0.28.0`
- 前端 `1.45.21`
- 模板 `0.11.9`
- Python `3.11.13`
- PyTorch `2.7.1+cu128`
- 启动参数包含 `--disable-api-nodes`

3090-A、3090-B 的 GPU Control 关键部署文件与 4090 合并摘要一致：

```text
90ac84cdbf185bfc731a001841037a1978286068c9a0f8a07ab13dde917d37cd
```

## 4. 外部业务管线基线

GPU Control 只部署并校验用户批准的上游版本，不拥有也不修改以下业务管线内容。

| 管线 | 批准的上游提交 | 恢复证据 |
| --- | --- | --- |
| ImageClip | `691770cd6a59fd7c51391456fe900dc57a313233` | 三节点 `HEAD` 与 `git ls-remote origin main` 一致；完整 Git bundle 已通过验证 |
| ModelViewCreator | `d318bb392040e2d5f6bbd10ae61d832d36d3cb4a` | 完整 Git bundle 已通过验证 |

在任何节点重新进入 `ACTIVE` 前必须逐台再次确认：

```bash
git -C /opt/imageclip rev-parse HEAD
git -C /opt/imageclip status --short
git -C /opt/modelviewcreator rev-parse HEAD
git -C /opt/modelviewcreator status --short
```

验收要求：

- 三台机器分别与上述批准提交一致；
- 工作树无未授权修改；
- 工作流 JSON、custom nodes、模型清单和最终输出节点未被 GPU Control 改写；
- 模型实际文件 SHA-256 与批准清单一致；
- 只返回批准的最终产物，不返回预览图或中间结果。

本轮三节点只读校验还确认：

- ImageClip pipeline SHA-256：
  `00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b`；
- ImageClip 模型清单 SHA-256：
  `4932d81a5a73ba8ea9c4afe5cf04a5dc48c8a506845a79d2a73460d360a540ee`；
- ModelViewCreator 模型清单 SHA-256：
  `388668d29b538b1a21a0ad852e5df81042f78a0821bf00da963c41fdbf26a731`；
- 上述摘要在 4090、3090-A、3090-B 三端一致。

当前数据库中三台 GPU 节点的 `model_manifest_version` 仍为空。这是**非阻断的可观测性展示缺口**：
运行兼容性已经使用三端实际提交与内容 SHA-256 做 fail-closed 校验，不影响当前已验证功能；后续仍应
补充为可查询的模型清单版本或摘要，避免只依靠人工记忆。

## 5. 候选全量备份审计

### 5.1 候选目录

```text
/srv/gpu-control/backups/full-20260730T023838Z-pre-rollout
```

该目录包含的主要数据：

- PostgreSQL custom-format dump：`gpu_control.dump`（约 2.14 MB）和 globals；
- 控制数据归档：`data/control-data.tar.zst`（约 17.75 GB）；
- PostgreSQL、Redis、Prometheus、Alertmanager、Grafana、Loki、Alloy 等卷快照；
- GPU Control、ImageClip、ModelViewCreator Git bundle 和现场 patch；
- ImageClip/ModelViewCreator 外部管线快照；
- 3090-A 节点快照；
- 3090-B WSL2 与 Windows 原生侧快照；
- `li3d/blender-worker:1.2.2` 镜像归档；
- 公开配置与受控 secrets 快照。

已完成的局部验证：

- `gpu_control.dump` 可由 `pg_restore -l` 正常解析；
- GPU Control、ImageClip、ModelViewCreator Git bundle 均为完整 bundle；
- Worker `1.2.2` 的 zstd 镜像归档通过 `zstd -t`；
- bundle 中的分支提交与本手册记录一致。

### 5.2 审计时为什么它不是正式恢复点

旧校验流程把 `SHA256SUMS.tmp` 本身纳入了待校验文件集合，形成自引用，最终无法产生可信的固定校验清单。审计时目录状态为：

- 只有不完整的 `SHA256SUMS.tmp`；
- 缺少最终 `SHA256SUMS`；
- 缺少 `BACKUP_COMPLETE`；
- 缺少与最终校验清单绑定的完成摘要。

因此必须遵守：

> 没有 `BACKUP_COMPLETE` 且其内容未绑定最终 `SHA256SUMS` 的目录，一律视为中断或候选备份，不得用于自动恢复，不得删除上一份已验证恢复点。

以上是 11:29 的原始审计结论；审计后的修复和异机验收结果见 5.4 节，不能再将该目录按旧状态判定为“缺少完成标记”。

### 5.3 正式恢复点完成标准

新的全量备份只有同时满足下列条件才可登记为 `VERIFIED`：

1. 备份开始前记录 Git 提交、所有镜像 tag/ID、数据库迁移版本和节点模式；
2. 备份只短暂采集一致性快照，不把三台生产节点长期保持在 `DRAINING`；
3. 最终 `SHA256SUMS` 只覆盖业务载荷，不覆盖临时校验文件和完成标记；
4. `sha256sum -c SHA256SUMS` 全部通过；
5. PostgreSQL dump 可列出，且在隔离实例完成一次恢复演练；
6. Git bundle `git bundle verify` 全部通过；
7. Docker 归档能通过压缩测试、载入，并匹配预期镜像 ID；
8. secrets 目录权限正确，备份未把凭据写入普通文档或日志；
9. `BACKUP_COMPLETE` 记录备份 ID、完成时间、清单 SHA-256、Git 提交和发布版本；
10. 至少复制一份到不同物理设备或受控离线存储，并再次校验。

推荐的完成标记内容：

```text
backup_id=<目录名>
completed_at=<ISO-8601>
git_commit=<40位提交>
release=1.5.4
sha256sums_sha256=<SHA256SUMS 文件自身摘要>
verification=passed
```

### 5.4 审计后全量恢复点异机验收

`full-20260730T023838Z-pre-rollout` 已在修正校验流程后完成正式标记与三地一致性验收：

| 验收位置 | 文件总数 | 总字节数 | 载荷校验 |
| --- | ---: | ---: | --- |
| 4090 本机（LOCAL） | 34 | 86,883,229,399 | 32/32 通过 |
| 3090-A | 34 | 86,883,229,399 | 32/32 通过 |
| 3090-B | 34 | 86,883,229,399 | 32/32 通过 |

这里的 34 个文件包含最终 `SHA256SUMS` 和 `BACKUP_COMPLETE`；清单覆盖的 32 个业务载荷在 A/B
两台异机均逐项通过。关键事实为：

- `SHA256SUMS` SHA-256：
  `4a7aefecb5b45d18ce6adeac970bd88bfed062c643db4c6e9953df6a6cfdd849`；
- `BACKUP_COMPLETE` SHA-256：
  `7bdc86b0318f6b55425bf47ec246e17d1c6e8cef6d0f0104e2072a7c30b35533`；
- marker：`CREATED_UTC=2026-07-30T03:35:46Z`，`MODE=full-custom-pre-rollout`；
- LOCAL、3090-A、3090-B 的文件数与总字节数完全一致，marker 字段完整；
- 两路局域网传输合计吞吐约 `949 Mbps`。

因此，5.2 节记录的自引用和缺少完成标记问题已经关闭；该恢复点现可作为已完成、已异机校验的
恢复输入。数据库隔离恢复和镜像离线载入属于更高层的恢复演练，不应与“文件级恢复点完整性”
混为一谈；这两项后续也已完成，证据见第 12、15 节及
[63 号收尾文档](63_2026-07-30_THREE_NODE_RELEASE_AND_RECOVERY_CLOSURE.md)。

同时已建立严格 format-2 小恢复点：

```text
/srv/gpu-control/backups/20260730T040031Z-small
```

该恢复点已经在本机通过 `verify-only`、全量 SHA-256、Git bundle 验证和 PostgreSQL dump catalog
解析，并已同步到 3090-A 与 3090-B。LOCAL/A/B 三端的控制文件摘要完全一致：

| 控制文件 | SHA-256 |
| --- | --- |
| `SHA256SUMS` | `0fa031babdc2b94edcfc6c1ac49e945601ec86350a2958f3769ce685c3120052` |
| `BACKUP_COMPLETE` | `979169912c5bf5520fdf3e77760a0f1b3e8a79d3c1477249080a5f622124c638` |
| `BACKUP_MANIFEST` | `78c46eb7d8e55e933cd47bc278951b9ae225f6926e9ada2f53bda72aa5e6ccbd` |

3090-A 与 3090-B 均逐文件 `14/14` 校验通过；上述三个控制文件在三端权限均为 `0600`、
属主属组均为 `root:root`。该恢复点用于快速验证恢复格式、元数据、源码 bundle 和数据库目录
可读性；不能替代包含全部生产数据与镜像载荷的全量恢复点。

## 6. 规范归档与 LFS 发布状态

当前 `/srv/gpu-control/images` 已具备三类生产规范归档：

```text
/srv/gpu-control/images/unified-scheduler-1.5.4-images.tar.gz
/srv/gpu-control/images/unified-scheduler-1.5.4-images.tar.gz.sha256
/srv/gpu-control/images/li3d-blender-worker-1.2.2.tar.zst
/srv/gpu-control/images/li3d-blender-worker-1.2.2.tar.zst.sha256
/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz.sha256
```

归档摘要分别为：

| 归档 | SHA-256 |
| --- | --- |
| `unified-scheduler-1.5.4-images.tar.gz` | `b3afe81e660f899f737819deabd46bd5c9dba847097df806a87b66ca79a94d51` |
| `li3d-blender-worker-1.2.2.tar.zst` | `7bb6c067c4a358a864e436fd2fc09271716ed7848b805b753fbbdb97ec09c72f` |
| `comfyui-projects-registry-0.2.3.tar.gz` | `20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586` |

压缩完整性和隔离 `docker load` 已验证，载入后的镜像 ID 与第 3 节一致。Worker `1.2.2` 的 LFS
分片和跟踪规则已随运行发布提交推送；Git blob 是 134 字节标准 LFS pointer，远端对象大小
685,495,065 字节，OID 为 `sha256:7bb6c067...c09c72f`。复核命令：

```bash
git lfs fsck
git lfs push --dry-run origin main
```

不得只保存 tag；必须同时保存内容归档、内容 SHA-256 和载入后的镜像 ID。

## 7. 配置版本漂移

11:29 原始审计发现实际生产已运行 `1.5.4 / 1.2.2 / 0.2.3`，但 Compose、lock、Python/Web
包和 UI footer 仍有旧默认值。当前工作树已经统一为：

`.env.example` 已是正确的当前值：

```text
APP_VERSION=1.5.4
ASSET_WORKER_VERSION=1.2.2
COMFYUI_VERSION=projects-0.2.3
```

Compose、`configs/versions.lock.env`、Python/Web 包版本和 UI footer 已按上述版本修正；它们仍需随
当前工作树一起提交和推送，才能成为远端默认值。新机器在此之前仍必须显式使用正确 `.env`，避免
从旧远端提交静默回退。

## 8. 不停服滚动更新流程

### 8.1 总原则

- 一次只滚动一台 GPU 节点；其余节点继续接单。
- 节点先 `DRAINING`，确认 `current_jobs=0` 后才允许重启或替换。
- 控制面服务更新与 GPU/CPU Worker 更新分开执行。
- GPU 任务队列与 CPU Asset 队列互相隔离，不因更新其中一类而暂停另一类。
- 外部管线只部署批准提交，不修改内容。
- 更新失败立即回滚当前节点，不继续滚动下一台。

### 8.2 更新前只读检查

```bash
cd /opt/gpu-control
git status --short
git rev-parse HEAD
git lfs fsck
docker compose --env-file .env -f deploy/control-plane/compose.yaml ps
docker compose --env-file .env -f deploy/control-plane/compose.yaml config --images
curl -fsS https://10.3.34.11/health/live
curl -fsS https://10.3.34.11/health/ready
```

还必须从 API/数据库确认：

- 目标节点没有 `RUNNING` 任务；
- GPU 批次和 Asset Job 不处于不可安全重放阶段；
- 当前镜像 ID、外部管线提交和模型摘要已记录；
- 上一个 `VERIFIED` 恢复点可读。

### 8.3 单节点滚动步骤

1. 将目标节点设为 `DRAINING`，禁止新任务进入。
2. 轮询至 `current_jobs=0`，同时确认 ComfyUI `/queue` 为空。
3. 校验目标镜像归档 SHA-256。
4. 加载镜像并确认 `docker image inspect` ID 与基线一致。
5. 同步 GPU Control 部署文件；外部管线只做批准提交的精确同步和只读 hash 比较。
6. 仅重建目标节点的相关容器或服务。
7. 验证容器 health、重启次数、GPU 访问、节点心跳、模型/节点清单。
8. 运行一个真实但受控的最终产物 canary；不得用预览替代。
9. 将节点恢复到既定模式：3090-A/B 为 `ACTIVE`，4090 按策略为 `OVERFLOW` 或管理员明确指定的模式。
10. 观察一个完整任务周期后再滚动下一台。

三台节点不能同时留在 `DRAINING`。审计快照中三台节点曾同时显示 `mode=DRAINING`、`current_jobs=0`；完成备份后必须立即恢复预期接单模式并验证调度分配。

### 8.4 推荐顺序

```text
3090-B -> 3090-A -> 4090 GPU Worker -> Asset API/Web -> Scheduler/API
```

原因：先验证最复杂的 Windows/WSL2 混合节点，再验证纯 Linux Worker，最后保留主控/4090 作为回退能力。若当时任务分布不同，应以“保留至少一台兼容节点在线”为硬约束调整顺序。

## 9. 控制面更新与数据库迁移

控制面更新必须使用精确镜像 tag，禁止 `latest`。

1. 记录当前数据库 Alembic revision；审计时为 `20260729_0010`。
2. 创建并验收数据库一致性备份。
3. 读取新镜像包含的迁移，确认 downgrade/回滚策略。
4. 先更新无状态 Web，再更新 Asset API/API，最后更新 Scheduler；需要迁移时严格按发布说明执行。
5. 每个服务更新后检查 `health`、日志错误率、队列深度和任务状态机。
6. 不允许把运行中任务改成 `FAILED` 或 `CANCELLED` 以便发布。
7. 更新失败时，停止继续滚动，恢复旧镜像；若数据库 schema 已不向后兼容，按已验证 dump 恢复到隔离环境确认后再执行生产回滚。

控制面部署验证：

```bash
docker compose --env-file .env -f deploy/control-plane/compose.yaml config >/dev/null
docker compose --env-file .env -f deploy/control-plane/compose.yaml ps
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
  gpu-control-api gpu-control-scheduler gpu-control-web
```

## 10. 回滚矩阵

| 故障范围 | 优先回滚动作 | 数据保护要求 | 回滚后验收 |
| --- | --- | --- | --- |
| 单台 ComfyUI 节点 | 节点保持 `DRAINING`，重新载入上一份已验证 ComfyUI 归档并仅重建该节点 | 保留输入、输出、模型卷，不删除任务 | `/queue` 空、health 正常、镜像 ID 匹配、真实 canary 成功 |
| 单台 Asset Worker | 保持 `DRAINING`，恢复上一份 Worker 镜像与相同 skills mount | 不覆盖用户源模型和最终归档 | 心跳、Blender/Codex/RetopoFlow 探针与真实 Asset Job 成功 |
| 3090-B Windows Baker | 暂停 Baker 新接单，恢复上一个 Windows Worker 包/服务配置 | 保留 Windows 任务目录和日志；不动 WSL GPU Worker | 四个槽位分别心跳；真实 bake 最终产物齐全 |
| Web UI | 恢复旧 Web 镜像 | API/数据库不变 | 页面加载、任务筛选、详情和下载正常 |
| API/Scheduler | 恢复旧镜像；必要时禁用新请求但不杀运行任务 | 保留 Redis、PostgreSQL 和 artifact 状态 | live/ready、队列、幂等重试、任务状态一致 |
| 数据库 | 仅在确认 schema/数据损坏后，使用 `VERIFIED` dump 恢复 | 先保存故障现场快照；恢复到隔离实例验证 | 迁移版本、任务计数、artifact 关联和审计日志一致 |
| 整机丢失 | 先恢复 OS/Docker/驱动，再载入精确镜像和部署包，最后挂载模型/数据 | 不从未完成候选目录自动恢复 | 节点身份/MAC/GPU UUID、镜像、管线、模型、真实任务全部通过 |

## 11. 从零恢复顺序

```text
验证 BACKUP_COMPLETE
  -> 验证 SHA256SUMS
  -> 恢复 GPU Control Git bundle/精确提交
  -> 恢复 PostgreSQL/Redis 与控制数据
  -> 加载控制面 1.5.4 精确镜像
  -> 启动并验证控制面
  -> 逐台恢复节点 OS/驱动/Docker/Agent
  -> 加载 ComfyUI 0.2.3 与 Asset Worker 1.2.2
  -> 恢复批准的外部管线提交和模型软链接
  -> 恢复 3090-B Windows Baker 四槽位
  -> 节点保持 DRAINING 做真实 canary
  -> 逐台 ACTIVE/OVERFLOW
  -> 验证端到端 API、任务追踪和最终产物
```

最小验收命令示例：

```bash
sha256sum -c SHA256SUMS
git bundle verify bundles/gpu-control.bundle
git bundle verify bundles/imageclip.bundle
git bundle verify bundles/modelviewcreator.bundle
zstd -t images/li3d-blender-worker-1.2.2.tar.zst
docker load < /srv/gpu-control/images/unified-scheduler-1.5.4-images.tar.gz
docker load < /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
```

实际文件名以恢复点清单为准。压缩格式不同的镜像必须先用对应解压程序流式解压，不能直接假定 `docker load` 支持该压缩格式。

## 12. 当前风险与待验收项

以下项目按最新证据分为已完成和非阻断清理项；运行发布、LFS 和三地恢复点已经闭环：

1. **已完成：**全量恢复点已产生可验证的 `SHA256SUMS` 与 `BACKUP_COMPLETE`，并通过 LOCAL/A/B
   三地一致性与 32/32 载荷校验，详见 5.4 节。
2. **已完成：**PostgreSQL custom dump 已在隔离实例真实恢复；恢复后表数 `29`、Alembic revision
   `20260729_0010`、节点数 `3`、任务数 `2383`，结果 `ISOLATED_DATABASE_RESTORE=PASS`。
3. **已完成：**控制面 `1.5.4`、Worker `1.2.2` 已落到规范归档目录；Worker `1.2.2` LFS 分片
   已作为 pointer 提交，685,495,065 字节对象完成远端上传并通过本地对象完整性检查。
4. **已完成：**控制面归档已包含 API、Asset API、Scheduler、Web；ComfyUI 与 Worker 独立归档均已
   校验并完成隔离载入。
5. **已完成：**ImageClip/ModelViewCreator 三节点实际 `HEAD`、权威远端提交、pipeline/model manifest
   摘要已逐台复核，结果见第 4 节。
6. **非阻断可观测性缺口：**为三台节点写入并展示非空的 `model_manifest_version`；实际兼容性
   已由三端 commit 与内容 SHA-256 校验，不影响当前功能门禁。
7. **已完成：**三节点已恢复 4090 `OVERFLOW/ACTIVE`、A/B `PRIMARY/ACTIVE`，没有长期排空；
   ImageClip manifest `2026.07.30-691770c-r1` 已启用、旧版已禁用，三节点 `3/3 compatible`，
   canary `87917fd6-1e38-4c5b-83a1-d0014a28ee91` 在 3090-A 以 `SUCCEEDED / HTTP 200` 完成，
   最终 `1080x1440 RGBA PNG` SHA-256 为
   `8f648f9b18f5c72bfec7cdf9f6531613cda53fff788b53918c6a929cf0415c4a`。
8. 将数据库中旧的 `asset-worker-3090-b-windows` 幽灵记录标记离线、归档或排除；保留当前四个真实 Windows Baker 槽位。
9. 3090-B 在队列为空时仍可能因 PyTorch 缓存只剩约 4.9 GiB 可用显存；应在节点 `DRAINING` 且无任务时使用安全的缓存释放机制或按真实可用显存准入，不能杀运行任务。
10. 确认并清理/隔离仍在运行的 `gpu-control-asset-realtest-*`、`gpu-control-asset-v3test-worker-4090` 测试栈；先证明它们不承载生产任务。
11. **已完成并发布：**Compose、lock 文件、Python/Web 包版本、UI footer 和实现文档默认值已统一，
    已随运行发布提交 `50f1d7b95e038fc5f313843dd9725c12a6b5e099` 推送。
12. **已完成：**全量与 strict format-2 恢复点均已复制到 3090-A、3090-B 两台不同物理主机并
    二次校验。
13. **已完成：**运行发布提交已推送 `main`；绑定该提交的 `20260730T045705Z-small` 已在 LOCAL、
    3090-A、3090-B 三地完成 14/14 载荷校验。

## 13. 最终交付检查表

### Git 与文档

- [x] 运行发布提交只包含本次明确授权的修改。
- [x] `git diff --check` 通过。
- [x] 测试与备份恢复演练通过。
- [x] 运行变更已提交到 `main` 并推送。
- [x] `git lfs fsck` 通过，无待推送 LFS 对象。
- [x] 本手册、发布记录、API 使用文档和镜像 README 记录同一版本。

### Docker 与归档

- [x] 控制面 `1.5.4` 完整归档、SHA-256、镜像 ID 三者对应。
- [x] ComfyUI `0.2.3` 完整归档、SHA-256、镜像 ID 三者对应。
- [x] Asset Worker `1.2.2` 完整归档、SHA-256、镜像 ID 三者对应。
- [x] 3090-B Windows Baker 包、服务定义和四槽位配置被纳入全量备份。
- [x] 所有镜像都执行过压缩完整性与离线载入验证。

### 三节点生产状态

- [x] 4090、3090-A、3090-B GPU Agent 心跳正常。
- [x] 三台 ComfyUI 容器 healthy、重启次数为 0、队列状态可查询。
- [x] 三台外部业务管线提交和模型 SHA-256 一致。
- [x] 三台 Linux/WSL Asset Worker 心跳、Blender、Codex、RetopoFlow 探针正常。
- [x] 3090-B Windows Baker 四个槽位均在线。
- [x] GPU 队列和 CPU Asset 队列互不阻塞。
- [x] 真实抠图、局部重绘、粗糙度、UV、重拓扑、Windows 烘焙已有近期成功证据；ImageClip
  新 manifest 的 Job ID、节点、终态与最终产物 SHA-256 已记录。
- [x] 4090/3090-A/3090-B 已恢复预期 `ACTIVE/OVERFLOW` 模式，不长期排空。

### 恢复点

- [x] 新全量备份具有 `BACKUP_COMPLETE`。
- [x] `SHA256SUMS` 在 LOCAL/A/B 的 32 个载荷上全量校验通过。
- [x] 数据库隔离恢复通过。
- [x] Git bundle、镜像、卷、节点快照和 secrets 均可读。
- [x] 3090-A、3090-B 异机副本完成并通过二次校验。
- [x] 上一份 `VERIFIED` 恢复点在新恢复点验收前未被删除。
- [x] 发布后恢复点 `20260730T045705Z-small` 绑定运行发布提交并在 LOCAL/A/B 通过 14/14 校验。

## 14. 变更纪律

- 不为缩短时间修改 ImageClip/ModelViewCreator 工作流、提示词、模型、节点参数或输出语义。
- 不在任务运行时重启节点；必须先 Drain 并确认空闲。
- 不使用浮动 tag、未校验归档或未完成备份。
- 不把测试数据混入真实客户任务视图。
- 不把中间图、预览图或候选资产当作最终交付。
- 不因节点失联就把用户任务静默标记为取消或失败；使用可恢复状态、幂等重试和明确错误码。
- 每次生产变更都记录：Git 提交、镜像 tag/ID、管线提交、模型摘要、执行人、时间、验证结果和回滚点。

本手册是恢复与发布的事实基线。第 12、13 节的发布硬门禁已经关闭；数据库旧行、可选节点缺失和
测试栈清理仍按非阻断维护项处理，不得为清理它们中断生产。

## 15. 审计后修复状态（2026-07-30 收尾窗口）

本节是 11:29 原始审计快照之后的增量状态，保留审计时间线并记录最终发布完成事实。

已完成、发布并通过静态/运行核对的项目：

- 控制面 Python 包、Web 包与 lock、Compose 默认值已经统一到 `1.5.4`；
- Asset Worker 的控制面与节点 Compose 默认值已经统一到 `1.2.2`；
- ComfyUI 锁定版本已经统一到 `projects-0.2.3`；
- 4090 / 3090-A / 3090-B 的示例角色恢复为 `OVERFLOW / ACTIVE / ACTIVE`；
- Asset Worker `1.2.2` 归档分片已经生成，`zstd -t` 通过，分片 SHA-256 为
  `7bb6c067c4a358a864e436fd2fc09271716ed7848b805b753fbbdb97ec09c72f`，并与局部及根清单一致；
- `.gitattributes` 已包含 Asset Worker `1.2.2` 分片的 Git LFS 跟踪规则；
- 当前文档相对链接检查未发现断链，`git diff --check` 通过；
- 三类生产镜像规范归档、摘要、压缩完整性与离线载入均已验证；
- PostgreSQL 已在隔离实例完成真实恢复，结果 `ISOLATED_DATABASE_RESTORE=PASS`；
- Python 测试 `101/101`、备份/恢复安全测试 `23/23`、Web Vitest `3/3` 与生产构建均通过。

最终闭环证据：

1. Asset Worker `1.2.2` 分片已确认是 LFS pointer；`git lfs fsck --pointers`、`--objects` 通过，
   `git lfs push --dry-run origin main` 无待推对象；
2. 源码、脚本、文档、清单和 LFS pointer 已形成运行发布提交
   `50f1d7b95e038fc5f313843dd9725c12a6b5e099`，发布时本地、`origin/main` 与 GitHub `main` 一致；
3. 发布后恢复点 `20260730T045705Z-small` 已绑定该运行提交，并复制到 3090-A、3090-B；三地
   `SHA256SUMS`、`BACKUP_COMPLETE`、`BACKUP_MANIFEST` 摘要一致且 14/14 校验通过；
4. 三节点在线状态、外部业务管线哈希和模型清单已在收尾窗口实时复核；ImageClip 兼容性元数据、
   `3/3 compatible` 与最终 canary 证据已经闭环；
5. 数据库旧 `asset-worker-3090-b-windows` 行和 3090-A 可选、未被批准工作流引用的
   `NunchakuDepthPreprocessor` 缺失属于非阻断清理项，不得据此中断生产服务。

因此，当前准确状态是：**版本漂移已修复并发布；全量与小恢复点已完成文件级和异机校验；
数据库隔离恢复、三类镜像归档/载入、代码与 Web 测试、ImageClip 最终 canary、Git/LFS 远端发布
以及绑定运行提交的最终恢复点均已通过。**

最终发布与恢复闭环证据统一记录在
[63 号收尾文档](63_2026-07-30_THREE_NODE_RELEASE_AND_RECOVERY_CLOSURE.md)。
