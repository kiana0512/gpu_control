# ModelView 蒙版局部重绘前端 API 对接文档

更新时间：2026-08-28  
GPU Control 版本：`1.5.21`  
工作流：`modelview-inpaint`  
工作流版本：`2026.08.28-cd48a78-truev3-gguf-mask-4input-rseed-r1`  
用户 UI JSON SHA-256：`cd48a782ccc9bd716412b8935de6cfff7df531a12f22ef8178b00df60f782cd9`  
API 模板规范化 SHA-256：`250768c7da952c81cd781247cf1ab6dbe1258a4055cd1b384d08e5b38ea3549e`

## 1. 接口

```http
POST https://10.3.34.11/api/v1/services/modelview-inpaint
Content-Type: multipart/form-data
```

接口同步等待任务完成，成功时响应体就是最终 PNG，不返回 JSON 包装。

## 2. 输入字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `image` | PNG/JPEG/WebP 文件 | 是 | 当前效果图，也是尺寸和最终对齐基准 |
| `material_image` | PNG/JPEG/WebP 文件 | 是 | 目标效果、材质、颜色或纹理参考图 |
| `mask` | 推荐 PNG | 是 | 局部重绘蒙版，尺寸必须与 `image` 完全一致 |
| `prompt` | UTF-8 字符串 | 否 | 本次局部编辑要求，最长 4096 字符；空字符串等同省略 |
| `parameters` | JSON 字符串 | 否 | 默认 `{}`；普通前端不要传工作流内部参数 |

前端不要上传 `seed` 或 `noise_seed`。每个真正的新任务由服务端生成随机 Seed。

旧字段 `viewport_reference` 不再属于业务合同。后端目前仍会接收并忽略它，前端不要再
创建控件、预览或上传该字段。

## 3. 蒙版合同

- 白色：允许重绘。
- 黑色：尽量保留当前效果图。
- 灰色：按灰度作为中间强度。
- 工作流读取蒙版图片的红色通道，不读取 Alpha 通道作为蒙版值。
- 不要发送“RGB 全黑、只有 Alpha 有内容”的透明 PNG。
- 全黑空蒙版会返回 `422 MASK_EMPTY`。
- 蒙版尺寸与 `image` 不一致会返回 `422 INPUT_INVALID`。
- 全白蒙版合法，语义为允许整张图重绘。

建议前端在上传前把绘制画布导出为可见的黑底白区域 PNG，并在本地检查宽高。

## 4. cURL 示例

```bash
curl --fail-with-body \
  --cacert /path/to/lan-ca.crt \
  -X POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  -H 'Idempotency-Key: modelview-mask-asset-123-001' \
  -F 'image=@current-result.png;type=image/png' \
  -F 'material_image=@reference.png;type=image/png' \
  -F 'mask=@mask.png;type=image/png' \
  -F 'prompt=只修改蒙版内面板的材质，改成参考图中的磨砂红色' \
  -o result.png \
  -D response.headers
```

## 5. TypeScript 示例

```ts
export interface ModelViewMaskInpaintInput {
  currentImage: File;
  referenceImage: File;
  mask: File;
  prompt?: string;
  idempotencyKey: string;
}

export interface ModelViewMaskInpaintResult {
  image: Blob;
  jobId: string;
  clientId: string | null;
}

export async function runModelViewMaskInpaint(
  input: ModelViewMaskInpaintInput,
): Promise<ModelViewMaskInpaintResult> {
  if (!input.idempotencyKey.trim()) {
    throw new Error("idempotencyKey is required");
  }
  if (
    input.currentImage.type !== "image/png" &&
    input.currentImage.type !== "image/jpeg" &&
    input.currentImage.type !== "image/webp"
  ) {
    throw new Error("Unsupported current image format");
  }

  const form = new FormData();
  form.append("image", input.currentImage, input.currentImage.name);
  form.append("material_image", input.referenceImage, input.referenceImage.name);
  form.append("mask", input.mask, input.mask.name);
  const prompt = input.prompt?.trim();
  if (prompt) form.append("prompt", prompt);

  const response = await fetch(
    "https://10.3.34.11/api/v1/services/modelview-inpaint",
    {
      method: "POST",
      headers: {
        "Idempotency-Key": input.idempotencyKey,
      },
      body: form,
    },
  );

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`ModelView inpaint failed: ${response.status} ${body}`);
  }

  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.startsWith("image/png")) {
    throw new Error(`Unexpected response type: ${contentType}`);
  }

  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID") ?? "",
    clientId: response.headers.get("X-Client-ID"),
  };
}
```

浏览器若通过同源业务后端转发，请把 URL 改成该后端的相对路径，不要在浏览器中绕过
证书或关闭 TLS 校验。

## 6. 成功响应

```http
HTTP/1.1 200 OK
Content-Type: image/png
X-Job-ID: <uuid>
X-Client-ID: <client-id>
```

响应体只包含一张最终图片。工作流内部的预览图或中间图不会发布。

## 7. Seed、重新生成与幂等

- 新建任务：使用新的 `Idempotency-Key`，服务端生成新的随机 Seed。
- 网络重试：原请求内容和原 `Idempotency-Key` 不变，返回原任务及原结果。
- 点击“重新生成”：必须创建新的 `Idempotency-Key`。
- 相同 key 搭配不同图片、不同蒙版或不同 prompt：返回
  `409 IDEMPOTENCY_CONFLICT`。

推荐格式：

```text
modelview-mask-<资产ID>-<递增编号或UUID>
```

## 8. 常见错误

| HTTP | code | 处理方式 |
|---:|---|---|
| 401/403 | 鉴权错误 | 检查 API Key、来源 IP 或权限 |
| 409 | `IDEMPOTENCY_CONFLICT` | 请求内容变化时生成新 key |
| 422 | `MASK_EMPTY` | 确保红色通道存在白色或灰色编辑区域 |
| 422 | `INPUT_INVALID` | 检查格式、文件大小、像素上限及蒙版尺寸 |
| 422 | FastAPI 字段错误 | 检查 `image`、`material_image`、`mask` 是否全部上传 |
| 503 | `WORKFLOW_CONTRACT_MISMATCH` | 后端与工作流切换未完成，停止重试并联系服务端 |
| 504 | `SERVICE_TIMEOUT` | 保存 `X-Job-ID` 或响应中的 job ID 后联系服务端查询 |

## 9. 前端切换清单

- [ ] 原“白模图”文案改为“当前效果图”。
- [ ] 增加必填蒙版编辑、预览与上传。
- [ ] 本地校验蒙版和当前效果图宽高一致。
- [ ] 蒙版导出为可见黑白 PNG，不只写 Alpha。
- [ ] `FormData` 字段严格使用 `image`、`material_image`、`mask`、`prompt`。
- [ ] 删除 `viewport_reference`、`seed`、`noise_seed`。
- [ ] 成功响应按 PNG Blob 读取，并保存 `X-Job-ID`。
- [ ] 网络重试复用 key，“重新生成”换新 key。

## 10. 工作流行为说明

本版本从当前效果图编码得到 latent，再通过蒙版限制加噪区域。它不是旧版本的空 latent
全图生成。实际参数为 4 个采样步、LoRA 强度 0.9；提示词直接进入文本编码节点，不再
自动拼接旧版本的隐藏几何保护提示词。

latent 蒙版通常能稳定限制编辑区域，但不承诺蒙版外逐像素完全相同。如果业务必须做到
像素级锁定，需要另行批准增加最终像素合成节点，不能由前端假设。
