# ModelView GGUF 三输入前端对接与发布记录

状态：`DEPLOYED / PRODUCTION VALIDATED`

日期：2026-08-26

## 1. 接口不换地址

```text
POST /api/v1/services/modelview-inpaint
Content-Type: multipart/form-data
```

成功响应仍是单张 PNG。业务上有三个输入，其中两项是图片、一项是文本：

| 字段 | 必填 | 类型 | 含义 | 生产绑定 |
| --- | --- | --- | --- | --- |
| `prompt` | 否 | string | 本次材质编辑要求，最长 4096 字符；空字符串等同省略 | `ttN text #41.inputs.text` |
| `image` | 是 | file | 白模主图，唯一构图、轮廓、尺寸和视角基准 | `LoadImage #4.inputs.image` |
| `material_image` | 是 | file | 材质参考多视图，只提供材质、颜色分区和纹理 | `LoadImage #5.inputs.image` |
| `parameters` | 否 | JSON string | 普通前端保持 `{}` 或省略 | 工作流扩展参数 |

`prompt` 不再覆盖工作流固定提示词。后端只写入新的空白节点 `#41`，工作流通过
`Text Concatenate #38` 将它与固定几何／材质保护词 `#40` 合并，再传给
`CLIPTextEncode #9`。即使调用方不传 prompt，固定保护词仍会生效。

旧字段 `viewport_reference` 不属于当前合同。滚动兼容期内后端会接收但忽略它；
新前端不得再显示或提交该字段。

## 2. 随机 Seed 与重新生成

前端不传 `seed` 或 `noise_seed`。每个真正的新任务由任务中心生成一个
`[0, 2^50)` 随机整数，并绑定到 `RandomNoise #14.inputs.noise_seed`。

- 首次生成：创建新的 `Idempotency-Key`。
- HTTP 超时或断线重试：沿用相同 key 和相同输入，返回原任务。
- 用户点击“重新生成”：创建新 key，后端才会新建任务和新 seed。
- 相同 key 配不同图片或 prompt：返回 `409 IDEMPOTENCY_CONFLICT`。
- Scheduler 对同一任务的节点重试：沿用已经持久化的 seed，保证可复现。

## 3. TypeScript 调用示例

```ts
export type ModelViewInpaintInput = {
  prompt?: string;
  whiteModel: File;
  materialMultiView: File;
};

export async function generateModelViewInpaint(
  input: ModelViewInpaintInput,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<{ image: Blob; jobId: string | null; sha256: string | null }> {
  const form = new FormData();
  const prompt = input.prompt?.trim();
  if (prompt) form.append("prompt", prompt);
  form.append("image", input.whiteModel);
  form.append("material_image", input.materialMultiView);

  const response = await fetch("/api/v1/services/modelview-inpaint", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: form,
    signal,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.message ?? `生成失败：HTTP ${response.status}`);
  }

  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}

// 首次生成和用户主动“重新生成”各创建一次；网络重试必须复用。
const idempotencyKey = crypto.randomUUID();
```

浏览器不要手工设置 `Content-Type`，让运行时自动生成 multipart boundary。结果预览用
`URL.createObjectURL` 时，替换和卸载后应调用 `URL.revokeObjectURL`。

## 4. 前端改动清单

1. 保留白模和参考多视图两个文件槽。
2. 新增一个可为空的提示词输入框，最长 4096 字符。
3. 提交字段严格使用 `prompt`、`image`、`material_image`。
4. 不提供 seed 输入框，不在 `parameters` 中写 seed。
5. 区分连接重试和重新生成：前者复用幂等键，后者换新键。
6. 成功时读取 PNG 响应体以及 `X-Job-ID`、`X-Artifact-SHA256`。
7. 删除 `viewport_reference` 的控件、校验、预览和 `FormData.append`。

## 5. 工作流固定身份

- 用户文件：`Flux2 Klein TrueV3-双图材质编辑-精简测试.json`
- UI SHA-256：
  `740115ae0ca6d00a072eed3c3ba150d753d9b3cedb8f9a21c11040d6e442db07`
- 不可变工作流版本：
  `2026.08.26-740115a-truev3-gguf-3input-rseed-r1`
- API 模板原始文件 SHA-256：
  `7640110dcad0885a3b2b891342e3e81761476216aaa818a64680cce8d79f85c1`
- API 模板规范化 SHA-256：
  `e78e21a0419a37aa030a2467f48120ff1c012b233087f24750a0bdfe9cc1c542`
- 主模型：`Flux2-Klein-9B-True-V3-Q5_K.gguf`
- 唯一输出：`CherryAlignReference #33 output 1 -> SaveImage #29`
- 工作流实际采样步数：`BasicScheduler #15 steps=2`。

节点标题虽然仍写“12步”，但 JSON 的真实执行值是 2。本次部署按用户文件逐字同步，
不会用标题猜测并改写参数。

## 6. 节点和硬件边界

新工作流新增并固定以下运行依赖：

