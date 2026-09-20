# 局部重绘前端对接：三张图输入、单张效果图输出

更新日期：2026-09-17
业务：ModelView 局部重绘（`modelview-inpaint`，不是“单视图局部重绘”）
工作流版本：`2026.09.17-li3d4500-defaultprompt-steps2-r1`
LoRA：`li3d_000004500.safetensors`；强度 0.9；采样 2 步。提示词已写入工作流。

## 1. 接口与输入

```http
POST https://10.3.34.11/api/v1/services/modelview-inpaint
Content-Type: multipart/form-data
```

| 顺序 | 表单字段 | 内容 | 必填 |
| --- | --- | --- | --- |
| 1 | `image` | 预览效果图：把本次需要生成的区域填成纯白后得到的合成图 | 是 |
| 2 | `material_image` | 同一物体的参考图，可为多视图设计参考拼图 | 是 |
| 3 | `mask` | 已外扩的编辑蒙版，黑底白色编辑区域 | 是 |

成功响应是单张生成效果图的 **PNG 二进制**，不是 JSON、URL、三图拼接或蒙版图。
输出宽高按第一张图恢复。接口名称和字段名沿用现有接口；本次变化是第一张图的预处理、
第三张图的外扩，以及采用工作流内置提示词。

**新版调用只上传这三张图，不传 `prompt`，也不传 `parameters.prompt`。**
旧接口仍支持提示词覆盖；如果继续发送旧提示词，会覆盖新版工作流内置提示词。
`parameters` 可以完全省略。不要上传 `seed`、`noise_seed`、`viewport_reference` 或 LoRA 名称。

## 2. 前端图像准备

1. 固定当前摄像机、画布宽高与裁切，取得预览效果图，并保存该次请求对应的视角信息。
2. 将用户选定的待生成区域在预览图中填为 RGB `255,255,255`，导出第一张合成图。
   填白区域是视觉提示；其余已有效果和黑色背景保持原样。
3. 将原始编辑蒙版向外扩张，生成第三张蒙版，覆盖待生成区域并包含边缘过渡带。
   外扩在前端或调用方完成；后端不会自动替调用方执行外扩。
4. 参考图作为第二张图上传，无需改成白模或蒙版。

蒙版要求：

- `mask` 与 `image` 的宽高、坐标、方向和裁切必须完全相同。外扩不能扩大画布尺寸。
- 白色表示允许生成/编辑，黑色表示保护。建议 RGB 黑白 PNG；灰度边缘可以表达过渡权重。
- 工作流读取红色通道，不把 Alpha 当作编辑区域。不要上传 RGB 全黑、仅 Alpha 有内容的蒙版。
- 不要把第三张蒙版本身作为 `image`，也不要把没有填白的原始预览直接当作本次第一张输入。
- 蒙版不能全黑。全白蒙版表示允许整幅图重绘，只有明确需要时才使用。
- 外扩像素数不是本接口参数，应由前端按分辨率和画笔策略确定；本次未规定统一半径。
- 推荐三张图均用 PNG，避免 JPEG 压缩污染纯白标记与蒙版边界。

外扩蒙版赋予了边缘过渡带编辑权限。内置提示词要求保留非白像素，但模型输出不等于
逐像素硬合成保证；如果产品要求保护区逐像素一致，调用方须明确自己的结果合成策略。
这不是本次工作流新增的后处理步骤。

## 3. 请求与重试

- 每次用户点击“生成/重新生成”，创建一个新的 `Idempotency-Key`。
- 同一任务网络重试，复用同一个 key 和完全相同的三张图。
- 服务端为真正的新任务生成随机 seed；同一任务重试保持原 seed。
- 工作流切换后，请新建生成任务，不要复用之前已经成功的旧 key。
- 本接口同步等待任务完成。服务端工作流超时 2400 秒，接口额外预留 60 秒；应用服务端/
  代理超时建议至少 45 分钟。这是超时上限，不是正常耗时。
