# ModelView 局部重绘三输入前端对齐文档

状态：`READY FOR FRONTEND INTEGRATION`

日期：2026-08-26

适用工作流：`modelview-inpaint`

生产版本：`2026.08.26-740115a-truev3-gguf-3input-rseed-r1`

## 1. 本次前端变化

局部重绘从“两张图片输入”调整为三个业务输入：

1. 提示词 `prompt`：文本，可为空；
2. 白模 `image`：图片，必填；
3. 参考多视图 `material_image`：图片，必填。

输出合同不变：接口同步返回唯一一张 PNG，不返回工作流 JSON、中间图或多图数组。

前端需要新增一个提示词输入框。原来的两个图片上传槽继续保留，不增加第三个图片槽。

## 2. API 地址和字段

```text
POST /api/v1/services/modelview-inpaint
Content-Type: multipart/form-data
```

| FormData 字段 | 类型 | 必填 | 前端含义 | 后端工作流绑定 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 否 | 用户本次补充的材质编辑要求，最长 4096 字符 | `ttN text #41` |
| `image` | file | 是 | 白模主图，是构图、视角、轮廓和几何结构基准 | `LoadImage #4` |
| `material_image` | file | 是 | 材质参考多视图，只提供颜色、材质分区和纹理 | `LoadImage #5` |

不要提交以下字段：

- `viewport_reference`：当前版本不再需要；
- `seed` 或 `noise_seed`：由后端为每个新任务随机生成；
- 第三张图片：当前工作流只有两张图片输入。

`prompt` 只是追加要求，不会覆盖工作流内置的几何保护提示词。后端工作流会把用户提示词
与固定保护词拼接后再执行，因此提示词留空时也可以正常生成。

## 3. 推荐界面

建议按以下顺序显示输入项：

1. 提示词；
2. 白模；
3. 参考多视图；
4. 生成按钮。

提示词控件建议：

```text
标题：补充提示词（可选）
占位文案：例如：保持原有几何结构，仅将参考图的金属和磨损材质应用到对应部位
最大长度：4096
```

提交按钮的可用条件只取决于两张图片是否都存在。提示词为空时允许提交。

建议在图片槽旁明确说明：

- 白模决定几何、轮廓、视角和构图；
- 参考多视图只提供材质，不应改变白模结构。

## 4. TypeScript 对接代码

```ts
export type ModelViewInpaintInput = {
  prompt?: string;
  whiteModel: File;
  materialMultiView: File;
};

export type ModelViewInpaintResult = {
  image: Blob;
  jobId: string | null;
  sha256: string | null;
};

export async function runModelViewInpaint(
  input: ModelViewInpaintInput,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<ModelViewInpaintResult> {
  const form = new FormData();
  const prompt = input.prompt?.trim();

  if (prompt) {
    form.append("prompt", prompt);
  }
  form.append("image", input.whiteModel, input.whiteModel.name);
  form.append(
    "material_image",
    input.materialMultiView,
    input.materialMultiView.name,
  );

  const response = await fetch("/api/v1/services/modelview-inpaint", {
    method: "POST",
    headers: {
      "Idempotency-Key": idempotencyKey,
    },
    body: form,
    signal,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(
      error?.detail?.message ?? `局部重绘失败（HTTP ${response.status}）`,
    );
  }

  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.startsWith("image/png")) {
    throw new Error(`返回格式异常：${contentType || "unknown"}`);
  }

  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}
```

浏览器不要手工设置 `Content-Type`，否则会缺失 multipart boundary。

## 5. 首次生成、重试和重新生成

前端必须区分三种操作：

| 操作 | `Idempotency-Key` | 结果 |
| --- | --- | --- |
| 首次点击生成 | 创建一个新 UUID | 后端创建新任务和随机 seed |
| 网络断线、超时重试 | 复用原 UUID | 查询或返回原任务，不重复生成 |
| 用户点击重新生成 | 创建另一个新 UUID | 后端创建新任务和新随机 seed |

示例：

```ts
const firstKey = crypto.randomUUID();
const firstResult = await runModelViewInpaint(input, firstKey);

// 网络重试继续使用 firstKey。
const retryResult = await runModelViewInpaint(input, firstKey);

// 用户主动要求重新生成时换新 key。
const regenerateResult = await runModelViewInpaint(
  input,
  crypto.randomUUID(),
);
```

同一个 `Idempotency-Key` 如果搭配不同图片或不同 prompt，服务会返回 HTTP `409`。

## 6. 结果展示

成功响应：

- HTTP `200`；
- `Content-Type: image/png`；
- 响应体：最终图片二进制；
- `X-Job-ID`：任务 ID；
- `X-Artifact-SHA256`：结果文件 SHA-256。

预览示例：

```ts
let currentPreviewUrl: string | null = null;

export function replacePreview(image: Blob): string {
  if (currentPreviewUrl) URL.revokeObjectURL(currentPreviewUrl);
  currentPreviewUrl = URL.createObjectURL(image);
  return currentPreviewUrl;
}
```

组件卸载时也需要调用 `URL.revokeObjectURL`，避免连续生成造成浏览器内存累积。

## 7. 错误处理建议

| HTTP | 前端建议提示 |
| ---: | --- |
| `409` | 本次请求标识已被不同输入使用，请重新发起生成 |
| `413` | 图片文件过大，请压缩后重试 |
| `422` | 图片或提示词格式不正确，请检查两个图片输入 |
| `429` | 当前请求较多，请稍后重试 |
| `503` | 暂无兼容 GPU 节点，请稍后重试 |
| `504` | 生成超时，可使用原幂等键查询或重试原任务 |

不要因为浏览器等待超时就立即创建新 key。先用原 key 重试，避免同一个操作产生两份任务。

## 8. 鉴权边界

- 浏览器通过现有同源网关调用时，沿用当前登录态或出口 IP 白名单，不新增前端密钥字段。
- 如果业务后端已分配 `X-API-Key`，应由业务后端保存和转发，不能把长期 API Key 写进
  浏览器源码、localStorage 或前端环境变量。
- 前端必须保留 `Idempotency-Key`，它不是鉴权凭据。

## 9. 前端验收清单

- [ ] 页面新增一个可为空、最长 4096 字符的提示词输入框。
- [ ] 仍然只有白模和参考多视图两个图片槽。
- [ ] 提交字段恰好是 `prompt`、`image`、`material_image`。
- [ ] 空 prompt 和非空 prompt 都能成功生成。
- [ ] 页面不再提交 `viewport_reference`、`seed` 或 `noise_seed`。
- [ ] 网络重试复用原 `Idempotency-Key`。
- [ ] “重新生成”使用新的 `Idempotency-Key`。
- [ ] 成功后展示 PNG，并保存 `X-Job-ID` 便于排查。
- [ ] 替换预览和卸载组件时释放旧的 Blob URL。

生产部署和逐节点验收记录见
[137_2026-08-26_MODELVIEW_GGUF_THREE_INPUT_FRONTEND_HANDOFF.md](137_2026-08-26_MODELVIEW_GGUF_THREE_INPUT_FRONTEND_HANDOFF.md)。
