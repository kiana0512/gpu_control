# 2026-07-30 三节点发布、备份与恢复闭环记录

> 状态：发布完成；ImageClip 兼容性元数据与真实 canary、Git/LFS 运行发布、三地最终恢复点均已关闭。<br>
> 时间基准：Asia/Singapore（UTC+08:00）。<br>
> 生产约束：本轮没有修改 ImageClip 或 ModelViewCreator 的工作流 JSON、custom nodes、模型、提示词、
> 推理参数、图拓扑或输出语义；没有为了文档审计重启生产后端，也没有把三台 GPU 长期留在
> `DRAINING`。

本文集中记录 4090、3090-A、3090-B 的发布门禁、业务管线一致性、镜像、恢复点、隔离恢复、测试、
滚动恢复和已知非阻断项。详细操作规范见
[可复现备份与滚动更新手册](62_2026-07-30_REPRODUCIBLE_BACKUP_AND_ROLLING_UPDATE.md)和
[备份恢复手册](17_BACKUP_AND_RESTORE.md)。

## 1. 结论与已关闭的硬门禁

当前已经完成：

- 三台 GPU 节点在线，4090 为 `OVERFLOW / ACTIVE`，3090-A/B 为 `PRIMARY / ACTIVE`，均没有
  运行中任务时才执行了本轮只读/元数据审计；
- 控制面、数据库、缓存、反向代理和监控组件健康，ready 门禁 `10/10`；
- 三台 ComfyUI 为同一 `projects-0.2.3` 镜像 ID，容器 healthy，重启次数为 0；
- 三台外部业务仓库已与权威远端提交和内容摘要对齐；
- 4090/A/B 的 Asset Worker、Codex、Blender、RetopoFlow 探针正常，3090-B 的四个 Windows
  Substance Baker 槽位在线；
- 全量恢复点和 strict format-2 恢复点均在 LOCAL、3090-A、3090-B 校验通过；
- PostgreSQL custom dump 已在隔离实例真实恢复；
- 控制面、Asset Worker、ComfyUI 三类生产镜像均有规范归档、SHA-256、压缩测试和离线载入证据；
- Python、Web 和备份/恢复安全测试通过。

发布硬门禁已关闭：

1. **Git/LFS 运行发布。**
   - 运行发布 commit：`50f1d7b95e038fc5f313843dd9725c12a6b5e099`
   - 发布时 `origin/main`：`50f1d7b95e038fc5f313843dd9725c12a6b5e099`
   - 发布时 GitHub `main`：`50f1d7b95e038fc5f313843dd9725c12a6b5e099`
   - `git lfs fsck --pointers` / `--objects`：PASS
   - `git lfs push --dry-run origin main`：无输出，无待推对象
   - Worker LFS 对象：685,495,065 B；OID `sha256:7bb6c067...c09c72f`
2. **发布后最终增量恢复点。**
   - backup ID：`20260730T045705Z-small`
   - `SHA256SUMS` SHA-256：`7caadb13ad2e19754bb011d7f82c1581eb4ff4333a0df6175e2d0564a755c6ad`
   - `BACKUP_COMPLETE` SHA-256：`a805e117f869429686d80dcb7f1b6ada1038ea41388c6f035e05bf1df4bd6af5`
   - `BACKUP_MANIFEST` SHA-256：`ae7d5ce087df84fd8a93e7c0b44c2e57a5bced51d83e2ae958470e5399aa1dcb`
   - LOCAL/A/B：三地均 14/14 payload PASS，`git-head.txt` 均为上述运行发布 commit

ImageClip 发布门禁已经关闭：manifest `2026.07.30-691770c-r1` 已启用，旧版已禁用，三节点
兼容矩阵为 `3/3 compatible`。真实 canary `87917fd6-1e38-4c5b-83a1-d0014a28ee91` 在
`worker-3090-a` 执行并以 `SUCCEEDED / HTTP 200` 结束；最终产物为 `1080x1440 RGBA PNG`，
SHA-256 为 `8f648f9b18f5c72bfec7cdf9f6531613cda53fff788b53918c6a929cf0415c4a`。

