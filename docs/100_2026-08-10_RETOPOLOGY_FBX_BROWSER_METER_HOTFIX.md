# 自动拓扑 FBX 浏览器米制热修复

日期：2026-08-10

## 结论

自动拓扑生成的低模几何、拓扑和 BLEND 坐标没有被放大。故障发生在最后一步 FBX 导出：Blender
默认 `FBX_SCALE_NONE` 把米制坐标按厘米写成 100 倍原始数值，而 Li3D 浏览器中的 Three.js
`FBXLoader` 直接使用这些数值，最终把低模显示成高模约 100 倍，产生 `尺寸差 9650.7%`。

本次只修改 GPU Control 拥有的拓扑交付适配器、Worker 和 Asset API，不修改 Direct V2/Codex
拓扑流程、模型、提示词、拓扑参数或 ImageClip/ModelViewCreator 工作流。

## 修复合同

- 坐标恢复报告升级为 `retopology_coordinate_restoration.v3`。
- Direct V2 交付清单升级为 `retopology_direct_delivery.v5`。
- FBX 明确使用米制场景、`global_scale=1`、`apply_unit_scale=true`、
  `FBX_SCALE_UNITS`、`-Z/Y` 轴；实际输出文件必须同时包含
  `UnitScaleFactor=100` 和 `OriginalUnitScaleFactor=100`。
- Worker 从真实 FBX 二进制回读单位字段；缺失、歧义或仍为旧值 `1` 时拒绝交付。
- 高模只读；低模只允许恢复对象世界变换，禁止修改顶点、边、面或拓扑。尺寸相对误差超过 5%
  仍然失败关闭，不通过缩放顶点掩盖错误。

> 后续说明：本文件记录的是 `1.4.9` 米制导出阶段的历史合同。最终生产实现已升级为
> `1.4.11-retopology-envelope-v2`，拓扑生成结束后允许只修改低模对象世界变换来恢复高模中心、
> 三轴尺寸和方向；仍禁止修改低模网格。详见
> `101_2026-08-10_RETOPOLOGY_ENVELOPE_V2_HOTFIX.md`。

## 真实问题模型验证

使用问题任务的原始 GLB、既有 Direct V2 BLEND 和新镜像进行离线重导出，并用生产前端同类的
Three.js loader 读取包围盒：

| 文件 | Three.js 中心 | Three.js 尺寸 |
|---|---|---|
| 原始高模 GLB | `[-0.000324398, 0.000157207, 0.000352859]` | `[1.224234, 1.197908, 1.897820]` |
| 旧 FBX | `[-0.0324368, 0.0157208, 0.0352858]` | `[126.50001, 120.85000, 185.05001]` |
| 修复后 FBX | `[-0.000324368, 0.000157208, 0.000352859]` | `[1.265000, 1.208500, 1.850500]` |

修复后中心残差小于 `3e-8` 米；三轴尺寸与高模同量级，最大相对误差约 `3.33%`，通过 5%
交付门禁。Blender 回读、FBX 二进制单位回读和 Three.js 浏览器读回均通过。

## 版本与验证

- 源码提交：`2c8684ccc714939bfef5fc2ef5f12e6271bdee7d`
- Asset API：`1.6.14-retopology-fbx-meter-v1`
- Blender Worker：`1.4.9-retopology-fbx-meter-v1`
- Python 全量回归：`525 passed, 12 skipped`
- 专项回归：`21 passed`
- Ruff：通过
- mypy：23 个源文件通过

## 发布说明

三台节点先进入 `DRAINING`；已有任务没有被中断。3090-B 上正在执行的 Direct V2 任务先正常
`SUCCEEDED`，随后才替换该 Worker。Asset API 和三台 Worker 已全部运行上述版本及同一个源码
revision，三节点恢复为 `ACTIVE / ONLINE`。发布没有重启任何 ComfyUI，也没有修改外部业务工作流。

离线镜像包位于
`/srv/gpu-control/images/retopology-fbx-meter-v1-2c8684c/`：

- Asset API：`92197339` bytes，SHA-256
  `c19bef0cd838eeaac24faee587043e1bf052ca5362b6196b2b21180cc400b49b`
- Blender Worker：`686855254` bytes，SHA-256
  `b1d337f8811bce93f82361068a302b5bae93a96f3c418a4d9619bd51c6b998af`

旧任务已经下载的旧 FBX 不会被静默覆盖；它仍携带旧单位合同。必须使用本版本部署后新生成或
重新交付的 FBX 才能得到浏览器米制结果。

为避免用户对部署前最后一个任务再次运行 Codex，已从任务
`1f11fccf-d51f-4d33-8d07-2fddc48f81f5` 的既有 BLEND 只做米制 FBX 重导出；服务器原 artifact
未被覆盖。修复文件写入
`/home/lilithgames/下载/li3d-retopology-1f11_GAME_LOW_meter_fixed.fbx`，SHA-256 为
`50042fbceb044dd2dee09cc16cc103299bd64cfd4d53c22650e1ee39c9f173a3`。Three.js 读回尺寸为
`[1.220000, 1.238000, 1.860000]`，中心为
`[-0.000324368, 0.000157154, 0.000352834]`；高低模最大轴向相对误差约 `3.35%`。
