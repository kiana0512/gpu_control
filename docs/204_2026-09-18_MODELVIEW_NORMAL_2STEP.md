# 局部重绘：法线参考版采样 4 → 2 步

2026-09-18，用户明确要求仅调整局部重绘采样步数。

- 已启用版本：`2026.09.18-refcontrol-normal-2step-r1`。
- 唯一执行差异：`BasicScheduler #15.inputs.steps` 从 4 改为 2。
  已逐项断言原生产模板修改该值后与新模板完全相等；UI 图也做了同样的深比较。
- simple、denoise=1、两套 LoRA 强度、默认提示词、四图输入及单图输出均未变。
- 单视图生成仍为 4 步，单视图局部重绘仍为 2 步，两个工作流版本不变。
- 前端不用修改，API/调度器/ComfyUI 均未重启；已有任务仍使用其冻结快照。
  新生成使用新的 Idempotency-Key 才创建新版本任务，旧任务重试不更换版本或 seed。

## 同步与验证

4090、3090-A、3090-B 均排空后验证，三次四图真实生成成功，唯一输出为最终 #29 的
2048×2048 PNG。对应本次样例耗时 20.31 / 30.34 / 35.31 秒，仅代表当次机器/缓存状态。
5 项工作流契约测试通过。三机可见 UI 哈希一致；发布后均恢复 ACTIVE/ONLINE。
5070 Ti 仍 OFFLINE，未同步也未获得新版本兼容性放行。

UI SHA-256：`81c7f6b2f427799ee0f185dbf817a29e5d6df5f618de8c31fe6ccf5aabbe0bdf`。
数据库 template_digest：`65b0c6d0a3445d8318c411fd421b1721e33991494bd5b63d770bbcf29b67da7f`。

证据目录：`/srv/gpu-control/jobs/deployment-modelview-normal-2step-20260918/`，
含每机 prompt_id、输入输出哈希、结果 PNG、verification.json 和 release.json。
脚本归档：`scripts/modelview_normal_2step_20260918/`。

旧 4 步版本停用但保留，备份 `backups/modelview-normal-2step-20260918/`；
远端 UI 备份 `/tmp/modelview-normal-before-2step-20260918.json`。
回退仅需恢复旧工作流启用状态和三机 UI，不涉及 API 镜像或模型。

已将工作流 README、契约测试、202 号前端文档及下载目录内该 MD 的参数说明更新为 2 步。
原 203 号发布报告保留为此前 4 步发布的历史记录；当前状态以本记录及数据库为准。
没有提交或推送 Git，没有新增模型或占用 Git LFS。
