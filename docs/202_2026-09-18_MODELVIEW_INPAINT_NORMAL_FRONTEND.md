# 局部重绘前端对齐文档：新增法线输入

日期：2026-09-18
工作流：`modelview-inpaint`
版本：`2026.09.18-refcontrol-normal-2step-r1`

本次只调整“ModelView 局部重绘”。“单视图生成”和“单视图局部重绘”的接口与输入不变。
已在 4090、3090-A、3090-B、5070 Ti 启用并通过真实四图生成；5070 Ti 恢复后已补齐部署及独立显存验收。
不要将旧三图接口文档用于此版本。

## 1. 前端需要改什么

在原有三张图片基础上，增加 **`normal_image`** 文件字段。请求仍是 multipart/form-data，
响应仍是一张效果图。法线不是提示词、蒙版、UV 法线贴图或第二张参考拼图。

```http
POST https://10.3.34.11/api/v1/services/modelview-inpaint
```

| 字段 | 必填 | 内容 |
| --- | --- | --- |
| `image` | 是 | 当前预览效果图，将需要生成的区域填成纯白后的合成图 |
| `material_image` | 是 | 同一物体的材质/多视图参考图 |
| `mask` | 是 | 已外扩的编辑蒙版：白色编辑，黑色保护 |
| `normal_image` | **是，新增** | 与效果图同视角、同画布的完整法线渲染图 |
| `prompt` | 否 | 显式传入会覆盖默认提示词；推荐不传 |

推荐只传四张图片，省略 `prompt` 和 `parameters`，使用工作流内置提示词。
注意：`prompt=""` 不等于省略，可能将内置提示词覆盖为空；不要自动发送空字段或旧默认词。
不传 seed、noise_seed、LoRA 名称、节点 ID、服务器文件路径、`viewport_reference`。

## 2. 图像准备与对齐

1. 固定同一次生成的摄像机、模型姿态、画布、缩放、裁切和方向。
2. 从当前效果图生成填白预览 `image`；参考图继续使用原有 `material_image`。
3. 原始编辑蒙版外扩后作为 `mask`；只扩编辑区域，不扩大画布。后端不自动外扩。
4. **使用相同摄像机与画布渲染法线通道**，作为 `normal_image`。导出完整可见模型，
   不把法线图的待编辑区域填白，不只截取蒙版区域，不从效果图自动估算法线。
5. 将四张图作为同一批快照提交；提交后用户继续旋转视图不能改变本次请求的图片。

约束：

- `image`、`mask`、`normal_image` 宽高必须完全一致，否则返回 422。尺寸一致不代表
  内容已经对齐，摄像机和构图对齐由前端保证。
- 推荐以上三图均为 **2048×2048 PNG**，与本次提供的真实样例一致。
  `material_image` 可用不同尺寸的多视图拼图，样例为 1536×1024。
- 法线保持当前已经在 ComfyUI 验证的导出编码与背景方式，不额外加光照、曝光、色调映射、
  调色、自动对比度或 JPEG 压缩；不要擅自翻转 RGB 通道或 Y 通道。
- 该 JSON 没有声明 normal 的坐标空间或 OpenGL/DirectX 约定。对接应复用提供样例的
  法线渲染流程，不能仅凭“normal”名字就将任意切线空间 UV 贴图直接上传。
- 蒙版读取 **红色通道**，不是 Alpha。RGB 全黑而仅 Alpha 有内容的图片会被判为空蒙版。
  允许灰度过渡，但不能全黑。法线图的 Alpha 也不作为编辑蒙版。
- 新图沿原工作流直接 VAE 编码，没有服务端法线缩放/重算法线步骤；必须上传正确原图。

## 3. 输出与任务语义

- 成功：HTTP 200，`image/png` **二进制**，不是 JSON 或图片 URL。
- 只返回最终节点 #29 的效果图，不返回法线、蒙版或中间预览。
- 当前最终对齐节点 #33 固定画布为 **2048×2048**。因此不要假设任意输入尺寸都原尺寸
  返回；前端按返回图片的实际宽高读取。此固定画布是既有工作流逻辑，本次没有更改。
- 响应头：`X-Job-ID`、`X-Client-ID`、`X-Artifact-SHA256`、`Cache-Control: no-store`。
- 外扩区域包含过渡带。提示词要求保护非白区域，但生成结果并非保护区逐像素硬合成保证。

