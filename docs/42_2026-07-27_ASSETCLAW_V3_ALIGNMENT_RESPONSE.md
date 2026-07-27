# 动画管家 ↔ 统一调度中心 V3 对齐回执

文档状态：`SOURCE ALIGNED / WEB DEPLOYED / GPU BACKEND FROZEN / JOINT ACCEPTANCE PENDING`
日期：2026-07-27
当前生产版本：`GPU Control 1.3.3`
工作树候选版本：`Unified Scheduling Center 1.4.0-dev`
上游对齐文档 SHA-256：`9a4daeb3454f6f01228b2d6b0408019b2b1262adb33a5976c0e50a6d7b7e9343`

## 1. 本次变更边界

收到动画管家 V3 对齐文档时，生产库存在 3 个 `RUNNING` 父批次、3 个运行帧和 20 个排队帧。
因此本次不改变 GPU 后端，只修改候选源码、独立 Web 和空闲 Worker 镜像：

- API、Scheduler、Nginx、PostgreSQL、Redis、ComfyUI 均未重启、重建或替换；
- 未执行 Alembic 数据库迁移；
- 未切换 ImageClip 工作流版本或三节点管线；
- 未提交真实批次、取消生产批次或调整调度策略；
- Web 已独立更新为 `gpu-control-web:1.4.0-dev.20260727.1`，后端容器 ID 保持不变；
- 3090-A、3090-B 已加载相同 Blender Worker 镜像并完成无网络验收，但没有启动常驻 Worker、没有心跳、没有领取任务。

这意味着下面标记为 `IMPLEMENTED_NOT_DEPLOYED` 的字段只存在于候选源码，当前生产 1.3.3 尚不返回。
必须在现有父批次全部终态后，按第 5 节灰度启用。

## 2. 固定格式对齐回执

```yaml
alignment_response: GPU_CONTROL_V3
gpu_control_version: "1.3.3-production / 1.4.0-dev-worktree"
document_read:
  upstream_sha256: 9a4daeb3454f6f01228b2d6b0408019b2b1262adb33a5976c0e50a6d7b7e9343
  assetclaw_alignment_version: 3.0.0-am1
contract:
  manifest_1_0: ACCEPTED
  zip_stored: ACCEPTED
  immutable_external_batch_id: ACCEPTED
  idempotency_key: ACCEPTED
  generation_on_input_change: ACCEPTED
  failure_policy_all_or_nothing: ACCEPTED
  unknown_state_same_batch_recovery: ACCEPTED
  no_silent_local_fallback: ACCEPTED
  parent_succeeded_before_publish: ACCEPTED
  ten_result_checks_before_publish: ACCEPTED
  parent_only_top_level: ACCEPTED
  strict_tls: ACCEPTED
  production_test_isolation: ACCEPTED
identity:
  production_api_key_ready: false
  isolated_test_api_key_ready: false
  source_ip_mode_end_date: PENDING_DEDICATED_KEYS
observability:
  parent_returns_workflow_version: LIVE
  parent_returns_pipeline_commit: IMPLEMENTED_NOT_DEPLOYED
  parent_returns_pipeline_sha256: IMPLEMENTED_NOT_DEPLOYED
  response_request_id_policy: SERVER_REWRITES_AT_EDGE_AND_ECHOES_ACTUAL
  scheduler_capacity: IMPLEMENTED_NOT_DEPLOYED_ADVISORY_V1
hot_update:
  active_batch_version_pinned: true
  drain_before_enable: true
  signed_heartbeat_gate: true
  object_info_refresh_seconds: 60
deviations:
  - code: PRODUCTION_GPU_BACKEND_FROZEN
    detail: Three AssetClaw parent batches were RUNNING when alignment started; only the dependency-free Web container was rolled out.
  - code: DEDICATED_KEYS_PENDING
    detail: Production and isolated acceptance API keys have not been issued to AssetClaw.
  - code: WORKTREE_FIELDS_NOT_LIVE
    detail: pipeline_commit, pipeline_sha256 and scheduler capacity are implemented and tested only in the worktree.
blocking_questions:
  - AssetClaw must provide the production client identity/name and a separate acceptance-test identity/name before keys are issued.
  - Both sides must agree on a drain window before candidate 1.4.0 is deployed.
ready_for_joint_acceptance: false
```

