# GPU Control 1.5.10 部分成功与失败帧补算对接合同

日期：2026-08-05  
对接方：GPU Control / AssetClaw 动画管家  
基准输入：`08_GPU_CONTROL_PARTIAL_SUCCESS_AND_FAILED_FRAME_REPAIR.md`  
基准 SHA-256：`94944e799158ea38bc89233a3e4772458b52095f739d8959e87603c610241e8a`  
优先级：P0  

## 1. 生效范围与兼容结论

本合同仅改变新提交的 ImageClip RGBA 序列帧批次。历史失败批次不回写、不恢复、不改变原始审计记录。

- 请求入口、Manifest 1.0、`ZIP_STORED`、幂等键和工作流身份字段保持兼容。
- 完整成功仍返回 `SUCCEEDED`，现有客户端无需修改。
- 部分成功新增终态 `PARTIAL_SUCCESS`。动画管家必须把它视为终态，下载结果包后仅补算 `failed_items`。
- 批次级身份、Manifest、路径归属或装配完整性错误仍为 `FAILED`，不发布不可信结果。
- GPU Control 不修改 ImageClip 工作流、模型、节点参数、Prompt、输出节点或业务语义。

## 2. GPU Control 新状态机

```text
RUNNING
  ├─ 单帧执行失败且 attempt < 3
  │    └─ 排除全部已尝试节点后重新排队
  ├─ 97/97 成功
  │    └─ ASSEMBLING -> SUCCEEDED
  ├─ 至少 1 帧成功且至少 1 帧三次失败
  │    └─ ASSEMBLING -> PARTIAL_SUCCESS
  └─ 0 帧成功或批次级重大故障
       └─ FAILED
```

`PARTIAL_SUCCESS`、`SUCCEEDED`、`FAILED`、`CANCELLED` 均为终态。SSE 收到这些状态后会正常结束，不应继续轮询为运行态。

## 3. 帧级重试规则

每个子帧最多执行三次，`attempts` 为已实际领取次数：

1. 第一次从健康兼容节点中正常调度；
2. 第二次排除第一次的物理 `node_id`；
3. 第三次排除前两次的物理 `node_id`；
4. 三次耗尽后才写入最终 `failed_items`；
5. 其他成功帧与运行中帧不取消、不清理、不重算。

以下帧级错误允许换节点重试：

- `COMFY_TIMEOUT`
- `JOB_TIMEOUT`
- `GPU_OOM`
- `COMFY_EXECUTION_ERROR`
- prompt 尚未提交前的可恢复传输错误

`COMFY_SUBMISSION_UNKNOWN`、重复提交身份不明和提交对账失败继续 fail closed，避免重复 GPU 执行。

## 4. 状态响应合同

部分成功示例：

```json
{
  "batch_id": "<uuid>",
  "external_batch_id": "assetclaw:<task>:matting:g1",
  "status": "PARTIAL_SUCCESS",
  "progress": 100,
  "counts": {
    "total": 97,
    "pending": 0,
    "queued": 0,
    "running": 0,
    "succeeded": 96,
    "failed": 1,
    "cancelled": 0
  },
  "failed_items": [
    {
      "ordinal": 93,
      "input_relative_path": "video_01/0093.png",
      "input_sha256": "<64-char-lowercase-sha256>",
      "code": "COMFY_TIMEOUT",
      "message": "<bounded original summary>",
      "node_id": "control-4090",
      "attempts": 3,
      "attempted_node_ids": [
        "worker-3090-b",
        "worker-3090-a",
        "control-4090"
      ]
    }
  ],
  "artifacts": [
    {
      "kind": "result_archive",
      "content_type": "application/zip",
      "size_bytes": 123,
      "sha256": "<archive-sha256>",
      "download_url": "/api/v1/batches/<batch-id>/artifacts/<artifact-id>"
    }
  ]
}
```

`failed_items` 的身份键固定为 `ordinal + input_relative_path + input_sha256`。动画管家不得仅按文件名合并。

## 5. 部分结果包合同

- 每个 `PARTIAL_SUCCESS` 批次只发布一个 `kind=result_archive`。
- ZIP 压缩方式固定为 `ZIP_STORED`。
- ZIP 只含 `manifest.json` 和成功帧的 `results/<output_relative_path>`。
- `manifest.total` 始终为原父批次总帧数。
- `manifest.items` 只列成功帧，按原 ordinal 升序、唯一，允许不连续。
- 每张输出在装配前重新校验持久化 SHA-256、PNG 可解码性和 Alpha 通道。
- 失败帧不生成空白图、不复制旧图、不伪造成功项。
- 归档先在私有 staging 构建并 `fsync`，再以原子替换发布；数据库 artifact 与终态在同一受控事务中提交。