## 2. 三节点固定身份与运行版本

IP 只用于路由；身份必须以 `node_id + MAC + GPU UUID` 为准。

| 节点 | 当前地址 | 固定身份 | GPU 角色/模式 | GPU 镜像 | Asset Worker |
| --- | --- | --- | --- | --- | --- |
| `control-4090` | `10.3.34.11` | MAC `58:11:22:c1:66:63` | `OVERFLOW / ACTIVE` | ComfyUI `projects-0.2.3` | `li3d/blender-worker:1.2.2`，2 槽 |
| `worker-3090-a` | `10.3.34.12` | MAC `18:c0:4d:9f:13:13` | `PRIMARY / ACTIVE` | ComfyUI `projects-0.2.3` | `li3d/blender-worker:1.2.2`，3 槽 |
| `worker-3090-b` | Windows `10.3.34.14`；WSL SSH `gpucontrol@10.3.34.14:2222` | MAC `2c:f0:5d:76:7b:70` | `PRIMARY / ACTIVE` | ComfyUI `projects-0.2.3` | Linux/WSL Worker 4 槽；Windows Baker 4 槽 |

统一版本：

| 层 | 版本/镜像 ID |
| --- | --- |
| GPU Control | `1.5.4` |
| API | `gpu-control-api:1.5.4` / `sha256:06147d527d4a146141c9cf3c56b62c474096543cbdbde2050b2d1a652e478cb3` |
| Asset API | `unified-scheduler-asset-api:1.5.4` / `sha256:827053b49248ea22296fb3b78fb3012f1a158577f34921b30dcf140567ce0c3d` |
| Scheduler | `gpu-control-scheduler:1.5.4` / `sha256:f9569a39438bbbc63a9b3f8c6ff3991e1bce67efddc69167467549c16f4a227b` |
| Web | `gpu-control-web:1.5.4` / `sha256:8f9558646a306600a24c2898355901a85b0e3b4fd94c3e807b7d2fa27cf408ae` |
| Asset Worker | `li3d/blender-worker:1.2.2` / `sha256:9bf4344503041abec7dd67067ccbbb0946223af53b06d1a4a67a27acfeaab6ad` |
| ComfyUI | `registry.local:5000/gpu-control/comfyui:projects-0.2.3` / `sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea` |
| Database schema | Alembic `20260729_0010` |

## 3. 服务与节点门禁

### 3.1 控制面

收尾只读验收结果：

- API、Asset API、Scheduler、Web：healthy；
- PostgreSQL、Redis、Nginx：healthy；
- Prometheus、Alertmanager、Grafana、Loki、Alloy：healthy；
- live/ready：`10/10`；
- GPU 活动任务、Asset 活动任务、活动租约：均为 `0`；
- 无遗留 reservation 或外部 ComfyUI queue。

### 3.2 GPU 节点

- 三台 `/system_stats` 均连续 `10/10` 返回成功；
- ComfyUI `0.28.0`、Python `3.11.13`、PyTorch `2.7.1+cu128`；
- 三台 GPU Agent 和 Docker service active；
- 3090-A SSH 连续 `10/10`；3090-B 正式账户 `gpucontrol@10.3.34.14:2222` 收尾回归连续 `5/5`；
- 三台节点均 `ONLINE / ACTIVE / current_jobs=0`，4090 保持 OVERFLOW 而不是长期排空。

### 3.3 CPU/Windows 资产节点

| Worker | 状态 | 槽位 | 关键探针 |
| --- | --- | ---: | --- |
| 4090 Asset Worker | ONLINE | 2 | Blender、Codex、RetopoFlow 正常 |
| 3090-A Asset Worker | ONLINE | 3 | Blender、Codex、RetopoFlow 正常 |
| 3090-B WSL Asset Worker | ONLINE | 4 | Blender、Codex、RetopoFlow 正常 |
| 3090-B Windows Baker #01–#04 | ONLINE | 4 | Windows 原生 Substance Baker 心跳正常 |

