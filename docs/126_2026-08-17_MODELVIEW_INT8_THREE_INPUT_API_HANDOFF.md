# ModelView INT8 局部重绘三输入 API 对接文档

## 1. 版本与不变项

- GPU Control：`1.5.17`
- 工作流：`2026.08.17-a9dbbca-flux2-klein-truev3-3input-r2`
- ModelViewCreator 批准提交：`a9dbbca846ee80734d0a6123ac32d8a8e51c7fcd`
- 主模型：`Flux2-Klein-9B-True-V3-int8mixedrow.safetensors`
- 路径不变：`POST /api/v1/services/modelview-inpaint`
- 响应不变：同步返回唯一最终 PNG，不返回中间图或 JSON 任务封装。

GGUF 版本不进入生产。生产保留批准工作流的 12 步、Euler、Simple、
`denoise=1.0`、LoRA `0.8`和原始提示词，本次没有用降步数换速度。

## 2. multipart 请求

| 字段 | 必填 | 含义 | 工作流绑定 |
| --- | --- | --- | --- |
| `image` | 是 | 白模主图；唯一几何、构图、视角和轮廓来源 | `LoadImage #4` |
| `material_image` | 是 | 六视图材质参考；只用于材质、颜色分区和纹理 | `LoadImage #5` |
| `viewport_reference` | 是 | 视窗参考图；只供最终对齐和调色 | `LoadImage #26` |
| `prompt` | 否 | 可选补充要求；省略时使用版本锁定的几何保护提示词 | `CLIPTextEncode #9` |
| `parameters` | 否 | JSON 对象字符串；当前只允许工作流 manifest 声明的字段 | 服务端校验 |

三个图片字段都必须传入真实 PNG/JPG/JPEG/WEBP 图片。每张图默认上限
50 MiB，解码后像素总数默认上限 40,000,000。任一张缺失或无法解码都返回
HTTP `422`，不会进入 GPU 队列。

最小请求只需要三个同名文件字段，不要把图片转成 base64，也不要手工设置
`Content-Type`；浏览器或 HTTP SDK 会自动生成 multipart boundary。

## 3. cURL 示例

```bash
curl -k -X POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  -H 'Idempotency-Key: modelview-asset-001-attempt-1' \
  -F 'image=@white-model.png' \
  -F 'material_image=@six-view-material.png' \
  -F 'viewport_reference=@viewport-reference.png' \
  --output result.png
```

如果客户已分配 API Key，再加：

```bash
-H 'X-API-Key: gpc_<client>_<secret>'
```

`Idempotency-Key` 建议每个业务任务固定。相同 Key、相同三图哈希和相同参数会复用
原任务；相同 Key 但任一图片或参数不同会返回 HTTP `409`。

## 4. Python 示例

```python
import requests

url = "https://10.3.34.11/api/v1/services/modelview-inpaint"
headers = {"Idempotency-Key": "modelview-asset-001-attempt-1"}

with (
    open("white-model.png", "rb") as white_model,
    open("six-view-material.png", "rb") as material,
    open("viewport-reference.png", "rb") as viewport,
):
    response = requests.post(
        url,
        headers=headers,
        files={
            "image": ("white-model.png", white_model, "image/png"),
            "material_image": ("six-view-material.png", material, "image/png"),
            "viewport_reference": ("viewport-reference.png", viewport, "image/png"),
        },
        timeout=1900,
        verify=False,  # 内网自签 CA 环境；正式客户应安装 CA 后改为 True
    )

response.raise_for_status()
with open("result.png", "wb") as output:
    output.write(response.content)

print("job_id:", response.headers.get("X-Job-ID"))
print("sha256:", response.headers.get("X-Artifact-SHA256"))
```

## 5. Web 前端示例

```ts
type ModelViewInputs = {
  whiteModel: File;
  materialSixView: File;
  viewportReference: File;
  prompt?: string;
};

export async function runModelViewInpaint(inputs: ModelViewInputs) {
  const form = new FormData();
  form.append("image", inputs.whiteModel);
  form.append("material_image", inputs.materialSixView);
  form.append("viewport_reference", inputs.viewportReference);
  if (inputs.prompt) form.append("prompt", inputs.prompt);

  const response = await fetch("/api/v1/services/modelview-inpaint", {
    method: "POST",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      // 已分配外部客户密钥时再增加："X-API-Key": apiKey
    },
    body: form,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      error?.detail?.message ?? `局部重绘失败（HTTP ${response.status}）`,
    );
  }

  return {
    imageUrl: URL.createObjectURL(await response.blob()),
    jobId: response.headers.get("X-Job-ID"),
    sha256: response.headers.get("X-Artifact-SHA256"),
  };
}
```

浏览器建议通过 GPU Control 同源域名访问。不要在浏览器代码里关闭 TLS 校验；内网
自签证书应安装 CA，或者由业务后端代发请求。当前是同步长请求，反向代理和业务端
超时建议至少 `1200s`，界面需要展示“生成中”并防止用户重复提交。取消浏览器请求
只会停止客户端等待，不保证已经进入 GPU 的任务被撤销。

