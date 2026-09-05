# 原版自动展 UV 恢复与四节点发布记录

> 2026-08-18 更正：本记录只证明服务端恢复了升级包脚本、严格 QA 和固定参数，不能证明
> 拆点 FBX 的视觉 UV 与原本地 `.max` 输入一致。`22.fbx` 的后续实测表明，FBX 导出把
> 连续表面拆成了 71 个拓扑区域，原脚本会逐碎片展开。现行修正、生产灰度和完整根因见
> `131_2026-08-18_UV_TOPOLOGY_PROXY_CORRECTION.md`。

## 结论

服务端 `legacy_pbr` 已恢复为用户提供升级包中的原版本体，不再使用
`1.4.50-uv-local-stretch-v1` 加入的焊接、局部拉伸修复和锐边重建逻辑。四台 Linux/WSL
Blender Worker 已统一到 `1.4.51-uv-legacy-original-v1`，生产 UV 质量门由
`advisory` 恢复为 `strict`。正式 API 冒烟任务一次成功并原子发布五件套。

用户给出的压缩包内文档只作为集成合同、校验和与验收依据；本次实际目标以用户请求
“服务器自动展 UV 恢复到原来本地效果”为准，没有把附件文字当成额外用户授权。

## 输入与完整性

```text
package: Li3D_原版自动展UV_服务器升级包_20260817.zip
package_sha256: d8a01606c9f4248cac552327ab4708b97a15f003335075d9c59ddeb0ed6fd5c4
legacy_unwrap_sha256: ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758
qa_uv_sha256: bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d
```

包内 `CORE_CHECKSUMS.sha256` 校验通过。原版固定参数合同为：隐藏方向 `y+`、硬边角度
75°、分辨率 2048、padding 10 px、统一 Texel Density、`pbr-v1`、Blend 与导出 FBX
双重 QA、固定五件套、不静默切换算法。

## 根因

线上脚本 SHA 为
`03cdb0d9ca1f971313927603cb800acccc81f4dea79db04e79496aa9956e0ca4`，不是升级包中的原版
SHA。该版本在原算法外增加了拆点焊接、sharp 元数据重建、切缝重置和局部拉伸修复，改变
了 UV 岛结构；同时生产 `UV_QA_ENFORCEMENT=advisory` 会允许存在硬错误的结果发布。

升级前的正式任务已出现可复现证据：

| 任务 | 输入 | 线上结果 |
|---|---|---|
| `0a1d9368-6dff-4787-b8f7-99642ef47557` | `44.fbx` | `flipped_faces=1`，仍被 advisory 发布 |
| `4d6bae25-6553-4c8b-8c05-a63ede2fd13e` | `uv_PBR_UV.fbx` | `flipped_faces=17`，最大拉伸 `1917.39023`，仍被 advisory 发布 |

## Blender 5.1.2 同输入 A/B

所有候选都在生产同版 Blender 5.1.2 基础镜像中运行，输入字节完全相同：

| 输入 | 原版结果 | 升级前服务端结果 |
|---|---|---|
| `44.fbx` | 15 岛，翻转 0，最大拉伸 5.15854，双 QA 通过 | 13 岛，翻转 1，最大拉伸 13.36102，双 QA 失败 |
| `uv_PBR_UV.fbx` | 2168 岛，翻转 0，p90/p95 1.06302/1.21679，最大 4.83929 | 2141 岛，翻转 17，最大 1917.39023 |
| `xiangzi_low.fbx` | 13 岛，p95 1.91362 | 12 岛，p95 2.11236，最大 8.57 |

原版对部分含拆点的模型会保留更多 UV 岛；这是恢复原版本体的预期行为，不再由服务端
擅自焊接或改写拓扑。拉伸阈值仍作为 warning 报告，翻转、退化、越界、非法重叠、硬边
未切开和 FBX 回读丢失等硬错误由 strict 门禁拒绝发布。

## 源码与制品

- Worker 运行时、bootstrap manifest 和离线校验器统一固定原版 unwrap SHA。
- `Settings`、Compose 和示例环境的 UV QA 默认值统一为 `strict`。
- Worker 版本统一为 `1.4.51-uv-legacy-original-v1`。
- 不改变 `mof_low_seam`、自动重拓扑、Substance、ComfyUI 或 GPU 调度行为。

