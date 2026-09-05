# 自动展 UV 拆点拓扑代理修正与四节点发布记录

> 更正（2026-08-18）：本记录中的 `1.4.52-uv-topology-proxy-v1` 只让预览按同位置边显示为
> 连续，交付 FBX 仍保留拆点。3ds Max 实际读取为 `191` 个拓扑 UV 壳，不是预览显示的
> `38` 个壳，因此该版本不能作为 Max 兼容交付。正式修复和生产证据见
> `132_2026-08-18_UV_MAX_COMPATIBLE_WELD_CORRECTION.md`。

## 结论

用户指出 `1.4.51-uv-legacy-original-v1` 的服务端结果仍与本地结果有明显差距，这个判断
正确。前次只校验了原版脚本 SHA、固定参数和硬错误，没有对同一模型的视觉 UV 连通性做
等价检查。

`22.fbx` 的原始 FBX 拓扑包含 258 个顶点、116 个面和 71 个松散区域；其中大量顶点只是
FBX 导出形成的同位置拆点。原版算法把这些区域当作真实独立零件逐个投影，因此产生服务端
截图中的大量细碎、狭长 UV 壳。按几何位置临时重建连通性后，同一模型只有 80 个唯一位置
顶点和 5 个几何连通区域，能够得到与本地目标相同类型的大块连续 UV 壳。

现行 `legacy_pbr` 在满足严格安全条件时创建临时 UV 代理，在代理上执行原版切缝、展开和
排版，再只把 348 个 UV loop 坐标传回原网格。交付模型仍保持 258 个顶点、303 条边、
116 个面和 348 个 loop；不会焊接、删面或替换用户模型。代理不满足条件时继续使用原版
路径，不擅自改拓扑。

附件内文字只作为脚本、参数和集成合同的参考；修正目标始终以用户提出的“服务器效果要
达到原来本地效果”为准。

## 根因证据

正式任务 `8443ec48-d8d3-431b-ad13-a1e142517323` 的输入 `22.fbx` SHA-256 为
`647a05f8153090f151f59ae5286d7e97faf0fcd1865aa3e522a528d53597e5e3`。旧版服务端报告：

```text
vertices: 258
polygons: 116
loose_regions / uv_islands: 71
```

这不是原版参数或脚本没有生效，而是输入语义发生了变化：本地 `.max` 中连续的表面在 FBX
里被导出为多个同位置拆点。相同脚本在这种 FBX 原生拓扑上运行，也会得到 71 个碎片。
因此，仅证明服务端与升级包脚本字节相同，不能证明服务端输入与本地输入的有效连通性相同。

直接物理焊接可以减少碎片，但会把 258 个顶点改成 80 个顶点，破坏交付拓扑。现行方案只
在临时代理中恢复连通性，并把 UV 数据按 loop 映射回原模型。

## 算法与 QA 修正

- 仅对单材质、无 shape key、拆点碎片显著的网格尝试代理。
- 同位置顶点按模型尺度派生的保守阈值聚类，并拒绝退化面、非流形或面/loop 数改变的代理。
- 原版 `legacy_pbr` 仍负责切缝、展开、统一 Texel Density 和排版。
- 最终只传回 UV loop 坐标，源对象顶点、边、面、材质和坐标保持不变。
- QA 在不改变模型的前提下，以同位置边的 UV 连续关系统计“视觉 UV 壳”；重复面共享多条边
  的情况不会被误判为邻接。
- Blend 与 FBX 回读继续执行 strict 双 QA，翻转、退化、越界和非法重叠仍会拒绝发布。

## Blender 5.1.2 回归

所有测试均使用生产同版 Blender 5.1.2；源/输出顶点与面数保持一致，Blend QA 和 FBX
回读 QA 均通过。

| 输入 | 代理 | 松散区域 | 视觉 UV 壳 | 翻转/重叠 | 拉伸 p90 / p95 / max |
|---|---:|---:|---:|---:|---:|
| `11.fbx` | 733 → 249 顶点 | 172 → 8 | 38 | 0 / 0 | 1.00114 / 1.00499 / 1.56223 |
| `22.fbx` | 258 → 80 顶点 | 71 → 5 | 27 | 0 / 0 | 1.00003 / 1.00004 / 1.00005 |
| `44.fbx` | 未启用 | 15 | 15 | 0 / 0 | 3.14790 / 3.68015 / 5.15854 |
| `xiangzi_low.fbx` | 未启用 | 13 | 13 | 0 / 0 | 1.64773 / 1.91362 / 4.31003 |
| `uv_PBR_UV.fbx` | 未启用 | 2168 | 2168 | 0 / 0 | 1.06302 / 1.21679 / 4.83929 |

表中的代理顶点只存在于计算阶段，不是交付顶点数。`22.fbx` 的最终交付仍是 258 个顶点。

## 源码与制品

