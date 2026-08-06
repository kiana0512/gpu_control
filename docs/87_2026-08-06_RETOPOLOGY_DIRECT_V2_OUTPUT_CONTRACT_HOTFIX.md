# Direct V2 输出契约热修复

日期：2026-08-06  
范围：仅自动拓扑 Direct V2

## 故障与根因

生产任务 `f35e0a93-c44d-42f0-b896-f9528bc9cbd4` 首先因进度阶段名超出接口的
32 字符限制在 2% 失败。接口兼容修复上线后，原任务已跨过输入归一化，但 Direct V2
Agent 在 29% 结束且没有写出约定的 Blend，最终返回
`BLENDER_EXECUTION_FAILED / codex_output_blend_missing`。这不是 QA 拦截，也不是输入模型上传失败。

## 修复

- 明确服务器 headless Blender 已授权，Agent 不得等待交互式 bridge。
- 未指定高模对象时，`ALL_HIGH_MESH_OBJECTS` 按输入 Blend 内的 Mesh 自动识别。
- Agent 结束前必须确认输出 Blend 和 `generation_report.json` 均存在且非空。
- 仍保持单次 builder、无自动 QA、无自动建模重试。
- 输出缺失时保留有界的 Agent 事件末尾，用于定位真实执行错误。

## 发布约束

结构化 Agent 事件进一步确认输出缺失的底层原因为 Worker 只挂载 Codex 主程序，
但没有提供同一 multi-call 二进制的 `codex-linux-sandbox` 入口，导致 workspace-write
沙箱在读取 Skill 前就失败。最终 Worker 镜像固定提供该伴生入口。

新 Worker 标签为 `li3d/blender-worker:1.3.5-retopo-direct-v2-sandbox`。每个节点必须在
`current_jobs=0` 时滚动替换；不重启 ComfyUI，不清理模型缓存，不修改 UV、PBR、
序列帧抠图或 Scheduler。