GPU 推理队列与 CPU/Windows Asset 队列彼此隔离；CPU 资产任务不能占用 GPU `current_jobs` 槽，
Windows Baker 也不能伪装为第四台 GPU 节点。

## 4. 外部业务管线权威版本与三节点摘要

GPU Control 只部署并校验以下批准内容，不拥有这些仓库的业务语义。

### 4.1 ImageClip

| 项目 | 4090 | 3090-A | 3090-B | 权威远端 |
| --- | --- | --- | --- | --- |
| Git commit | `691770cd6a59fd7c51391456fe900dc57a313233` | 同左 | 同左 | `git ls-remote origin main` 同左 |
| Pipeline SHA-256 | `00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b` | 同左 | 同左 | 批准摘要 |
| 模型清单 SHA-256 | `4932d81a5a73ba8ea9c4afe5cf04a5dc48c8a506845a79d2a73460d360a540ee` | 同左 | 同左 | 批准摘要 |

收尾审计曾发现数据库中已启用的旧 GPU Control manifest 把 ImageClip commit 标为
`721f7d68635ee36d45f545ce2c82037046147442`，而实际三节点和权威远端已经是 `691770c…`。
这会触发正确的 fail-closed 兼容性保护。修复方式只允许更新 GPU Control 的不可变 manifest
兼容性元数据；**不得回滚、重写或改动 ImageClip 仓库/工作流**。现已启用
`2026.07.30-691770c-r1` 并禁用旧版，三节点均为 compatible；真实 canary 与最终 PNG 摘要见
第 1 节。

### 4.2 ModelViewCreator

| 项目 | 4090 | 3090-A | 3090-B | 权威远端 |
| --- | --- | --- | --- | --- |
| Git commit | `d318bb392040e2d5f6bbd10ae61d832d36d3cb4a` | 同左 | 同左 | `origin/main` 同左 |
| 模型清单 SHA-256 | `388668d29b538b1a21a0ad852e5df81042f78a0821bf00da963c41fdbf26a731` | 同左 | 同左 | 批准摘要 |

三端工作树均 clean。每次节点从 `DRAINING` 恢复前都必须重新核对 Git commit、pipeline/model
manifest、模型软链接和最终输出节点；任何漂移都必须 fail closed。

## 5. 功能门禁与近期真实生产证据

| 能力 | 执行面 | 收尾前最近成功证据 | 本轮状态 |
| --- | --- | --- | --- |
| ImageClip RGBA 抠图/序列帧 | 三台 GPU | `87917fd6-1e38-4c5b-83a1-d0014a28ee91`，3090-A，`SUCCEEDED / HTTP 200` | manifest 已启用，`3/3 compatible`，最终 PNG 摘要已验证 |
| ModelView 局部重绘 | 三台 GPU | 2026-07-30 13:49 | 通过 |
| ModelView Roughness | 三台 GPU | 2026-07-29 19:54 | 通过 |
| Blender PBR UV | 三台 Asset Worker | 2026-07-29 18:36 | 通过 |
| AI 重拓扑 | 三台 Asset Worker | 2026-07-29 18:48 | 通过 |
| Windows Substance PBR Baker | 3090-B Windows 四槽 | 2026-07-29 19:53 | 通过 |

这里的“通过”只表示已有真实最终产物成功证据；不允许用 UI 模拟记录、预览或中间文件替代最终
交付。ImageClip 已通过新 manifest 下的真实最终产物 canary；PNG 为 `1080x1440 RGBA`，SHA-256
为 `8f648f9b18f5c72bfec7cdf9f6531613cda53fff788b53918c6a929cf0415c4a`。

## 6. 恢复点闭环

### 6.1 全量恢复点

```text
/srv/gpu-control/backups/full-20260730T023838Z-pre-rollout
```