## 6. 成功响应

- HTTP `200`
- `Content-Type: image/png`（以实际产物为准）
- `X-Job-ID`: 服务端任务 ID
- `X-Client-ID`: 认证客户 ID
- `X-Artifact-SHA256`: 返回文件的 SHA-256
- `Cache-Control: no-store`
- Body：`SaveImage #32` 产出的唯一最终图

前端收到 Blob 后应在组件卸载或替换预览时执行
`URL.revokeObjectURL(imageUrl)`，避免浏览器内存持续增长。

## 7. 常见错误

| HTTP | `detail.code` | 说明 |
| ---: | --- | --- |
| 401/403 | 鉴权相关 | API Key、来源 IP 或优先级不允许 |
| 409 | `IDEMPOTENCY_CONFLICT` | 相同幂等 Key 对应了不同请求 |
| 422 | `INPUT_INVALID` | 缺图、非图片、图片过大或参数不合法 |
| 429 | `RATE_LIMITED` | 客户或系统队列已达上限 |
| 500 | `GENERATION_FAILED` / `GPU_OOM` | ComfyUI 执行失败；应携带 `X-Job-ID` 或错误中的 `job_id` 报障 |
| 504 | `SERVICE_TIMEOUT` | 同步等待超过工作流时限 |

业务校验错误体通常是：

```json
{
  "detail": {
    "code": "INPUT_INVALID",
    "message": "错误说明"
  }
}
```

如果请求在进入业务处理前就缺少 multipart 必填字段，FastAPI 会返回标准 `422`
校验数组，此时 `detail` 是数组而不是上述对象；前端需同时兼容两种结构。

## 8. 调度与显存安全

- 只有 `control-4090`、`worker-3090-a`、`worker-3090-b` 兼容此工作流。
- `worker-4070ti-animation-host-01` 总显存不足 24,000 MiB，在兼容表中硬失败，
  不会领取局部重绘。
- 4090 是跨算力池首选；新局部重绘到达时刷新可续期的 10 分钟保护窗口。
- 从其它模型家族切入前，Scheduler 必须等待 ComfyUI 队列清空，调用 `/free`，并验证
  显存已恢复；不达标则节点自动 `DRAINING`，不提交新 prompt。
- 三台 24 GiB ComfyUI 使用 `--reserve-vram 2.0`。4090 与 3090-A 已按默认 12 步分别
  完成冷/热实测，均无 OOM；4090 峰值 22,156 MiB，3090-A 峰值 20,989 MiB。
- 4070 Ti 只被排除在本局部重绘工作流之外，仍可正常领取抠图、粗糙度等兼容任务。

## 9. 已完成的 API 联调验收

- 生产 API 版本：`1.5.17`
- 成功任务：`634b3745-a5a0-4498-92a8-9934ee1aa99e`
- 调度节点：`control-4090`
- 输入：三个独立文件字段，数据库记录的文件名和工作流绑定全部一致
- 输出：单张 `2048 x 2048` RGB PNG
- 输出 SHA-256：`59c5c0507f6fbcb977c03cbf2f30861f616b271fd0914bda6c89209b8af3b928`
- 缺少 `material_image` 或 `viewport_reference` 的请求已验证返回 HTTP `422`
- 4090 局部重绘保护窗口实测为准确的 10 分钟；4070 Ti 未被错误打上保护标签

这组验收证明本章接口已经可以供前端对接。3090-B 的 WSL2 性能治理属于后端节点
优化，不改变请求字段、响应格式或外部 URL。

## 10. 后端性能基线（默认工作流参数）

| 节点 | 冷启动 | 热启动 | 峰值显存 | OOM | 当前结论 |
| --- | ---: | ---: | ---: | --- | --- |
| 4090 | 22.250s | 16.677s | 22,156 MiB | 否 | 正常，局部重绘首选 |
| 3090-A | 35.350s | 30.518s | 20,989 MiB | 否 | 正常，扩展节点 |
| 3090-B / WSL2 | 82.807s | 58.398s | 18,769 MiB | 否 | 功能正确；暂在治理 WSL2 传输性能 |

3090-B 的结果证明不会 OOM，但当前时延不作为性能承诺。后端会在不改变本接口的
前提下继续优化；优化完成后再更新该行数据。

## 11. 发布与回滚标识

生产发布逐台执行 `DRAINING -> current_jobs=0 -> 替换 -> 预检/真实 canary -> ACTIVE`，
不会抢占正在运行的普通任务。

- ComfyUI 镜像：`registry.local:5000/gpu-control/comfyui:projects-0.2.5`
- 镜像 ID：`sha256:fcb49c98cdeb5c6b702a72bf4ea86b2b39e6be12d1c9b2264c2deceda5f37a05`
- 离线归档 SHA-256：`e5da4d13435f247c7befdbec5373eaa694cb8b90f2997e9624000502d8217435`
- 4090 回滚容器：`comfyui-4090-rollback-20260817-truev3`（已停止保留）

对接方只依赖本文件前九节；镜像、模型、调度与 WSL2 调优均为服务端内部实现。
