# ModelView PBR 粗糙度 API 对接使用文档

文档版本：`V1.1`
发布日期：`2026-07-29`
服务地址：`https://10.3.34.11`
服务键：`modelview-roughness`
提交接口：`POST /api/v1/services/modelview-roughness`

## 1. 给调用方的结论

这是同步图片接口：上传一张材质图片，HTTP 连接等待 GPU 完成后，响应体直接返回最终粗糙度图片。

- 内网调用默认**不需要 API Key**，服务端按 TCP 连接的真实来源 IP 自动登记和隔离客户数据。
- 不设置、不维护额外 IP 白名单；已有 `X-API-Key` 仅作为兼容用法。
- 上传字段固定为 `image`，调用方不能覆盖生产工作流中的提示词、模型或推理参数。
- 只返回批准的最终输出节点，不把预览图或中间结果当作最终结果。
- 建议客户端超时至少 `1900` 秒；排队和首次模型加载均包含在请求时间内。
- 每次业务尝试携带稳定、唯一的 `Idempotency-Key`，网络重试时复用原值。

## 2. 固定生产版本

| 项目 | 固定值 |
|---|---|
| ModelViewCreator commit | `d318bb392040e2d5f6bbd10ae61d832d36d3cb4a` |
| 原始工作流 SHA-256 | `8a52740b90ac47e77919b460a0e35241c94d91fde035effb3285600642e2ea38` |
| API 模板 SHA-256 | `5752acb2c37dece0dd514a5e75104661fd12f06a19e70d6e190d17155599d865` |
| 工作流版本 | `2026.07.29-d318bb39-roughness-v1` |
| 输入节点 | `323 / LoadImage` |
| 固定提示词节点 | `332 / TextEncodeQwenImageEditPlus` |
| 最终输出节点 | `355 / PreviewImage` |

GPU Control 只同步上述已批准版本、校验模型并绑定输入输出，不修改 ModelViewCreator 工作流本身。

## 3. 请求合同

请求类型为 `multipart/form-data`。

| 位置 | 名称 | 必填 | 说明 |
|---|---|---:|---|
| Form | `image` | 是 | 输入图片；建议 PNG，也支持服务端已允许的常见图片格式 |
| Form | `parameters` | 否 | 当前必须省略或传 `{}`；不接受提示词或推理参数 |
| Header | `Idempotency-Key` | 建议 | 1～128 字符；同一次业务重试必须保持不变 |
| Header | `X-API-Key` | 否 | 默认不需要；只有已显式发放密钥的旧客户端才使用 |

同一来源 IP、同一幂等键、同一输入会复用原任务；同一幂等键对应不同输入会返回冲突，调用方应生成新的业务尝试键。

## 4. cURL 示例

生产调用应信任公司 LAN CA，不要长期使用 `-k`：

```bash
curl --fail-with-body --show-error \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/services/modelview-roughness' \
  -H 'Idempotency-Key: asset-chair-roughness-attempt-1' \
  -F 'image=@material.png;type=image/png' \
  -D roughness-response.headers \
  --output material_roughness.png
```

验证响应与任务 ID：

```bash
file material_roughness.png
grep -iE '^(x-job-id|x-request-id|x-client-id|content-type):' roughness-response.headers
```

## 5. Python 示例

```python
from pathlib import Path
import requests

base_url = "https://10.3.34.11"
ca_file = "GPU_CONTROL_LAN_CA.crt"

with Path("material.png").open("rb") as source:
    response = requests.post(
        f"{base_url}/api/v1/services/modelview-roughness",
        headers={"Idempotency-Key": "asset-chair-roughness-attempt-1"},
        files={"image": ("material.png", source, "image/png")},
        verify=ca_file,
        timeout=(10, 1900),
    )

if not response.ok:
    print(response.status_code, response.text)
    response.raise_for_status()

Path("material_roughness.png").write_bytes(response.content)
print("job_id =", response.headers.get("X-Job-ID"))
print("request_id =", response.headers.get("X-Request-ID"))
```

## 6. 成功响应

- HTTP：`200 OK`
- Body：最终图片二进制
- `Content-Type`：最终图片 MIME 类型
- `X-Job-ID`：GPU Control 内部任务 ID，日志和排障必须保留
- `X-Request-ID`：本次 HTTP 请求链路 ID
- `X-Client-ID`：服务端按来源 IP 自动登记的客户 ID

不要把错误 JSON 按图片保存。调用方必须先判断 HTTP 状态码和 `Content-Type`。

## 7. 排队、节点与性能语义

粗糙度任务进入 GPU 推理队列，可由已完成模型 SHA-256 校验且工作流兼容的 `4090`、`3090-A`、`3090-B` 执行。调度器综合空闲槽位、工作流热缓存、排队长度和节点健康选择节点。3090-B 原生 Windows Baker 占用物理 GPU 时，3090-B WSL ComfyUI 会被围栏，粗糙度任务自动留在队列或转给其他节点。

接口是同步返回，因此调用方体验为“等待排队 → 执行 → 收到最终图片”。客户端断线不会把预览图当成结果；使用相同幂等键重试可找回同一次业务尝试。

## 8. 常见错误

| HTTP | `detail.code`（示例） | 处理方式 |
|---:|---|---|
| 400/422 | `VALIDATION_ERROR` 或输入错误 | 检查字段名必须为 `image`，文件必须是有效图片 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一键上传了不同内容；使用新的尝试键 |
| 429 | 配额/排队限制 | 按响应提示退避，不要高频立即重试 |
| 503 | 暂无兼容节点 | 等待节点恢复；不要绕过调度直连 ComfyUI |
| 5xx | 执行或基础设施错误 | 保存 `X-Job-ID`、`X-Request-ID` 和响应 JSON 后联系管理员 |

## 9. 与 Substance Baker 串联

粗糙度是三节点 ComfyUI GPU 图片能力；Substance Baker 是 3090-B 原生 Windows 独占能力。上层业务可以先取得粗糙度图片，再独立提交 Baker，但两者是不同任务、不同幂等键和不同结果合同。任何阶段失败均不得发布中间产物。

## 10. 调用方验收清单

1. 使用 LAN CA 成功完成 TLS 校验。
2. 不带 `X-API-Key` 可从真实来源 IP 调用。
3. 返回 HTTP 200 且响应是可解码图片。
4. 保存 `X-Job-ID` 与 `X-Request-ID`。
5. 同一输入和幂等键重试不会产生不同业务结果。
6. 不向 `parameters` 注入提示词、模型或采样参数。
