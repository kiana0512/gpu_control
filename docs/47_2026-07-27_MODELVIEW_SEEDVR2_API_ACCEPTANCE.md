# ModelView 局部重绘 API（可选提示词 + SeedVR2）调用与验收记录

状态：`PRODUCTION ENABLED / THREE NODES ONLINE ACTIVE / REAL IMAGE PASSED`
日期：2026-07-27
统一入口：`https://10.3.34.11/api/v1/services/modelview-inpaint`

## 1. 给调用方的最短结论

- 请求使用 `multipart/form-data`。
- `image` 必填；`prompt` 可选，最长 4096 个 UTF-8 字符。
- 在已登记的局域网来源 IP 下无需 API Key；已分配 API Key 的客户端也可以发送
  `X-API-Key`。
- HTTP 请求会等待任务完成；成功时响应体就是唯一的最终 PNG，不是 JSON，也不是中间图。
- 最终图片固定来自 `SeedVR2VideoUpscaler #110 -> SaveImage #9`。
- 响应头 `X-Job-ID` 是端到端追踪 ID，调用方必须记录。
- 建议客户端超时为 1900 秒。排队、冷启动或首次切换工作流时可能明显慢于稳态。
- 每次新的业务代次使用新的 `Idempotency-Key`；网络重试必须复用原 key 和完全相同的请求体。

## 2. 带提示词的 cURL

生产调用应信任集群 CA，不建议长期使用 `-k`：

```bash
curl --fail-with-body --show-error \
  --cacert /path/to/lan-ca.crt \
  --max-time 1900 \
  --dump-header response.headers \
  --request POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  --header 'Idempotency-Key: asset-123:inpaint:g2:attempt-1' \
  --form 'image=@input-with-mask.png;type=image/png' \
  --form 'prompt=修复蒙版区域的破损边缘，保持主体结构、视角、光照和其他区域不变' \
  --output result-inpaint.png
```

如果管理员为调用方分配了 API Key，再增加：

```bash
--header 'X-API-Key: <client-api-key>'
```

## 3. 不带提示词的 cURL

```bash
curl --fail-with-body --show-error \
  --cacert /path/to/lan-ca.crt \
  --max-time 1900 \
  --dump-header response.headers \
  --request POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  --header 'Idempotency-Key: asset-124:inpaint:g1:attempt-1' \
  --form 'image=@input-with-mask.png;type=image/png' \
  --output result-inpaint.png
```

省略 `prompt` 时，服务会把 Qwen 节点的用户提示词设为空字符串，由 Qwen 根据输入图片自动反推；
这不是跳过 Qwen。传入 `prompt` 时，每个任务的独立渲染副本会用本次值覆盖
`Qwen3 VL Plus #94.inputs.prompt`，不会沿用上一单，也不会修改全局模板。

## 4. Python 调用示例

```python
from pathlib import Path
import hashlib
import requests

url = "https://10.3.34.11/api/v1/services/modelview-inpaint"
ca_file = "/path/to/lan-ca.crt"
input_file = Path("input-with-mask.png")
output_file = Path("result-inpaint.png")

headers = {
    "Idempotency-Key": "asset-123:inpaint:g2:attempt-1",
    # 如果已分配 API Key，再启用下一行：
    # "X-API-Key": "<client-api-key>",
}
data = {
    "prompt": "修复蒙版区域的破损边缘，保持主体结构、视角、光照和其他区域不变",
}

with input_file.open("rb") as image_handle:
    response = requests.post(
        url,
        headers=headers,
        data=data,
        files={"image": (input_file.name, image_handle, "image/png")},
        verify=ca_file,
        timeout=(10, 1900),
    )

response.raise_for_status()
content_type = response.headers.get("Content-Type", "")
if not content_type.startswith("image/"):
    raise RuntimeError(f"unexpected content type: {content_type}")

output_file.write_bytes(response.content)
print("job_id=", response.headers.get("X-Job-ID"))
print("client_id=", response.headers.get("X-Client-ID"))
print("sha256=", hashlib.sha256(response.content).hexdigest())
print("result=", output_file)
```

不带提示词时删除 `data` 中的 `prompt`，或者传入空字符串。业务侧推荐直接省略字段。

## 5. 幂等与重试

