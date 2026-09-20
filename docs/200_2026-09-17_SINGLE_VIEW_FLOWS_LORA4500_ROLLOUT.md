# 单视图生成与单视图局部重绘：LoRA / 固定提示词更新

用户明确授权将这两个工作流换成与局部重绘相同的 `li3d_000004500.safetensors` 和
973 字符英文提示词。本次仅更新两套流程的 LoRA 文件名、固定提示词及发布版本。

## 工作流

| 工作流 | 新版本 | 固定提示词节点 | 原参数保留 |
| --- | --- | --- | --- |
| modelview-single-view | `2026.09.17-li3d4500-single-view-4step-r1` | 40 | 4 步，LoRA 强度 0.8 |
| modelview-single-view-inpaint | `2026.09.17-li3d4500-single-view-inpaint-2step-r1` | 62 | 2 步，LoRA 强度 0.9 |

新 LoRA SHA-256：`80c1e864ebe62c2a5e975385e0b70d2d7c7fb679cc39cb8f458a12c46cb3343e`。
四机复用上一轮已同步的规范文件 `/opt/modelviewcreator/model/lora/flux-kelin/li3d_000004500.safetensors`，
未重复下载或上传 Git/LFS。

单视图 UI SHA-256：`a94a44a04554394a4d412aec8766470cc4fc8cd182841c7da351321ebfd63dfc`。
单视图局部重绘 UI SHA-256：`deae8f69d18ad63c603123701c89a647e8889fbc50fa7acc5847d82ca912e240`。
对应规范化 API 模板 SHA-256：

- 单视图：`ade31a2df3549ba22916982fdcf9cc3b70396ec82c87db0ca192cc29524e5681`。
- 单视图局部重绘：`287d631d3bd53d975cef519f421eabe85544c4d39bb3f9c659f6a2c4b113aec9`。

逐项结构对比确认每套 API 模板仅两处执行值变化：节点 21 的 `lora_name` 和各自固定
提示词节点的 `text`；新提示词与 `modelview-inpaint` 节点 60 逐字符一致。
UI 挂载文件与 LoRA 在 4090、3090-A、3090-B、5070 Ti 上逐一 SHA-256 一致。

保留用户补充提示词节点 41 / 61 及原有拼接节点，默认补充为空。前端省略提示词时只使用
新固定文字；旧前端传来的补充文字仍会拼接，故新文档要求删除历史提示词自动上传。
未修改普通局部重绘、采样连接、输入输出、主模型或业务 API 代码。

## 部署及验证

发布脚本：`scripts/deploy_modelview_single_flows_lora4500_20260917.py`，通过
`MODELVIEW_ROLLOUT_KEY` 分别执行两套发布，限制为上述两个 key。
每套先导入不可变禁用版本，在四节点分别排空后执行一条受控真实生成验收；全部通过后
事务内启用新版、停用旧版，更新 5070 Ti 专用资源验收档案并写审计。
其他节点的显存门槛未放宽，12GB 4070 Ti 保持不兼容。

验收使用各自最近成功任务的两张/三张输入，seed 固定 4500，只用于受控测试。
每次验证成功状态、最终节点 29 仅一张图、PNG 可解码、输出 2048×2048 与第一张图一致。
不将该检查当作任意输入的视觉质量或保护区逐像素一致性保证。

发布报告、每机 JSON 和结果图保存在：

- `/srv/gpu-control/jobs/deployment-modelview-single-view-lora4500-20260917/`
- `/srv/gpu-control/jobs/deployment-modelview-single-view-inpaint-lora4500-20260917/`

各目录 `release.json` 是发布成功后的最终记录，包含版本、兼容表、4 次验收的 prompt ID、
耗时、输入输出哈希。未重启 GPU/ComfyUI 或控制服务。

最终两套新版本均已启用，8 次真实生成全部通过，四台兼容节点均恢复 ACTIVE/ONLINE。
本次提交至取回图像耗时如下，仅代表样例及当时缓存状态：

| 节点 | 单视图生成 | 单视图局部重绘 |
| --- | --- | --- |
| 4090 | 15.20 秒 | 10.26 秒 |
| 3090-A | 25.23 秒 | 15.44 秒 |
| 3090-B | 155.97 秒 | 83.85 秒 |
| 5070 Ti | 62.94 秒 | 40.12 秒 |

## 回退及前端文档

原版本和模型保留。原 API/manifest/UI 备份：
`backups/modelview-single-flows-li3d4500-20260917/`。
回退时恢复对应旧启用版本、UI 文件和 5070 Ti 的旧版资源验收档案，不删除新模型。

- [单视图生成前端对接](198_2026-09-17_SINGLE_VIEW_LORA4500_FRONTEND.md)：两张图片输入。
- [单视图局部重绘前端对接](199_2026-09-17_SINGLE_VIEW_INPAINT_LORA4500_FRONTEND.md)：填白预览、参考图、外扩蒙版三张输入。

两份均已另存到用户下载目录，说明了真实接口、字段、内置提示词、幂等、错误处理与示例。
