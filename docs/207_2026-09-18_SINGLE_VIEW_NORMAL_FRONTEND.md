# 单视图生成：法线输入版前端对接

更新日期：2026-09-20。替代旧版双图调用约定。本次仅后端采样步数从 4 改为 2，前端无需再次改接口。

## 接口

`POST https://10.3.34.11/api/v1/services/modelview-single-view`

请求为 `multipart/form-data`，同步等待生成；成功直接返回一张 **2048 × 2048 PNG**，不是 JSON，也不是图片 URL。

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `image` | 图片文件 | 是 | 当前目标视角的白模图/效果图，需要生成的表面按约定填白 |
| `material_image` | 图片文件 | 是 | 同一物体的参考图或多视图参考拼图 |
| `normal_image` | 图片文件 | 是，新增 | 与 `image` 相同视角、构图、分辨率的法线渲染图 |
| `prompt` | 字符串 | 否 | 补充要求，最多 4096 字符；追加到工作流固定提示词，不替换固定提示词 |

常规前端只提交三张图，省略 `prompt` 和 `parameters`。**本接口不接收蒙版作为业务输入，不是局部遮罩修复接口。** 如需限制编辑区域，请使用 `modelview-single-view-inpaint`。

## 图片约定

- `image` 与 `normal_image` 必须同尺寸；建议使用原始 2048 × 2048 PNG。
- 法线图必须从同一相机、同一帧、同一物体变换导出，画布和像素坐标一一对应。不能以 UV 展开的切线法线贴图代替视角法线渲染图。
- 法线图保留完整几何信息，不填白、不套用蒙版，不自动翻转通道；沿用已经验证的渲染端法线编码。接口不自动转换 OpenGL/DirectX 或坐标空间。
- 参考图可以有不同尺寸和宽高比。不要把参考多视图拼图传到 `normal_image`。
- 此工作流从空 latent 生成整张图；固定提示词虽要求保留已有外观，但不提供蒙版外像素严格不变的保证。

## 调用示例

```bash
curl --cacert /path/to/lan-ca.crt --max-time 2700 \
  -H "Idempotency-Key: single-view-新的唯一请求ID" \
  -F "image=@current.png" \
  -F "material_image=@reference.png" \
  -F "normal_image=@normal.png" \
  https://10.3.34.11/api/v1/services/modelview-single-view \
  --output result.png
```

前端建议调用自己的后端代理，再由代理访问上述服务；不要把管理凭证或服务密钥放入浏览器。

```ts
async function generateSingleView(
  image: File, reference: File, normal: File,
  requestKey: string, prompt = "",
) {
  const form = new FormData();
  form.append("image", image);
  form.append("material_image", reference);
  form.append("normal_image", normal);
  if (prompt.trim()) form.append("prompt", prompt);
  const response = await fetch("/your-backend/modelview-single-view", {
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
// 用户点击“生成/重新生成”时创建 crypto.randomUUID()；同一次网络重试复用它。
```

`/your-backend/...` 是应用自行实现的代理路径，不是任务中心新增路由。代理需转发字段、幂等头、状态码和图片响应；不要手动设置 multipart 的 Content-Type/boundary。代理与浏览器超时建议至少 2700 秒。跨域直连需要部署方配置 CORS 和响应头暴露，不能假设默认已开放。

## 重试、认证与错误

- 服务端为每个新任务生成随机 seed 并持久化。客户端不要传 `noise_seed`；同一任务重试保留 seed。
- 每次真正重新生成都使用新的 `Idempotency-Key`；相同 key、相同内容返回原任务结果。更换图片、法线或提示词时必须换 key，否则返回 409。
- 目前入口支持按来源 IP 识别客户端；已有服务端 API Key 的调用方沿用现有认证配置。不要为了接入而关闭 TLS 校验；安装内网 CA。排队、并发和限流策略仍适用。
- 422：缺少必填图片、图片无效、法线尺寸与当前图不一致或参数不合法。
- 409：幂等键与请求内容冲突。
- 429：准入/排队/限流拒绝，读取返回的具体错误，不要统一显示“每日配额已用完”。
- 503：工作流输入契约未同步；500/504：生成失败或等待超时。保留错误体中的 `job_id` 供排查，避免无脑创建新任务。
- 成功响应含 `X-Job-ID`、`X-Client-ID`、`X-Artifact-SHA256`，`Cache-Control: no-store`。

## 后端工作流（前端无需配置）

版本 `2026.09.20-refcontrol-normal-single-view-2step-r1`。原 LoRA `li3d_000004500.safetensors` 强度 0.8，新增 `flux2_klein_9b_refcontrol_normal.safetensors` 强度 0.8；采样为 **2 步**。

三图绑定节点 4 / 5 / 42；法线 42 → VAEEncode 44（VAE 3）→ ReferenceLatent 45。输出节点 29，来自节点 33 的第二个输出。固定提示词节点 40，补充提示词节点 41。

与单视图局部重绘、普通局部重绘是三个独立接口。前端本次必须新增并上传 `normal_image`；旧双图请求将返回 422。
