# ModelView 单视图局部重绘前端 API 对接文档

更新时间：2026-08-31

GPU Control 版本：`1.5.23`

任务名称：`单视图局部重绘`

工作流：`modelview-single-view-inpaint`

工作流版本：`2026.08.31-e39ed5f-single-view-inpaint-4input-rseed-steps2-r1`

用户 UI JSON SHA-256：`e39ed5f5ec3916e5b3d45415a472734d0707fcbf3ca971ee2064f0d94f056c26`

API 模板规范化 SHA-256：`32f09e228407e0f5c78720c6f4c09bf6451dd47e9139e69d2a5da1deeef31322`

## 1. 接口

```http
POST https://10.3.34.11/api/v1/services/modelview-single-view-inpaint
Content-Type: multipart/form-data
```

接口同步等待生成完成。成功时 HTTP 响应体就是一张最终 PNG，不返回 JSON 包装。

## 2. 前端任务定义

| 项目 | 值 |
|---|---|
| 显示名称 | 单视图局部重绘 |
| 任务键 | `modelview-single-view-inpaint` |
| 当前效果图字段 | `image` |
| 参考图字段 | `material_image` |
| 蒙版字段 | `mask` |
| 提示词字段 | `prompt` |
| 输出 | 一张 PNG |

建议输入区按以下顺序展示：

1. 当前效果图；
2. 参考图；
3. 蒙版遮罩；
4. 提示词。

## 3. multipart 输入合同

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `image` | PNG/JPEG/WebP 文件 | 是 | 当前效果图，也是画布、尺寸和最终对齐基准 |
| `material_image` | PNG/JPEG/WebP 文件 | 是 | 材质、颜色和纹理参考图 |
| `mask` | 推荐 PNG | 是 | 局部重绘蒙版，尺寸必须与 `image` 完全一致 |
| `prompt` | UTF-8 字符串 | 否 | 本次编辑要求，最长 4096 字符；空字符串等同省略 |
| `parameters` | JSON 字符串 | 否 | 默认 `{}`；普通前端不要传工作流内部参数 |

前端不得传 `seed` 或 `noise_seed`。每个真正的新任务由服务端生成随机 Seed。

## 4. 蒙版规则

- 工作流读取蒙版 RGB 的红色通道，不读取 Alpha 作为蒙版值。
- 白色或灰色区域参与重绘，黑色区域尽量保留当前效果图。
- RGB 全黑、只有 Alpha 有内容的透明蒙版会被视为空蒙版。
- 全黑蒙版返回 `422 MASK_EMPTY`。
- 蒙版宽高与 `image` 不一致返回 `422 INPUT_INVALID`。
- 全白蒙版合法，表示整张图均允许重绘。

前端应把画布导出为可见的黑底白色/灰色编辑区域 PNG，并在提交前检查宽高。

## 5. cURL 示例

```bash
curl --fail-with-body \
  --cacert /path/to/lan-ca.crt \
  -X POST 'https://10.3.34.11/api/v1/services/modelview-single-view-inpaint' \
  -H 'Idempotency-Key: single-view-inpaint-asset-123-001' \
  -F 'image=@current-result.png;type=image/png' \
  -F 'material_image=@reference.png;type=image/png' \
  -F 'mask=@mask.png;type=image/png' \
  -F 'prompt=只重绘蒙版内面板，使用参考图的磨砂红色并保持当前视角' \
  -o result.png \
  -D response.headers
```

## 6. TypeScript 示例

