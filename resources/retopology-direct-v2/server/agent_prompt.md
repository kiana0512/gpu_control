# Blender FBX 一键拓扑服务器任务

使用 `$blender-retopology-compare-iterate` 完成任务。必须读取任务 `CODEX_HOME` 中该技能的
完整 `SKILL.md`，并按技能要求读取 references；不要用简化提示词代替技能。

任务参数：

- 用户上传源文件：`{{INPUT_SOURCE}}`
- 已准备的拓扑工作 Blend：`{{WORKING_BLEND}}`
- FBX 源清单：`{{SOURCE_MANIFEST}}`
- 输出 Blend：`{{OUTPUT_BLEND}}`
- 高模对象：`{{HIGH_OBJECTS}}`
- Blender：`{{BLENDER_EXECUTABLE}}`
- 任务目录：`{{JOB_DIR}}`

FBX 输入已经由技能自带的 `prepare_fbx_source.py` 导入为工作 Blend，并固定为
`SOURCE_HIGH`。不要重新导入 FBX，不要创建参考低模、当前低模、Decimate bootstrap 或
通用代理。

`SOURCE_HIGH` 的 joined 状态不能作为“一体有机模型”的证据，因为 FBX 预处理会主动合并
导入的 Mesh。选择方法前必须检查断开的网格岛、截面系统、开口、遮挡关系和结构区域。受控
直接减面仍然保留，但只允许用于证据充分的一体有机区域；若高模包含碗、桶、托盘、箱体等
结构外壳和不规则内容物，必须采用组件混合：外壳按高模截面结构重建，只在内容物区域使用
受控直接减面、合格重网格或高模派生笼，禁止把两者作为整个 `SOURCE_HIGH` 一次性减面。

必须真正执行 Blender 并生成输出文件，不能只给方案、代码或文字说明：

1. 只读打开工作 Blend；其中指定高模是唯一形状依据。
2. 创建低模前完成测量、方法选择和 shape-authority plan；写入 `{{JOB_DIR}}/plans/`，并运行
   技能的 `guard_shape_authority_plan.py`。FBX 计划需记录原始 FBX、工作 Blend 和源清单。
3. 每个指定高模只生成一个低模；按技能规则决定结构重建、受控直接减面或组件混合。不得因
   `SOURCE_HIGH` 只有一个对象就选择整物体直接减面。组件混合计划必须写
   `component_method_map`，分别记录结构区域和不规则区域的方法及高模边界证据。
4. 大平面和非轮廓区保持极简，把面数用于轮廓、截面变化、开口、负空间和关键连接。
5. 低模一旦创建，只设置黄色实体/wire、平移摆排、保存并记录创建时已有数量；不得自动
   复查、渲染、评分、重开、修正、重试或生成第二版。
6. 高模保持不变且可见，把场景保存到 `{{OUTPUT_BLEND}}`。
7. 同一次 Blender 构建结束前写 `{{JOB_DIR}}/generation_report.json`，包含每个高模和低模
   名称、faces、triangles、method_decision、actual_plugin_use，以及
   `status: generated_for_user_inspection`。
8. 保存成功后立即停止，不宣称 accepted、final_pass、validated 或 game_ready。

服务器无交互窗口时使用给定 Blender 执行 headless Python。允许在创建几何前测量；正式
builder 对每个高模只运行一次，生成后不得重新读取网格做质量审查。
