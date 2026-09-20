# ModelViewCreator 单视图局部重绘 API

- 业务仓库：`/opt/modelviewcreator`
- 用户批准的 UI 工作流：`ModelViewCreator_flux_fill_inpaint -View generation.json`
- UI 工作流 SHA-256：
  `1b078109ac3342c00dd2ae013561084f680439fb92e56ba001e4c591d9fb5fa6`
- GPU Control 工作流键：`modelview-single-view-inpaint`
- API：`POST /api/v1/services/modelview-single-view-inpaint`

2026-09-18 版本：`2026.09.18-refcontrol-normal-single-view-inpaint-2step-r1`。
LoRA 更新为 `flux-kelin/li3d_000004500.safetensors`，强度仍为 0.9。
固定提示词节点 62 已替换为用户批准的白色区域生成提示词（973 字符）。

新增法线 LoRA `flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors`，强度 0.8。
采用用户确认的 `ModelViewCreator_flux_fill_inpaint -View generation (2).json`，
其中法线 VAEEncode 66 的 VAE 已连接节点 3，不使用缺少连接的 (1) 版本。

新版前端提交四张图片，省略提示词；旧的补充提示词能力仍兼容：

- `image`：待生成区域填成纯白后的当前预览效果图；
- `material_image`：参考图；
- `mask`：调用方已外扩的蒙版，读取红色通道，尺寸必须与 `image` 相同；
- `normal_image`：必填法线渲染图，与 `image` 同尺寸、同视角、像素对齐；
- `prompt`：旧调用方可选补充文字，最长 4096 字符，与固定提示词拼接；新前端省略；
- `noise_seed`：由任务中心为每个新任务生成，调用方不能传入。

工作流内部将用户提示词节点 `61` 与不可由调用方覆盖的固定白色区域生成提示词
节点 `62` 通过 `Text Concatenate #63` 合并，再送入 `CLIPTextEncode #9`。最终输出
仍取 `CherryAlignReference #33` 的第二个输出。UI 的 `PreviewImage #29` 在 API 模板中
仅替换为标准 `SaveImage #29`，图像连接保持为 `["33", 1]`。

工作流使用 `BasicScheduler #15 steps=2`、LoRA 强度 `0.9`，与现有
`modelview-inpaint` 和 `modelview-single-view` 共用同一 ModelView 模型缓存家族，但
工作流身份、任务记录、幂等键和统计完全独立。

最低声明显存保持 24000 MiB，部署到 4090、3090-A、3090-B。
5070 Ti 通过绑定当前版本、模板哈希的实际生成验收档案兼容；4070 Ti 不兼容。

历史：2026-09-05 的固定表面修复提示词已在 2026-09-17 被用户指定的白色区域生成提示词替换。
本次新增法线 64 → VAEEncode 66（VAE 3）→ ReferenceLatent 67，以及串接 LoRA 65；
步数与输出节点保持不变。通过专属验收标签限定四台已验证节点，新云节点不会自动承接此版本。

[最新前端对接文档](../../../docs/208_2026-09-18_SINGLE_VIEW_INPAINT_NORMAL_FRONTEND.md)。
