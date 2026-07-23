# ImageClip RGBA API

- 业务仓库提交：`/opt/imageclip` `bb243808`
- 用户输入：multipart 字段 `input_image`
- 参数注入：`image_filename -> 28.inputs.image` (`LoadImage #28`)
- 唯一业务输出：`SaveImage #25`
- 输出来源：`122_FootRegionPaste #52` 的最终 RGBA 图

API 模板删除了中间保存、预览、对比和镜像保存节点，避免任务产物混入黑底图、
中间抠图或调试预览。原始业务工作流不做修改。
