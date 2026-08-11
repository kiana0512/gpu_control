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
7. 所有 modifier 应用、曲线转网格和对象 join 完成后，必须先在新生成低模上做一次确定性的构建收尾，再创建 UV：以模型对角线计算尺度容差，只合并完全重合/数值容差内的重复点，运行 `bmesh.ops.dissolve_degenerate`，删除零面积面及由此产生的游离边/顶点，并重算法线。接着实测边界边；不得只检测后直接退出。每个意外边界必须在同一次构建中按当前组件修复：端部用封口，成对截面环用 bridge，简单闭合缺口环才允许 `bmesh.ops.holes_fill`，并把新填充面三角化；真正的开口必须有内外壁和连接 rim，不能用一张跨开口的大盖片冒充。边界图不是简单闭合环、修复会跨组件或会封死计划中的负空间时，必须重建当前新低模组件。每次修复后更新 BMesh 并重新实测，只有 `boundary_edges == 0` 才能继续。所有这些操作只允许作用于本次新生成低模，绝不允许作用于 `SOURCE_HIGH`；不得用宽距离焊接、Decimate、remesh 或全物体重建冒充收尾。收尾必须发生在 UV 展开之前，避免破坏 UV。随后在同一个生成脚本中创建至少一个非空 UV 层；语义重建低模可按岛标记接缝并展开，禁止把无 UV 的模型写成成功。最后只运行一次确定性的拓扑可交付性检查、设置显示、保存和记录数量；不得自动渲染、评分、重开或在同一次构建中生成第二版。低模必须为闭合流形；若缺少 UV，或仍存在边界/开边、游离边/顶点、重合几何、退化面、多面非流形边或不连续法线，必须以 `RETOPOLOGY_TOPOLOGY_INVALID` 让本次生成失败，禁止写成功报告。
8. 高模保持不变并可见，把生成阶段场景保存到 `{{OUTPUT_BLEND}}`。仅写出 `build_once.py`、计划或测量图片不算完成；必须实际执行 Blender 构建命令，并在结束前确认 `{{OUTPUT_BLEND}}` 是非空有效 Blend、`{{JOB_DIR}}/generation_report.json` 已存在。
9. 同一次构建结束前写 `{{JOB_DIR}}/generation_report.json`，状态为 `generated_for_user_inspection`。每个 asset 必须包含：
   - `high_object`
   - `low_object`
   - `faces`
   - `triangles`
   - `uv_layers`（整数且至少为 1）
   - `method_decision`
   - `actual_plugin_use`
   - `coordinate_space: source_high_local`
   - `coordinate_authority: high_object_matrix_world`
   - `presentation_offset_applied: false`
10. 保存后立即停止。不要在本次 Codex 生成阶段调用 ICP 或对齐脚本；服务器包装器会单独运行纯坐标恢复、拓扑/UV 指纹和 FBX 回读。

不得宣称 accepted、final_pass、validated 或 game_ready。最终状态仍是交给用户检查，但服务器会附加坐标对齐校验。
