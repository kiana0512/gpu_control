# 单视图生成采样步数改为 2

用户明确批准仅将单视图生成的采样步数由 4 改为 2。

- 工作流键：`modelview-single-view`。
- 新版本：`2026.09.20-refcontrol-normal-single-view-2step-r1`。
- 唯一推理参数变更：`BasicScheduler #15.inputs.steps: 4 → 2`。
- UI 与 API 图分别做结构比对，除该参数外保持相同。提示词、两套 LoRA、三图输入、随机 seed、输出节点及分辨率均不变。
- `modelview-inpaint`、`modelview-single-view-inpaint` 未修改。
- 前端接口不用变，不需要新传任何参数。

## 部署与验证

4090、3090-A、3090-B、5070 Ti 四台均经过排空、真实生成、正式 UI 同步、容器内哈希校验。
无需重启 ComfyUI 或 API，无 Docker 镜像变更。云节点原本不兼容此工作流，本次未扩展其路由范围。

| 节点 | 两步验证 prompt_id | 单次提交到取回耗时 |
| --- | --- | --- |
| control-4090 | `03ab3f2c-8880-4f5a-af1b-f21ded5b194d` | 20.25 秒 |
| worker-3090-a | `0fbde107-933c-478b-acce-24dd07ecde9a` | 35.31 秒 |
| worker-3090-b | `bd206a7a-928a-4e43-9911-0f667e14a01a` | 75.35 秒 |
| worker-5070ti-01 | `f8326872-2a4f-4124-86ff-02cb05efd5cf` | 65.21 秒 |

四次均为最终单张 2048×2048 PNG。此耗时包含模型加载等开销，不是稳定延时承诺。
单元测试 2 passed。发布时无旧版待执行任务，旧版停用，保留数据库记录。
正式业务 API 回归任务 `593e69c7-b9a0-478b-801b-009198fd8335` 在 4090 成功，
16.11 秒返回 PNG；核对落盘任务版本为新版、渲染图节点 15 步数确为 2。
四台节点最终均 ACTIVE/ONLINE。
5070 Ti 的单视图显存验收档案绑定新版本、模板摘要、新实测证据和当前 runtime 摘要；其余档案不动。

- API 模板标准化 SHA：`ffb261b21a03cd8512cb6541c64da0958cd18a5fbf2c992dda74f6bc17df45ea`。
- 正式 UI SHA：`02ffdd63bde091469604e250e761853181450d94d9a01b6691c486b7c8177fc5`。
- 原 UI 备份：每台机器 `/opt/modelviewcreator/ModelViewCreator_flux_Single-view Generation.json.before-single-2step-20260920`。
- 证据：`/srv/gpu-control/jobs/deployment-single-view-2step-20260920/`。
- 发布脚本：`scripts/modelview_single_2step_20260920/`，仅作本次发布复现与审计，不能盲目跨版本重跑。

接口文档的后端版本/步数说明已经更新：[单视图生成前端对接](207_2026-09-18_SINGLE_VIEW_NORMAL_FRONTEND.md)。
用户“下载”目录中的对应文档也已同步。未推送 Git 或 LFS。
