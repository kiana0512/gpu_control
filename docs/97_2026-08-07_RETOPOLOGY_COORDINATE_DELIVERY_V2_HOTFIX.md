# 2026-08-07 自动拓扑坐标交付 V2 热修复

## 结论

生产任务 `98f806f7-4a50-4e39-a0f7-06e4c45b0680` 在 92% 失败的直接原因不是拓扑或坐标恢复
失败，而是 Worker 上报阶段名 `RETOPOLOGY_DIRECT_V2_COORDINATE_RESTORE` 时，Asset API 因数据库
阶段字段长度限制返回 HTTP 422。失败发生在坐标恢复 Blender 子进程启动前。

修复后交付规则固定为：

- 高低模世界包围盒中心在容差内一致：不写回 Blend，原始 Agent Blend 字节保持不变，只导出并回读
  验证正式 FBX；
- 中心发生偏移：只修改生成低模的世界平移，把中心恢复到高模坐标；
- 两条路径都必须证明高模、低模网格、旋转、缩放和尺寸未被修改，并通过 FBX 回读；
- 不重新拓扑，不修改冻结的 Direct V2.3.0 包、模型、提示词或图拓扑。

## 实现

提交 `8575ffabc5dcfaeffe017f80be79c9d4c643bd3d` 完成以下修改：

1. 新 Worker 直接上报数据库安全的 `RETOPOLOGY_V2_COORD_RESTORE`；
2. 新 Asset API 将滚动窗口内旧 Worker 的长阶段名映射为上述短阶段名；
3. 坐标脚本先计算偏移和容差，只有偏移超出容差才赋值 `matrix_world.translation`；
4. 全部对象均无需恢复时不执行 `save_as_mainfile`，并强制要求 Agent Blend 与交付 Blend SHA-256
   相同；
5. 证据中记录 `coordinate_action=unchanged|translation_restored` 和
   `blend_translation_changed`，Worker 与 Asset API 双重校验决策、哈希和 FBX 回读闭环。

坐标脚本 SHA-256 为
`f4ffe4aef0628a151224553d78b67ebf31ff470d8970922269ce0e7dbbdf38e2`，三台生产 Worker 完全一致。

## 验证

对已有生产交付物的只读副本使用 Blender 5.1.2 连续验证两次：

| 路径 | 结果 |
| --- | --- |
| 有偏移 | 检出 X 轴 `+1.3999996` 偏移，只应用 `[-1.3999996, 0, 0]` 平移；中心残差 0 |
| 无偏移 | `coordinate_action=unchanged`，应用平移 `[0, 0, 0]` |
| 无偏移 Blend | 输入、输出 SHA-256 均为 `a103edfc...b25b1a0`，字节完全一致 |
| FBX 回读 | 两条路径均通过；中心最大误差 `9.5367e-07`，尺寸最大误差 `1.1921e-07` |

代码验证：

- 目标 Ruff：通过；
- 单元测试：`317 passed`；
- 新增阶段兼容与两种坐标决策 API 合同：通过；
- Asset API 全文件回归：`86 passed`；另有 5 个既存的退役 V1 测试仍使用旧 Worker 身份，失败于
  领取任务，与本热修复无关；
- 候选 Worker 内置脚本哈希、无偏移 Blend 字节不变及 FBX 回读：通过；
- 冻结 Direct V2 包 `verify_package.py`：通过。

## 生产发布

发布前把三个节点置为 `DRAINING`，确认 GPU job、batch、Asset job 和 node lease 均为 0。随后仅替换
三个 Blender Worker 和 Asset API，验证新实例、认证、脚本哈希和健康状态后恢复 `ACTIVE`。

| 组件 | 镜像 | 镜像 ID |
| --- | --- | --- |
| Blender Worker（三台一致） | `li3d/blender-worker:1.4.6-retopology-coordinate-restore-v2` | `sha256:a5e4e89adee813ec876362c5b35e3e75b3c1bd3c874143ee0d28a7d00eaaced3` |
| Asset API | `unified-scheduler-asset-api:1.6.11-retopology-coordinate-restore-v2` | `sha256:156703b3022f473fd020668abf2e731ef1562af2e68091fa1a3d66651e20b4ba` |
| Web（未改） | `gpu-control-web:1.5.10-retopo-direct-v2` | `sha256:c674c41679dcbd42b422cddb5fdb7f5f64dabba4d4ec424ef8aa2925e4699cd8` |

发布后：

- 三节点均为 `ACTIVE / ONLINE / current_jobs=0`；
- 三个 Linux Worker 均为 `ONLINE / AUTHENTICATED / HEALTHY`，Skill 仍为
  `asset-skills-retopology-v2.3.0`；
- 四个 Windows Substance Baker 保持 `ONLINE / current_jobs=0`，未重启；
- Asset API 为 `healthy`，数据库 ready；
- 三台 ComfyUI 容器 ID 仍为 `d306e1facb7b`、`1547af00e12f`、`95acf7b332f2`，启动时间和
  RestartCount 未变化；
- 六次模式变更均由 `codex-operator` 通过 Admin API 记录为
  `node.mode.change / SUCCESS`；
- 三台回滚环境备份均为 `.env.pre-retopology-coordinate-delivery-v2-8575ffa`。

## 原失败任务

失败任务的持久目录中只有 `retopology_input.zip`，92% 时尚未发布 Blend/FBX，Worker 临时目录也已按
失败清理，因此不能在不重新运行拓扑的前提下恢复这一次的输出。该任务保持失败记录，不做隐式自动重试；
客户端需以新的任务/幂等键重新提交原模型。新任务会使用本 V2 规则完成坐标判断和交付。
