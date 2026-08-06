# 自动拓扑 V6 多 FBX 并行用户端对接

## 目标

用户在自动拓扑 V6 窗口一次选择多个 `.fbx`。每个 FBX 必须创建一个独立的
`RETOPOLOGY_PROCESS_V2` 任务，由 GPU Control 按可用 V6 Agent Worker 并行调度。

不要把多个高模合进一个 FBX、一个 ZIP 或一个后端任务。独立任务可以保证：

- 每个模型独立排队、运行、取消、重试和下载；
- 一个模型失败不影响同批其他模型；
- 每个模型保留自己的源 SHA、V6 Skill/policy 身份、QA 证据和最终 BLEND/FBX；
- Worker 离线时只恢复其领取的模型，不重新创建整批任务；
- 继续兼容现有单文件调用方，服务器 API 不需要破坏性升级。

## 当前服务端能力

### v2.3.0 包接入边界

冻结上游包：`blender-retopology-compare-iterate-server-package-v2.3.0.zip`。

- 原包 SHA-256：`d86f218d2194bd6260a491da66f89b8954a72ef8e5309c0ff1062c639d8f6ec4`；
- Skill SHA-256：`03dff7efe9ffac9a365a0b81637bc3065fd4fe7259c67a9d2eb4ebf697e450aa`；
- 原包 `verify_package.py` 已通过，清单中的 17 个文件逐项 SHA 已核对；
- 原包 `batch_retopology.py` 按上传顺序串行调用单文件入口，因此只作为上游批量参考，生产
  Worker 不调用该脚本；
- GPU Control 使用同一个上游 `one_click_retopology.py` 处理每个独立任务，调度器负责把多个
  FBX 分配给不同健康 Worker；
- 一个 FBX 内的多个 Mesh 属于同一个资产，由上游准备脚本无损归一为一个 `SOURCE_HIGH`，
  不得把不同道具合进同一个 FBX。

原 ZIP 的 `manifest/FILES.sha256` 使用 CRLF；Linux 命令行核验时需先去掉行尾 `\r`。该格式问题
不改变任何文件摘要，也不影响包内 `verify_package.py`。

用户端可以在启动或进入页面时读取：

```text
GET /api/v1/assets/version
```

其中 `retopology` 返回 `engine_contract`、`package_version`、`package_sha256`、
`submission_mode=one_file_per_job` 和建议上传并发数。客户端不应把建议上传并发数解释为实际建模
并发数；实际并发仍由健康 Worker 数决定。

创建接口保持不变：

```text
POST /api/v1/assets/retopology/process
Content-Type: multipart/form-data
Authorization: Bearer <token>
Idempotency-Key: <每个文件独立且稳定的 key>
```

每次请求字段：

- `project`: 一个 FBX；
- `metadata`: 这个 FBX 对应的 V6 metadata JSON；
- `reference_images`: 可选，仅属于这个 FBX 的参考图。

返回一个独立 `job_id`。用户端把多个请求产生的 `job_id` 归入本地 `batch_id` 即可，不需要服务器伪造一个会扩大故障域的父任务。

## 用户端交互合同

### 文件选择

```html
<input type="file" accept=".fbx" multiple />
```

选择完成后显示模型列表，而不是覆盖上一个文件。每行至少显示：

- 原始文件名和文件大小；
- 本地状态：等待上传、上传中、已入队、运行中、已完成、失败、已取消；
- 服务端 `job_id`、Worker、进度、阶段和预计剩余时间；
- 单独取消、重新提交、下载 BLEND、下载 FBX；
- 错误码和警告，不用一个模型的错误替换整个列表。

页面顶部可显示本地聚合值：总数、等待、运行、成功、失败和总体完成比例。聚合值仅用于 UI，不改变每个任务的服务端权威状态。

### 稳定身份

用户端在选择文件时创建并持久化：

```text
batch_id = UUID
item_id  = UUID
external_asset_id = li3d:<batch_id>:<ordinal>:retopology:v6
idempotency_key   = retopology-v6:<batch_id>:<item_id>
```

网络重试必须复用原 `external_asset_id` 和 `Idempotency-Key`。不能在每次 retry 时生成新 UUID，否则会创建重复模型任务。

## 推荐提交实现

用户端可一次选任意多个文件，但上传请求并发建议限制为 3；上传完成后，实际拓扑并行度由服务器 Worker 调度决定。

```ts
type LocalRetopoItem = {
  itemId: string;
  file: File;
  ordinal: number;
  jobId?: string;
  status: "waiting" | "uploading" | "queued" | "running" |
          "succeeded" | "failed" | "cancelled";
  error?: unknown;
};

async function submitOne(batchId: string, item: LocalRetopoItem, token: string) {
  const metadata = {
    api_version: "6.0",
    external_asset_id: `li3d:${batchId}:${item.ordinal}:retopology:v6`,
    options: {
      algorithm: "agent",
      budget_mode: "automatic",
      topology_style: "mixed_game_ready",
      preserve_source: true,
      preserve_sharp_edges: true,
      preserve_boundaries: true,
      delivery_profile: "next_gen_game_prop",
    },
    reference_views: [],
  };
  const body = new FormData();
  body.append("project", item.file, item.file.name);
  body.append("metadata", JSON.stringify(metadata));
  const response = await fetch("/api/v1/assets/retopology/process", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Idempotency-Key": `retopology-v6:${batchId}:${item.itemId}`,
    },
    body,
  });
  if (!response.ok) throw await response.json();
  return response.json();
}
```

队列执行必须使用 `Promise.allSettled` 或等价的有界 worker pool，不能用 fail-fast `Promise.all` 让一个上传失败后停止记录其他返回结果。

## 状态、事件和取消

每个 `job_id` 独立使用现有接口：

```text
GET  /api/v1/assets/jobs/{job_id}
GET  /api/v1/assets/jobs/{job_id}/events
POST /api/v1/assets/jobs/{job_id}/cancel
```

列表恢复时读取本地持久化的 `batch_id/items/job_id` 并逐项查询服务端。刷新页面不能重新上传已经获得 `job_id` 的文件。

“取消全部”只能在用户确认后逐项调用 cancel；每个 item 保留自己的 cancel 结果。不要仅在前端把整批标成 cancelled。

## 并行度事实

V6 Agent 每个 Linux Asset Worker 同时只运行一个正式拓扑任务，防止多个 Codex 任务争用同一执行锁并导致租约过期。因而：

```text
实际并行拓扑数 = min(已认证且健康的 V6 Worker 数, 当前未完成模型数)
```

三台 Worker 全部认证健康时最多三个模型真正并行，其余模型安全排队。2026-08-06 当前只有 3090-A 为 `AUTHENTICATED / HEALTHY`；4090 和 3090-B 为 `AUTH_REFRESH_REUSED`，恢复认证前多文件可以一起提交，但只会单路执行。

不得通过在同一 Worker 进程内同时启动多个 Codex V6 Agent 来伪造并行；这会重现任务租约过期和重复恢复问题。

## 验收

1. 一次选择至少 3 个不同 FBX，窗口出现 3 行且顺序稳定。
2. 三个请求分别返回不同 `job_id`；刷新后仍能恢复。
3. Worker 足够时不同模型显示不同 Worker 并行运行；不足时其余显示排队而非失败。
4. 取消其中一个模型，其他模型继续。
5. 让其中一个输入失败，其他成功项仍能下载各自单对象 BLEND/FBX。
6. 同一个 item 重放创建请求只返回原 job，不产生第二份任务。
7. 每个结果都保持 V6 Skill/policy SHA 和 `LI3D_<job_id>_GAME_LOW` 单对象交付合同。
