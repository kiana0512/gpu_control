# ModelView 两图输入与随机 Seed 前端对接

状态：`IMPLEMENTED / ROLLOUT PENDING DRAIN`

日期：2026-08-22

## 1. 当前接口合同

请求路径保持不变：

```text
POST /api/v1/services/modelview-inpaint
```

请求是 `multipart/form-data`，成功响应仍是一张 PNG：

| 字段 | 必填 | 含义 | 生产节点 |
| --- | --- | --- | --- |
| `image` | 是 | 白模主图；唯一的构图、轮廓、尺寸和视角基准 | `LoadImage #4` |
| `material_image` | 是 | 材质参考图，可为六视图；只提供材质、颜色分区和纹理 | `LoadImage #5` |
| `prompt` | 否 | 覆盖默认材质迁移提示词，最长 4096 字符 | `CLIPTextEncode #9` |
| `parameters` | 否 | JSON 对象字符串；普通前端应保持 `{}` 或不传 | 工作流参数 |

旧字段 `viewport_reference` 已从业务合同移除。滚动迁移期间后端仍会接受它，
但新工作流不会上传、绑定或使用该文件；前端应删除第三张图的选择器、必填校验、
预览状态和 `FormData.append`。

## 2. Seed 由服务端负责

前端不要新增 seed 输入框，也不要把 `seed` 或 `noise_seed` 放进 `parameters`。
任务中心会在每个真正新建的任务中生成一个 `[0, 2^50)` 随机整数，将它持久化到
任务参数及 `rendered.api.json`，并绑定到：

```text
RandomNoise #14.inputs.noise_seed
```

这样既不会清除模型、CLIP、VAE 等安全缓存，也不会让同一任务的 Scheduler 重试
改变结果。UI 工作流 JSON 中保存的 seed 数字只是占位值，生产 API 一定会覆盖它。

幂等键的使用规则：

- 第一次点击“生成”：创建新的 `Idempotency-Key`；
- 请求超时、断网或 HTTP 连接重试：复用同一个 key 和完全相同的两张图片；
- 点击“重新生成”：创建新 key，服务端才会建立新任务并生成新 seed；
- 相同 key 但图片或 prompt 不同：返回 `409 IDEMPOTENCY_CONFLICT`。

## 3. 前端 TypeScript 示例

```ts
export type ModelViewInpaintInput = {
  whiteModel: File;
  materialReference: File;
  prompt?: string;
};

export type ModelViewInpaintResult = {
  image: Blob;
  jobId: string | null;
  sha256: string | null;
};

export async function generateModelViewInpaint(
  input: ModelViewInpaintInput,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<ModelViewInpaintResult> {
  const form = new FormData();
  form.append("image", input.whiteModel);
  form.append("material_image", input.materialReference);
  if (input.prompt?.trim()) form.append("prompt", input.prompt.trim());

  const response = await fetch("/api/v1/services/modelview-inpaint", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: form,
    signal,
  });

  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail?.message ?? `生成失败：HTTP ${response.status}`);
  }

  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}

// 新建任务或“重新生成”时调用一次；网络重试不要重新调用。
const generationKey = crypto.randomUUID();
```

浏览器不得手工设置 `Content-Type`，否则 `multipart/form-data` 的 boundary 会丢失。
展示结果时可用 `URL.createObjectURL(result.image)`，替换或卸载预览时应调用
`URL.revokeObjectURL`。

## 4. 前端改动清单

1. 保留白模和材质参考两个文件槽；删除第三张视窗参考图槽。
2. 提交时只追加 `image`、`material_image`，可选追加 `prompt`。
3. 删除 seed 表单和客户端随机化逻辑；seed 完全由任务中心负责。
4. 将“网络重试”和“重新生成”分开：前者复用 key，后者生成新 key。
5. 成功时读取响应体 PNG，并记录 `X-Job-ID`、`X-Artifact-SHA256` 方便定位。
6. 对 `409` 提示“本次重新生成需要新的任务键”；对 `422` 显示后端字段错误。
7. 请求期间禁用重复点击；若要取消等待，仅取消浏览器请求，不要自动换 key 重发。

## 5. 工作流变更与校验身份

- 用户提供 UI 文件：`ComfyUI_04696_.json`
- ModelViewCreator 本地分支：`codex/two-input-rseed-20260822`
- ModelViewCreator 提交：`877c006345518870bfee6c71cd6291d28a7141cb`
- UI 文件 SHA-256：
  `02b250430f974a4c88504968fc90ad8a93547f7c2303072bec0f247a630badbd`
- 不可变工作流版本：
  `2026.08.22-02b2504-truev3-2input-rseed-r1`
- API 模板规范化 SHA-256：
  `1e2b95cd5eb3de03759722786c6b29202d24335989282130cb8c69c81d40468b`
- 输入节点：`LoadImage #4`、`LoadImage #5`
- 对齐节点：白模 `#4 -> #33 图像A`，生成图 `#25 -> #33 图像B`
- 唯一输出：`CherryAlignReference #33 output 1 -> SaveImage #32`
- 删除：`LoadImage #26`、`easy imageColorMatch #30/#31`

## 6. 发布与回滚边界

新 API 代码先兼容旧工作流：只有启用版本声明了 `noise_seed` binding 时才自动生成
seed。额外上传字段也只在当前工作流声明对应 filename binding 时才写入任务。
因此发布时可以先更新 API，再导入并启用新不可变工作流；旧客户端短期上传第三张图
不会阻塞切换。

回滚只需重新启用上一版本
`2026.08.17-a9dbbca-flux2-klein-truev3-3input-r2` 并恢复对应 API 镜像；不删除历史
任务、WorkflowVersion、输入、输出或审计记录。
