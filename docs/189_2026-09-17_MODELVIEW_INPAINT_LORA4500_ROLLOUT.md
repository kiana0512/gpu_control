# 2026-09-17 局部重绘 LoRA 与默认提示词部署

用户明确授权替换 `modelview-inpaint` 的 LoRA 为本机下载目录中的
`li3d_000004500.safetensors`，并将指定的 973 字符英文提示词写入工作流。
前端输入合同随后明确为：填白后的预览效果图、参考图、外扩蒙版，输出一张生成效果图。

## 变更范围与文件身份

- 新工作流版本：`2026.09.17-li3d4500-defaultprompt-steps2-r1`。
- 旧版本：`2026.08.29-cba4414-truev3-gguf-mask-4input-rseed-steps2-r1`，保留记录并停用。
- API 模板只有两处执行值变化：`21.inputs.lora_name` 与 `60.inputs.text`。
- UI 工作流同步修改节点 21 的 LoRA 文件名、节点 60 的提示词内容。
- `LoraLoaderModelOnly` 强度仍为 0.9，`BasicScheduler` 仍为 2 步，三图绑定和最终输出节点 29 不变。
- 其他工作流（包括单视图生成、单视图局部重绘）未修改。
- 新 LoRA：165704368 字节，SHA-256 `80c1e864ebe62c2a5e975385e0b70d2d7c7fb679cc39cb8f458a12c46cb3343e`。
- 新旧 LoRA 均含 224 个张量，名称、形状和 dtype 完全匹配。
- UI SHA-256：`c213d736d052c45fd8585ff7c4badc9a6b548de2c28254b220c44cad9a67099b`。
- API 规范化模板 SHA-256：`15d2b39358a37ffe0260fde03124b3ec73aac2a531339c2fe33e57d7206eb067`。

四台节点的规范 LoRA 路径为
`/opt/modelviewcreator/model/lora/flux-kelin/li3d_000004500.safetensors`。
UI 文件为 `/opt/modelviewcreator/Flux2 Klein TrueV3-双图材质编辑-局部重绘.json`，
挂载到 ComfyUI `/opt/comfyui/user/default/workflows/ModelViewCreator_flux_fill_inpaint.json`。
四机 LoRA 和容器可见 UI 文件均逐一校验 SHA-256 一致。
旧模型保留，未上传 Git/LFS 或模型仓库；未重启 ComfyUI。

## 实际生成验收

以最近一条成功的局部重绘生产任务三图作为受控回归输入，使用新模板内置提示词和测试 seed 4500。
逐节点设置 DRAINING，等待 GPU/Asset 任务和 ComfyUI 队列清空后提交；完成后恢复 ACTIVE。
四次均成功，最终节点只返回一张可解码 PNG，大小均为 2048×2048，与输入一致。

| 节点 | prompt_id | 验收耗时（提交至取回图像） |
| --- | --- | --- |
| control-4090 | `8495fba3-e527-45f9-9f5d-2a7c65dd222e` | 10.94 秒 |
| worker-3090-a | `f13946cf-8c46-4e37-b1e4-277cd88ff85a` | 15.30 秒 |
| worker-3090-b | `d07f6678-a329-4d86-8551-85c5c277095d` | 126.77 秒 |
| worker-5070ti-01 | `00e500b5-3eea-47ac-956e-983213369877` | 55.75 秒 |

这些耗时仅代表本次样例及缓存状态，不承诺正常请求延迟。3090-B 仍较慢，本次没有修改其运行环境。
验收覆盖加载新 LoRA、执行新提示词、完整采样和最终输出；没有宣称所有保护区逐像素一致或
所有模型的材质效果已通过人工评审。

5070 Ti 的 16GB 调度例外已用本次真实生成结果重新验收，档案绑定新版本、模板 SHA-256、
该 GPU UUID 和新证据 SHA-256；原运行配置未变化。未放宽全局 24000 MiB 声明显存门槛。
12GB 4070 Ti 保持不兼容此工作流。

验收成功后，在同一数据库事务内启用新版本并停用旧版本，写入发布审计。
最终四台兼容节点均为 ACTIVE/ONLINE，旧任务快照保留。

## 证据与回退

- 发布报告、每机结果 JSON 和 PNG：`/srv/gpu-control/jobs/deployment-modelview-lora4500-20260917/`。
- 发布脚本：`scripts/deploy_modelview_lora4500_20260917.py`，为本次定向发布脚本，已有版本不会重复导入。
- 原 API/UI 文件备份：`backups/modelview-inpaint-li3d4500-20260917/`。
- 节点模式、5070 Ti 前后资源档案和工作流发布操作均已写入 `audit_logs`。
- 回退需恢复旧启用版本及 UI 文件，并恢复 5070 Ti 对应旧模板的验收档案；旧 LoRA 文件仍保留。

## 前端交付

[三张图输入前端对接 MD](188_2026-09-17_MODELVIEW_INPAINT_LI3D4500_FRONTEND_HANDOFF.md)。
另存一份到 `/home/lilithgames/下载/局部重绘-三图输入-前端对接-20260917.md`。
新调用方不要发送 `prompt` 或 `parameters.prompt`，以采用新工作流默认提示词。
现有显式提示词覆盖能力仍兼容，因此旧前端需要去掉旧提示词上传。