`Idempotency-Key` 必须表达“同一个业务输入、同一个生成代次、同一次逻辑尝试”。推荐：

```text
<asset-or-shot-id>:inpaint:g<generation>:attempt-<attempt>
```

- 请求超时、连接中断或未拿到响应：复用原 key、原图片和原 prompt。
- 修改图片或 prompt：必须升级 generation 或 attempt，使用新 key。
- 同 key、同请求重复提交会返回同一个 Job/结果，不会再次执行 GPU 推理。
- 同 key 却改变请求内容会被拒绝，禁止调用方把旧任务偷偷改成新任务。

2026-07-27 的真实验收中，同 key、同 payload 重试在 `1.040549s` 返回；两次返回 PNG 的
SHA-256 完全相同：

```text
0d6624f46d97282266d389abbe68dacd733b0c595a6a0ece16e365d17914449b
```

## 6. 状态码和调用方处理

| 状态码 | 含义 | 调用方动作 |
|---|---|---|
| 200 | 成功，响应体是最终图片 | 保存图片与 `X-Job-ID` |
| 400/422 | 字段、图片、prompt 或 parameters 冲突 | 修正请求；不要原样重试 |
| 401/403 | 来源 IP/API Key 未授权 | 联系管理员登记客户或密钥 |
| 409 | 幂等冲突或状态冲突 | 核对 key；新业务代次使用新 key |
| 429 | 客户并发、排队或配额达到限制 | 读取响应并退避重试 |
| 5xx | 服务端暂时失败 | 保留同 key 和同 payload 退避重试并上报 Job ID |

服务端排队时，调用方当前保持 HTTP 连接等待。不要因“暂时没有返回图片”就生成新 key 重提，
否则会制造重复任务。需要异步批量处理时，应使用批次 API，而不是并发堆积同步图片接口。

## 7. 本次生产版本与真实结果

```text
GPU Control API/Scheduler/Web: 1.3.4
ComfyUI image: registry.local:5000/gpu-control/comfyui:projects-0.2.3
ModelView approved implementation: 8c37f07b0a8ed87a94f4159c173d3d2e03a20b61
ModelView remote HEAD after audit revert: c58249a29c2cc1b1e0cdeef5d26f27265ca28220
ModelView workflow JSON SHA-256: eec3a66ded9290b8d7f5c2eb1cbfdeaeec7acd5d5260c08266a8430750d0eaaf
Workflow: modelview-inpaint:2026.07.27-8c37f07-seedvr2
Workflow template SHA-256: df13ca08fab5a20cb57aaa07ef81d78ca1e9aaa3e541715e4ba03eeb3bf86ccd
```

三节点均为 `ONLINE / ACTIVE`。调度器只在节点从一种工作流切换到另一种工作流时显式调用
ComfyUI `/free`；GPU Control 不改动外部工作流内部的缓存参数。

真实结果：

| 节点 | 模式 | Job ID | HTTP | 结果 | 端到端耗时 |
|---|---|---|---:|---|---:|
| 3090-A | 带 prompt，冷启动 | `d2b9fde8-790a-461c-931e-c9d453864677` | 200 | 2048×1152 PNG | 29.105s |
| 3090-B | 不带 prompt，冷启动 | `76572f1d-504d-4027-98df-bef82334c639` | 200 | 2048×1152 PNG | 39.101s |
| 4090 | 带 prompt，冷启动 | `e17a371f-54b8-428f-92e9-5634ff792501` | 200 | 2048×1152 PNG | 25.096s |
| 4090 | 带 prompt，原工作流连续请求 | `203c73bb-5b6e-4b97-9a38-9042c44655f7` | 200 | 2048×1152 PNG | 17.089s |

当前端到端连续请求仍未达到 10 秒目标；主要剩余时间在外部 Qwen 图像理解、Flux 局部重绘以及
SeedVR2 推理，不能把它描述为 10 秒内完成。一次越界的工作流缓存/Qwen 节点实验已用 Git revert
完整撤销，三机文件 SHA 已重新对齐；实验版本保持 disabled，仅作为数据库审计记录存在。后续性能
调整只允许发生在 GPU Control 自有边界内，并继续以输出正确性为门禁，禁止通过修改同事工作流或
返回 SeedVR2 前中间图来伪造低延迟。仓库根目录 `AGENTS.md` 已固化这条约束。
