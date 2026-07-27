# ModelViewCreator Inpaint API

- 业务仓库提交：`/opt/modelviewcreator` `8c37f07b0a8ed87a94f4159c173d3d2e03a20b61`
- 用户输入：multipart 字段 `image`（必填）和 `prompt`（可选）
- 图片注入：`image_filename -> 127.inputs.image` (`LoadImage #127`)
- 提示词注入：`prompt -> 94.inputs.prompt` (`Qwen3 VL Plus #94`)
- 唯一业务输出：`SaveImage #9`
- 输出来源：`InpaintStitchImproved #79` 后经 `SeedVR2VideoUpscaler #110`
  放大的最终图；中间图不作为 API 产物返回。

每个任务都从不可变模板做深拷贝，再把该请求的 `prompt` 覆盖到
`94.inputs.prompt`。不传 `prompt` 时保留模板空值，由 Qwen 根据图片自动反推；
不得使用前一个任务的提示词。

输入图需要包含工作流可识别的蒙版/Alpha；Qwen3 VL Plus 凭据必须通过服务器
Secret 管理，不能继续保存在业务 Git 仓库中。
