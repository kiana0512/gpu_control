# 单视图生成：前端对接文档

更新日期：2026-09-17
工作流：`modelview-single-view`
版本：`2026.09.17-li3d4500-single-view-4step-r1`
LoRA：`li3d_000004500.safetensors`；强度 0.8；采样 4 步。
适用节点：4090、3090-A、3090-B、5070 Ti。

## 接口

```http
POST https://10.3.34.11/api/v1/services/modelview-single-view
```

## 两张输入图、一张输出图

| 顺序 | 表单字段 | 必填 | 内容 |
| --- | --- | --- | --- |
| 1 | `image` | 是 | 指定视角的白模/预览图，待生成区域为纯白，背景为纯黑 |
| 2 | `material_image` | 是 | 同一物体的多视图设计参考图或参考图 |

输出是一张该视角的生成效果图。不要向这个接口上传第三张蒙版：单视图生成不使用独立
`mask` 输入，也不等同于带采样蒙版的局部重绘。

整物体生成时，用黑底白模作为第一张图；若输入中已有渲染表面，内置提示词要求延续这些
表面并生成白色区域，但本工作流没有独立蒙版硬约束，不能保证非白区域逐像素不变。
需要通过外扩蒙版明确编辑范围时，调用“单视图局部重绘”接口。

第一张图决定视角、画布、轮廓和裁切；参考图提供物体身份、结构与材质线索，不作为输出
画布。推荐 PNG，纯白区域使用 RGB `255,255,255`，避免有损压缩污染白色标记。

## 提交示例

```bash
curl --fail-with-body \
  --cacert /path/to/lan-ca.crt --max-time 2700 \
  -H 'Idempotency-Key: single-view-asset123-new-generation001' \
  -F 'image=@white-model-view.png;type=image/png' \
  -F 'material_image=@reference-multiview.png;type=image/png' \
  'https://10.3.34.11/api/v1/services/modelview-single-view' \
  --output single-view-result.png
```

前端表单：

```ts
const form = new FormData();
form.append("image", whiteModelOrWhiteFilledPreview, "white-model-view.png");
form.append("material_image", referenceImage, "reference.png");
// 不添加 mask、prompt 或 parameters。
```

## 调用规则

- 使用 `multipart/form-data`，图片直接传文件，不传本机路径字符串或 Base64。
- 成功返回 `200 image/png`，响应体就是单张生成效果图，宽高按第一张输入图恢复。
- 可记录响应头 `X-Job-ID`、`X-Client-ID` 和 `X-Artifact-SHA256`。失败时保存状态码和错误体。
- 每次新生成使用新的 `Idempotency-Key`；同一次网络重试复用原 key 和相同图片。
  工作流更新后，重新生成请使用新 key，避免取回旧任务。
- `noise_seed` 由任务中心生成；前端不要提交 seed、LoRA、步数或工作流版本。
- 本版使用内置固定提示词，前端省略 `prompt` 和 `parameters.prompt`，`parameters` 也可以省略。
  旧的可选 `prompt` 参数仍兼容，但它会作为补充文字与固定提示词拼接；不是替换固定提示词。
  请移除旧前端自动发送的历史提示词，避免引入冲突或重复内容。
- 当前内网按来源 IP 识别客户，已有独立 API Key 的应用继续由服务端携带 `X-API-Key`。
  不要在浏览器中暴露服务端密钥。
- 推荐浏览器经应用后端代理调用真实服务地址，由代理保持请求字段、幂等键及响应体。
  后端应信任内网 CA；不要关闭 TLS 验证。浏览器跨域读取自定义响应头须由代理正确暴露。
- 工作流执行超时上限 2400 秒，业务接口额外预留 60 秒；调用方/代理建议至少 45 分钟超时。
  这是最大等待上限，不代表通常耗时。取消浏览器等待不等于取消服务器任务。
- 每日任务数量不限；排队、请求频率和并发限制仍然有效。
- 同步等待期间保留对象、摄像机、画布和请求标识。返回图片要关联提交时的视角，
  不要投射到用户后来旋转出的新视角。

## 响应处理

```ts
async function submitGeneration(
  endpoint: string, // 应用已有代理地址；服务端也可使用文档中的真实地址
  form: FormData,
  key: string,
  signal?: AbortSignal,
) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: form,
    signal,
  });
  // 不手写 Content-Type，运行时自动添加 multipart boundary。
  if (!response.ok) {
    throw new Error(`生成失败 (${response.status}): ${await response.text()}`);
  }
  if (!response.headers.get("Content-Type")?.startsWith("image/")) {
    throw new Error("服务未返回图像");
  }
  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}
```

| HTTP 状态 | 处理 |
| --- | --- |
| 422 | 检查必填图片、格式和尺寸等输入约束 |
| 409 | 同一幂等键对应了不同请求；新生成应创建新 key |
| 429 | 请求频率或队列达到上限，退避重试且保留原 key |
| 401 / 403 | 核对客户启停状态或 API Key |
| 5xx | 保存错误体和已有 job ID，排查节点或执行错误 |

## 内置固定提示词（不需要前端再次上传）

```text
Using image 1 as the current view of the object, in which every pure white area marks the region to be generated, and image 2 as the multi-view design reference sheet of the same object, render the white region with the object's true appearance from exactly the viewpoint of image 1, whether it is a top-down, bottom-up, oblique, or eye-level view, so that the object's silhouette, proportions, and surfaces follow image 1 precisely. Reconstruct the geometry, materials, textures, colors, and fine details by matching image 2, using it only for identity, material, and structure cues and never copying its layout, background, or multiple views. Where image 1 already shows rendered surfaces, continue them seamlessly into the white region; where the entire object is white, produce the complete object. Keep every non-white pixel, the camera view, the soft even unlit shading, and the pure black background unchanged, and output a single clean rendered image of the object.
```
