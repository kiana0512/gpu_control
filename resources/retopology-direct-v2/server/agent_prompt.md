# Blender 自动拓扑并恢复原坐标

使用 `$blender-auto-retopo-align` 完成任务。必须完整读取任务 `CODEX_HOME` 中该技能的 `SKILL.md`，并按技能路由读取四份 references；不要用简化提示词替代技能。

任务参数：

- 用户上传源文件：`{{INPUT_SOURCE}}`
- 已准备的拓扑工作 Blend：`{{WORKING_BLEND}}`
- 静态源模型清单：`{{SOURCE_MANIFEST}}`
- 生成阶段输出 Blend：`{{OUTPUT_BLEND}}`
- 高模对象：`{{HIGH_OBJECTS}}`
- Blender：`{{BLENDER_EXECUTABLE}}`
- 任务目录：`{{JOB_DIR}}`

FBX/GLB/GLTF/OBJ 输入已经由技能的 `prepare_fbx_source.py` 导入为唯一 `SOURCE_HIGH`。不要重新导入源文件。`SOURCE_HIGH` 的 joined 状态不是“一体有机模型”的证据；选择方法前检查断开的网格岛、截面系统、开口、遮挡关系和机械组件。

必须真实执行 Blender 并生成输出文件：

1. 只读打开工作 Blend；指定高模是唯一形状依据，也是唯一坐标依据。
2. 在创建几何前完成测量、方法选择和 shape-authority plan；写入 `{{JOB_DIR}}/plans/` 并运行 `guard_shape_authority_plan.py`。
3. 每个指定高模只生成一个低模。方法只能是 `semantic_reconstruction`、`controlled_direct_reduction` 或 `per_component_hybrid`。普通硬表面优先结构重建/组件混合；不要用全物体 Decimate 或 remesh 代替分析。必须读取源清单的 `source_topology` 和 `normalized_work_source`。当原高模是重复顶点 triangle soup 时，只有清单明确 `normalized_work_source.qualified=true` 才能对 `SOURCE_HIGH_NORMALIZED_WORK` 做 controlled direct reduction；计划中同时写 `source_identity.normalized_work_object` 和 `direct_reduction_evidence.uses_normalized_work_source=true`。禁止焊接、降面或替换 `SOURCE_HIGH`。没有合格工作副本时改用语义重建/组件混合。
   - 本次尝试指令：{{ATTEMPT_GUIDANCE}}
4. 低模必须直接建立在高模本地坐标系：所有构建点使用 `source_high_local`，低模 `matrix_world` 必须等于对应高模的源矩阵。不要归零高模，不要只靠包围盒恢复坐标。
5. 无人值守任务不做左右分开展示；不得给低模保留展示平移。低模使用不透明黄色/橙色材质或对象色，保持可见，不用半透明或 X-ray。
6. 若构建时临时归一化，保存完整 4x4 `work_to_world`，并在保存前按技能的坐标恢复公式回到高模本地坐标。
7. 所有 modifier 应用、曲线转网格和对象 join 完成后，只对新生成低模执行“无破面”收尾：删除零面积/退化面并保证全部顶点坐标为有限数值。不得修改 `SOURCE_HIGH`。开放边、非流形边、游离点边、重复点面和面朝向仅记录为诊断，不得因此拒绝交付。自动拓扑阶段不生成 UV，也不得删除、重展或修改已有 UV；低模原本有 UV 就原样保留，没有 UV 也允许交付，并报告实际 `uv_layers`。最后保存唯一低模候选，不渲染方向图，不执行 FBX 重新导入验证，也不在同一次构建中生成第二版。
   若低模为空、坐标非有限、面数不低于高模或仍含零面积/退化面，明确返回 `RETOPOLOGY_TOPOLOGY_INVALID`。
8. 高模保持不变并可见，把生成阶段场景保存到 `{{OUTPUT_BLEND}}`。仅写出 `build_once.py`、计划或测量图片不算完成；必须实际执行 Blender 构建命令，并在结束前确认 `{{OUTPUT_BLEND}}` 是非空有效 Blend、`{{JOB_DIR}}/generation_report.json` 已存在。
9. 同一次构建结束前写 `{{JOB_DIR}}/generation_report.json`，状态为 `generated_for_user_inspection`。每个 asset 必须包含：
   - `high_object`
   - `low_object`
   - `faces`
   - `triangles`
   - `uv_layers`（必须为整数 0）
   - `method_decision`
   - `actual_plugin_use`
   - `coordinate_space: source_high_local`
   - `coordinate_authority: high_object_matrix_world`
   - `presentation_offset_applied: false`
10. 保存后立即停止。不要在本次 Codex 生成阶段调用 ICP 或对齐脚本；服务器包装器只运行纯坐标恢复、保存后 Blend 指纹检查和 FBX 导出，不运行方向检查或 FBX 回读。

不得宣称 accepted、final_pass、validated 或 game_ready。最终状态仍是交给用户检查，但服务器会附加坐标对齐校验。
