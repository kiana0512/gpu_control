# 自动拓扑 Direct V2 服务端与客户端交接

日期：2026-08-05  
状态：替换 V6 默认运行路径  
外部提交包：`blender-retopology-compare-iterate-server-package-v2.0.0.zip`  
原始包 SHA-256：`5f8e66324f3bf9a804699b8976b2938ffc88cf7b7f68ef7bc4bfe4d9e38242ef`

## 1. 当前唯一运行语义

`POST /api/v1/assets/retopology/process` 的地址、鉴权、幂等键、任务查询、事件查询和取消接口保持兼容，内部执行引擎改为：

- `engine_contract: retopology-direct-v2`
- `package_version: 2.0.0`
- 高模是唯一形状依据；不使用旧低模或固定模板。
- 每个高模只生成一个候选低模。
- 生成后保存一次并立即停止，状态为 `generated_for_user_inspection`。
- 不运行旧 V6 独立 QA、七视图评分、自动修正、自动重开或第二次建模。
- 用户显式要求最终交付格式为 FBX；Blend 只作为 Worker 内部生成与确定性导出中间文件，不是用户正式交付。

## 2. 输入兼容

客户端仍可上传 `.blend`、`.fbx`、`.obj`、`.glb` 或 `.gltf`。非 Blend 输入由 Worker 只做格式归一化，随后交给 Direct V2；不会修改用户上传源文件。

旧的 `target_faces` 等手动面数字段继续按兼容规则忽略。最终密度和构造方法由 Direct V2 Agent 根据高模结构决定。

## 3. 正式交付

成功任务状态为 `SUCCEEDED`，`delivery_ready=true`。正式模型文件为：

- `<源文件名>_GAME_LOW.fbx`，artifact kind 为 `fbx`。

同时返回审计与排障证据：

- `generation_report.json`
- `delivery_manifest.json`
- `result.json`
- `agent_events.jsonl`
- `wrapper_events.jsonl`

`delivery_manifest.json` 固定记录源文件 SHA、归一化 Blend SHA、Agent 生成 Blend SHA、最终 FBX SHA/大小、低模对象名、包 SHA，以及明确的 `automatic_post_generation_review=false` 和 `automatic_retry=false`。

## 4. 用户端显示规则

- 任务名称：`Direct V2 自动拓扑`
- 成功文案：`FBX 已交付 · 等待检查`
- 不得显示“严格 QA 通过”或把用户检查前的候选宣称为 `accepted`、`validated`、`game_ready`。
- 失败时显示真实执行阶段和错误码；不得伪造 QA 失败。

## 5. 调度与更新

只有心跳上报 `skill_version=blender-retopology-direct-v2.0.0` 的 Worker 可以领取 `RETOPOLOGY_PROCESS_V2`。旧 Worker 不得领取新任务。更新采用逐节点 `DRAINING -> 镜像替换 -> 健康与包哈希检查 -> ACTIVE`，不影响 GPU 推理、UV 或 PBR Baker。

旧 V6 代码只保留在 Git 历史和回滚镜像中，不再注册生产完成接口，也不在新 Worker 镜像中安装。
