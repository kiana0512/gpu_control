# 公共图片 API

外部统一入口为 `https://CONTROL`，当前主控地址为 `https://10.3.34.11`。用户软件不应
直接访问任何 GPU 节点的 `8188`。

默认按真实来源 IP 自动识别客户，无需 API Key。新 IP 第一次调用时自动建立客户记录，
不同 IP 分别计算限流、排队、并发和日配额。管理员可在“API 客户”页查看最后访问 IP、
最后访问时间和配额；IP 白名单作为可选限制能力保留，不需要预先录入。

链路为：

`用户上传图片 → 统一 API → PostgreSQL 任务队列 → Scheduler 分配 GPU → 监控 ComfyUI → 返回图片或失败 JSON`

## 直接图片服务（推荐给现有软件）

上传字段固定为 `image`。成功时 HTTP 响应体就是最终图片，响应头 `X-Job-ID` 用于任务跟踪。
失败时返回 JSON，包含 `detail.code`、`detail.message` 和已创建任务的 `detail.job_id`；维护
人员可用 `job_id` 在任务页或日志中心排查。

ImageClip 抠图，返回最终 RGBA PNG：

```bash
curl -X POST 'https://10.3.34.11/api/v1/services/imageclip-rgba' \
  -H 'Idempotency-Key: order-001-attempt-1' \
  -F 'image=@input.png' \
  --output result-rgba.png
```

ModelView 局部重绘，返回最终输出图：

```bash
curl -X POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  -H 'Idempotency-Key: order-002-attempt-1' \
  -F 'image=@input.png' \
  --output result-inpaint.png
```

PBR 粗糙度生成，返回最终粗糙度图片。该接口只接收图片；工作流提示词由生产版本固定，
调用方不能传入或覆盖：

```bash
curl -X POST 'https://10.3.34.11/api/v1/services/modelview-roughness' \
  -H 'Idempotency-Key: material-roughness-001-attempt-1' \
  -F 'image=@material.png' \
  --output result-roughness.png
```

Python 示例：

```python
import requests

with open("input.png", "rb") as source:
    response = requests.post(
        "https://10.3.34.11/api/v1/services/imageclip-rgba",
        headers={"Idempotency-Key": "order-001-attempt-1"},
        files={"image": ("input.png", source, "image/png")},
        timeout=1900,
    )

response.raise_for_status()
with open("result-rgba.png", "wb") as output:
    output.write(response.content)
print("job_id:", response.headers.get("X-Job-ID"))
```

客户端应安装内网 CA `deploy/control-plane/nginx/certs/lan-ca.crt`。正式调用不要使用 `-k`。
同步图片服务会持续等待调度和 GPU 执行，客户端超时建议设置为 `1900` 秒。

## 异步与批量任务

批量业务可继续调用 `POST /api/v1/jobs`，表单字段为 `workflow_key`、`workflow_version`、
`parameters` 和 `input_image`。成功立即返回 `202`、`job_id/status_url/events_url`；随后可查询
`GET /api/v1/jobs/{id}`、订阅 `/events`、列出 `/artifacts` 并下载产物。

每次提交建议使用唯一 `Idempotency-Key`。同一 IP、同一 Key、同一内容会返回已有任务；
同 Key 不同内容返回 `409`。

### 动画序列帧批量 ImageClip RGBA

大量序列帧不要逐张调用单图接口，也不要逐张生成顶层任务。使用：

```text
POST /api/v1/batches/imageclip-rgba
GET  /api/v1/batches/{batch_id}
GET  /api/v1/batches/{batch_id}/manifest
GET  /api/v1/batches/{batch_id}/events
GET  /api/v1/batches/{batch_id}/artifacts/{artifact_id}
POST /api/v1/batches/{batch_id}/cancel
```

创建请求为严格 manifest 1.0 + `ZIP_STORED`；一个业务批次只显示一个父任务，GPU Control 在内部
把帧动态分配给三台 GPU。只有每帧都成功且输出 PNG/Alpha/SHA 完整校验后才提供结果 ZIP。

父状态的 `performance.nodes[]` 只序列化服务端持久证据，关键字段口径如下：

