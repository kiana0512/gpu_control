# 自动展 UV 的 3ds Max 实际连壳修正与 1.4.53 发布记录

## 结论

用户在 3ds Max 中确认 `1.4.52-uv-topology-proxy-v1` 的交付文件仍然“全切”，这个判断
正确。前版只在临时代理上恢复几何连通性，再把 UV loop 坐标传回原始拆点网格。网页预览按
同位置边把这些 UV 视觉合并，但 3ds Max 读取的是交付 FBX 的真实拓扑，因而同一个
`11.fbx` 实际得到 `191` 个 UV 壳，而不是预览显示的 `38` 个。

`1.4.53-uv-max-weld-v1` 不再把虚拟连通性当成交付通过。对能够证明是 FBX 导出拆点的安全
单材质网格，交付副本会物理焊接同位置拆点，再执行原版 PBR 切缝、展开、统一密度和排版；
源文件保持不变。Blend 与 FBX 回读 QA 新增 Max 兼容硬门：

```text
topological_uv_islands == uv_islands
virtual_welded_uv_edges == 0
```

任一不满足即拒绝发布。旧版错误的 `11.fbx` 产物会被新门禁明确拒绝：

```text
max_incompatible_split_uv_shells=191
visual_uv_shells=38
virtual_welded_uv_edges=162
```

## 安全范围

物理修复只在以下条件全部成立时启用：单材质、无 shape key、存在显著精确同位置重复顶点、
原始松散区域严重碎裂，且焊接后连通区域下降到安全上限。修复还必须同时满足：

- 面数和 loop 数不变；
- 无退化面和新增非流形边；
- 材质、对象变换和包围盒不变；
- FBX 回读结构与 Blender 交付结构一致；
- 翻转、退化 UV、非法重叠和 0–1 越界均为零。

不满足条件的模型继续走原版路径，不擅自焊接。该变更只作用于交付副本，上传源文件和历史
任务产物均不改写。

## Blender 5.1.2 回归

| 输入 | 顶点 | 面 | 松散区域 | Max 实际 UV 壳 | 虚拟焊接边 | 翻转/退化/重叠/越界 |
|---|---:|---:|---:|---:|---:|---:|
| `11.fbx` | 733 → 249 | 392 → 392 | 172 → 8 | 38 | 0 | 0 / 0 / 0 / 0 |
| `22.fbx` | 258 → 80 | 116 → 116 | 71 → 5 | 27 | 0 | 0 / 0 / 0 / 0 |
| `44.fbx` | 245（未修复） | 422 | 15 | 15 | 0 | 0 / 0 / 0 / 0 |
| `xiangzi_low.fbx` | 40（未修复） | 33 | 13 | 13 | 0 | 0 / 0 / 0 / 0 |
| `uv_PBR_UV.fbx` | 6810（多材质，未修复） | 6096 | 2168 | 2168 | 0 | 0 / 0 / 0 / 0 |

`11.fbx` 的拉伸 p90 / p95 / max 为 `1.00114 / 1.00499 / 1.56223`；`22.fbx`
为 `1.00003 / 1.00004 / 1.00005`。两份目标模型的 Blend QA 与 FBX 回读 QA 完全一致。

## 源码与制品

```text
worker_version: 1.4.53-uv-max-weld-v1
unwrap_fbx_sha256: 04c09e0907ad8ad3838be2ece177b8c9c4b4d33c151633849bfd6262a70748c9
qa_uv_sha256: a263d0fc05947d70988317972f9b0bb38e7c85a165274756d3c4dbf4e05f91c3
skill_sha256: 36910d034a70de1b64799244dba20a5999d310fe7d1d80ad17a44fb3244a38d5
qa_adapter_sha256: 14c1c2d121bc72d2d4df2111da78295a48938e6250c71acac4c6e555cea67502
image: li3d/blender-worker:1.4.53-uv-max-weld-v1
image_id: sha256:8ea7ab7bca31a776853cf2c673ced02c5a8236a6b2e5c6c00fbabd7b29ecc200
oci_revision: working-tree-uv-max-weld-v1
archive: /srv/gpu-control/images/blender-worker-1.4.53-uv-max-weld-v1.tar.gz
archive_sha256: d3afedf116a0cc57bc685d26df04b730cf27d6036e4326341a52cba386390371
runtime_bundle: /srv/gpu-control/images/blender-pbr-uv-max-weld-v1.tar.gz
runtime_bundle_sha256: dd9ebcfd0b3b4592eb727af6953afac68b338d49f6a2b13fe90ccd29b85144fc
```

## 四节点滚动发布

