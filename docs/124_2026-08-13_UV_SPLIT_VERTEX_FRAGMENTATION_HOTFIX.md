# 2026-08-13 UV 裂点碎片化热修与四节点发布记录

## 结论

自动展 UV 的“切得很碎”已定位为输入 FBX 的裂点导出问题，而不是本次任务新增了大量硬边切线。
同一份 `11.fbx` 在修复前有 392 个面、733 个顶点和 172 个断开的面区域，其中 484 个顶点与其他顶点处于完全相同的位置。安全焊接后保留 392 个面，变为 249 个顶点和 8 个真实连通部件；正式生产任务最终生成 38 个 UV 岛。

## 修复范围

- `blender-pbr-uv/scripts/unwrap_fbx.py` 增加保守的 split-vertex export repair：
  - 仅处理单材质、无 Shape Key、碎裂程度达到阈值的网格；
  - 焊接距离为包围盒对角线的 `1e-7`；
  - 面数必须保持不变；
  - 不允许产生无效面或超过两个面的非流形边；
  - 连通区域必须显著下降，否则自动放弃修复；
  - 焊接后重新按 75° 硬边规则建立真实切线。
- 展 UV 报告新增 `split_vertex_repair` 证据，记录焊接前后顶点、连通区域和焊接距离。
- Worker 对展开脚本继续执行 fail-closed SHA-256 固定校验。
- API 仓库已增加已知自动拓扑 `high_fbx` 的 UV 上传拒绝测试与保护代码，避免把烘焙高模误当 GAME_LOW；该 API 改动未包含在本次仅重启 Blender Worker 的线上热修范围内。

固定脚本 SHA-256：

```text
1a2129cef337321de1b6536fe8e7e6697313f6509c0e66b44bbb8581776f9691
```

## 发布制品

```text
image: li3d/blender-worker:1.4.49-uv-split-weld-v1
image_id: sha256:9ac9fd96bf4add5e0532061ba866cbc16fdb64b46709a170560764060894bb95
oci_version: 1.4.49-uv-split-weld-v1
oci_revision: working-tree-uv-split-weld-v1
archive: /srv/gpu-control/images/blender-worker-1.4.49-uv-split-weld-v1.tar.gz
archive_sha256: fa8f9325737224673983781b8712b8ab978b03a12d23fce29b800764c6218772
archive_size_bytes: 689764098
```

控制节点没有可用共享 Registry，因此镜像以离线归档方式传输到三个 GPU 节点；每个节点加载前均校验归档 SHA-256，加载后均校验镜像 ID。

## 四节点滚动发布

发布顺序逐节点执行 `ACTIVE -> DRAINING -> ACTIVE`，每次只重建对应 Blender Worker，未重启 ComfyUI。

| 节点 | Asset Worker | 发布后状态 | 镜像 ID | RestartCount |
| --- | --- | --- | --- | ---: |
| `control-4090` | `asset-control-4090` | ONLINE | `sha256:9ac9fd96bf4...` | 0 |
| `worker-3090-a` | `asset-worker-3090-a` | ONLINE | `sha256:9ac9fd96bf4...` | 0 |
| `worker-3090-b` | `asset-worker-3090-b` | ONLINE | `sha256:9ac9fd96bf4...` | 0 |
| `worker-4070ti-animation-host-01` | `asset-worker-4070ti-animation-host-01` | ONLINE | `sha256:9ac9fd96bf4...` | 0 |

发布后的管理 API 复核显示四节点均为 `ACTIVE / ONLINE`、`current_jobs=0`，Linux Asset Worker 心跳均新鲜且 `scheduler_eligible=true`。
四台主机的 `ASSET_WORKER_VERSION` 与 `ASSET_WORKER_IMAGE_TAG` 也已统一为 `1.4.49-uv-split-weld-v1`；最后一次仅同步环境元数据，未触发额外容器重启。

运行时备份：

```text
/srv/gpu-control/runtime-backups/uv-split-weld-v1-pre/control-4090
/srv/gpu-control/runtime-backups/uv-split-weld-v1-pre/worker-3090-a
/srv/gpu-control/runtime-backups/uv-split-weld-v1-pre/worker-3090-b
/opt/gpu-control/runtime/backups/uv-split-weld-v1-pre/worker-4070ti-animation-host-01
```

## 正式生产验收

通过生产 HTTPS Nginx 和 `/api/v1/assets/uv/process` 上传原问题文件，未绕过 API、调度器、Worker、产物发布或下载校验。

```text
job_id: e186d5d3-cb82-4dac-8acb-35b777d9caa4
source: 11.fbx
source_sha256: 43645ee9e0248a1ed4a5cb3b1c7c9be3c808adb84a6f722120c1e075fcb78211
worker_id: asset-worker-3090-b
attempt_count: 1
status: SUCCEEDED
delivery_ready: true
```

核心 UV 证据：

```text
polygons: 392 -> 392
vertices: 733 -> 249
loose_regions: 172 -> 8
welded_vertices: 484
uv_islands: 38
hard_edges: 126
hard_edges_not_uv_boundary: 0
flipped_faces: 0
degenerate_uv_faces: 0
overlap_triangle_pairs: 0
out_of_0_1_loops: 0
stretch_p90: 1.00114
stretch_p95: 1.00499
texel_density_relative_p10: 0.99631
texel_density_relative_p90: 1.00119
```

正式 FBX 与 Blend QA 均为 `passed=true`，`hard_failures=[]`，`warnings=[]`。五个正式产物下载后的 SHA-256 与 API 元数据逐个一致。

## 验证记录

- Ruff：修改涉及的 Python 文件全部通过。
- API 定向集成测试：`3 passed, 98 deselected`。
- Worker UV/单元测试：`5 passed`。
- Worker bootstrap/image 测试：`14 passed`。
- Blender 5.1.2 精确输入本地验收：38 个 UV 岛，严格 QA 通过。
- 候选镜像隔离 bootstrap：通过。
- `git diff --check`：通过。
- `verify_asset_skills.sh` 的 UV 文件全部通过；脚本随后因仓库之外既有自动拓扑 canonical/runtime SHA 差异失败，本次未改动该无关 canonical 自动拓扑技能。

## 用户侧行为

已完成任务的预览和产物是不可变记录，不会被新算法回写。网页上必须重新上传原文件或重新发起一次新的自动展 UV 任务，才会由 `1.4.49-uv-split-weld-v1` 处理。

UV 预览仍会绘制岛内部的多边形边；这些内部线不是切缝。应以独立 UV 岛数量和报告中的 `uv_islands` 判断实际切分数量。

## 回滚

若出现回归，逐节点排空后将 `.env` 的 `ASSET_WORKER_IMAGE_TAG` 恢复为 `1.4.48`，恢复对应运行时备份，再只重建 Blender Worker。回滚过程仍须保持每个节点在恢复和健康检查完成前处于 `DRAINING`。

本次生产候选的 OCI revision 明确记录为 `working-tree-uv-split-weld-v1`；后续正式仓库发布应在提交、推送并以不可变 Git revision 重建后替换该紧急热修镜像。