- 每日任务数量限制已取消；排队、请求频率和并发限制仍有效。
- 当前内网允许按来源 IP 自动识别客户端；若调用方已有独立 `X-API-Key`，继续由应用服务端
  携带。不要把服务端密钥放入浏览器代码。
- 推荐浏览器调用应用自己的后端代理，由后端连接上述 HTTPS 地址。代理透传表单、幂等键、
  HTTP 状态、PNG 响应和相关响应头；信任内网 CA，不要关闭 TLS 校验。

## 4. cURL 示例

```bash
curl --fail-with-body \
  --cacert /path/to/lan-ca.crt \
  --max-time 2700 \
  -H 'Idempotency-Key: repaint-asset123-unique-generation001' \
  -F 'image=@preview-white-filled.png;type=image/png' \
  -F 'material_image=@reference-multiview.png;type=image/png' \
  -F 'mask=@mask-dilated.png;type=image/png' \
  'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  --output generated-result.png
```

示例 key 仅供说明，生产环境每次新生成必须使用新值。失败时响应可能为 JSON，
不要把错误体当作 PNG。

## 5. TypeScript 请求示例

此函数的 `endpoint` 使用应用已配置的代理地址；如果运行于应用服务端，也可以使用上面的
真实服务地址。三张 Blob 都由前端图像准备步骤产生。

```ts
export async function repaint(
  endpoint: string,
  whiteFilledPreview: Blob,
  reference: Blob,
  dilatedMask: Blob,
  idempotencyKey: string, // 新生成用新 UUID；网络重试复用
  signal?: AbortSignal,
): Promise<{ image: Blob; jobId: string | null; sha256: string | null }> {
  const body = new FormData();
  body.append("image", whiteFilledPreview, "preview-white-filled.png");
  body.append("material_image", reference, "reference.png");
  body.append("mask", dilatedMask, "mask-dilated.png");

  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body,
    signal,
  });
  // 不手写 Content-Type；浏览器/运行时会补 multipart boundary。
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`局部重绘失败 (${response.status}): ${text}`);
  }
  if (!response.headers.get("Content-Type")?.startsWith("image/")) {
    throw new Error("局部重绘响应不是图像");
  }
  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}
```

原生响应包含 `X-Job-ID`、`X-Client-ID`、`X-Artifact-SHA256`、
`Cache-Control: no-store`。跨域浏览器读取自定义响应头需要应用代理正确暴露这些头。
中止浏览器等待不等于取消服务器任务，重新尝试应复用原 key。

生成完成后，把 PNG 关联回提交时保存的视角和对象。请求期间用户旋转视图后，
不要误把旧视角结果投射到新的视角上。

## 6. 常见错误

| 状态 | 情况与处理 |
| --- | --- |
| 422 | 缺少图片、非法图片、`image` 与 `mask` 尺寸不一致，或 `MASK_EMPTY`；修正输入 |
| 409 | `IDEMPOTENCY_CONFLICT`：相同 key 对应不同请求；新生成需新 key |
| 429 | 请求频率或队列达到上限；按响应退避，重试保留 key |
| 401 / 403 | 客户身份、API Key 或启停状态异常 |
| 5xx | 服务、节点或执行失败；保存错误体和已有 job ID 供排查 |

## 7. 内置提示词（前端不要再次提交）

```text
Using image 1 as the current view of the object, in which every pure white area marks the region to be generated, and image 2 as the multi-view design reference sheet of the same object, render the white region with the object's true appearance from exactly the viewpoint of image 1, whether it is a top-down, bottom-up, oblique, or eye-level view, so that the object's silhouette, proportions, and surfaces follow image 1 precisely. Reconstruct the geometry, materials, textures, colors, and fine details by matching image 2, using it only for identity, material, and structure cues and never copying its layout, background, or multiple views. Where image 1 already shows rendered surfaces, continue them seamlessly into the white region; where the entire object is white, produce the complete object. Keep every non-white pixel, the camera view, the soft even unlit shading, and the pure black background unchanged, and output a single clean rendered image of the object.
```