| 位置 | 文件数 | 总字节数 | 被清单覆盖的载荷 |
| --- | ---: | ---: | --- |
| LOCAL | 34 | 86,883,229,399 | 32/32 PASS |
| 3090-A | 34 | 86,883,229,399 | 32/32 PASS |
| 3090-B | 34 | 86,883,229,399 | 32/32 PASS |

- `SHA256SUMS` SHA-256：
  `4a7aefecb5b45d18ce6adeac970bd88bfed062c643db4c6e9953df6a6cfdd849`；
- `BACKUP_COMPLETE` SHA-256：
  `7bdc86b0318f6b55425bf47ec246e17d1c6e8cef6d0f0104e2072a7c30b35533`；
- marker：`CREATED_UTC=2026-07-30T03:35:46Z`、`MODE=full-custom-pre-rollout`；
- 两路局域网复制合计约 `949 Mbps`；
- 包含数据库、Redis/监控卷、Git bundles、外部管线快照、节点快照、生产镜像、Windows Baker
  配置与受控 secrets；secrets 未写入普通文档。

旧校验过程曾把临时校验文件纳入自身形成自引用；该问题已修复，最终 32 个业务载荷不包含
`SHA256SUMS`、临时文件或完成 marker 本身。

### 6.2 strict format-2 小恢复点

```text
/srv/gpu-control/backups/20260730T040031Z-small
```

| 控制文件 | SHA-256 |
| --- | --- |
| `SHA256SUMS` | `0fa031babdc2b94edcfc6c1ac49e945601ec86350a2958f3769ce685c3120052` |
| `BACKUP_COMPLETE` | `979169912c5bf5520fdf3e77760a0f1b3e8a79d3c1477249080a5f622124c638` |
| `BACKUP_MANIFEST` | `78c46eb7d8e55e933cd47bc278951b9ae225f6926e9ada2f53bda72aa5e6ccbd` |

- 本机 `verify-only`、全量 SHA、Git bundle 和 PostgreSQL dump catalog：PASS；
- 3090-A、3090-B：分别 `14/14` PASS；
- 三端控制文件权限 `0600 root:root`；
- 该恢复点用于快速恢复格式验证，不能替代全量恢复点。

### 6.3 隔离数据库恢复

不是只执行 `pg_restore -l`，而是把 custom dump 恢复到临时隔离 PostgreSQL 实例并查询：

```text
tables=29
alembic_revision=20260729_0010
nodes=3
jobs=2383
ISOLATED_DATABASE_RESTORE=PASS
```

隔离演练没有写入生产数据库，没有停止生产 API/Scheduler。

### 6.4 发布后增量恢复点

```text
/srv/gpu-control/backups/20260730T045705Z-small
```

该恢复点在运行发布提交推送后创建，`git-head.txt` 绑定
`50f1d7b95e038fc5f313843dd9725c12a6b5e099`。本机 `verify-only` 通过；随后以不删除远端旧备份的
在线增量复制方式同步到 A/B，三地均为 16 个顶层文件、14/14 payload PASS。两路实际传输各
6,770,191 字节；3090-A 耗时约 0.29 秒，3090-B 耗时约 0.81 秒。旧全量和 strict format-2
恢复点均未删除。本文件的后续收口提交只更新审计文档，不改变运行代码、部署配置、镜像或业务
管线，因此恢复点继续绑定运行发布提交，避免产生“为记录备份而再次备份”的自引用链。

## 7. 镜像归档、摘要与离线载入

| 归档 | 大小 | SHA-256 | 预期镜像 ID |
| --- | ---: | --- | --- |
| `/srv/gpu-control/images/unified-scheduler-1.5.4-images.tar.gz` | 190,465,348 B | `b3afe81e660f899f737819deabd46bd5c9dba847097df806a87b66ca79a94d51` | 第 2 节四个控制面 ID |
| `/srv/gpu-control/images/li3d-blender-worker-1.2.2.tar.zst` | 685,495,065 B | `7bb6c067c4a358a864e436fd2fc09271716ed7848b805b753fbbdb97ec09c72f` | `sha256:9bf434…` |
| `/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz` | 8,271,225,047 B | `20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586` | `sha256:d76e54…` |

