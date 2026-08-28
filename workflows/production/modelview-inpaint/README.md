# ModelViewCreator Flux2 Klein TrueV3 GGUF 蒙版局部重绘 API

## 固定来源

- 业务仓库：`rd_center/ai_art/modelviewcreator`
- 原始 UI 工作流：`Flux2 Klein TrueV3-双图材质编辑-局部重绘.json`
- 用户提供的 2026-08-28 UI 工作流 SHA-256：
  `cd48a782ccc9bd716412b8935de6cfff7df531a12f22ef8178b00df60f782cd9`
- GPU Control 不可变版本：
  `2026.08.28-cd48a78-truev3-gguf-mask-4input-rseed-r1`

`template.api.json` 由上述 UI 工作流逐节点转换。对外共有四个业务输入：当前效果图、
参考图、蒙版和提示词；输出仍只有一张图。API 模板只把 UI 的最终 `PreviewImage #29`
等价改为 `SaveImage #29`，输入保持 `CherryAlignReference #33` 的第二路输出，因此不会
发布采样中间图。

新工作流原样保留 `BasicScheduler #15` 的实际参数 `steps=4` 和
`LoraLoaderModelOnly #21` 的实际强度 `0.9`。节点标题中的“12步”和“0.8”不是执行值，
不得据此静默改写。UI 文件中保存的 `RandomNoise #14` 只作来源占位，生产执行时一定由
任务中心覆盖。

## 对外 API 契约

- 路径仍为 `POST /api/v1/services/modelview-inpaint`。
- multipart `prompt` 可选，绑定到 `ttN text #60.inputs.text`，并直接交给
  `CLIPTextEncode #9`；此版本没有隐藏的固定保护提示词。
- multipart `image` 必填，绑定到 `LoadImage #4`，语义为当前效果图。
- multipart `material_image` 必填，绑定到 `LoadImage #5`，语义为参考图。
- multipart `mask` 必填，绑定到 `LoadImage #44`。工作流通过 `ImageResize+ #52`、
  `ImageToMask #45` 的红色通道和 `SetLatentNoiseMask #43` 约束局部重绘；白色区域重绘，
  黑色区域保留。蒙版必须与当前效果图尺寸相同，且不能是全黑空蒙版。
- `noise_seed` 不是公共表单字段。每个真正新建的任务由 API 生成一个 50 位随机整数，
  保存到任务参数和渲染快照，再绑定到 `RandomNoise #14.inputs.noise_seed`。
- 同一个 `Idempotency-Key` 的重复请求返回原任务并保留原 seed；“重新生成”必须使用新
  key。Scheduler 对同一任务的网络或节点重试也复用任务快照中的 seed。
- 响应仍同步返回最终 PNG，并保留现有鉴权、幂等、优先级和响应头契约。
- 唯一业务输出为 `SaveImage #29`。
- 旧字段 `viewport_reference` 继续接受但忽略；新前端不得上传。

## 三张图的语义边界

- `image` 是本次被编辑的当前效果图，同时决定画布、尺寸和最终对齐基准。
- `material_image` 提供目标效果、材质、颜色分区或纹理参考。
- `mask` 决定允许加噪和重绘的区域；工作流读取红色通道，不读取 Alpha 作为蒙版值。
- `prompt` 描述本次局部编辑意图，可为空。
- 生成图恢复当前效果图原始尺寸后，与 `image` 一起进入 `CherryAlignReference #33`。

三张图都会以任务 UUID 为子目录上传到 ComfyUI，上传后校验字节数与 SHA-256，
不依赖服务器 input 根目录中的历史同名文件。

## 模型与插件

- 主模型改为 `Flux2-Klein-9B-True-V3-Q5_K.gguf`，大小 `6813702528` 字节，
  SHA-256 为
  `8167105716c715be31018f682916b5b9988f9afec4e20d7ad7cd9b42aa9774ce`。
- 内部 LoRA 仍为 `baimo_shangcaizhi_klein_v1_000005500.safetensors`，大小
  `165704408` 字节，SHA-256 为
  `5352ada24a83b36e7bf8b3004eae5f6b1676479f93e0d002c9f521d133804fb9`。
  工作流使用 `flux-kelin/` 子路径；各节点只建立指向规范文件的软链接，不重复占用模型
  空间。
- `ComfyUI-GGUF` 固定提交 `6ea2651e…`，提供 `UnetLoaderGGUF`。
- `ComfyUI_tinyterraNodes` 固定 2.0.11 对应提交 `97096a39…`，只使用
  `ttN text`。
- 自研 `Cherry_KleinWorkflowTools` 与 `ComfyUI_essentials` 保持既有固定提交。

最低调度显存为 `24000 MiB`，因此只部署并兼容 4090、3090-A 和 3090-B；
12 GiB 4070 Ti 被兼容表硬排除。超时保持 `2400` 秒。