部分 `manifest.json`：

```json
{
  "schema_version": "1.0",
  "batch_id": "<uuid>",
  "external_batch_id": "assetclaw:<task>:matting:g1",
  "workflow_key": "imageclip-rgba",
  "workflow_version": "<approved-version>",
  "pipeline_commit": "<approved-commit>",
  "pipeline_sha256": "<approved-sha256>",
  "output_node": "SaveImage #25",
  "total": 97,
  "items": [
    {
      "ordinal": 0,
      "input_relative_path": "video_01/0000.png",
      "input_sha256": "<sha256>",
      "output_relative_path": "video_01/0000.png",
      "output_sha256": "<sha256>",
      "status": "SUCCEEDED",
      "job_id": "<uuid>",
      "node_id": "worker-3090-a",
      "attempts": 1
    }
  ]
}
```

## 6. 动画管家补算请求

动画管家下载并验证成功子集后：

1. 以原输入全集减去已验证成功 ordinal 得到缺失集合；
2. 新建批次，只在 ZIP 和 Manifest 中放缺失帧；
3. 使用新的 `external_batch_id` 与新的幂等键；
4. 本地记录 `skip_existing=true`，不得删除或覆盖已经验收的 matte；
5. 补算最多两轮，补算仍强制走 GPU 集群；
6. 合并完成后执行全帧连续性、输入/输出 SHA-256、Alpha 和防串帧校验。

`skip_existing` 是动画管家本地合并策略，不要求作为 GPU Control 工作流参数传入，因此不会改变批准的 ImageClip 工作流身份。

## 7. GPU OOM 结构化证据

当 ComfyUI 返回明确 CUDA OOM 证据时，GPU Control 使用 `GPU_OOM`，并在对应 `JobAttempt.error` 保存：

- `code`
- `message`
- 物理 `node_id`
- `exception_type`
- `comfy_node_id`
- `node_type`
- `raw_summary`（最多 8192 字符）

判定使用 ComfyUI 结构化 `exception_type` 与原始异常摘要，不依赖 API 展示层截断字符串。

## 8. 批次级重大故障

下列错误不降级为部分交付：

- 工作流身份或三重 SHA 门禁不匹配；
- Manifest 结构、输入大小或输入 SHA-256 整体不可信；
- 输出路径冲突、越界或结果归属无法验证；
- 成功帧在装配时出现 SHA 变化、PNG 无法解码或 Alpha 缺失；
- 全部节点不兼容批准的工作流；
- 有审计证据的用户取消。

## 9. Web UI 与下载兼容

- Web UI 将 `PARTIAL_SUCCESS` 显示为“部分成功”，不显示为运行中，也不提供取消按钮。
- 详情页显示部分结果归档和待补算 ordinal、路径、错误码、尝试次数。
- 公共与管理员 artifact 下载端点均允许 `SUCCEEDED` 和 `PARTIAL_SUCCESS`。
- `FAILED` 且无受信归档的批次仍返回 `ARTIFACT_NOT_READY`。

## 10. 联合验收

| ID | 场景 | 必须结果 |
|---|---|---|
| B1 | 97 帧中第 93 帧首次超时 | 换节点重试；其余 96 帧不重算；最终 97/97 `SUCCEEDED` |
| B2 | 第 93 帧三节点均失败 | `PARTIAL_SUCCESS`；结果包 96 张；`failed_items` 仅 ordinal 93 |
| B3 | 部分包内任一 SHA/路径/ordinal 被篡改 | 动画管家拒绝合并；已有合格 matte 不删除 |
| B4 | 工作流身份不匹配 | `FAILED`；无 artifact；不做帧级补算 |

## 11. 发布回执

```text
GPU Control 版本：1.5.10
实现源码 commit：发布后回填
部署 commit：发布后回填
部署时间：发布后回填
API 镜像 digest：发布后回填
Scheduler 镜像 digest：发布后回填
Web 镜像 digest：发布后回填
PARTIAL_SUCCESS 已实现：是
FAILED + partial artifact 兼容：不采用；正式使用 PARTIAL_SUCCESS
failed_items 已实现：是
失败帧换节点重试：是，最多三次且排除已尝试节点
成功帧保留：沿用批次持久化保留策略，不因单帧失败清理
历史事故批次恢复：按双方最新决定不执行
B1/B2/B3/B4：部署后联合回填
```

在镜像 digest、部署 commit 和 B1–B4 证据回填前，状态为 `IMPLEMENTED_PENDING_DEPLOYMENT_ACCEPTANCE`。
