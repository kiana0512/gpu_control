# 工作流接入

1. 在与生产镜像相同版本的 ComfyUI 中打开并验证工作流。
2. 使用 **Export Workflow (API)**；若 JSON 顶层有 `nodes`/`links`，这是 UI 格式，必须重新导出。
3. 保存模板，不写服务器绝对路径、Secret 或输入文件名。
4. 创建 manifest：声明 `workflow_key/version`、JSON Schema、参数到 `<node_id>.inputs.<name>` 的 bindings、允许的 `class_type`、模型、自定义节点、最低显存、输出节点和超时。
5. `python -m packages.gpu_control_core.workflow_cli validate MANIFEST`；再用 `diff` 检查新旧版本。
6. 管理台“工作流”导入。系统会拒绝未知节点类型、危险 binding、UI JSON 和 schema 外参数。
7. 先保持 disabled；在每个 3090 上检查 `/object_info` 与 `/models`，生成兼容性结果后仅灰度启用新版本。
8. 用非敏感测试图提交一次，核对输出 SHA、历史 JSON、任务事件与日志链路，再逐步放量。

版本不可原地覆盖；变更必须创建新 version。失败回滚为禁用新版本并重新启用上一版本。真实工作流未提供，仓库不会伪造生产 JSON。

