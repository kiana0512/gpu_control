# ModelView 局部重绘：法线参考版

## 来源与范围

用户于 2026-09-18 提供并授权替换：
`Flux2 Klein TrueV3-双图材质编辑-局部重绘 (1).json`。
原始 UI SHA-256：`20c0e6a15cdf5529eb547f6f14ead3ddc0a2bb45c91692ce23e5c356425cbdab`。
用户随后明确要求只将 #15 的 steps 从 4 改为 2，其他执行值与连接保持。
当前 UI SHA-256：`81c7f6b2f427799ee0f185dbf817a29e5d6df5f618de8c31fe6ccf5aabbe0bdf`。
版本：`2026.09.18-refcontrol-normal-2step-r1`。
数据库规范化 API SHA-256：`65b0c6d0a3445d8318c411fd421b1721e33991494bd5b63d770bbcf29b67da7f`。

仅更新 `modelview-inpaint`，不修改单视图生成、单视图局部重绘、ImageClip 等工作流。
API 模板用 `scripts/build_imageclip_api_workflow.py` 逐节点转换；仅将最终
`PreviewImage #29` 等价适配为 `SaveImage #29`，保留输入 `33:1`，不返回中间预览 #62。
32 个祖先节点保留原执行参数及连线。节点标题不代表实际参数。

## 输入输出

`POST /api/v1/services/modelview-inpaint`，multipart/form-data：

| 字段 | 必填 | 语义与绑定 |
| --- | --- | --- |
| image | 是 | 待生成区域填白的效果图，`73.inputs.image` |
| material_image | 是 | 多视图材质参考，`5.inputs.image` |
| mask | 是 | 外扩蒙版，`44.inputs.image` |
| normal_image | 是 | 当前视角法线渲染图，`79.inputs.image` |
| prompt | 否 | 显式发送会覆盖 `60.inputs.text`；推荐省略以保留内置提示词 |

新增法线必须上传，不从旧输入目录取默认图，也不使用效果图代替法线。API 校验法线、
蒙版与效果图宽高相同；蒙版读取红通道且不能全黑。法线 RGB 不做额外转换，沿原图直接
进入 VAEEncode #77，再由 ReferenceLatent #74 接入第三路参考条件。工作流没有给法线
增加缩放节点，不能在服务端擅自补上。

四张输入均进入独立任务目录，哈希参与幂等指纹，Scheduler 上传全部输入。
每次新生成由服务端生成随机 seed；同一 key、相同输入重试复用原任务和 seed。
四图之外其他契约不变：同步返回最终单张 PNG，X-Job-ID / X-Client-ID /
X-Artifact-SHA256，Cache-Control: no-store，超时 2400 秒加接口等待余量 60 秒。
不承诺保护区逐像素不变；原工作流没有额外硬合成步骤。

## 模型与参数

- GGUF：`Flux2-Klein-9B-True-V3-Q5_K.gguf`，CLIP / VAE / 自定义插件不变。
- #21：`flux-kelin/li3d_000004500.safetensors`，强度 **1.0**。
- #78：`flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors`，强度 **0.8**，串接 #21。
- #15：simple，**2 步**，denoise=1；#16：euler。
- #60：保留用户这次 JSON 内置的 973 字符英文提示词。
- 新法线 LoRA：165704512 字节，SHA-256
  `baa97f297d048330850f0cb8063392678927a34282de961760693f6897983fd2`。

默认显存门槛仍为 24000 MiB。5070 Ti 的旧版验收档案不能直接复用到新模板；必须重新
实测并单独更新版本/哈希匹配的验收记录。12GB 4070 Ti 不放行。模型不上传 Git/LFS。

前端对齐文档：[四图输入与法线](../../../docs/202_2026-09-18_MODELVIEW_INPAINT_NORMAL_FRONTEND.md)。
生产是否已启用，以部署报告和数据库当前启用版本为准，不以文件已复制为准。

2026-09-18 补部署：5070 Ti 恢复后已同步当前 UI 和新增 LoRA，并通过同一组四图真实
生成（2048×2048 单张结果）。已登记该节点与本版模板绑定的 16GB 验收档案并恢复调度；
当前 4090、3090-A、3090-B、5070 Ti 均可运行本版。详见
[5070 Ti 补部署记录](../../../docs/205_2026-09-18_5070TI_NORMAL_2STEP.md)。