三份归档均已完成：

1. SHA-256 对照；
2. gzip/zstd 压缩完整性检查；
3. 隔离 `docker load`；
4. 载入后 `docker image inspect` 与预期 ID 比较。

控制面 `1.5.4` 已有 Git LFS 分卷；Worker `1.2.2` 的
`artifacts/asset-worker/1.2.2/li3d-blender-worker-1.2.2.tar.zst.part-00` 已确认在 Git 中为
134 字节标准 LFS pointer，对象大小 685,495,065 字节，内容摘要与规范归档相同。LFS 上传
100% 完成，平均约 5.5 MB/s；`fsck` 通过且 dry-run 无待推对象。

## 8. 测试与审计结果

| 门禁 | 结果 |
| --- | --- |
| Python tests | `101/101` PASS |
| 备份/恢复安全测试 | `23/23` PASS |
| Web Vitest | `3/3` PASS |
| Web 生产构建 | Vite `2232` modules，PASS |
| PostgreSQL 隔离恢复 | PASS |
| Markdown 相对链接 | PASS |
| `git diff --check` | PASS |
| secret scan | 0 个生产密钥/API Key/密码泄漏 |

测试与发布证据必须同时成立；本轮 Git/LFS 推送和发布后恢复点已按第 1 节关闭。

## 9. Git/LFS 发布闭环

审计开始时：

```text
HEAD        1a912bbce56b744ca668ddd4ee8e149d46d939d2
origin/main 1a912bbce56b744ca668ddd4ee8e149d46d939d2
```

上述经授权的控制面、Web、版本锁、备份/恢复脚本、测试、文档、ImageClip 兼容性元数据以及
Worker `1.2.2` LFS 归档分片，已形成新的远端运行发布基线：

```text
50f1d7b95e038fc5f313843dd9725c12a6b5e099
```

实际采用的发布顺序：

```bash
cd /opt/gpu-control
git diff --check
git status --short
git add --all
git lfs ls-files
git diff --cached --check
git commit -m "release: close three-node 1.5.4 recovery baseline"
git push origin main
git lfs fsck
git lfs push --dry-run origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

实际执行结果：685 MB Worker part 在 index 中是 LFS pointer，而不是普通 Git blob；LFS 对象
上传完成，本地、`origin/main`、GitHub `main` 三者一致，随后已生成并三地校验发布后恢复点。

## 10. 滚动更新与恢复步骤

### 10.1 节点滚动更新

一次只处理一台，推荐顺序：

```text
3090-B -> 3090-A -> 4090 GPU Worker -> Web/Asset API -> Scheduler/API
```

每台固定步骤：

1. 将单个目标节点设为 `DRAINING`；另外两台继续接单；
2. 确认 `current_jobs=0`、ComfyUI `/queue` 为空且没有不可重放 Asset Job；
3. 校验镜像归档 SHA-256，离线载入并核对镜像 ID；
4. 同步 GPU Control 部署文件；外部仓库只同步批准 commit，不改内容；
5. 仅重建目标节点相关服务；
6. 验证 Agent、Docker、ComfyUI、GPU、Asset/Codex/RetopoFlow 探针；
7. 核对 ImageClip/ModelViewCreator commit、pipeline/model manifest SHA；
8. 执行真实最终产物 canary；
9. A/B 恢复 `PRIMARY / ACTIVE`，4090 恢复 `OVERFLOW / ACTIVE`；
10. 观察一个完整任务周期再处理下一台。

任何时刻都不得把三台节点同时长期留在 `DRAINING`，也不得通过把用户任务改成
`FAILED/CANCELLED` 来制造空闲窗口。

### 10.2 从恢复点重建

```text
验证 BACKUP_COMPLETE 与 SHA256SUMS
  -> 验证 Git bundles 与最终 GPU Control commit
  -> 在隔离实例恢复 PostgreSQL 并核对 revision/计数
  -> 恢复 Redis/控制数据和受控 secrets 权限
  -> 载入控制面 1.5.4 精确镜像
  -> 启动并验证 API/Scheduler/Web/监控
  -> 逐台恢复 Agent、ComfyUI 0.2.3 和 Asset Worker 1.2.2
  -> 恢复批准的外部 commit、模型和软链接
  -> 恢复 3090-B Windows Baker 四槽位
  -> DRAINING 状态执行真实 canary
  -> 逐台恢复 ACTIVE/OVERFLOW
  -> 验证 API、队列、任务追踪、最终制品和 SHA
