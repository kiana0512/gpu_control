# ImageClip RGBA API

- 业务仓库提交：`/opt/imageclip` `691770cd6a59fd7c51391456fe900dc57a313233`
- 管线文件哈希：`00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b`
- 用户输入：multipart 字段 `input_image`
- 参数注入：`image_filename -> 28.inputs.image` (`LoadImage #28`)
- 唯一业务输出：`SaveImage #25`
- API 模板修订：`r1` 正确跳过 ComfyUI 在 seed/PrimitiveInt 后序列化的
  `control_after_generate` UI 控制值；已通过 `/prompt` 实机校验，不再发生
  KSampler 参数错位。
- 输出来源：v4.3 最终发布节点 `CodexLazyShadowBypassV43 #57` 的 RGBA 图；其上游依次包含
  `CodexExistingBlackShadowJudgeV43 #56`、`CodexFootPasteGuardV42 #55`、
  `CodexShoePixelProtectV41 #54`、`122_ShadowBranchDecide #53` 与
  `122_FootRegionPaste #52`

API 模板只保留 `SaveImage #25` 的祖先子图，并拒绝出现第二个输出节点。中间保存、
预览、对比和镜像保存节点不会进入 API prompt，避免任务产物混入黑底图、中间抠图
或调试预览。UI 工作流的版本真相仍位于 ImageClip Git 仓库。

调度器还会在领取任务时逐项比较工作流声明的 Git 提交和管线内容哈希与节点最近一次
签名心跳；任一值缺失或不一致，该节点都不能领取此版本的抠图任务。

`2026.07.30-691770c-r1` 仅更新 GPU Control 的不可变兼容标签。三台节点与
ImageClip 远端 `main` 均为 `691770c…`，API 管线内容哈希仍为
`00e7104762f0…`，因此没有修改业务工作流、模型、提示词、节点参数或输出语义。
