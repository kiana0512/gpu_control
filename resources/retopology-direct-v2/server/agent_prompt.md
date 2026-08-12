# Blender 自动拓扑并恢复原坐标

使用 `$blender-auto-retopo-align` 完成任务。完整读取任务 `CODEX_HOME` 中该技能的
`SKILL.md` **一次**。本任务是标准 generated-low 服务器快速路径：`SKILL.md`、本提示和
不可变源清单已经包含全部适用规则；四份长 references 仍完整安装并经过哈希校验，但本任务
不要重复打开，除非源清单缺失或规则互相矛盾。不要读取 `guard_shape_authority_plan.py` 源码；
直接按下面的计划合同写 JSON 并运行它。Worker 不保证安装 `rg`，不要调用 `rg`。

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
2. 在创建几何前完成测量、方法选择和 shape-authority plan；写入 `{{JOB_DIR}}/plans/` 并且只运行一次计划守卫。守卫是普通 Python 脚本，不是 Blender 脚本，精确命令为：

   `python3 "$RETOPOLOGY_SKILL_ROOT/scripts/guard_shape_authority_plan.py" "{{JOB_DIR}}/plans/source_high_shape_authority.json"`

   若源清单包含 `semantic_measurements`，直接使用其中的局部坐标包围盒、最大组件及面数，不得另写或运行 `measure_source.py`。只有没有源清单的直接 Blend 输入才允许一次文本测量。禁止创建 `render_measurement_views.py`，禁止输出任何测量图或方向图。

   下列是守卫的精确快速合同，不得自创同义字段或值：

   - 顶层必须为 `output_behavior: "save_and_stop"`、`user_inspects_result: true`、`automatic_post_generation_actions: []`。
   - `source_identity` 必须包含非空 `blend_filepath/object_name/mesh_data_name`、`measurement_space: "high_local"` 和恰好 16 个数的 `matrix_world`。
   - `method_decision` 只能是 `semantic_reconstruction`、`controlled_direct_reduction` 或 `per_component_hybrid`。
   - `shape_authority.authority` 必须精确为 `"high_poly_only"`；`global_registration_inputs` 为非空数组；`uses_only_global_bounds` 和 `fixed_geometry_proportions_from_template` 均为 false。
   - `local_profile_sections` 至少一项，每项必须有 `coordinate_space: "high_local"`、`source: "high_measurement"` 和非空 `controlling_views`。
   - `feature_controls` 和 `openings` 必须存在；没有确切证据时直接写空数组，不得猜测。若写特征，使用 `authority: "high_measurement"`、非空 `controlling_views` 和非空 `measurements`；若写开口，使用 `authority: "high_measurement"`、非空字符串 `boundary_measurement` 和非空 `controlling_views`。
   - `surface_correspondence_method` 只能是 `measured_local_sections`、`bounded_surface_projection`、`fresh_high_derived_cage` 或 `per_component_hybrid`。`template_constants` 必须是数组，没有常量就写 `[]`。
   - `component_evidence` 至少一项，每项必须有非空 `component_id` 和 `evidence`。`component_decisions` 至少一项，其 `evidence_id` 必须精确匹配上述某个 `component_id`。
   - `count_evidence_policy` 必须包含 `fixed_face_count_is_shape_evidence/fixed_component_count_is_shape_evidence/budget_or_count_can_satisfy_shape_gate`，三者均为 false。
   - 使用 `semantic_reconstruction` 时不要写 `component_method_map`或 `direct_reduction_evidence`。使用 `per_component_hybrid` 或 `controlled_direct_reduction` 时，只有完全满足 `SKILL.md` 中的附加证据才允许选择；证据不足就使用 `semantic_reconstruction`。

   计划一旦守卫失败，不得猜测守卫源码或连续改写重试；立即终止并返回计划错误。此规则确保每个生成任务只消耗一次计划检查。
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
   - `uv_layers`（必须为大于等于 0 的整数；已有 UV 原样保留）
   - `method_decision`
   - `actual_plugin_use`
   - `coordinate_space: source_high_local`
   - `coordinate_authority: high_object_matrix_world`
   - `presentation_offset_applied: false`
10. 保存后立即停止。不要在本次 Codex 生成阶段调用 ICP 或对齐脚本；服务器包装器只运行纯坐标恢复、保存后 Blend 指纹检查和 FBX 导出，不运行方向检查或 FBX 回读。

不得宣称 accepted、final_pass、validated 或 game_ready。最终状态仍是交给用户检查，但服务器会附加坐标对齐校验。
