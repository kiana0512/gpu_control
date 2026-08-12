# ImageClip RGBA API

- 业务仓库批准提交：`c39ed0b3b637f0a1435bbe10e5a3acf6bfca07bd`
- 管线组合 SHA-256：`07928d57852ed56ed37527960ec9955d867c0090456fda687fbcd12fecf1775c`
- GPU Control 版本：`2026.08.12-c39ed0b-fp8-r1`
- 参数注入：`image_filename -> 108.inputs.image`（`LoadImage #108`）
- 唯一最终输出：`SaveImage #102`
- UNET：`diffusion_models/flux-2-klein-9b-fp8.safetensors`
- 最低调度显存：`12000 MiB`，因此 12 GiB 4070 Ti 可参与执行。

API 模板是批准的上游 `c39ed0b` 工作流导出的 18 节点祖先子图。GPU Control 不修改
工作流节点、模型、提示词、采样参数、图拓扑或输出语义；运行时只替换上传图片文件名。

四个节点的签名心跳必须同时满足 `imageclip_commit` 和
`imageclip_pipeline_sha256` 两个标签，才允许领取此版本。最终产物严格从
`SaveImage #102` 获取，禁止把预览或中间结果作为批次产物。

2026-08-12 已在 4090、3090-A、3090-B、4070 Ti 上对齐相同提交、相同组合哈希、
相同 FP8 模型和自定义节点；旧 `691770c / SaveImage #25 / Q6_K` 版本已禁用。
