# 2026-08-10 自动拓扑高低模对齐 V3 热修复

## 目标

Li3D 自动拓扑产生的低模必须在交付前回到原始高模的世界位置、旋转和缩放，随后才能作为
Substance 烘焙低模使用。后处理只允许修改低模对象世界矩阵，不允许修改低模顶点、边、面、UV
或拓扑。

## 已定位根因

生产任务 `e3308c0c-c003-4d93-8b71-7d9201ed5cb2` 与
`e5f46a73-4061-47e5-807e-cfb0eaff9f24` 使用同一个 GLB，均在 8%、3–4 秒失败，错误为
`input does not have a valid Blend signature`。Codex 和拓扑构建尚未启动。

同一真实 GLB 的旧归一化路径存在两个确定性缺陷：

1. Blender 5.1 默认把归一化结果保存为 Zstd 压缩 Blend，而冻结 Direct V2 输入合同只接受原始
   `BLENDER` 文件头；
2. `--factory-startup` 创建的默认 Cube 未被删除。旧归一化场景同时包含 `Cube`（尺寸 `2×2×2`）
   和用户 `model`（尺寸约 `1.224×1.898×1.198`），会污染高模选择和尺寸依据。

## V3 交付规则

1. GLB、GLTF、OBJ 先在真正的空场景中导入，并显式以 `compress=False` 保存；压缩 Blend 也会先
   安全重存为原始 Blend；
2. Direct V2 生成完成后，以对应高模的世界 3×3 线性矩阵作为低模旋转和缩放权威；
3. 再把低模世界包围盒中心恢复到高模中心；
4. 全程校验高模指纹不变、低模 mesh SHA-256 不变；
5. 恢复后三轴尺寸相对误差必须全部不超过 5%，不允许用额外缩放掩盖错误几何；
6. 只导出低模 FBX，清空场景后重新导入，中心和尺寸回读必须通过；
7. 对象矩阵原本未变化时不写回 Blend，输入输出 SHA-256 必须完全相同；
8. Worker 与 Asset API 双重验证 `retopology_coordinate_restoration.v2` 和
   `retopology_direct_delivery.v4` 的动作、哈希、尺寸及 FBX 证据。

候选版本：Asset API `1.6.13-retopology-alignment-v3`、Linux Worker
`1.4.8-retopology-alignment-v3`。冻结 Direct V2.3.0 包、Codex 模型、提示词、拓扑方法和外部
ImageClip/ModelViewCreator 流水线均未修改。

## 验证

- 失败任务原始 7.7 MB GLB：归一化后仅剩用户 `model`，无默认 Cube；输出为 13.2 MB 原始
  `BLENDER` 文件；
- 压缩 Blend：Zstd 文件头成功重存为 `BLENDER` 文件头；
- 故意扰乱低模位置、旋转和缩放：恢复成功，中心残差为 0，低模 mesh 保持不变，FBX 回读尺寸
  最大误差约 `9.54e-7`；
- 原本对齐：`coordinate_action=unchanged`，交付 Blend SHA-256 与 Agent Blend 完全一致；
- 故意将低模真实几何缩小 50%：按 `0.5 > 0.05` 失败关闭，不发布错误制品；
- 目标合同测试：12 passed；
- Python 全量：524 passed、12 skipped；
- Ruff：通过；mypy strict：61 个源文件通过。

## 发布状态

代码和候选镜像完成后，必须等待三台节点无运行中 Asset 任务，将节点置为 `DRAINING`，再滚动替换
三台 Worker 和 Asset API。发布期间不重启 ComfyUI，不清模型缓存，不修改外部业务流水线。上线后
使用同一真实 GLB 重新提交自动拓扑，并以最终低模与原高模通过烘焙对齐检查作为闭环证据。
