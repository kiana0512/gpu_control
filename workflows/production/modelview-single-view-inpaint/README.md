# ModelViewCreator 单视图局部重绘 API

- 业务仓库：`/opt/modelviewcreator`
- 用户批准的 UI 工作流：`ModelViewCreator_flux_fill_inpaint -View generation.json`
- UI 工作流 SHA-256：
  `d49d6228c4d7d24f2280a110dfe60a57de20c3aba2070a76b88e17acb4d66238`
- GPU Control 工作流键：`modelview-single-view-inpaint`
- API：`POST /api/v1/services/modelview-single-view-inpaint`

公开输入为三张图片和一个可选提示词：

- `image`：当前效果图；
- `material_image`：参考图；
- `mask`：蒙版，读取红色通道，尺寸必须与 `image` 相同；
- `prompt`：可选文字要求，最长 4096 字符；
- `noise_seed`：由任务中心为每个新任务生成，调用方不能传入。

工作流内部将用户提示词节点 `61` 与不可由调用方覆盖的固定蒙版表面修复提示词
节点 `62` 通过 `Text Concatenate #63` 合并，再送入 `CLIPTextEncode #9`。最终输出
仍取 `CherryAlignReference #33` 的第二个输出。UI 的 `PreviewImage #29` 在 API 模板中
仅替换为标准 `SaveImage #29`，图像连接保持为 `["33", 1]`。

工作流使用 `BasicScheduler #15 steps=2`、LoRA 强度 `0.9`，与现有
`modelview-inpaint` 和 `modelview-single-view` 共用同一 ModelView 模型缓存家族，但
工作流身份、任务记录、幂等键和统计完全独立。

最低显存为 24000 MiB，仅部署到 4090、3090-A、3090-B；4070Ti 不兼容。

2026-09-05 仅更新节点 `62` 的默认固定提示词，强调蒙版只表示编辑区域、清除区域内
旧内容并无缝延续邻近表面；输入、输出、采样步数、模型和图结构均未改变。