每次真正“生成/重新生成”创建新的 `Idempotency-Key`，由服务器生成新随机 seed。
同一任务网络重试复用相同 key 和完全相同的**四张图片及参数**，保留原任务/seed。
法线图内容变化也属于新请求：同 key 配不同法线返回 409 `IDEMPOTENCY_CONFLICT`。
升级后新建任务，不复用旧三图版本的 key。

沿用同步等待机制。工作流超时 2400 秒，接口额外等待余量 60 秒；应用代理超时建议至少
2700 秒（45 分钟），这是上限，不是正常耗时。浏览器断开不等于服务端任务已取消。
不要用新 key 自动重试未知结果的请求，否则可能重复生成。

## 4. TypeScript 示例

浏览器推荐调用自己应用的后端代理，由应用后端连接上面的内网地址。
代理透传四个文件、幂等键、响应状态、图片内容及响应头。不要手写 multipart boundary。

```ts
export async function repaintWithNormal(
  endpoint: string,
  input: { image: Blob; materialImage: Blob; mask: Blob; normalImage: Blob },
  idempotencyKey: string, // 新生成创建 UUID；同一次网络重试复用
  signal?: AbortSignal,
): Promise<{ image: Blob; jobId: string | null; sha256: string | null }> {
  const body = new FormData();
  body.append("image", input.image, "preview-white-filled.png");
  body.append("material_image", input.materialImage, "reference.png");
  body.append("mask", input.mask, "mask-dilated.png");
  body.append("normal_image", input.normalImage, "normal.png");
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body,
    signal,
  });
  if (!response.ok) {
    throw new Error(`局部重绘失败 (${response.status}): ${await response.text()}`);
  }
  if (!response.headers.get("Content-Type")?.startsWith("image/png")) {
    throw new Error("服务未返回预期 PNG，请检查代理响应");
  }
  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}
```

## 5. cURL 示例

```bash
curl --fail-with-body \
  --cacert /path/to/lan-ca.crt \
  --max-time 2700 \
  -H 'Idempotency-Key: repaint-normal-unique-generation-001' \
  -F 'image=@preview-white-filled.png;type=image/png' \
  -F 'material_image=@reference.png;type=image/png' \
  -F 'mask=@mask-dilated.png;type=image/png' \
  -F 'normal_image=@normal.png;type=image/png' \
  'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  --output generated-result.png
```

示例 key 每次新生成需替换。失败响应可能为 JSON，不要把错误体当 PNG 使用。
鉴权保持既有来源 IP / 客户端识别方式；若应用已有独立 X-API-Key，由服务端继续携带，
不要把密钥下发浏览器。信任内网 CA，不关闭 TLS 校验。
每日任务数量限制已取消，原有请求速率、队列长度和并发限制仍然存在。

## 6. 错误与验收清单

| HTTP / code | 处理 |
| --- | --- |
| 422，缺少 `normal_image` | 前端尚未升级四图表单；不要回退使用效果图冒充法线 |
| 422 / `INPUT_INVALID` | 检查图片可解码、大小，以及法线/蒙版与效果图同尺寸 |
| 422 / `MASK_EMPTY` | 蒙版红色通道全黑，修正蒙版导出 |
| 409 / `IDEMPOTENCY_CONFLICT` | 同 key 的图片或参数发生变化；真正新任务使用新 key |
| 429 | 依具体错误处理请求速率或排队限制，不解释为每日额度耗尽 |
| 503 / `WORKFLOW_CONTRACT_MISMATCH` | API 与启用工作流未匹配，联系后台；不静默丢弃法线 |
| 500 / 504 | 保留错误详情和 job_id，查询/重试原任务，避免盲目重复生成 |

验收：能提交四图；法线漏传与尺寸错误均提示明确；参考图可异尺寸；重新生成使用新 key；
网络重试保留旧 key；收到一个有效 PNG；记录 X-Job-ID；单视图的两套老接口不强制增加法线。

后台参数按用户最新要求固定为 2 步、原 LoRA 强度 1.0、法线 LoRA 强度 0.8。
这些不需要前端传入，也不作为前端可调选项。本轮不会上传模型到 Git/LFS。
