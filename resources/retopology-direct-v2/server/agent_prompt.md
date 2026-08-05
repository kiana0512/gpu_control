# Blender 一键拓扑服务器任务

使用 `$blender-retopology-compare-iterate` 完成这次任务。必须读取已安装技能的完整
`SKILL.md`，并按其中要求读取相关 references；不要凭一段简化提示词替代技能。

任务参数：

- 输入 Blend：`{{INPUT_BLEND}}`
- 输出 Blend：`{{OUTPUT_BLEND}}`
- 需要处理的高模对象：`{{HIGH_OBJECTS}}`
- Blender 可执行文件：`{{BLENDER_EXECUTABLE}}`
- 任务目录：`{{JOB_DIR}}`

必须真正执行 Blender 并生成输出文件，不能只给方案、代码或文字说明。

执行要求：

1. 输入文件只读使用；高模是唯一形状依据，不使用旧低模、通用代理或固定模板尺寸。
2. 在创建低模前完成高模测量、方法选择和每个对象的 shape-authority plan；把计划写入
   `{{JOB_DIR}}/plans/`，并使用技能自带的
   `scripts/guard_shape_authority_plan.py` 验证。验证失败就停止，不生成低模。
3. 每个指定高模只生成一个低模。机械/硬表面按结构重建；只有技能明确允许的有机一体
   模型才从当前高模的新副本受控减面；混合物体按组件决定。
4. 大平面和非轮廓区保持极简，面数用于轮廓、截面变化、开口、负空间、连接根部和关键结构。
5. 低模一旦创建，只允许设置黄色实体和 wire、按要求平移摆排、保存、记录创建时已有的
   对象/面/三角面数量。不得自动复查、渲染、评分、重开、修正、重试或生成第二版。
6. 高模保持不变且可见；低模命名独立。把最终场景保存到 `{{OUTPUT_BLEND}}`。
7. 在同一次 Blender 构建脚本结束前写入 `{{JOB_DIR}}/generation_report.json`，至少包含：
   每个高模及对应低模名称、faces、triangles、method_decision、actual_plugin_use，以及
   `status: generated_for_user_inspection`。
8. 保存成功后立即停止。不要宣称 accepted、final_pass、validated 或 game_ready。

服务器没有交互式 Blender 窗口时，可用给定 Blender 可执行文件执行 headless Python。
允许在创建几何前做测量调用；正式 builder 对每个高模只运行一次。不要在生成后再读取网格
做质量审查。
