# ImageClip RGBA API

- 业务仓库批准提交：`c39ed0b3b637f0a1435bbe10e5a3acf6bfca07bd`
- 管线组合 SHA-256：`07928d57852ed56ed37527960ec9955d867c0090456fda687fbcd12fecf1775c`
- GPU Control 版本：`2026.08.17-c39ed0b-colorfix-r1`
- 参数注入：`image_filename -> 108.inputs.image`（`LoadImage #108`）
- 唯一最终输出：`SaveImage #109`
- 最终处理链：`CherryHoldoutSimple #116 -> CherrySelfComposite #105 ->
  122_ColorMatchToSource #123 -> CherryAlphaDenoise #119 -> SaveImage #109`
- UNET：`diffusion_models/flux-2-klein-9b-fp8.safetensors`
- 最低调度显存：`12000 MiB`，因此 12 GiB 4070 Ti 可参与执行。

API 模板是批准的上游 `c39ed0b` 工作流中 `CherryMirrorSave #109` 最终分支导出的
21 节点祖先子图。Windows 文件夹镜像保存节点只在 UI 中有意义，API 适配层保留它的
最终 IMAGE 输入，并改用标准 `SaveImage #109` 返回便携 PNG；运行时只替换上传图片文件名。

四个节点的签名心跳必须同时满足 `imageclip_commit` 和
`imageclip_pipeline_sha256` 两个标签，才允许领取此版本。最终产物严格从
`SaveImage #109` 获取，禁止把校色前的 `SaveImage #102`、预览或其它中间结果作为产物。

2026-08-17 已确认 4090、3090-A、3090-B、4070 Ti 均注册
`122_ColorMatchToSource`、`CherryAlphaDenoise`、`CherrySelfComposite`。旧
`2026.08.12-c39ed0b-fp8-r1 / SaveImage #102` 只返回校色前分支，已由本版本替代。