四个节点逐台执行 `DRAINING -> GPU/Asset 作业归零 -> 只替换 Blender Worker -> 镜像、
脚本哈希和新实例心跳验证 -> ACTIVE`。ComfyUI、Windows Substance Worker 和 Windows
MOF Worker 均未停止、重启或重建。

| 节点 | Worker | 最终状态 | 镜像 ID | restart_count |
|---|---|---|---|---:|
| `control-4090` | `asset-control-4090` | ACTIVE / ONLINE | `sha256:8ea7ab7bca31...` | 0 |
| `worker-3090-a` | `asset-worker-3090-a` | ACTIVE / ONLINE | `sha256:8ea7ab7bca31...` | 0 |
| `worker-3090-b` | `asset-worker-3090-b` | ACTIVE / ONLINE | `sha256:8ea7ab7bca31...` | 0 |
| `worker-4070ti-animation-host-01` | `asset-worker-4070ti-animation-host-01` | ACTIVE / ONLINE | `sha256:8ea7ab7bca31...` | 0 |

发布前备份：

```text
/srv/gpu-control/runtime-backups/uv-max-weld-v1-pre/control-4090
/srv/gpu-control/runtime-backups/uv-max-weld-v1-pre/worker-3090-a
/srv/gpu-control/runtime-backups/uv-max-weld-v1-pre/worker-3090-b
/opt/gpu-control/runtime/backups/uv-max-weld-v1-pre/worker-4070ti-animation-host-01
```

## 正式生产 canary

通过正式 HTTPS API 重新提交与用户任务完全相同的 `11.fbx` 和回归模型 `22.fbx`。两笔
任务均由新镜像一次完成，五个产物的下载 SHA-256 与数据库元数据逐项一致；临时 canary
客户随后已停用。

| 输入 | Job ID | Worker | 输入 SHA-256 | 实际壳 | 结果 |
|---|---|---|---|---:|---|
| `11.fbx` | `84b9231e-cd2d-4912-9736-ed87a76f55bf` | `asset-control-4090` | `43645ee9e0248a1ed4a5cb3b1c7c9be3c808adb84a6f722120c1e075fcb78211` | 38 | SUCCEEDED |
| `22.fbx` | `c1f651d3-0306-45ba-b74f-175d6daeef02` | `asset-control-4090` | `647a05f8153090f151f59ae5286d7e97faf0fcd1865aa3e522a528d53597e5e3` | 27 | SUCCEEDED |

`11.fbx` 的 Blend/FBX QA 均为 `38 visual = 38 topological / 0 virtual`，交付结构为
1 个网格、249 个顶点、649 条边、392 个面、1176 个 loop、1 个 UV 层和 1 个材质槽；
回读完全相同，中心和尺寸误差为零。

`22.fbx` 的 Blend/FBX QA 均为 `27 visual = 27 topological / 0 virtual`，交付结构为
1 个网格、80 个顶点、191 条边、116 个面、348 个 loop、1 个 UV 层和 1 个材质槽；
回读完全相同，中心与尺寸最大误差为 `5.960464477539063e-08`。

## Li3D 网页预览边界

`10.3.34.9:4517` 当前发布的 Li3D 客户端会对交付 FBX 的每个三角面执行闭合描边，因此内部
三角边也会显示成青色线，即使它们不是 UV seam。这是独立的客户端显示问题，不能用于判断
3ds Max 中的真实壳数量。该客户端源码不在 GPU Control 仓库，主机也未提供可用的 SSH 发布
通道，本次没有直接篡改线上压缩 JS。客户端后续应把逐三角描边改为只绘制 UV 边界；在此之前
以下载 FBX、双 QA 的 `topological_uv_islands` 和 3ds Max 检查为准。

## 验证与回滚

- Ruff、Compose 解析和 `git diff --check`：通过。
- Python 3.11 定向测试：`17 passed`。
- `11`、`22` 的完整单位/坐标合同与五个非目标路径回归：通过。
- 候选镜像隔离 bootstrap 和三份运行时脚本哈希验证：通过。
- 全量 skill verifier 的 UV 与对齐项目通过；随后因本次范围外、仓库既有的自动重拓扑
  canonical/runtime SHA 差异停止，本次没有改写该无关技能。

历史任务和旧产物不可变。用户必须重新提交 `11.fbx`，才能获得 `1.4.53` 的实际连壳结果。
如需回滚，先逐节点排空，再从上述备份恢复 `1.4.52` 运行时和镜像；完成 bootstrap、哈希、
新鲜心跳与零任务门禁前保持 `DRAINING`。
