# ModelView 单视图生成 API 前端对接文档

更新时间：2026-08-26

业务名称：单视图生成

任务中心工作流：`modelview-single-view`

生产入口：`POST https://10.3.34.11/api/v1/services/modelview-single-view`

## 1. 接口用途

前端上传一张白模图、一张参考多视图，并可附加一段提示词。任务中心同步等待 GPU
工作流完成，成功后直接返回一张最终 PNG。

该服务和“局部重绘”输入输出一致，但任务身份和工作流版本独立。单视图生成固定执行
用户批准工作流中的 `BasicScheduler #15 steps=4`；局部重绘继续使用自己的 2 步版本。
两者同级排队并共用 ModelView 热模型缓存。

## 2. 请求

### 2.1 HTTP

```http
POST /api/v1/services/modelview-single-view
Content-Type: multipart/form-data
Idempotency-Key: <每次新生成使用的新键>
X-API-Key: <按客户配置，可选>
```

当前控制面支持两种身份识别：传 `X-API-Key` 时校验 Key；不传 Key 时按来源
IP 匹配客户，未见过的 IP 会自动登记为生产客户。新集群正式上线前建议运维先固定
出口 IP 并绑定客户；需要跨出口或更强身份隔离时使用 API Key。浏览器必须信任
`10.3.34.11` 使用的 HTTPS 证书。

### 2.2 multipart 字段

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `image` | PNG/JPEG/WebP 文件 | 是 | 白模图 |
| `material_image` | PNG/JPEG/WebP 文件 | 是 | 参考多视图 |
| `prompt` | UTF-8 字符串 | 否 | 补充生成要求，最长 4096 字符；空字符串按未填写处理 |
| `parameters` | JSON 字符串 | 否 | 高级兼容字段，普通前端请省略或传 `{}` |

前端不能传 `noise_seed`。服务端会为每个新任务生成随机 Seed，并写入
`RandomNoise #14.inputs.noise_seed`。同一任务的调度重试保留原 Seed。

## 3. cURL 示例

```bash
curl -k -X POST 'https://10.3.34.11/api/v1/services/modelview-single-view' \
  -H 'Idempotency-Key: single-view-asset-10001-generation-1' \
  -H 'X-API-Key: <API_KEY>' \
  -F 'image=@white-model.png' \
  -F 'material_image=@reference-multiview.png' \
  -F 'prompt=保持白模结构与轮廓，根据参考多视图生成目标单视图材质' \
  --output 'single-view-result.png'
```

若调用方已加入来源 IP 白名单，删除 `X-API-Key` 请求头即可。生产浏览器或正式客户端
应安装并信任证书，不要长期使用跳过 TLS 校验的方式。

## 4. 浏览器 / TypeScript 示例

```ts
export type SingleViewRequest = {
  whiteModel: File;
  referenceMultiview: File;
  prompt?: string;
  idempotencyKey: string;
  apiKey?: string;
};

export type SingleViewResult = {
  image: Blob;
  jobId: string;
  sha256: string;
};

export async function generateSingleView(
  input: SingleViewRequest,
): Promise<SingleViewResult> {
  const form = new FormData();
  form.append("image", input.whiteModel, input.whiteModel.name);
  form.append(
    "material_image",
    input.referenceMultiview,
    input.referenceMultiview.name,
  );
  const prompt = input.prompt?.trim();
  if (prompt) form.append("prompt", prompt);

  const headers: Record<string, string> = {
    "Idempotency-Key": input.idempotencyKey,
  };
  if (input.apiKey) headers["X-API-Key"] = input.apiKey;

  const response = await fetch(
    "/api/v1/services/modelview-single-view",
    {
      method: "POST",
      headers,
      body: form,
    },
  );

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message =
      payload?.detail?.message ?? payload?.detail ?? `HTTP ${response.status}`;
    throw new Error(String(message));
  }

  return {
    image: await response.blob(),
    jobId: response.headers.get("X-Job-ID") ?? "",
    sha256: response.headers.get("X-Artifact-SHA256") ?? "",
  };
}
```

不要手动设置 `Content-Type`。浏览器会为 `FormData` 自动生成包含 boundary 的正确请求头。

## 5. 成功响应

```http
HTTP/1.1 200 OK
Content-Type: image/png
Content-Disposition: attachment; filename="...png"
X-Job-ID: <任务中心 job id>
X-Client-ID: <客户 id>
X-Artifact-SHA256: <最终图片 sha256>
Cache-Control: no-store
```

响应 Body 就是最终图片二进制。前端应使用 Blob 展示或保存，不要按 JSON 解码。

如需在任务中心定位本次生成，保存 `X-Job-ID`。任务详情会显示“ModelView 单视图生成”、
执行节点、工作流版本、耗时、状态和最终产物。

## 6. Idempotency-Key 与“重新生成”

- 一次真实的新生成必须使用一个从未用过的 `Idempotency-Key`。
- 相同 Key、相同图片、相同提示词：返回原任务和原结果，不产生新 Seed。
- 相同 Key 但请求内容不同：返回 HTTP 409。
- 网络超时后的原请求重试：继续使用原 Key，避免重复生成。
- 用户点击“重新生成”：创建新 Key，例如把末尾 generation 从 `1` 改成 `2`。

推荐格式：

```text
single-view-<资产ID>-generation-<递增编号或UUID>
```

## 7. 排队与超时

