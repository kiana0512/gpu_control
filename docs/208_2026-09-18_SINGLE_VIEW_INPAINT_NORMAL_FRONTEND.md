# 单视图局部重绘：法线输入版前端对接

更新日期：2026-09-18。使用用户确认的 `ModelViewCreator_flux_fill_inpaint -View generation (2).json`，替代旧版三图调用约定。

## 接口

`POST https://10.3.34.11/api/v1/services/modelview-single-view-inpaint`

请求为 `multipart/form-data`，同步等待生成；成功直接返回一张 **2048 × 2048 PNG**，不是 JSON，也不是图片 URL。

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `image` | 图片文件 | 是 | 当前预览效果图，待生成区域已经填成纯白 |
| `material_image` | 图片文件 | 是 | 同一物体的参考图或多视图参考拼图 |
| `mask` | 图片文件 | 是 | 前端已外扩的蒙版，白色允许重绘、黑色保留，灰度表示过渡 |
| `normal_image` | 图片文件 | 是，新增 | 与当前效果图完全对齐的法线渲染图 |
| `prompt` | 字符串 | 否 | 补充要求，最多 4096 字符；与固定提示词拼接，不覆盖固定提示词 |

常规前端提交四张图，省略 `prompt` 和 `parameters`。提示词已内置，无需复制到每个请求。

## 前端图片准备

1. 固定相机和物体状态，保存当前预览图。
2. 将选定待生成区域填纯白，得到 `image`。
3. 提交已外扩的 `mask`；蒙版必须覆盖填白区域，可保留柔和过渡。工作流读的是 **红色通道，不是 alpha 通道**；推荐不透明黑白 PNG，不能只在透明度里存蒙版。
4. 在完全相同相机、构图、物体变换下导出 `normal_image`。保留完整法线，不把编辑区填白，不以参考图或纯色伪造法线。

`image`、`mask`、`normal_image` 必须同尺寸、同画布、像素对齐，推荐原始 2048 × 2048 PNG。蒙版不能全黑。参考图可使用不同尺寸和宽高比。

法线是当前视角的渲染图，不是按 UV 展开的切线法线贴图。沿用已验收的法线编码，不自行翻转通道；接口不自动转换法线坐标空间。前端合成回贴时继续沿用自己的蒙版与过渡带规则，不把工作流结果当作蒙版外逐像素保持的保证。

## 调用示例

```bash
curl --cacert /path/to/lan-ca.crt --max-time 2700 \
  -H "Idempotency-Key: single-inpaint-新的唯一请求ID" \
  -F "image=@white-filled.png" \
  -F "material_image=@reference.png" \
  -F "mask=@expanded-mask.png" \
  -F "normal_image=@normal.png" \
  https://10.3.34.11/api/v1/services/modelview-single-view-inpaint \
  --output result.png
```

建议通过应用后端代理调用，管理凭证或服务密钥不能放在浏览器。

```ts
async function repaintSingleView(
  image: File, reference: File, mask: File, normal: File,
  requestKey: string, prompt = "",
) {
  const form = new FormData();
  form.append("image", image);
  form.append("material_image", reference);
  form.append("mask", mask);
  form.append("normal_image", normal);
  if (prompt.trim()) form.append("prompt", prompt);
  const response = await fetch("/your-backend/modelview-single-view-inpaint", {
    method: "POST",
    headers: { "Idempotency-Key": requestKey },
    body: form,
    signal: AbortSignal.timeout(2700000),
  });
  if (!response.ok) throw new Error(await response.text());
  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}
// 新一次生成使用 crypto.randomUUID()；同一次请求的网络重试复用原 key。
```

示例 `/your-backend/...` 是应用自己的代理路由，需要自行实现并转发字段、响应头和错误状态。不要手动设置 multipart Content-Type/boundary。代理和浏览器等待时间建议至少 2700 秒；跨域直连需另外配置 CORS 和自定义响应头暴露。

## 重试、认证与错误

- 每个新任务由服务器生成并保存随机 seed，客户端不要传 `noise_seed`。
- 同一次网络重试复用原 `Idempotency-Key` 和全部请求内容；重新生成或更换任意图像（含法线）/提示词必须使用新 key。内容变了却复用旧 key 返回 409。
- 当前入口按来源 IP 识别客户端；如调用方已有服务端 API Key，沿用既有配置。使用可信内网 CA，不关闭 TLS 校验。并发、排队和限流仍适用。
- 422：缺图、图片无效、三张空间图尺寸不一致、蒙版全黑或参数不合法。
- 409：幂等键内容冲突；429：读取具体准入/排队/限流错误，不能一律写成“每日配额已用完”。
- 503：工作流输入契约未同步；500/504：生成失败或等待超时。保留返回错误中的 `job_id`，网络超时不等于服务端任务被取消。
- 成功返回 `Content-Type: image/png`，并带 `X-Job-ID`、`X-Client-ID`、`X-Artifact-SHA256` 和 `Cache-Control: no-store`。

## 后端工作流（前端无需配置）

版本 `2026.09.18-refcontrol-normal-single-view-inpaint-2step-r1`。原 LoRA `li3d_000004500.safetensors` 强度 0.9，新增 `flux2_klein_9b_refcontrol_normal.safetensors` 强度 0.8；采样保持 **2 步**。

四图绑定节点 4 / 5 / 44 / 64。法线 64 → VAEEncode 66（VAE 3，使用已修复连接版本）→ ReferenceLatent 67。蒙版进入 SetLatentNoiseMask 43；最终输出节点 29，来自节点 33 的第二个输出。固定提示词 62，补充提示词 61。

这是 `modelview-single-view-inpaint`，不是普通 `modelview-inpaint`。本次前端唯一新增必填图片字段是 `normal_image`；旧三图请求将返回 422，返回值仍是一张效果图。
