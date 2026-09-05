# ModelViewCreator Roughness API

- 业务仓库：`/opt/modelviewcreator`
- 固定提交：`d318bb392040e2d5f6bbd10ae61d832d36d3cb4a`
- 原始 UI 工作流：`材质还原对照流(roughness) .json`
- 原始工作流 SHA-256：`8a52740b90ac47e77919b460a0e35241c94d91fde035effb3285600642e2ea38`
- 用户输入：multipart 字段 `image`（必填）
- 图片注入：`image_filename -> 323.inputs.image` (`LoadImage #323`)
- 固定提示词：保留在 `TextEncodeQwenImageEditPlus #332`，API 不允许覆盖
- 唯一业务输出：`PreviewImage #355`
- API 模板 SHA-256：`5752acb2c37dece0dd514a5e75104661fd12f06a19e70d6e190d17155599d865`
- GPU Control 版本：`2026.08.12-d318bb39-roughness-12g-r1`
- 最低调度显存：`12000 MiB`；原模板已在 4070 Ti WSL2 实机完成并产出节点 `#355`。

GPU Control 只把每个请求的隔离输入文件名注入不可变模板，不修改提示词、
模型、采样参数、节点拓扑或输出语义。API 只返回 `VAEDecode #330` 进入
`PreviewImage #355` 的最终粗糙度图，不得返回预览过程或中间产物。

12 GiB 版本只调整 GPU Control 的调度准入值，不修改外部业务工作流内容。
