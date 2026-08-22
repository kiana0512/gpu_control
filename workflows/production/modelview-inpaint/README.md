# ModelViewCreator Flux2 Klein TrueV3 API

## 固定来源

- 业务仓库：`rd_center/ai_art/modelviewcreator`
- 分支：`codex/flux2-klein-truev3-workflow`
- 基线提交：`a9dbbca846ee80734d0a6123ac32d8a8e51c7fcd`
- 两图 UI 工作流提交：`877c006345518870bfee6c71cd6291d28a7141cb`
- 原始 UI 工作流：`Flux2 Klein TrueV3-双图材质编辑-精简测试.json`
- 用户确认的 2026-08-22 UI 工作流 SHA-256：
  `02b250430f974a4c88504968fc90ad8a93547f7c2303072bec0f247a630badbd`
- GPU Control 不可变版本：`2026.08.22-02b2504-truev3-2input-rseed-r1`

`template.api.json` 由上述 UI 工作流逐节点转换。它保留两个 `LoadImage`，移除旧版
`LoadImage #26` 及两级 `easy imageColorMatch`，并把 `CherryAlignReference #33`
的图像 A 直接连接到白模、图像 B 连接到恢复原尺寸的生成图；唯一业务输出仍是
`SaveImage #32`。UI 文件中保存的 `RandomNoise #14` 数值只作来源占位，生产执行
时一定由任务中心覆盖。

## 对外 API 契约

- 路径仍为 `POST /api/v1/services/modelview-inpaint`。
- multipart `image` 必填，绑定到 `LoadImage #4`（白模主图）。
- multipart `material_image` 必填，绑定到 `LoadImage #5`（六视图材质参考）。
- multipart `prompt` 仍为可选，传入时只覆盖 `CLIPTextEncode #9.inputs.text`；
  不传时保留仓库工作流的完整几何锁定／材质迁移提示词。
- `noise_seed` 不是公共表单字段。每个真正新建的任务由 API 生成一个 50 位随机整数，
  保存到任务参数和渲染快照，再绑定到 `RandomNoise #14.inputs.noise_seed`。
- 同一个 `Idempotency-Key` 的重复请求返回原任务，因此保留原 seed；重新生成必须换新
  `Idempotency-Key`。Scheduler 对同一任务的网络或节点重试也读取同一份任务快照。
- 响应仍同步返回最终 PNG，并保留鉴权、幂等、优先级和响应头契约。
- 唯一业务输出为 `SaveImage #32`，不会把中间预览或未校色结果返回给调用方。
- 滚动迁移期间旧客户端传入的 `viewport_reference` 会被接受但忽略；新前端不得再上传。

## 两张图的语义边界

- `image` 决定画布、视角、轮廓和所有几何边界。
- `material_image` 只提供六视图中的材质、颜色分区和微观纹理。
- 生成图恢复白模原始尺寸后，与 `image` 一起进入 `CherryAlignReference #33`；
  不再使用第三张图片做额外调色。

两张图都会以任务 UUID 为子目录上传到 ComfyUI，上传后校验字节数与
SHA-256，不依赖服务器 input 根目录中的历史同名文件。

## 模型与插件

- TrueV3 主模型来自公开发布
  `wikeeyang/Flux2-Klein-9B-True-V3` 的固定 revision `2938a5a`；预期
  SHA-256 为 `6d23ea6946f410a496bf706b136b17bea5e1cdd1a6ba17a1b5f23c64d30c7088`。
- 内部 LoRA 必须是
  `baimo_shangcaizhi_klein_v1_000005500.safetensors`，不能用相近名称替代；
  实测大小为 `165704408` 字节，SHA-256 为
  `5352ada24a83b36e7bf8b3004eae5f6b1676479f93e0d002c9f521d133804fb9`，
  已固定在 `configs/modelviewcreator.models.manifest.yaml`。
- 自研 `Cherry_KleinWorkflowTools` 六个文件逐字节来自上述 ModelViewCreator
  提交，文件哈希见 `custom_nodes/Cherry_KleinWorkflowTools/UPSTREAM.sha256`。
- `ComfyUI_essentials` 固定工作流记录的官方提交 `9d9f4bed…`，只使用
  `ImageResize+`；不引入其他节点的可选依赖，避免升级生产 Torch/NumPy/Pillow/Scipy。

最低调度显存为 `24000 MiB`，因此只有 4090、3090-A 和 3090-B 可兼容；
12 GiB 4070 Ti 必须被兼容表硬排除。三台 24 GiB 容器统一使用
`--reserve-vram 2.0`；4090 是首选局部重绘节点，新任务到达后建立可续期的
10 分钟保护窗口。超时保持 `2400` 秒。
