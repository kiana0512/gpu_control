# ModelViewCreator Flux2 Klein TrueV3 API

## 固定来源

- 业务仓库：`rd_center/ai_art/modelviewcreator`
- 分支：`codex/flux2-klein-truev3-workflow`
- 固定提交：`a9dbbca846ee80734d0a6123ac32d8a8e51c7fcd`
- 原始 UI 工作流：`Flux2 Klein TrueV3-双图材质编辑-精简测试.json`
- 原始 UI 工作流 SHA-256：`73102b3ab6f48f2b52568f5dc33910ce1f59cd2e58fa16098cfb43256f849596`
- GPU Control 不可变版本：`2026.08.17-a9dbbca-flux2-klein-truev3-3input-r2`

`template.api.json` 以源 ComfyUI 成功任务
`91329912-26b4-4ba7-b7d8-94bfd4d594ff` 的 API prompt 为转换基线，并与上述
UI 工作流逐节点核对。生产模板移除了三个 `PreviewImage` 和历史遗留的断开
`SaveImage #20`，只保留 `SaveImage #32` 作为业务输出；固定种子恢复为提交中
保存的 `293365702567203`。

## 对外 API 契约

- 路径仍为 `POST /api/v1/services/modelview-inpaint`。
- multipart `image` 必填，绑定到 `LoadImage #4`（白模主图）。
- multipart `material_image` 必填，绑定到 `LoadImage #5`（六视图材质参考）。
- multipart `viewport_reference` 必填，绑定到 `LoadImage #26`（视窗参考图，只供最终调色）。
- multipart `prompt` 仍为可选，传入时只覆盖 `CLIPTextEncode #9.inputs.text`；
  不传时保留仓库工作流的完整几何锁定／材质迁移提示词。
- 响应仍同步返回最终 PNG，并保留鉴权、幂等、优先级和响应头契约。
- 唯一业务输出为 `SaveImage #32`，不会把中间预览或未校色结果返回给调用方。

## 三张图的语义边界

- `image` 决定画布、视角、轮廓和所有几何边界。
- `material_image` 只提供六视图中的材质、颜色分区和微观纹理。
- `viewport_reference` 只进入 `CherryAlignReference -> easy imageColorMatch`
  后处理链，不参与几何生成。

三张图都会以任务 UUID 为子目录上传到 ComfyUI，上传后校验字节数与
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
- `ComfyUI-Easy-Use` 固定官方 `v1.3.6` 提交 `b5e31ef…`。
- `ComfyUI_essentials` 固定工作流记录的官方提交 `9d9f4bed…`，只使用
  `ImageResize+`；不引入其他节点的可选依赖，避免升级生产 Torch/NumPy/Pillow/Scipy。

最低调度显存为 `24000 MiB`，因此只有 4090、3090-A 和 3090-B 可兼容；
12 GiB 4070 Ti 必须被兼容表硬排除。三台 24 GiB 容器统一使用
`--reserve-vram 2.0`；4090 是首选局部重绘节点，新任务到达后建立可续期的
10 分钟保护窗口。超时保持 `2400` 秒。
