# Blender 自动拓扑并恢复原坐标

使用 `$blender-auto-retopo-align` 完成任务。必须完整读取任务 `CODEX_HOME` 中该技能的 `SKILL.md`，并按技能路由读取四份 references；不要用简化提示词替代技能。

任务参数：

- 用户上传源文件：`{{INPUT_SOURCE}}`
- 已准备的拓扑工作 Blend：`{{WORKING_BLEND}}`
- FBX 源清单：`{{SOURCE_MANIFEST}}`
- 生成阶段输出 Blend：`{{OUTPUT_BLEND}}`
- 高模对象：`{{HIGH_OBJECTS}}`
- Blender：`{{BLENDER_EXECUTABLE}}`
- 任务目录：`{{JOB_DIR}}`

FBX 输入已经由技能的 `prepare_fbx_source.py` 导入为 `SOURCE_HIGH`。不要重新导入 FBX。`SOURCE_HIGH` 的 joined 状态不是“一体有机模型”的证据；选择方法前检查断开的网格岛、截面系统、开口、遮挡关系和机械组件。

必须真实执行 Blender 并生成输出文件：

1. 只读打开工作 Blend；指定高模是唯一形状依据，也是唯一坐标依据。
2. 在创建几何前完成测量、方法选择和 shape-authority plan；写入 `{{JOB_DIR}}/plans/` 并运行 `guard_shape_authority_plan.py`。
3. 每个指定高模只生成一个低模。方法只能是 `semantic_reconstruction`、`controlled_direct_reduction` 或 `per_component_hybrid`。普通硬表面优先结构重建/组件混合；不要用全物体 Decimate 或 remesh 代替分析。必须读取源清单的 `source_topology`；当其碎片或重复顶点超过 guard 安全范围时，禁止整模 Decimate，即使资产被判断为“极复杂”也不例外。
4. 低模必须直接建立在高模本地坐标系：所有构建点使用 `source_high_local`，低模 `matrix_world` 必须等于对应高模的源矩阵。不要归零高模，不要只靠包围盒恢复坐标。
5. 无人值守任务不做左右分开展示；不得给低模保留展示平移。低模使用不透明黄色/橙色材质或对象色，保持可见，不用半透明或 X-ray。
6. 若构建时临时归一化，保存完整 4x4 `work_to_world`，并在保存前按技能的坐标恢复公式回到高模本地坐标。
7. 低模创建后只允许运行一次确定性的拓扑可交付性检查、设置显示、保存和记录数量；不得自动渲染、评分、修正、重开、重试或生成第二版。若存在游离边/顶点、重合几何、退化面、多面非流形边或不连续法线，必须以 `RETOPOLOGY_TOPOLOGY_INVALID` 让本次生成失败，禁止写成功报告。
8. 高模保持不变并可见，把生成阶段场景保存到 `{{OUTPUT_BLEND}}`。
9. 同一次构建结束前写 `{{JOB_DIR}}/generation_report.json`，状态为 `generated_for_user_inspection`。每个 asset 必须包含：
   - `high_object`
   - `low_object`
   - `faces`
   - `triangles`
   - `method_decision`
   - `actual_plugin_use`
   - `coordinate_space: source_high_local`
   - `coordinate_authority: high_object_matrix_world`
   - `presentation_offset_applied: false`
10. 保存后立即停止。不要在本次 Codex 生成阶段调用 ICP 或对齐脚本；服务器包装器会单独运行纯坐标恢复、拓扑/UV 指纹和 FBX 回读。

不得宣称 accepted、final_pass、validated 或 game_ready。最终状态仍是交给用户检查，但服务器会附加坐标对齐校验。