| 字段 | 权威口径 |
|---|---|
| `gpu_model` | 可选遥测：Node Agent 通过 `nvidia-smi --query-gpu=name` 上报并经长度/字符校验后保存；查询失败时省略且定期重试，UUID/IP/pipeline 心跳不受影响；旧 Agent 未上报时为 `null` |
| `frames_assigned` | 该节点持久 `JobAttempt` 涉及的唯一 child job 数；一次帧从 A 改派到 B 会在 A、B 各计一次 |
| `frames_final_assignment` | 当前/最终 `JobBatchItem.node_id` 指向该节点的帧数，用于与 `frames_assigned` 区分 |
| `node_started_at` | 该节点真实 attempt 中最早的 `gpu_started_at` |
| `node_finished_at` | 仅当该节点所有出现 GPU 起止证据的 attempt 都有合法成对时间时，返回最晚 `gpu_finished_at`；否则为 `null` |
| `reassignments_in/out` | 同一 child job 相邻 attempt 的节点发生变化时，新节点 `in + 1`、原节点 `out + 1` |
| `max_concurrent_prompts` | 节点表中 Scheduler 当前使用的 `max_concurrency` 槽位上限；不是前端估算的历史峰值 |
| `gpu_service_ms`、`frame_ms_p50/p95` | 只聚合具有合法 `gpu_started_at → gpu_finished_at` 的真实 attempt；覆盖是否完整见 `gpu_service_measurements_complete` |

父级 `straggler_ratio` 严格按联合合同定义为
`(最晚 node_finished_at - 各参与节点 node_finished_at 的中位数) / parent_gpu_wall`，其中
`parent_gpu_wall = execution_finished_at - started_at`。只有批次已进入终态、至少两个参与节点、所有参与
节点均有权威 `node_finished_at`、节点完成时间位于父 GPU wall 内且父 GPU wall 为正数时才返回数值；时间
缺失、负数或越界时返回 `null`。该字段不使用各节点累计 `gpu_service_ms` 计算。每个成功帧还必须在其最终分配节点上至少有一条完整 GPU 样本，否则父级
`gpu_service_measurements_complete=false`，吞吐和拖尾均为 `null`。`scheduler_restarts` 在没有批次绑定
的持久重启证据前固定返回 `null`，不得推测。

动画管家不得根据早期草案自行增加 callback、best_effort 或 retry-failed 字段。完整字段、幂等、
路径、错误码、结果校验和真实联调清单以
[`38_GPU_CONTROL_MATTING_HANDOFF_V2.md`](38_GPU_CONTROL_MATTING_HANDOFF_V2.md) 为准。

## 调度与基础防护

- 每个来源 IP 默认 `10 requests/s`、突发 `20`，同时最多 `20` 个 API 连接，超限返回 `429`。
- 每个自动发现客户默认还受客户维度 `5 requests/s`、突发 `10` 的限制。
- `max_queued` 控制同一 IP 客户最多排队任务，`max_running` 控制同时运行任务。
- 多个来源 IP 之间由 Scheduler 公平轮转；系统总队列仍受全局上限保护。
- Nginx 会覆盖客户端传入的转发头，只把真实连接 IP 传给仅在 Docker 后端网络开放的 API。
- 单图入口仍受 `MAX_UPLOAD_BYTES=50 MiB`；批次入口由 API 单独限制为 5000 帧、单帧 64 MiB、
  archive/解压总量 100 GiB，Nginx 使用 101 GiB multipart 上限并关闭请求缓冲。

稳定错误码包括 `RATE_LIMITED`、`INPUT_INVALID`、`WORKFLOW_NOT_FOUND`、
`IDEMPOTENCY_CONFLICT`、`SERVICE_TIMEOUT` 和 `GENERATION_FAILED`。同时记录响应中的
`X-Request-ID` 和 `X-Job-ID` 供排障。

## 管理来源 IP 客户

进入管理台“API 客户”页即可看到系统根据真实请求自动建立的客户。业务软件不需要登录
管理台，也不需要填写 API Key；这里只供维护人员管理。

每行“管理设置”可修改：

- 是否允许继续提交新任务；
- 最多排队、最大并发、每日配额和调度权重；
- 固定来源 IP（留空仍按自动发现逻辑处理）；
- 可选回调域名。

后端对应接口为 `PUT /admin/clients/{client_id}`，仅管理员/操作员 JWT 可以调用。接口要求
`confirm=true` 和操作原因，检查 IP 是否被其他客户占用，并把变更前后值写入审计日志。
停用客户只阻止新提交，不删除历史任务、图片产物或审计记录。

## 管理台排障

用户报告失败时，优先记录响应头 `X-Job-ID` 和 `X-Request-ID`：

1. 在“任务”页点击任务行，查看状态、执行节点、Prompt ID、时间和真实错误。
2. 点击“下载诊断包”保存该任务的诊断材料。
3. 在“日志中心”按 Job ID 或 Request ID 检索任务与管理操作；需要容器级日志时再打开 Grafana。
4. 不要让业务软件直接访问 `8188`，也不要让用户自行配置管理台客户或密钥。