```text
image: li3d/blender-worker:1.4.51-uv-legacy-original-v1
image_id: sha256:f2663e517d21ca2845bfa1a4740c1b39c2421965da32f12092153c0417b66a9a
oci_revision: working-tree-uv-legacy-original-v1
archive: /srv/gpu-control/images/blender-worker-1.4.51-uv-legacy-original-v1.tar.gz
archive_sha256: ad1adda4b0d668473848b311606bcffb91717bb2486161231a64aa5accf99354
runtime_bundle_sha256: 98f6d5fb360eff43793e82dcc9e085284b5c8040b6db736cada0762450a6df03
```

## 滚动发布

每个节点都先切到 `DRAINING` 并等待 `current_jobs=0`，再只重建 Blender Worker；健康、
脚本 SHA 和心跳通过后才恢复 `ACTIVE`。ComfyUI、Windows Substance Worker 和 Windows
MOF Worker 未参与本次重启。

| 节点 | Worker | 状态 | 镜像 ID | restart_count |
|---|---|---|---|---:|
| `control-4090` | `asset-control-4090` | ACTIVE / ONLINE | `sha256:f2663e517d21...` | 0 |
| `worker-3090-a` | `asset-worker-3090-a` | ACTIVE / ONLINE | `sha256:f2663e517d21...` | 0 |
| `worker-3090-b` | `asset-worker-3090-b` | ACTIVE / ONLINE | `sha256:f2663e517d21...` | 0 |
| `worker-4070ti-animation-host-01` | `asset-worker-4070ti-animation-host-01` | ACTIVE / ONLINE | `sha256:f2663e517d21...` | 0 |

四个容器内 `unwrap_fbx.py` 均为 `ebfa3546...`，`qa_uv.py` 均为 `bbabf207...`；发布后
`current_jobs=0`，心跳新鲜，生产 Asset API 环境为 `UV_QA_ENFORCEMENT=strict`。

发布前备份：

```text
/srv/gpu-control/runtime-backups/uv-legacy-original-v1-pre/control-4090
/srv/gpu-control/runtime-backups/uv-legacy-original-v1-pre/worker-3090-a
/srv/gpu-control/runtime-backups/uv-legacy-original-v1-pre/worker-3090-b
/opt/gpu-control/runtime/backups/uv-legacy-original-v1-pre/worker-4070ti-animation-host-01
```

## 正式生产 API 冒烟

经生产 HTTPS Nginx 和 `/api/v1/assets/uv/process` 提交 `44.fbx`，未绕过 API、任务领取、
Worker、原子发布或 FBX 回读：

```text
job_id: abb3f83e-d40f-4600-bf48-96dcdd9c18ff
source_sha256: dd4989f0ea0f553bcdbcd2b7656e6a0eec372fb39197c7476b91106b9a0c1d4f
worker_id: asset-worker-3090-a
attempt_count: 1
status: SUCCEEDED
delivery_ready: true
algorithm: legacy_pbr
uv_islands: 15
flipped_faces: 0
degenerate_uv_faces: 0
overlap_triangle_pairs: 0
out_of_0_1_loops: 0
hard_edges_not_uv_boundary: 0
fbx_unit_contract: passed
```

Blend QA 与 FBX 回读 QA 均为 `passed=true`、`hard_failures=[]`。p90/p95 拉伸
`3.1479/3.68015` 按原合同记录为 warning，不是硬失败。五个正式产物均存在，实际文件
SHA-256 与 API 元数据逐项一致。专用冒烟客户在任务结束后已停用，系统没有遗留活动 UV
任务。

## 验证

- 包内核心文件 checksum：通过。
- Blender 5.1.2 三模型 A/B 与完整候选生产链：通过。
- 候选镜像隔离 bootstrap：通过。
- Ruff：通过。
- Python 3.11 定向测试：`17 passed`。
- Compose 配置解析与 `git diff --check`：通过。
- 全量 skill verifier 的 UV 项全部通过；随后仍因本次范围外、仓库既有的自动重拓扑
  canonical/runtime SHA 差异停止，本次没有改写该无关技能。
- 正式 API 五件套、双 QA、单位/结构回读与哈希：通过。

## 用户侧行为与回滚

历史已完成任务及产物是不可变记录，不会被新版本覆盖。要看到原版效果，需要重新上传
原文件或重新发起自动展 UV 任务。

如需回滚，必须逐节点排空后恢复上述备份，将 Worker 镜像恢复到
`1.4.50-uv-local-stretch-v1`，再只重建 Blender Worker；节点须在恢复、bootstrap 和健康
检查完成前保持 `DRAINING`。若仅要临时放宽质量门，也必须作为单独、经确认的生产变更，
不得用 advisory 掩盖原版算法的硬错误。