```ts
export interface SingleViewInpaintInput {
  currentImage: File;
  referenceImage: File;
  mask: File;
  prompt?: string;
  idempotencyKey: string;
}

export interface SingleViewInpaintResult {
  image: Blob;
  jobId: string;
  clientId: string | null;
  artifactSha256: string | null;
}

export async function runSingleViewInpaint(
  input: SingleViewInpaintInput,
): Promise<SingleViewInpaintResult> {
  const form = new FormData();
  form.append("image", input.currentImage, input.currentImage.name);
  form.append("material_image", input.referenceImage, input.referenceImage.name);
  form.append("mask", input.mask, input.mask.name);
  const prompt = input.prompt?.trim();
  if (prompt) form.append("prompt", prompt);

  const response = await fetch(
    "/api/v1/services/modelview-single-view-inpaint",
    {
      method: "POST",
      headers: { "Idempotency-Key": input.idempotencyKey },
      body: form,
    },
  );

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Single-view inpaint failed: ${response.status} ${body}`);
  }
  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.startsWith("image/png")) {
    throw new Error(`Unexpected response type: ${contentType}`);
  }

  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID") ?? "",
    clientId: response.headers.get("X-Client-ID"),
    artifactSha256: response.headers.get("X-Artifact-SHA256"),
  };
}
```

## 7. 成功响应

```http
HTTP/1.1 200 OK
Content-Type: image/png
X-Job-ID: <uuid>
X-Client-ID: <client-id>
X-Artifact-SHA256: <sha256>
Cache-Control: no-store
```

响应体只包含一张最终图片。前端应按 Blob 读取，不要调用 `response.json()`。

## 8. Seed、重新生成与幂等

- 新建任务或点击“重新生成”：创建新的 `Idempotency-Key`，服务端生成新的随机 Seed。
- 同一次请求的网络重试：保持原请求内容和原 key，服务端返回原任务及原结果。
- 相同 key 搭配不同图片、蒙版或提示词：返回 `409 IDEMPOTENCY_CONFLICT`。

推荐 key 格式：

```text
single-view-inpaint-<资产ID>-<递增编号或UUID>
```

## 9. 常见错误

| HTTP | code | 前端处理 |
|---:|---|---|
| 401/403 | 鉴权错误 | 检查 API Key、来源 IP或权限 |
| 404 | `WORKFLOW_NOT_FOUND` | 服务端工作流尚未启用，停止自动重试 |
| 409 | `IDEMPOTENCY_CONFLICT` | 请求内容变化时生成新 key |
| 422 | `MASK_EMPTY` | 确保蒙版红色通道存在白色或灰色区域 |
| 422 | `INPUT_INVALID` | 检查图片格式、大小、像素上限和蒙版尺寸 |
| 422 | FastAPI 字段错误 | 检查三个图片字段是否全部上传 |
| 503 | `WORKFLOW_CONTRACT_MISMATCH` | 停止自动重试并联系服务端 |
| 504 | `SERVICE_TIMEOUT` | 保存 job ID 后联系服务端查询 |

错误响应通常为：

```json
{
  "detail": {
    "code": "MASK_EMPTY",
    "message": "蒙版红色通道不能是全黑；白色或灰色区域才会参与重绘"
  }
}
```

## 10. 与现有任务的区别

| 任务 | 图片输入 | 蒙版 | 提示词处理 |
|---|---:|---:|---|
| `modelview-inpaint` | 当前效果图 + 参考图 | 有 | 用户提示词直接进入编码器 |
| `modelview-single-view` | 白模 + 参考多视图 | 无 | 用户提示词与固定保护词合并 |
| `modelview-single-view-inpaint` | 当前效果图 + 参考图 | 有 | 用户提示词与固定视角/结构保护词合并 |

固定保护提示词由工作流管理，前端不能读取、替换或覆盖。

## 11. 前端验收清单

- [ ] 新增独立入口“单视图局部重绘”，不要覆盖现有两个任务。
- [ ] 四个输入按当前效果图、参考图、蒙版、提示词排序。
- [ ] `image`、`material_image`、`mask` 均为必填文件。
- [ ] `prompt` 允许省略或空字符串。
- [ ] 本地校验蒙版与当前效果图宽高一致。
- [ ] 蒙版导出为可见黑白/灰色 PNG，不只写 Alpha。
- [ ] 不传 `seed`、`noise_seed` 或固定保护提示词。
- [ ] 成功响应按 PNG Blob 读取并记录 `X-Job-ID`。
- [ ] 网络重试复用 key；重新生成使用新 key。

## 12. 调度说明

该任务属于 ModelView 交互任务：优先使用 4090，3090-A 和 3090-B 可并行或回退；
4070Ti 因显存不足不参与。任务与其它 ModelView 交互任务按创建时间公平排队，共用模型缓存，
但任务身份、结果记录和幂等键互相独立。

## 13. 生产部署与真实验收回执

2026-08-31 已完成 GPU Control `1.5.23` 部署。API、Scheduler 和 Web 镜像均绑定源码
提交 `143098c479664e71d9743ae26ee56391726e462a`，工作流以禁用状态导入，确认兼容表后启用：

- `control-4090`、`worker-3090-a`、`worker-3090-b`：兼容；
- `worker-4070ti-animation-host-01`：因 `12282 MiB < 24000 MiB` 且缺少必要节点，不兼容；
- 三台兼容节点容器内 UI JSON SHA-256 均为
  `e39ed5f5ec3916e5b3d45415a472734d0707fcbf3ca971ee2064f0d94f056c26`。

逐台只开放一个兼容节点执行了真实四输入任务：

| 节点 | Job ID | Seed | 最终 PNG SHA-256 | 结果 |
|---|---|---:|---|---|
| 4090 | `83bdd06a-9069-4e28-ad32-6ace3d8325a4` | `93047692334906` | `76d97a25977c5242ba07326b3774a8b4319ddb52629a85b2732f977ebfbe4094` | `SUCCEEDED`，2048×2048 |
| 3090-A | `6d7e7ab8-2335-4aa5-a239-e0da0d216c48` | `360748602103887` | `474d4302fada3bf4d5e5161abfc8d4946aea7a64723f8998434c7f4382bfb13b` | `SUCCEEDED`，2048×2048 |
| 3090-B | `060f9443-9971-443f-9a73-0c209beffa96` | `80235822114795` | `1a8a3201570772f1b02c8db21015288d41a89d338985b984f4fe37da990fa992` | `SUCCEEDED`，2048×2048 |

渲染快照核对了 `BasicScheduler #15 steps=2`、用户提示词 `#61`、不可变保护提示词
`#62`、合并节点 `#63`、服务端随机 Seed `#14` 和唯一 `SaveImage #29`。3090-B 的相同
输入和相同 `Idempotency-Key` 重试返回原 Job ID 与相同输出 SHA，数据库任务总数没有增加。

验收结束后三台节点均恢复 `ACTIVE / ONLINE / 0 jobs`，GPU 队列与 Asset 队列均为 0。
