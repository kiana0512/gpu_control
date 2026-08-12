# Blender 训练技能自动拓扑并恢复原坐标

使用 `$blender-retopology-compare-iterate` 负责低模的结构分析与几何构建；使用
`$blender-auto-retopo-align` 只负责服务器坐标、输出和交付格式。完整读取训练拓扑技能的
`SKILL.md` 及其直接要求的四份 reference **一次**，不要反复读取。随后读取坐标/输出技能的
`SKILL.md` **一次**，但不得用它的快速包围盒代理规则代替训练拓扑规则。Worker 不保证安装
`rg`，不要调用 `rg`。

用户已明确取消交付前的方向审查、拓扑流审查、轮廓评分、FBX 回读和 UV 生成。训练技能中的
建形方法、轮廓/组件/开口/负空间保护规则仍适用；其审查、渲染、批次等待和用户确认步骤在本
无人值守任务中不执行，也不得成为交付门禁。本任务唯一几何门禁仍是最终 Blend 有效且没有
零面积/退化破面；这项用户策略优先于技能中的正式审查流程。

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
2. `semantic_measurements` 只提供坐标和总体尺寸，不能证明形状相似。必须直接检查工作 Blend 中 `SOURCE_HIGH` 的真实网格，识别主要轮廓、截面变化、开口、负空间、台阶、附件和接地结构；禁止根据对象名、文件名或全局 AABB 猜测模型身份，更禁止把任意模型简化成一个圆角盒、球、柱或其他通用基础体。为建形可执行一次有界的高模只读分析；优先文本几何/截面测量，确有必要时最多生成一次低分辨率高模工作台观察图。它只是生成输入，不是交付审查：不得渲染低模对比、不得评分、不得等待确认。完成这一次分析后立即构建，不得重复测量。可写简短计划作为诊断，但不得运行计划守卫，也不得让计划字段缺失中断生成。最终有效 Blend 和无破面结果才是交付门禁。
3. 每个指定高模只生成一个低模。方法只能是训练技能规定的 `semantic_reconstruction` 或 `hybrid_per_component`。必须保留决定物体身份的外轮廓、真实组件、开口、负空间、结构台阶和附件位置；宽平面保持稀疏，浅表纹理交给后续烘焙。不得使用全物体 Decimate、remesh、包围盒拟合或通用代理代替结构分析。任何自动降面只能作为内存中的密度参考，不得成为候选、导出物或 `SOURCE_LOW`。禁止焊接、降面或替换 `SOURCE_HIGH`。
   对原木、管束、栏杆、轮辐、支架等重复或细长组件，必须从同一次高模分析结果逐件锁定组件数量、中心、两端锚点、主轴/中心线、截面半径和相对层位；生成环必须以这些原始锚点为准。不得把组件重新均匀排列、交换顺序、合并为整体包络，也不得让曲线平滑、截面稀疏化或端盖生成移动端点、缩短长度、放大弯曲或造成原本没有的交叉。小表面纹理可以省略，但每个影响轮廓或负空间的大组件必须保持其高模中的位置和跨度。这些约束只指导一次生成，不增加第二次测量、视觉审查或交付门禁。
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
10. 保存后立即停止。不得运行训练技能的 pair audit、topology-flow audit、方向渲染、轮廓评分、ICP 或 FBX 回读；服务器包装器只运行纯坐标恢复、保存后 Blend 指纹检查和 FBX 导出。

不得宣称 accepted、final_pass、validated 或 game_ready。最终状态仍是交给用户检查，但服务器会附加坐标对齐校验。
