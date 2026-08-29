# ModelView 蒙版局部重绘前端 API 对接文档

更新时间：2026-08-28  
GPU Control 版本：`1.5.22`
工作流：`modelview-inpaint`  
工作流版本：`2026.08.29-cba4414-truev3-gguf-mask-4input-rseed-steps2-r1`
用户 UI JSON SHA-256：`cba4414a694b9fe427477f3a4782f3111248f06193efb0c1cbc6c85d3c71349a`
API 模板规范化 SHA-256：`cc95d74d69df5f1ec96add1f1205e83c0e7bff1f2f1f3198a7ca3b6fa9520b32`

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
全图生成。实际参数为 2 个采样步、LoRA 强度 0.9；提示词直接进入文本编码节点，不再
自动拼接旧版本的隐藏几何保护提示词。

latent 蒙版通常能稳定限制编辑区域，但不承诺蒙版外逐像素完全相同。如果业务必须做到
像素级锁定，需要另行批准增加最终像素合成节点，不能由前端假设。

## 11. 2026-08-28 首次生产部署与验收记录

生产控制面版本为 `1.5.22`，源码提交为
`7a09ea97f8bcb7963429fcd4a555fbf38bdea9c5`。API、Scheduler、Web 镜像均具有相同版本和
revision 标签。三台可执行节点挂载的用户 UI JSON 与批准源文件 SHA-256 一致；生产启用的
API 模板规范化 SHA-256 为本文开头记录的 `250768c7...`，上一工作流版本保留但已禁用。

第一次 `1.5.21` 的 4090 验收任务 `05025e16-807c-4ea6-9715-35ceaea3ef8d` 失败。现场日志
确认不是工作流执行错误，而是 Scheduler 根据 `mask-` 文件名前缀调用了 ComfyUI 的
`/upload/mask`；该交互式端点要求 `original_ref`，不适用于本工作流的普通 `LoadImage #44`。
发布随即停止并排空全部节点。`1.5.22` 只把 `modelview-inpaint` 的蒙版改走
`/upload/image`，其他交互式蒙版工作流仍保持原端点。

热修复回归结果为 `129 passed`。节点隔离实跑结果如下：

| 节点 | Job ID | 结果 | 输出 SHA-256 |
|---|---|---|---|
| `control-4090` | `3d6e933e-6a02-4cb9-9479-41042ff12ba8` | `SUCCEEDED`，一次尝试，2048×2048 PNG | `92065fdae806d93870519c2af1cf58a5aeb14818506f9ca2557ad4dbed0784dd` |
| `worker-3090-a` | `3f2d617f-69b3-4b1b-b6f6-d81c151143ab` | `SUCCEEDED`，一次尝试，2048×2048 PNG | `45fc08df8146170d490d787a9a944c0eb55d975f30fdb070b241b70bc0b68a49` |
| `worker-3090-b` | `733d604f-d826-4959-90b5-43b4bca7335e` | `SUCCEEDED`，一次尝试，2048×2048 PNG | `909bc98a24c30d2214a57ca4dae33198bbf7d6d6284997b07f4d9eaab415d13b` |

三项上传记录都显示 `type=input` 且哈希已验证；渲染快照分别保存了服务端随机 Seed、
`LoadImage #44` 的蒙版路径和唯一 `SaveImage #29` 输出。3090-B 使用相同请求内容和相同
`Idempotency-Key` 重放后，仍返回同一个 Job ID 和相同输出 SHA-256，数据库中没有新增任务。

验收结束时四个节点均为 `ACTIVE / ONLINE / current_jobs=0`，四个 ComfyUI 队列均为 0，
控制面活动任务为 0。4090 和两台 3090 对本工作流兼容；4070Ti 因 12 GiB 显存和缺少
ModelView 自定义节点继续按合同排除，只处理其他兼容任务。