```text
worker_version: 1.4.52-uv-topology-proxy-v1
unwrap_fbx_sha256: fc98881c27fb2a0f60ab977a185503cb1761786a74b10b142d3b383cc207e66a
qa_uv_sha256: 2c7fee86d234823e102b6396ef9ca53b5a68d62b973b1330c90182589d17dc23
skill_sha256: 6145e97d3281ba9208bc8ec6d353f8017784c63ed3dfdf1dd770b200ccd39271
qa_adapter_sha256: 2a901a02425f7db57b2815fcb828155290fcce6def1f2c9335dd2e88aad58317
image: li3d/blender-worker:1.4.52-uv-topology-proxy-v1
image_id: sha256:b0d128fb7fbfcc7985cdc39c2a66c4f0c8b0a6b817637fd2d06d920e69b7c11d
oci_revision: working-tree-uv-topology-proxy-v1
archive: /srv/gpu-control/images/blender-worker-1.4.52-uv-topology-proxy-v1.tar.gz
archive_sha256: a879d629d25e55401a3b90e77f7a50ec5347b0c6a3754d2815ddded9225edcc5
runtime_bundle_sha256: 9ed764568ea5278d21611c7acf54cd27f81172e5123683818f3dfae16722fdd6
```

## 滚动发布

四个节点逐台切到 `DRAINING`，等待该节点与 Linux Asset Worker 的活动任务归零，再只重建
Blender Worker；新容器通过 bootstrap、脚本哈希、ONLINE 心跳检查后恢复 `ACTIVE`。
ComfyUI、Windows Substance Worker 和 Windows MOF Worker 均未被重建。

| 节点 | Worker | 状态 | 镜像 ID | restart_count |
|---|---|---|---|---:|
| `control-4090` | `asset-control-4090` | ACTIVE / ONLINE | `sha256:b0d128fb7fb...` | 0 |
| `worker-3090-a` | `asset-worker-3090-a` | ACTIVE / ONLINE | `sha256:b0d128fb7fb...` | 0 |
| `worker-3090-b` | `asset-worker-3090-b` | ACTIVE / ONLINE | `sha256:b0d128fb7fb...` | 0 |
| `worker-4070ti-animation-host-01` | `asset-worker-4070ti-animation-host-01` | ACTIVE / ONLINE | `sha256:b0d128fb7fb...` | 0 |

发布前备份：

```text
/srv/gpu-control/runtime-backups/uv-topology-proxy-v1-pre/control-4090
/srv/gpu-control/runtime-backups/uv-topology-proxy-v1-pre/worker-3090-a
/srv/gpu-control/runtime-backups/uv-topology-proxy-v1-pre/worker-3090-b
/opt/gpu-control/runtime/backups/uv-topology-proxy-v1-pre/worker-4070ti-animation-host-01
```

## 正式生产灰度

通过正式 HTTPS API 重新提交完全相同的 `22.fbx`，任务经过正式调度、Worker、原子发布、
Blend QA 和 FBX 回读 QA：

```text
job_id: d309b6f0-8a31-4558-a1c8-18a97b30a60a
source_sha256: 647a05f8153090f151f59ae5286d7e97faf0fcd1865aa3e522a528d53597e5e3
worker_id: asset-control-4090
attempt_count: 1
status: SUCCEEDED
delivery_ready: true
split_vertex_proxy.applied: true
source/output vertices: 258 / 258
proxy vertices: 80
source/output polygons: 116 / 116
loose_regions: 71 -> 5
visual_uv_islands: 27
virtual_welded_uv_edges: 50
flipped_faces: 0
degenerate_uv_faces: 0
overlap_triangle_pairs: 0
out_of_0_1_loops: 0
stretch_p90 / p95 / max: 1.00003 / 1.00004 / 1.00005
fbx_unit_contract: passed
browser_coordinate_contract: passed
```

FBX 回读结构与预期完全相同：1 个网格、258 个顶点、303 条边、116 个面、348 个 loop、
1 个 UV 层和 1 个材质槽。包围盒中心与尺寸最大误差均为
`5.960464477539063e-08`，低于 `0.0001` 合同阈值。五个产物的实际文件 SHA-256 与数据库
元数据逐项一致；专用生产灰度客户已停用，没有遗留活动 UV 任务。

生产 Blend 导出的 UV layout 已人工核对为大块连续的十字形和矩形壳，不再是此前大量细碎
条带的布局。

## 验证与回滚

- Ruff：通过。
- Python 3.11 定向测试：`17 passed`。
- Compose 配置解析和 `git diff --check`：通过。
- 五模型同输入回归、候选镜像 bootstrap、完整生产单元/坐标合同：通过。
- 全量 skill verifier 的 UV 与对齐项目均通过；随后仍因本次范围外、仓库既有的自动重拓扑
  canonical/runtime SHA 差异停止，本次没有改写该无关技能。

历史完成任务及产物不可变。用户需要重新发起 `22.fbx` 自动展 UV 任务，才能取得修正后的
结果。若需回滚，须逐节点排空后恢复上述备份和
`li3d/blender-worker:1.4.51-uv-legacy-original-v1`，节点在恢复、bootstrap 和健康检查完成
前保持 `DRAINING`。
