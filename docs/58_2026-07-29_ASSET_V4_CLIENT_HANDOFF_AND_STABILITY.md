# 统一调度中心 Asset V4 客户端对接与稳定性验收

文档日期：2026-07-29
当前适用基线：GPU Control `1.5.4`；Asset Worker `li3d/blender-worker:1.2.2`
适用方：Li3D 资产客户端、动画管家、内部资产 Agent

> 文档状态（2026-07-30）：本文保留 2026-07-29 的接口设计和真实验收记录，其中明确写有
> `1.5.1` / `1.2.1` 的数据均为当时的历史事实，不代表当前部署版本。当前客户端合同与发布、
> 恢复要求应同时参阅 59 号粗糙度/烘焙交接、60 号 UV/重拓扑交接、61 号 Baker 四槽发布记录
> 以及 62 号可复现备份与滚动更新手册；发生冲突时以后述文档和当前锁定版本为准。

本文替代 55 号文档中“重拓扑必须人工复核”的旧合同。当前生产规则是：服务端自动执行严格
QA；QA 通过后直接原子交付，QA 未通过则返回结构化失败。统一调度中心只负责调度、可观测、
诊断和制品追踪，不提供人工批准或驳回操作。

## 1. 已冻结的边界

- UV、重拓扑走独立 Asset API/CPU Worker，不占 ComfyUI GPU 任务槽。
- 输入模型只读，所有输出写入独立 Job 目录；不得覆盖客户源文件。
- UV 成功固定发布 5 件套，重拓扑成功发布候选模型、四视图、审计与 Agent 证据。
- 任务完成状态、进度、Worker、ETA、错误和制品 SHA 均由服务端 API 返回；WebUI 只是同一事实的管理视图。
- 所有最终制品只有在 QA 通过后才进入 artifacts；日志、预览和中间文件不能冒充最终结果。
- RetopoFlow 是 Blender 交互式插件。三台 Worker 都真实加载插件并调用其 Blender operator 做健康探针；
  无人值守任务使用可审计的 `cleanup_existing` 或 QuadriFlow 路径，不能虚构 RetopoFlow 是无头自动算法。

## 2. 网络与认证

```bash
BASE_URL=https://10.3.34.11
CA_FILE=/absolute/path/GPU_CONTROL_LAN_CA.crt
```

局域网默认允许调用，并按真实来源 IP 自动建立/归档客户身份；不要求白名单。需要跨 NAT、代理或稳定
租户额度时，可以额外使用 `X-API-Key`。无论哪种身份方式，都应发送业务唯一幂等键：

```text
Idempotency-Key: <业务任务唯一键>
X-Request-ID: <链路追踪 ID>
```

同一幂等键和相同请求返回原 Job；同一键但内容不同返回 `409`。正式客户端必须信任 LAN CA，不能把
`-k/--insecure` 当生产配置。

## 3. UV API

### 3.1 提交

`POST /api/v1/assets/uv/process`，multipart：

- `asset`：`.fbx/.obj/.glb/.gltf/.blend`；
- `metadata`：JSON 字符串。

```bash
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/uv/process" \
  -H 'Idempotency-Key: li3d:chair:uv:001' \
  -H 'X-Request-ID: li3d-chair-uv-001' \
  -F 'asset=@/data/chair.fbx;type=application/octet-stream' \
  -F 'metadata={"external_asset_id":"li3d:chair:uv:001","options":{"hidden_axis":"y+","hard_edge_angle_degrees":75,"resolution":2048,"padding_px":10,"texel_density_mode":"uniform","qa_profile":"pbr-v1"}};type=application/json'
```

UV Worker 会自动切缝、展开、统一密度、排版、修复可恢复的退化 UV，再分别执行 BLEND QA 和
“导出 FBX 后重新导入”QA。两层 QA 都通过才发布：

1. `<stem>_PBR_UV.blend`
2. `<stem>_PBR_UV.fbx`
3. `<stem>_PBR_UV_report.json`
4. `<stem>_PBR_UV_QA.json`
5. `<stem>_PBR_UV_FBX_QA.json`

