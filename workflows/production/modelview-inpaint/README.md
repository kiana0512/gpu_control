# ModelViewCreator Flux2 Klein TrueV3 GGUF 三输入 API

## 固定来源

- 业务仓库：`rd_center/ai_art/modelviewcreator`
- 原始 UI 工作流：`Flux2 Klein TrueV3-双图材质编辑-精简测试.json`
- 用户提供的 2026-08-26 UI 工作流 SHA-256：
  `740115ae0ca6d00a072eed3c3ba150d753d9b3cedb8f9a21c11040d6e442db07`
- GPU Control 不可变版本：`2026.08.26-740115a-truev3-gguf-3input-rseed-r1`

`template.api.json` 由上述 UI 工作流逐节点转换。对外共有三个业务输入：提示词、
白模主图、参考多视图；输出仍只有一张图。API 模板把 UI 的最终 `PreviewImage #29`
等价改为 `SaveImage #29`，输入保持 `CherryAlignReference #33` 的第二路输出，因此不会
发布采样中间图。

新工作流原样保留 `BasicScheduler #15` 的实际参数 `steps=2`。节点标题中的“12步”不是
执行值，不得据此静默改成 12。UI 文件中保存的 `RandomNoise #14` 只作来源占位，生产
执行时一定由任务中心覆盖。

## 对外 API 契约

- 路径仍为 `POST /api/v1/services/modelview-inpaint`。
- multipart `prompt` 可选，绑定到空白的 `ttN text #41.inputs.text`。该文本会经
  `Text Concatenate #38` 与版本锁定的几何／材质保护提示词 `ttN text #40` 合并，
  再交给 `CLIPTextEncode #9`；调用方不会覆盖固定保护词。
- multipart `image` 必填，绑定到 `LoadImage #4`，语义为白模主图。
- multipart `material_image` 必填，绑定到 `LoadImage #5`，语义为参考多视图。
- `noise_seed` 不是公共表单字段。每个真正新建的任务由 API 生成一个 50 位随机整数，
  保存到任务参数和渲染快照，再绑定到 `RandomNoise #14.inputs.noise_seed`。
- 同一个 `Idempotency-Key` 的重复请求返回原任务并保留原 seed；“重新生成”必须使用新
  key。Scheduler 对同一任务的网络或节点重试也复用任务快照中的 seed。
- 响应仍同步返回最终 PNG，并保留现有鉴权、幂等、优先级和响应头契约。
- 唯一业务输出为 `SaveImage #29`。
- 滚动兼容期内旧客户端传入的 `viewport_reference` 会被接受但忽略；新前端不得上传。

## 两张图的语义边界

- `image` 决定画布、视角、轮廓、组件数量、遮挡关系和所有几何边界。
- `material_image` 只提供对应部位的材质、颜色分区和微观纹理。
- `prompt` 只追加本次任务的材质编辑意图，不能解除固定的白模轮廓和几何锁定约束。
- 生成图恢复白模原始尺寸后，与 `image` 一起进入 `CherryAlignReference #33`。

两张图都会以任务 UUID 为子目录上传到 ComfyUI，上传后校验字节数与 SHA-256，
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
- `WAS-Node-Suite` 固定 3.0.1 对应提交 `afeee09b…`，只使用
  `Text Concatenate`。
- `ComfyUI_tinyterraNodes` 固定 2.0.11 对应提交 `97096a39…`，只使用
  `ttN text`。
- 自研 `Cherry_KleinWorkflowTools` 与 `ComfyUI_essentials` 保持既有固定提交。

最低调度显存为 `24000 MiB`，因此只部署并兼容 4090、3090-A 和 3090-B；
12 GiB 4070 Ti 被兼容表硬排除。超时保持 `2400` 秒。
