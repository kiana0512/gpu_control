# ModelViewCreator Flux2 Klein TrueV3 单视图生成 API

- 业务仓库：`/opt/modelviewcreator`
- 用户批准的 UI 工作流：`ModelViewCreator_flux_Single-view Generation.json`
- UI 工作流 SHA-256：
  `02ffdd63bde091469604e250e761853181450d94d9a01b6691c486b7c8177fc5`
- GPU Control 工作流键：`modelview-single-view`
- API：`POST /api/v1/services/modelview-single-view`

2026-09-20 版本：`2026.09.20-refcontrol-normal-single-view-2step-r1`。
用户批准将节点 15 的采样步数从 4 改为 2；其余图、提示词、LoRA 和接口保持不变。
LoRA 更新为 `flux-kelin/li3d_000004500.safetensors`，强度仍为 0.8。
固定提示词节点 40 已替换为用户批准的白色区域生成提示词（973 字符）。

新增法线 LoRA `flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors`，强度 0.8。
使用用户确认的 `ModelViewCreator_flux_Single-view Generation (1).json`。

新版前端提交三张图片，省略提示词。旧的补充提示词能力仍兼容：

- `image`：白模图；
- `material_image`：参考多视图；
- `normal_image`：必填法线渲染图，与 `image` 同尺寸、同视角、像素对齐；
- `prompt`：旧调用方可选补充文字，最长 4096 字符，与固定提示词拼接；新前端省略；
- `noise_seed`：由服务端为每个新任务生成，调用方不能传入。

最终输出仍取节点 `29`。UI 里的预览节点在 API 模板中仅替换为标准
`SaveImage`，其输入图像连接不变。工作流使用 `BasicScheduler #15 steps=2`；与
`modelview-inpaint` 共用同一 ModelView 模型缓存家族，但工作流身份、版本、任务记录、
幂等键和统计保持独立。

该工作流最低声明显存仍为 24000 MiB，部署到 4090、3090-A、3090-B。
5070 Ti 通过绑定当前版本、模板哈希的实际生成验收档案兼容；4070 Ti 不兼容。

法线节点 42 → VAEEncode 44（VAE 3）→ ReferenceLatent 45；LoRA 43 串接在原 LoRA 21 后。
通过专属验收标签限定四台已验证节点，新云节点不会自动承接此版本。

[最新前端对接文档](../../../docs/207_2026-09-18_SINGLE_VIEW_NORMAL_FRONTEND.md)。
