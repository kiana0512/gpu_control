# 2026-08-07 自动拓扑交付坐标恢复 V1

## 结论

自动拓扑算法和冻结的 Direct V2.3.0 包保持不变。GPU Control 只在 Codex 已完成拓扑并保存
`final_low.blend` 后执行交付后处理：把每个生成低模的世界空间包围盒中心平移到对应高模中心，
然后保存交付 Blend、导出 FBX 并重新导入验证。

该步骤不改低模顶点、边、面、旋转、缩放、UV、材质或拓扑方法，也不修改高模。任何不可证明为
“仅平移”的变化都会使任务失败，不发布 Blend/FBX。

## 原因

生产任务 `c84d3850-6974-49b0-b9a6-7bbccd96ba75` 的 Direct V2 输出把生成低模沿 X 轴平移
约 `+1.4` 用于并排展示，随后同一个对象被直接导出：

- 高模中心：`[7.912394, 12.289606, 0.662301]`；
- 低模中心：`[9.312393, 12.289606, 0.662301]`；
- 世界中心偏移：`1.399999`；
- 低模尺寸误差仅为 `[0.7462%, 1.3877%, 0%]`。

这证明故障是展示平移污染交付坐标，不是尺寸或拓扑问题。因此不能通过缩放、重新拓扑或在烘焙
入口猜测矩阵来修复。

## 实现与硬门禁

`packages/asset_processing/blender_retopology_restore_coordinates.py` 在拓扑任务返回后运行：

1. 从 `generation_report.json` 读取高模/低模对象对；
2. 记录高模完整对象指纹、低模网格指纹、世界 3x3 线性变换、行列式和尺寸；
3. 仅修改低模 `matrix_world.translation`；
4. 验证高模未变，低模网格、旋转、缩放和尺寸未变；
5. 保存校准后的交付 Blend；
6. 只导出低模 FBX，清空场景后重新导入；
7. 验证 FBX 回读中心和尺寸仍与校准后的低模一致；
8. 把原始 Agent Blend 哈希、交付 Blend 哈希、FBX 哈希和校准证据写入
   `retopology_direct_delivery.v3`。

Asset API 只接受 V3 清单，并再次验证原始 Agent 输出、交付 Blend、交付 FBX、高低模对象对和
FBX 回读证据之间的哈希闭环。

## 已撤销的方案

此前新增的预烘焙坐标对齐功能已完整删除，包括：

- `BAKE_ALIGNMENT_V1` 任务和完成接口；
- 烘焙上传的 `alignment_manifest`；
- Blender Worker 的预烘焙对齐脚本和镜像资源；
- Web 中的“坐标准备”状态；
- 对应文档和测试。

PBR 烘焙恢复原有 `SUBSTANCE_BAKE_V1` Windows-only 流程。独立拖入高低模时，服务器不再尝试
猜测或修改它们的坐标；自动拓扑产生的低模在拓扑交付端已经回到高模坐标。

## 真实模型验证

对上述生产任务的只读副本执行 Blender 5.1.2 验证：

| 指标 | 拓扑原输出 | 坐标恢复后 |
| --- | ---: | ---: |
| 高模指纹 | `4c546380...e2bd` | `4c546380...e2bd` |
| 低模顶点 | 336 | 336 |
| 低模面 | 314 | 314 |
| 低模尺寸 | `[0.922703, 0.922703, 1.894610]` | `[0.922702, 0.922703, 1.894610]` |
| 世界中心偏移 | `1.399999` | `0.0` |
| 归一化中心偏移 | `0.738938` | `0.0` |
| 应用平移 | — | `[-1.3999996, 0, 0]` |
| FBX 回读中心最大误差 | — | `0.0000009537` |
| FBX 回读尺寸最大误差 | — | `0.0000001192` |

尺寸最后一位差异来自 Blender 32 位浮点世界矩阵，低模本地网格 SHA-256 在处理前后相同。该模型
原有 2 个 N-gon 与本坐标修复无关，未被隐藏或修改。

## 测试

- 目标 Ruff：通过；
- 单元测试：`316 passed, 1 skipped`；
- 新增 Direct V2 V3 完成合同、旧 V2 拒绝、原 Substance Windows-only 回归：`3 passed`；
- Asset API 全文件回归：`85 passed`，另有 5 个既存的退役 V1 测试仍以旧 Worker 身份创建
  Direct V2 任务而无法领取；与本次坐标恢复路径无关；
- Blender 5.1.2 真实 Blend 校准、FBX 导出/回读及官方 `audit_pair.py`：通过。

## 生产发布

已在零任务窗口完成发布。由于旧 API 只接受 V2、而新 API 只接受带坐标证据的 V3，为避免兼容窗口
交付未校准文件，先将三台节点全部置为 `DRAINING` 并确认 GPU/Asset 活动作业均为 0，再依次替换三台
Worker；所有 Worker 保持 DRAINING 时切换 Asset API 和 Web，验证后统一恢复 `ACTIVE`。

| 组件 | 生产镜像 | 镜像 ID |
| --- | --- | --- |
| Blender Worker（三台一致） | `li3d/blender-worker:1.4.5-retopology-coordinate-restore-v1` | `sha256:6cf827a6ec6b0c1626082359117abebc3c411683b180f6010b28b349c8b1f3c4` |
| Asset API | `unified-scheduler-asset-api:1.6.10-retopology-coordinate-restore-v1` | `sha256:1d80c5228001d8685eb691e20c52b7375af6dbfaf66e410c193cfd4d65a79d7c` |
| Web（撤销坐标准备 UI） | `gpu-control-web:1.5.10-retopo-direct-v2` | `sha256:c674c41679dcbd42b422cddb5fdb7f5f64dabba4d4ec424ef8aa2925e4699cd8` |

Worker 与 Asset API 的 OCI revision 为
`1d85f5d467d1433bc3bd3cb5bda4dd30eeafe9be`。三台 Worker 中脚本 SHA-256 均为
`a1dadcd72318b1475377cc02f3e70876d8cc3ad350ebe86b17d7ed72b10568c5`。

发布后验证：

- `control-4090`、`worker-3090-a`、`worker-3090-b` 均为
  `ACTIVE / ONLINE / current_jobs=0`；
- 三个 Linux Asset Worker 均为
  `ONLINE / AUTHENTICATED / HEALTHY / asset-skills-retopology-v2.3.0`；
- Asset API 与 Web healthcheck 均为 `healthy`；
- OpenAPI 不再包含 `bake-alignment-complete` 或 `alignment_manifest`；Web 静态产物不再包含
  `BAKE_ALIGNMENT`、`坐标准备` 或 `bake-alignment`；
- 三台 ComfyUI 容器 ID 仍分别为 `d306e1facb7b`、`1547af00e12f`、`95acf7b332f2`，没有重启、
  重建或清理缓存；Windows Substance Baker 未重启；
- 六次节点模式变更均由 `codex-operator` 通过受审计 Admin API 写入
  `node.mode.change / SUCCESS`；
- 三台环境文件备份为 `.env.pre-retopology-coordinate-restore-1d85f5d`，可按精确旧镜像回滚。