## 4. 重拓扑 API

### 4.1 提交合同

`POST /api/v1/assets/retopology/process`，multipart：

- `project`：`.blend`、`.fbx`、`.obj`、`.glb/.gltf`，不能上传 ZIP；
- `metadata`：对象名、目标面数、结构要求和参考图声明；
- `reference_images`：可重复上传 0～32 张真实参考图。

```bash
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/retopology/process" \
  -H 'Idempotency-Key: li3d:crate:retopo:001' \
  -H 'X-Request-ID: li3d-crate-retopo-001' \
  -F 'project=@/data/crate.blend;type=application/octet-stream' \
  -F 'reference_images=@/data/crate_front.png;type=image/png' \
  -F 'reference_images=@/data/crate_side.png;type=image/png' \
  -F 'metadata={"external_asset_id":"li3d:crate:retopo:001","options":{"high_object":"crate_high","reference_object":"crate_reference_low","low_object":"crate_current_low","generated_low_object":"crate_generated_v001","algorithm":"agent","topology_style":"quad_dominant","target_faces":500,"preserve_sharp":true,"preserve_boundary":true,"render_resolution":512,"max_repair_rounds":1,"require_closed":false},"reference_views":[{"filename":"crate_front.png","view":"front"},{"filename":"crate_side.png","view":"side"}],"user_request":"高模决定轮廓，参考低模决定布线；优先四边面，保留硬边和组件边界。"};type=application/json'
```

`target_faces` 范围为 `50～50000`；常用快捷值为 `50 / 500 / 1000 / 3000 / 5000`。
`topology_style` 支持 `quad_dominant`（四边面优先）和兼容模式。这里的“四边面优先”是可验证目标，
不是对任意破碎输入承诺 100% 全四边面；最终报告会给出 quad/triangle/N-gon 数量。

### 4.2 确定性算法选择

Agent 可以建议算法，但 Worker 在执行前做确定性几何预检：

- 高模单组件、封闭且法线一致时，允许 QuadriFlow；
- 高模存在多个组件、开放边界或非流形边时，拒绝不适用的 QuadriFlow 建议，固定使用
  `cleanup_existing`；
- 解析结果和原因写入 manifest 的 `agent_plan.resolved_algorithm` 与 `resolution_reason`。

这避免同一个不可变输入因 Agent 建议波动而一会成功、一会失败。源高模、参考低模和当前低模均不覆盖。

### 4.3 自动交付

严格审计通过后直接 `SUCCEEDED` 并原子发布；不再进入 `WAITING_REVIEW`，也不存在管理员批准/驳回
按钮。客户端可以展示四视图和审计结果，但它们是验收证据，不是阻塞状态。

## 5. 状态、进度与双向同步

提交返回 HTTP `202`：

```json
{
  "job_id": "uuid",
  "status": "QUEUED",
  "status_url": "/api/v1/assets/jobs/{job_id}",
  "events_url": "/api/v1/assets/jobs/{job_id}/events",
  "cancel_url": "/api/v1/assets/jobs/{job_id}/cancel"
}
```

轮询：`GET /api/v1/assets/jobs/{job_id}`。建议 2～5 秒并加入抖动。实时事件：
`GET /api/v1/assets/jobs/{job_id}/events`，使用 SSE；断线后携带 `Last-Event-ID` 继续。轮询结果是最终事实，
SSE 用于低延迟提示。

终态为 `SUCCEEDED / FAILED / CANCELLED`。终态的 elapsed 必须冻结，不能刷新后继续计时。失败响应会给
`error.code` 与面向客户端的摘要；完整 Blender/Codex 原始日志仅放管理端“高级诊断”，不应直接塞进用户 UI。

取消：`POST /api/v1/assets/jobs/{job_id}/cancel`。只有调用方明确请求取消时才进入取消状态；断线、单帧错误
或 Worker 暂时离线不能被解释成用户取消。

## 6. 制品完整性

每个 artifact 都包含 `filename/kind/size_bytes/sha256/download_url`。下载后必须满足：

```text
SHA256(response body) == artifact.sha256 == X-Artifact-SHA256
```