```

### 10.3 快速回滚

| 范围 | 动作 | 不得破坏 |
| --- | --- | --- |
| 单台 ComfyUI | 保持该节点 DRAINING，载入上一份已验证 ComfyUI 归档，仅重建该节点 | 输入、输出、模型卷、任务记录 |
| Asset Worker | 停止该 Worker 新接单，恢复精确 Worker 镜像和 skills mount | 用户源模型、最终制品、GPU 队列 |
| 3090-B Baker | 暂停 Baker 新接单，恢复 Windows Worker 包和四槽服务 | Windows 任务目录、日志、WSL GPU Worker |
| Web | 恢复旧 Web 镜像 | API/数据库 |
| API/Scheduler | 停止继续滚动并恢复旧镜像；必要时只禁新请求 | 运行中任务、PostgreSQL、Redis、artifact 状态 |
| Database | 先保存故障现场，再从 VERIFIED dump 恢复到隔离实例确认 | 原故障现场、审计链 |

## 11. 已知非阻断项

1. 3090-A 缺少可选 `NunchakuDepthPreprocessor`；批准的当前生产工作流没有引用它，不能据此修改
   外部业务仓库或中断生产。若未来批准工作流引用，必须在上线前补齐并重新计算兼容性。
2. 数据库仍有一个旧 Windows Baker Worker 历史行；当前四个真实 Baker 槽位正常。应在独立清理窗口
   归档/排除旧行，不得在生产任务运行时直接删除。
3. 3090-A/B 本地 `origin/main` remote-tracking ref 可能显示旧提交，但实际 `HEAD` 与权威
   `git ls-remote origin main` 一致；下一次只读 `git fetch --prune` 后可刷新展示，不能因此 reset
   外部仓库。
4. `model_manifest_version` 展示字段仍应写入明确版本/摘要；运行兼容性已经使用实际三端摘要核对，
   该展示完善不阻断当前任务。

## 12. 最终签收栏

以下发布与恢复签收项均已完成：

| 项目 | 结果 |
| --- | --- |
| 运行发布 Git commit 与远端 main 一致 | `PASS`：`50f1d7b95e038fc5f313843dd9725c12a6b5e099` |
| LFS pointer / fsck / 无待推对象 | `PASS`：134 B pointer；685,495,065 B 对象；OID `7bb6c067...c09c72f` |
| ImageClip 新 manifest 3/3 compatible | `PASS`：`2026.07.30-691770c-r1` 已启用，旧版已禁用 |
| ImageClip 最终 canary 与产物 SHA | `PASS`：`87917fd6-1e38-4c5b-83a1-d0014a28ee91` / `8f648f9b…0415c4a` |
| 发布后增量备份 LOCAL/A/B | `PASS`：`20260730T045705Z-small`，三地 14/14 payload PASS |
| 三节点最终 ONLINE/ACTIVE、无长期排空 | `PASS`：4090 `OVERFLOW/ACTIVE`，A/B `PRIMARY/ACTIVE`；发布后继续按滚动门禁复核 |

`full-20260730T023838Z-pre-rollout`、`20260730T040031Z-small` 和
`20260730T045705Z-small` 当前均保留。后续删除必须由管理员明确执行保留策略，不能把本轮验收
当作自动清理授权。
