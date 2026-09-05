# ModelViewCreator Flux2 Klein TrueV3 单视图生成 API

- 业务仓库：`/opt/modelviewcreator`
- 用户批准的 UI 工作流：`ModelViewCreator_flux_Single-view Generation.json`
- UI 工作流 SHA-256：
  `c0e6218a599da460124cc955e2d3fb3125293808f541a67b0e97fabe17579d33`
- GPU Control 工作流键：`modelview-single-view`
- API：`POST /api/v1/services/modelview-single-view`

公开输入保持为两张图片加一个可选提示词：

- `image`：白模图；
- `material_image`：参考多视图；
- `prompt`：可选文字要求，最长 4096 字符；
- `noise_seed`：由服务端为每个新任务生成，调用方不能传入。

最终输出仍取节点 `29`。UI 里的预览节点在 API 模板中仅替换为标准
`SaveImage`，其输入图像连接不变。工作流使用 `BasicScheduler #15 steps=4`；与
`modelview-inpaint` 共用同一 ModelView 模型缓存家族，但工作流身份、版本、任务记录、
幂等键和统计保持独立。

该工作流最低显存为 24000 MiB，仅部署到 4090、3090-A、3090-B；4070Ti 不兼容。
