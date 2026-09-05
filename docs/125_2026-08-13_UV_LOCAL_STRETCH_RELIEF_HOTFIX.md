# 2026-08-13 UV 局部拉伸释放切线热修与生产验收

## 结论

`11.fbx` 的裂点碎片化修复后仍有 4 个局部狭长三角面出现可见拉伸。上一版整体指标正常，但局部最大拉伸为 `1.56223`，因此仅依据 P90/P95 判定通过不够充分。

本次在不增加 UV 岛的前提下，为两组相邻异常面各选择一条最短软边作为释放切线，并重新执行 conformal unwrap。正式生产结果仍为 38 个 UV 岛，局部最大拉伸降为 `1.00120`。

## 算法变更

`blender-pbr-uv/scripts/unwrap_fbx.py` 增加稀疏局部异常修复：

- 保留 `repair_stretch=6.0` 作为粗修阈值，避免对正常曲面大范围加缝；
- 增加 `local_stretch=1.5` 作为粗修稳定后的局部复查阈值；
- 仅当异常面不超过 `max(4, ceil(face_count * 2%))` 且异常面面积不超过总面积 10% 时自动处理；
- 每个异常面组优先选择组内最短软边，只添加一条释放切线；
- 释放后重新 conformal unwrap，并再次计算局部拉伸；
- 若属于分布式拉伸则停止自动加缝并记录 `distributed_distortion_requires_review`，防止重新产生大量碎岛；
- 报告记录异常面索引、释放边索引、释放切线数量和残留异常面数量。

固定脚本 SHA-256：

```text
03cdb0d9ca1f971313927603cb800acccc81f4dea79db04e79496aa9956e0ca4
```

## 回归对比

| 指标 | 1.4.49 | 1.4.50 |
| --- | ---: | ---: |
| UV 岛 | 38 | 38 |
| 局部异常面 | 4 | 0 |
| 新增局部释放切线 | 0 | 2 |
| Stretch P90 | 1.00114 | 1.00010 |
| Stretch P95 | 1.00499 | 1.00017 |
| Stretch P99 | 1.08724 | 1.00074 |
| Stretch Max | 1.56223 | 1.00120 |
| 密度相对值 P10 | 0.99631 | 0.99998 |
| 密度相对值 P90 | 1.00119 | 1.00002 |

两条释放切线均位于原 138 面大岛内部的短软边。重新展开后 UV 连通性保持不变，因此独立 UV 岛数量没有增加。

## 发布制品

```text
image: li3d/blender-worker:1.4.50-uv-local-stretch-v1
image_id: sha256:0de13a169211c827bdfde7cb8024abe73becf234d5c2554b8b5ef5ec971de1b5
oci_version: 1.4.50-uv-local-stretch-v1
oci_revision: working-tree-uv-local-stretch-v1
archive: /srv/gpu-control/images/blender-worker-1.4.50-uv-local-stretch-v1.tar.gz
archive_sha256: feba93f05816c050ef375d38cfe4ae54777060e571a0654a604910517879c848
archive_size_bytes: 689763845
```

没有可用共享 Registry，因此三个远端节点使用同一离线归档。传输后逐台校验归档 SHA-256，加载后逐台校验镜像 ID。

## 四节点滚动发布

四台节点均通过受审计 Admin API 执行 `ACTIVE -> DRAINING -> ACTIVE`。排空后确认该 Worker 的 `CLAIMED / RUNNING / CANCELLING` 资产任务为零，随后仅重建 Blender Worker。

| 节点 | Asset Worker | 镜像 ID | 脚本 SHA | RestartCount |
| --- | --- | --- | --- | ---: |
| `control-4090` | `asset-control-4090` | `sha256:0de13a169211...` | `03cdb0d9ca1f...` | 0 |
| `worker-3090-a` | `asset-worker-3090-a` | `sha256:0de13a169211...` | `03cdb0d9ca1f...` | 0 |
| `worker-3090-b` | `asset-worker-3090-b` | `sha256:0de13a169211...` | `03cdb0d9ca1f...` | 0 |
| `worker-4070ti-animation-host-01` | `asset-worker-4070ti-animation-host-01` | `sha256:0de13a169211...` | `03cdb0d9ca1f...` | 0 |

最终管理 API 复核：四节点均为 `ACTIVE / ONLINE`，`current_jobs=0`；Asset Worker 均为 `ONLINE`、心跳新鲜、`scheduler_eligible=true`。

本次未重启 ComfyUI。四台 ComfyUI 容器 ID、启动时间及 `RestartCount=0` 与发布前一致。

运行时备份：

```text
/srv/gpu-control/runtime-backups/uv-local-stretch-v1-pre/control-4090
/srv/gpu-control/runtime-backups/uv-local-stretch-v1-pre/worker-3090-a
/srv/gpu-control/runtime-backups/uv-local-stretch-v1-pre/worker-3090-b
/opt/gpu-control/runtime/backups/uv-local-stretch-v1-pre/worker-4070ti-animation-host-01
```

## 正式生产 API 验收

同一份源文件通过生产 HTTPS Nginx 和正式 `/api/v1/assets/uv/process` 接口重新提交：

```text
job_id: 696766de-6893-414a-80b9-6219eb2af1fe
source: 11.fbx
source_sha256: 43645ee9e0248a1ed4a5cb3b1c7c9be3c808adb84a6f722120c1e075fcb78211
worker_id: asset-control-4090
attempt_count: 1
status: SUCCEEDED
delivery_ready: true
```

生产报告证据：

```text
polygons: 392
vertices: 733 -> 249
loose_regions: 172 -> 8
uv_islands: 38
local_stretch_threshold: 1.5
initial_local_bad_faces: 4
relief_seams: 2
remaining_bad_faces: 0
hard_edges_not_uv_boundary: 0 (Blend authoritative QA)
flipped_faces: 0
degenerate_uv_faces: 0
overlap_triangle_pairs: 0
out_of_0_1_loops: 0
stretch_p90: 1.00010
stretch_p95: 1.00017
stretch_p99: 1.00074
stretch_max: 1.00120
```

Blend QA 与 FBX 回读 QA 均为 `passed=true`、`hard_failures=[]`、`warnings=[]`。五个正式产物下载后逐个核对 SHA-256，均与 API 元数据一致。

## 验证记录

- Blender 5.1.2 精确输入回归：38 个 UV 岛，最大拉伸 `1.00120`。
- 候选镜像 fail-closed bootstrap 与脚本双固定校验：通过。
- Worker 定向单元测试：`41 passed`。
- Asset API 定向集成测试：`3 passed, 98 deselected`。
- Ruff：修改涉及的 Python 文件通过。
- Compose control-plane / gpu-node 配置展开：通过。
- `git diff --check`：通过。
- `verify_asset_skills.sh` 的 UV 全部通过；脚本随后因仓库外既有自动拓扑 canonical `SKILL.md` SHA 差异退出，本次未修改该无关技能。

## 用户侧说明

旧的已完成任务不可变，不会自动替换其预览或下载文件。用户必须重新发起一个任务；新任务输出 FBX 大小约 35 KB，而截图中的旧结果显示约 43 KB。

本次生产候选 OCI revision 为 `working-tree-uv-local-stretch-v1`。后续正式仓库发布应在提交和推送后以不可变 Git revision 重建镜像。

## 回滚

逐节点排空后，将 `ASSET_WORKER_VERSION` 与 `ASSET_WORKER_IMAGE_TAG` 恢复为 `1.4.49-uv-split-weld-v1`，恢复对应运行时备份，再只重建 Blender Worker；健康验证完成前保持节点处于 `DRAINING`。