任何一个不一致都视为交付失败。重拓扑必须保留四视图/对比图、baseline/final audit、process report、
manifest、Agent prompt/plan/events 和候选 BLEND/FBX；客户端可只向普通用户展示最终模型与质量摘要。

## 7. 运维 WebUI

- `/nodes`：只展示 GPU/ComfyUI 推理节点，不混入 Codex 细节。
- `/codex`：独立显示三台 Codex CLI 的安装版本、认证、真实调用探针、最近任务输入/输出上下文与历史。
- `/asset-processing`：资产队列、Worker 和任务详情；默认抽屉只显示结果、进度、质量和最终模型下载，
  ID、SHA、原始事件、Agent 证据和错误全文折叠到“高级诊断”。
- 真实任务与隔离测试任务分栏，压测不能刷生产任务列表。

## 8. 2026-07-29 真实验收

- 三台 Asset Worker：`ONLINE`，并发槽 `2 + 3 + 4`；
- 三台 Codex：版本 `codex-cli 0.146.0-alpha.3.1`、`AUTHENTICATED / HEALTHY`；
- 三台 RetopoFlow：真实 Blender operator 探针 `HEALTHY`，revision
  `ac2570c5292c1dd90190fd3641b4dbc42cf4bd63`；
- 真实 UV：使用此前失败的 `uv.fbx`，当时的 Worker `1.2.1` 并发 6/6 成功，全部 attempt=1；
- 真实重拓扑：同一不可变 BLEND 与 4 张真实参考图并发 3 单，3/3 `SUCCEEDED`、全部
  `attempt_count=1`，分别耗时 177 / 177 / 245 秒；4090 执行 2 单、3090-B 执行 1 单；
  每单 23 件制品，SSE 事件 20 / 21 / 24 条，制品响应体、API SHA 与响应头 SHA 全部一致。
- 确定性回退已实测：其中一单 Agent 建议 QuadriFlow，几何预检识别碎片化开放高模后固定回退
  `cleanup_existing`，任务一次成功；manifest 保存建议、最终算法和原因。
- GPU 7:3 真实混合压力：10 个隔离测试客户无序提交 7 个 ImageClip + 3 个 ModelView，10/10
  HTTP 202、10/10 `SUCCEEDED`、全部一次执行、0 输出校验失败、0 限流重试；4090 / 3090-A /
  3090-B 分别执行 5 / 3 / 2 单。提交延迟 p50 0.868 秒、p95 1.866 秒；排队 p50
  78.603 秒、p95 220.836 秒；总耗时 p50 189.614 秒、p95 273.870 秒。
- 压测全过程生产排队任务数保持 0；压测结束后 10 个测试客户及其 Key 已停用。
- 静态与回归审计：Ruff 全通过，Python 测试 93/93 通过；Web lint、构建和 Vitest 3/3 通过；
  浏览器真实 HTTPS 页面检查无 console error。

完整机器可读报告：

- `output/acceptance/asset-retopo-121.json`
- `output/acceptance/closure-20260729-1345-7to3.json`

`target_faces` 是目标而不是伪造的完成承诺；是否达到目标、quad/triangle/N-gon 数量以及最终选择的
算法都保存在审计与 manifest。当前自动交付门禁是结构与制品严格 QA，客户端不得把目标面数显示成
“必然精确命中”。

## 9. 客户端联调清单

1. 安装/指定 LAN CA，确认 `GET /health/live` 为 200；
2. 用真实来源 IP或 API Key 提交；
3. 固定业务幂等键，不用新键盲目重试；
4. 记录 `job_id/request_id/external_asset_id`；
5. 轮询 + SSE 同步 stage、progress、ETA；
6. 只把 `SUCCEEDED` 当交付成功；
7. 下载全部必需制品并核对三方 SHA；
8. 客户端不再实现人工批准/驳回；
9. 遇到 `422 ASSET_INPUT_INVALID` 先修输入扩展名、对象名或 metadata，不应让 Worker 重试；
10. 提供失败 Job ID 给管理员，高级诊断由调度中心统一留存。