- 单视图生成与局部重绘同为最高交互优先级，二者之间按提交时间先到先执行。
- 可同时分配到 4090、3090-A、3090-B；4070Ti 不参与。
- 服务是同步长请求，网关允许长连接。调用端超时建议不小于 2500 秒。
- 调用端主动中断 HTTP 不代表后台任务一定取消；需要排障时使用 `X-Job-ID` 查询任务中心。

## 8. 常见错误

| HTTP | 典型 code/原因 | 前端处理建议 |
|---:|---|---|
| 401/403 | API Key 无效或客户已停用 | 提示联系管理员，不自动重试 |
| 404 | `WORKFLOW_NOT_FOUND` | 服务未启用，提示运维检查版本 |
| 409 | `IDEMPOTENCY_CONFLICT` | 为真正的新生成换一个 Key |
| 422 | 缺图片、图片无效、提示词过长、参数冲突 | 展示服务端 `detail.message` |
| 429 | 队列或客户配额已满 | 退避后重试，保留原 Key |
| 500 | `GENERATION_FAILED` 或工作流执行失败 | 记录 `job_id` 并提示重试 |
| 504 | `SERVICE_TIMEOUT` | 记录返回的 `job_id`，先查任务状态，不要立即换 Key 重复提交 |

## 9. 与局部重绘的接口区分

| 业务 | API | 工作流键 | 采样步数 |
|---|---|---|---:|
| 局部重绘 | `/api/v1/services/modelview-inpaint` | `modelview-inpaint` | 2 |
| 单视图生成 | `/api/v1/services/modelview-single-view` | `modelview-single-view` | 4 |

前端不要通过额外 `mode` 字段复用局部重绘接口。两个按钮分别调用各自 URL，表单字段可以
复用同一个组件。

## 10. 前端验收清单

- [ ] 白模缺失时不允许提交。
- [ ] 参考多视图缺失时不允许提交。
- [ ] 提示词允许为空，最大 4096 字符。
- [ ] 每次点击“重新生成”创建新的 `Idempotency-Key`。
- [ ] 网络重试继续使用原 Key。
- [ ] 成功响应按 Blob 读取，并保存 `X-Job-ID`。
- [ ] 任务中心显示“单视图生成”，而不是“自定义工作流”。
- [ ] 不向 API 发送 `noise_seed`、采样步数、模型名或工作流节点参数。

## 11. 生产部署与真实验收记录

生产发布已于 2026-08-26 完成。

- GPU Control 版本：`1.5.20`
- 源码 revision：`69c00d121284924d733eecbec5eafc9ea9823049`
- 工作流版本：`2026.08.26-c0e6218-single-view-4step-r1`
- UI 工作流 SHA-256：
  `c0e6218a599da460124cc955e2d3fb3125293808f541a67b0e97fabe17579d33`
- API 模板 SHA-256：
  `13cc9c17e4ceecc6149351b119e84c987d813003319758c661cc6237ab2e2818`
- 兼容节点：`control-4090`、`worker-3090-a`、`worker-3090-b`
- 不兼容节点：`worker-4070ti-animation-host-01`（12282 MiB 显存低于
  24000 MiB 门槛）

控制面镜像：

| 组件 | 镜像 | image ID |
|---|---|---|
| API | `gpu-control-api:1.5.20` | `sha256:e549c331fbf936e7a7aafe310a6528015bac5a82d6598bbb5b665f23f2c1bdfe` |
| Scheduler | `gpu-control-scheduler:1.5.20` | `sha256:b426ce33c3f1e49b6334ca7c209a3db36a5cd484bb6c96b8a2077b1f5f4ac12e` |
| Web | `gpu-control-web:1.5.20` | `sha256:5c45817b41f57b21abb1fcf7deb989746d4e2b01364ee1b20e03101da600d23d` |

真实三节点 canary：

| 节点 | Job ID | 服务端 Seed | GPU 执行耗时 | 结果 SHA-256 |
|---|---|---:|---:|---|
| 4090 | `829974f3-2b4c-4e2c-8abc-f6380ce665cd` | `327978606625285` | 11 s | `a7e2dfc25ba87f191af83c5da3ec632f001c1250165e59304848d64081d6ec8a` |
| 3090-A | `6ee57b8c-d569-4f5b-ae04-59ed17399475` | `19200809589581` | 20 s | `3d5868c7970a52317379a77ddb747158870cfaeb903c36349af4bebadb8fc9d2` |
| 3090-B | `712e816b-2337-444a-b46d-12e21c94a6f7` | `868946067193107` | 50 s | `e8d1d9543c7025330be9ab0fc80ad38e1fcfdf22bafd110101c0faff31c6f439` |

三笔任务均验证为 `critical + pinned`、`BasicScheduler steps=4`、节点 29
`SaveImage`、2048×2048 RGB PNG。三个 Seed 互不相同。相同幂等键重放在 1 秒内返回
3090-B 原 Job 和原 SHA，没有产生第二笔推理。

局部重绘与单视图生成切换时，三个 canary 的 `free.response.json` 均记录
`skipped=true`，证明同模型家族没有被误判为切换并重新加载。

三台 ComfyUI 的 UI 工作流已与用户文件 SHA 对齐。本次只原子同步工作流
文件，没有重启 ComfyUI。4090 旧文件备份位于：

```text
/opt/gpu-control/backups/modelview-single-view-pre-20260826-1507/control-4090.previous.json
```

发布结束后四台 GPU 均恢复为 `ACTIVE / ONLINE / current_jobs=0`。