## 3. 已完成的源码对齐

### 3.1 父批次管线身份

`GET /api/v1/batches/{batch_id}` 候选响应新增：

```json
{
  "workflow_version": "2026.07.27-721f7d6-r1",
  "pipeline_commit": "721f7d68635ee36d45f545ce2c82037046147442",
  "pipeline_sha256": "00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b"
}
```

这两个字段从父批次创建时固定的 `WorkflowVersion.node_labels` 读取，而不是读取查询瞬间某个节点的
心跳。因此重启、节点 IP 改变或之后启用新工作流，都不会篡改历史父批次的管线身份。

### 3.2 只读容量提示

候选接口：

```http
GET /api/v1/scheduler/capacity
X-API-Key: gpc_<prefix>_<secret>
```

返回 `eligible_nodes`、`total_slots`、`used_slots`、`available_slots`、系统排队/运行数，以及当前客户
自己的排队/运行数和配额。它是 `advisory=true` 的瞬时提示，不是槽位预留；动画管家提交后仍必须以
父批次状态机为准，不得根据容量响应自行认定任务已经开始或完成。

### 3.3 X-Request-ID 规则

当前 Nginx 在边缘生成实际请求 ID 并覆盖上游同名请求头，API 在响应头回显实际采用值。因此动画管家
发送的 ID 是建议值，必须持久化响应 `X-Request-ID` 作为服务器侧真值。若未来改为保留合法调用方 ID，
需要升文档版本，不能静默改变。

## 4. 保持不变的冻结合同

- 创建仍是 `POST /api/v1/batches/imageclip-rgba`；
- multipart 字段仍严格为 `archive` 与 `manifest`；
- manifest 仍严格为 `1.0`，帧数 1～5000，`parameters={}`；
- ZIP 条目必须 `ZIP_STORED`，且与 manifest 图片集合完全相等；
- 同 key 同内容返回原父批次，不同内容返回 `IDEMPOTENCY_CONFLICT`；
- 每个帧 job 可跨三节点乱序完成，父级按 ordinal/目录/文件名重组；
- 只有父状态 `SUCCEEDED` 才暴露唯一最终 result archive；
- 任一帧失败或最终包校验不通过，动画管家不得进入 Cherry、编码或发布；
- Web 顶层只显示父批次，帧级任务只进入父详情。

## 5. 联合验收前的安全启用顺序

1. 等当前所有生产父批次进入 `SUCCEEDED`、`FAILED` 或 `CANCELLED`。
2. 备份数据库与当前 1.3.3 镜像，记录三节点 ImageClip commit/pipeline SHA。
3. 在隔离环境执行 Alembic dry-run、全量单元/集成测试和 Nginx 配置检查。
4. 只升级控制面到候选版本，不切换 ImageClip 工作流。
5. 验证旧父批次查询和 artifact 下载保持兼容。
6. 创建一个独立 `client_kind=test` 客户和专用测试 Key；不得复用生产来源 IP 身份。
7. 动画管家用 1、6、64 帧三组不可发布素材联合验收创建、重放、恢复、取消和结果校验。
8. 验证 `workflow_version`、`pipeline_commit`、`pipeline_sha256` 与三节点签名心跳完全一致。
9. 创建独立生产 Key，撤销临时来源 IP 认证窗口。
10. 双方共享 batch ID、实际 request ID、输入/输出 SHA 与日志证据后，将状态改为 `FROZEN`。

## 6. 当前结论

协议语义已经接受，V3 可观察性缺口已在源码补齐；但由于生产仍有活动批次且专用 Key 未签发，当前
不能宣称联合验收完成，也不能要求动画管家立即切换到候选字段。动画管家应继续使用已冻结的 1.3.3
字段集合，直到双方完成第 5 节。