- `ComfyUI-GGUF`：`UnetLoaderGGUF`；
- `WAS-Node-Suite 3.0.1`：`Text Concatenate`；
- `ComfyUI_tinyterraNodes 2.0.11`：`ttN text`。

工作流最低显存仍为 24000 MiB，只兼容 4090、3090-A、3090-B。4070 Ti 不安装、
不启用这条局部重绘工作流。LoRA 的 `flux-kelin/` 路径使用软链接指向已有规范文件，
不会重复占用 165 MB 模型空间。

## 7. 回滚

回滚时重新启用上一不可变版本
`2026.08.22-02b2504-truev3-2input-rseed-r1`，并在三台 24 GiB 节点恢复上一 ComfyUI
镜像及已备份的 UI 文件。不得删除历史任务、旧 WorkflowVersion 或审计记录。

## 8. 生产验收

2026-08-26 已在零运行任务门禁下逐台滚动 3090-A、3090-B、4090。滚动只重建各机
ComfyUI 服务，API、Scheduler、Asset API、Windows Substance Baker 和 4070 Ti 均未替换。
三台恢复后均为 `ONLINE / ACTIVE / current_jobs=0`。

### 8.1 三节点一致性

| 项目 | 4090 | 3090-A | 3090-B |
| --- | --- | --- | --- |
| ComfyUI tag | `projects-0.2.6` | `projects-0.2.6` | `projects-0.2.6` |
| image ID | `sha256:5bdf5c62118881afe5439ea69557618e13dc3da250764e308e245d0e0876844d` | 同左 | 同左 |
| bundle SHA-256 | `61b20c9351250d3a6f39d8eb37678b1bde2ac7cd7aad9775c534ed982a10da63` | 同左 | 同左 |
| lock SHA-256 | `0b3b59a8048bce4f1daf13e58eb0c4909a70d10758876d982e117d4a801661b2` | 同左 | 同左 |
| UI JSON SHA-256 | `740115ae0ca6d00a072eed3c3ba150d753d9b3cedb8f9a21c11040d6e442db07` | 同左 | 同左 |
| 新增类 | `Text Concatenate`、`ttN text`、`UnetLoaderGGUF` 均存在 | 同左 | 同左 |
| GGUF/LoRA 可见 | 是 | 是 | 是 |

GGUF 文件三机 SHA-256 均为
`8167105716c715be31018f682916b5b9988f9afec4e20d7ad7cd9b42aa9774ce`；嵌套 LoRA
路径三机 SHA-256 均为
`5352ada24a83b36e7bf8b3004eae5f6b1676479f93e0d002c9f521d133804fb9`。

WAS Node Suite 启动时会输出其上游的模型目录写权限提示。生产继续坚持模型挂载只读，
不会为消除提示而放开写权限；`Text Concatenate` 已在三机注册，并由下述三次真实任务
成功执行，因此该提示不影响本工作流。

生产已启用 `2026.08.26-740115a-truev3-gguf-3input-rseed-r1`，并禁用上一版本
`2026.08.22-02b2504-truev3-2input-rseed-r1`。数据库兼容性结果恰好为三台 24 GiB
节点通过；4070 Ti 因显存仅 12282 MiB 且缺少新增类保持不兼容。

### 8.2 逐节点真实回归

三次请求均使用相同白模和材质多视图，后端各生成了不同随机 seed。3090-A 特意省略
`prompt`，确认空提示词节点也可正常执行；4090 和 3090-B 使用非空 prompt。

| 节点 | job ID | prompt ID | seed | HTTP / 耗时 | 输出 |
| --- | --- | --- | ---: | --- | --- |
| 4090 | `9fe6c34b-1d3e-45ca-bd5c-523ad5f8dd83` | `733569d1-9aaa-4a4f-9098-114bba798da9` | `1102520553916267` | `200 / 12.106s` | 2048×2048 RGB PNG，1,271,806 B，`dc967fbb293f457172561d795a25c6fa768026c660936f6991527c09c5939ef5` |
| 3090-A | `16f0e9ef-2e0a-45fc-83e3-b52154ec3393` | `2f459a76-085c-46d5-9822-51188dd9ddbf` | `1047880533910591` | `200 / 18.124s` | 2048×2048 RGB PNG，1,373,458 B，`a8d31db0a8afd085cdd00ecc7bdeac9640ba15e5d77c5d0d9b1461c6c5f1fc94` |
| 3090-B | `ff4d44ff-4b9a-41be-8866-dc3068df9b35` | `a9a43bfb-0368-4ae0-b39e-9768e62d3b4f` | `319407867417238` | `200 / 35.127s` | 2048×2048 RGB PNG，1,290,655 B，`7973a8785895843746aaa3b75384d447761f4131caaf942e3a43818e08a1ec42` |

三份渲染快照均确认：`#1` 使用 Q5_K GGUF、`#14` 使用任务级随机 seed、`#15`
实际为 2 步、`#21` 使用 `flux-kelin/` LoRA、`#41` 接收用户 prompt、`#29` 是唯一
`SaveImage` 输出。最终集群队列和运行任务均为 0，接单状态已恢复。
