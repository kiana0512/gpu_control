# UV 交付继承源 FBX 单位热修复

日期：2026-08-11

## 结论

烘焙页显示“尺寸差 99.0%、中心偏移 46.5%”不是拓扑重新缩坏，而是 UV 交付阶段把源 FBX 的
`UnitScaleFactor=1` 强制改成 `100`。两份模型在 Blender 世界空间中仍然重合，但浏览器原始坐标路径
看到 100 倍的数值约定差异，因而拒绝烘焙。

本次修复让 UV 交付继承输入 FBX 的单位合同，同时保留原低模的拓扑、UV、材质、对象结构和世界坐标。
输入单位为 `1` 时继续输出 `1`，为 `100` 时继续输出 `100`；不支持的 FBX 单位硬拒绝发布。

## 精确故障证据

- 项目：`a898d6c6-d01c-414a-a56e-3a6b9c29b38d`
- 拓扑任务：`d8fb9f62-761b-420c-b201-3f0815ce167c`
- UV 任务：`b929eb61-3792-4cca-9dce-549d876c3389`
- 原高模和拓扑低模：`UnitScaleFactor=1`
- 旧 UV 输出：`UnitScaleFactor=100`
- 旧拓扑低模结构：2936 顶点、9000 边、6000 面、18000 loops、1 个 UV 层、1 个材质槽

用生产前端同一 Three.js FBXLoader 读取时，旧 UV 输出的原始坐标约为源模型的 1/100，吻合页面的
`99.0%` 尺寸误差。高模和 UV 前低模本身的 Blender 世界包围盒尺寸误差只有约 `0.157%`。

## 修改范围

- `packages/asset_processing/blender_uv_fbx_units.py`
  - 增加必选 `--source-asset`；
  - 读取并验证源 FBX 的 `UnitScaleFactor` 和 `OriginalUnitScaleFactor`；
  - 按源单位选择 Blender FBX 导出路径；
  - 全新 Blender 场景回读世界包围盒和结构；
  - 报告升级为 `uv_fbx_unit_contract.v2`，记录浏览器原始坐标与规范厘米坐标证据。
- Blender Worker 在批准 UV Skill 完成后执行该适配器；UV Skill 本身及外部业务工作流均未修改。

该修复遵循纯变换/交付适配边界：不重拓扑、不重网格、不减面、不三角化、不修改 UV、不替换低模，
也不覆盖用户原始文件。

## 本地 Blender 5.1.2 回归

精确问题样本（源单位 `1`）修复后：

- 输出单位：`1 / 1`；
- 世界中心和三轴尺寸回读最大误差：`0`；
- 顶点、边、面、loops、UV 层和材质槽全部不变；
- 高低模浏览器包围盒：尺寸差约 `0.22%`，中心偏移约 `0.000005%`，轮廓比例差约 `0.13%`。

单位 `100` 的回归样本修复后仍输出 `100 / 100`，世界中心、尺寸和结构同样保持不变。

## 生产发布

- Git revision：`30b16a7a9f768113f0a95de06cd3640c8b40b4b4`，已推送 `origin/main`。
- Worker：`1.4.23-uv-source-units-v2`；三节点统一 image ID
  `sha256:02652e8b643eeb583a20523e7fc1f4c41f95255e5c18a07c0744479a3381ca45`；单位适配器
  SHA-256 为 `67e98dc5db415a83736ee154856b2c3b54f057e69440d1edbc76e43873afa24e`。
- 离线归档：`/srv/gpu-control/images/uv-source-units-v2-30b16a7/blender-worker.tar.zst`，
  `690758141` bytes，SHA-256
  `e687deb31a508b4ef2b07a5295d0ac9c9e6b6ae8b55dd1cb07dd86d85bb6146e`。
- 三节点严格逐台执行 `DRAINING -> GPU/Asset/lease=0 -> 只替换 Blender Worker -> 包/脚本/探针验证
  -> ACTIVE`。真实 ImageClip 任务均等待自然完成；3090-A/B 的 ComfyUI 容器 ID、启动时间和
  `RestartCount=0` 保持不变，未修改外部业务工作流。
- 精确问题低模真实 UV canary `b4b87366-0d77-4cd3-a310-2b4a7bc6ce23` 在
  `asset-worker-3090-a` 一次成功，排队到交付共 `11s`，五件制品均有 SHA。正式报告为
  `uv_fbx_unit_contract.v2 / passed=true`，源与输出单位均为 `1 / 1`，世界中心和三轴尺寸回读误差为
  `0`；2936 顶点、9000 边、6000 面、18000 loops、1 个 UV 层和1个材质槽全部保持。
- 用生产前端同一 Three.js FBXLoader 重新读取高模与 canary FBX：尺寸差 `0.220394%`、中心偏移
  `0.000005108%`、轮廓比例差 `0.125617%`，均低于烘焙页门禁。
- 已把同一正式 canary FBX 和报告复制到用户下载目录。旧制品保持不可变；受影响项目需要重新执行一次
  UV，或导入新修复的低模 FBX，才能使用新的单位合同。
