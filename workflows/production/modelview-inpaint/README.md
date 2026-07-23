# ModelViewCreator Inpaint API

- 业务仓库提交：`/opt/modelviewcreator` `b22bb377`
- 用户输入：multipart 字段 `input_image`
- 参数注入：`image_filename -> 96.inputs.image` (`LoadImage #96`)
- 唯一业务输出：`SaveImage #9`
- 输出来源：最右侧 `InpaintStitchImproved #79` 的最终修复图

输入图需要包含工作流可识别的蒙版/Alpha；Qwen3 VL Plus 凭据必须通过服务器
Secret 管理，不能继续保存在业务 Git 仓库中。
