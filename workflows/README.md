# 工作流注册目录

这里只保存经过批准的 ComfyUI **Export Workflow (API)** 模板、参数 schema、manifest 和
最小合同说明，不把 UI 普通保存格式复制进控制仓库。生产包位于 `production/`；原始 UI JSON
继续由各业务仓库管理并以 SHA-256 锁定。导入步骤见
[`docs/10_WORKFLOW_ONBOARDING.md`](../docs/10_WORKFLOW_ONBOARDING.md)。

当前 ModelView 生产任务包括：

- `modelview-inpaint`：当前效果图、参考图、蒙版和可选提示词；
- `modelview-single-view`：白模、参考多视图和可选提示词；
- `modelview-single-view-inpaint`：当前效果图、参考图、蒙版和可选提示词，并保留固定视角/结构保护提示词；
- `modelview-roughness`：PBR 粗糙度生成。
