# Li3D Asset V4：UV 与自动重拓扑最新应用端对接合同

文档版本：`V4.2026-08-03-r3`

生效日期：2026-08-03（Asia/Singapore）

适用方：Li3D 应用端、动画管家、GPU Control、Asset Worker 运维方

接口前缀：`/api/v1/assets`。这里的 “Asset V4” 是业务合同版本，不代表 URL 使用 `/v4`。

## 1. 当前有效基线

| 项目 | 当前事实 |
| --- | --- |
| 生产控制面 | API/Scheduler/Web 仍为 `1.5.7`；Asset API 为 `1.5.8`，image revision `7f7fd197f86288ffbeeab622cc39199335e22c61` |
| 生产数据库 | `20260803_0012` |
| Linux Asset Worker | 三节点 tag 均为 `1.2.4`；镜像 revision 不同，但 Worker 相关源码与三项批准 Skill 文件 SHA 一致；统一 OCI image digest/SBOM 待归档 |
| 生产 UV QA 策略 | `UV_QA_ENFORCEMENT=advisory` |
| 重拓扑 QA 策略 | `RETOPOLOGY_QA_ENFORCEMENT=advisory` |
| 发布状态 | `DEPLOYED_NOT_ACCEPTED`；五条真实任务（PBR、两条 UV、两条重拓扑）已成功，但尚未统一全控制面版本并完成长期观察 |
| 1.5.8 | 仅 Asset API 局部部署；不代表整套控制面已升级 |
| Worker 1.2.4 / Agent v5 | Linux Worker 与四个 Baker Agent v5 已滚动；统一 OCI image digest/SBOM 和已安装脚本 SHA 待归档 |

本文件取代旧 58/60 号文档作为应用端最新 UV/自动重拓扑合同，但不改写旧文档中的历史测试和发布
事实。最重要的变化是：

- 自动重拓扑当前只接受一个 `.blend` 项目；旧文档中的 FBX/OBJ/GLB 提交方式已失效；
- `target_faces` 服务端硬范围为 `50～5,000,000`；
- 生产重拓扑采用 `advisory`：几何 QA 告警不再隐藏用户需要的 BLEND/FBX；
- 生产 UV 已为 `advisory`；几何 QA 未通过仍返回 BLEND、FBX 和三份报告并附带告警；
- 真实 PBR、UV warning、UV clean 与连续两笔重拓扑任务均已成功，告警和正式制品按合同交付；
- 当前 Li3D 页面提示“公司 CA 未配置或文件不可用”属于应用端 TLS 信任包问题，不是 GPU/Worker 授权失败；
- Asset CPU 队列与 ComfyUI GPU 推理队列隔离，UV 不应因 Codex 或 ComfyUI 状态被应用端禁用。

应用端应把 `UV_QUALITY_GATE_WARNING` 显示为“已交付·质量告警”，不得隐藏五件套下载。
当前仍为 `DEPLOYED_NOT_ACCEPTED`；上述五条真实任务验收已完成，但控制面统一、API artifact
三重 SHA、统一 OCI image digest/SBOM、回滚和观察未闭环前不构成全量 SLA 验收。

## 2. 服务地址、CA 与身份认证

```text
BASE_URL=https://10.3.34.11
CA_URL=http://10.3.34.11/GPU_CONTROL_LAN_CA.crt
CA_SHA256=ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b
```

应用端必须随安装包携带这张 CA，或首次启动下载后用上面的 SHA-256 校验，再显式加入 Asset V4 HTTP
客户端的信任库。禁止使用 `verify=false`、`-k` 或忽略证书错误作为正式方案。

建议启动预检顺序：

1. 检查 CA 文件存在并核对 SHA-256；
2. 使用该 CA 请求 `GET /health/ready`；
3. 请求 `GET /api/v1/assets/version`；
4. 请求 `GET /api/v1/assets/capacity`；
5. 只有 HTTP `401/403` 才显示“服务身份认证失败”。TLS 握手失败应显示“服务证书未信任”，不能混称
   “等待服务授权”。

业务认证优先使用安全渠道下发的 `X-API-Key`：

```text
X-API-Key: gpc_<prefix>_<secret>
```

密钥不得写入本文、Git、日志或截图。公司局域网也支持按唯一真实来源 IP 自动识别；跨 NAT、代理或多
客户端共用出口时必须使用 API Key，避免 `CLIENT_IP_CONFLICT`。

## 3. 通用请求头、幂等与追踪

每次提交必须发送幂等键；客户端也可发送自己的请求 ID：

```text
Idempotency-Key: <同一业务尝试的稳定唯一键，1～128 字符>
X-Request-ID: <可选，必须匹配 ^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$>
```

`external_asset_id` 必须匹配：

```regex
^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$
```

幂等语义：

| 场景 | HTTP | 行为 |
| --- | ---: | --- |
| 首次合法提交 | `202` | 创建并返回新 job |
| 同 client、同 key、规范化请求身份与输入 SHA 相同 | `200` | 返回原 job，不重复执行 |
| 同 key、内容不同 | `409` | `IDEMPOTENCY_CONFLICT` |
| 同 client 重复使用 `external_asset_id` | `409` | `EXTERNAL_ASSET_CONFLICT` |

幂等判定不比较 multipart 的原始编码字节，而是比较规范化后的 `external_asset_id`、补齐默认值的
options 与输入文件 SHA；重拓扑还包括 project SHA、`reference_views`、`user_request` 和按文件名排序的
参考图 SHA。JSON 空白不同或显式填写默认值仍可能命中同一请求。

幂等记录保留 7 天。网络超时、连接中断或 create 响应丢失时，必须使用原 key、相同业务字段和相同输入
文件重放；不能换 key 盲目创建第二个任务。客户端必须把 `200` 和 `202` 都视为成功取得 `job_id`。

服务端会在所有响应中返回权威链路 `X-Request-ID`。生产 Nginx 当前可能以网关 request ID 替换客户端
发送值；Asset API 直连则会回显合法值。因此应用必须保存**响应头中的值**，不能假设它等于请求值。
应用应同时保存：

```text
job_id / X-Request-ID / external_asset_id / Idempotency-Key / input_sha256
```

## 4. 接口总览

| 功能 | 方法与路径 | 说明 |
| --- | --- | --- |
| 自动展 UV | `POST /api/v1/assets/uv/process` | 异步，固定五件套；生产当前 strict，候选支持 advisory 告警交付 |
| 自动重拓扑 | `POST /api/v1/assets/retopology/process` | 异步，只接受 BLEND，可附 0～32 张参考图 |
| 独立拓扑审计 | `POST /api/v1/assets/retopology/audit` | 可选，只审计现有 BLEND，不生成新低模 |
| 查询任务 | `GET /api/v1/assets/jobs/{job_id}` | 权威状态 |
| 实时事件 | `GET /api/v1/assets/jobs/{job_id}/events` | SSE，支持 `Last-Event-ID` |
| 取消任务 | `POST /api/v1/assets/jobs/{job_id}/cancel` | 无 body；终态重放幂等 |
| 下载制品 | `GET /api/v1/assets/jobs/{job_id}/artifacts/{artifact_id}` | 必须校验 SHA-256 |
| Asset 容量 | `GET /api/v1/assets/capacity` | 聚合容量提示，不是自动拓扑 Codex SLA |
| Asset 版本 | `GET /api/v1/assets/version` | 组件、构建版本和 source revision |

## 5. 自动展 UV

### 5.1 提交

```text
POST /api/v1/assets/uv/process
Content-Type: multipart/form-data
```

| Form 字段 | 必填 | 合同 |
| --- | ---: | --- |
| `asset` | 是 | `.fbx/.obj/.glb/.gltf/.blend`；只允许安全 basename |
| `metadata` | 是 | JSON 字符串，未知字段会被拒绝 |

metadata：

```json
{
  "external_asset_id": "li3d:chair:uv:001",
  "options": {
    "resolution": 2048,
    "padding_px": 10,
    "hard_edge_angle_degrees": 75,
    "hidden_axis": "auto",
    "texel_density_mode": "uniform",
    "qa_profile": "pbr-v1"
  }
}
```

| 参数 | 默认值 | 允许值 |
| --- | ---: | --- |
| `resolution` | `2048` | `1024 / 2048 / 4096 / 8192` |
| `padding_px` | `10` | `2～128` |
| `hard_edge_angle_degrees` | `75` | `1～179` |
| `hidden_axis` | `auto` | `auto / x+ / x- / y+ / y- / z+ / z-` |
| `texel_density_mode` | `uniform` | 仅 `uniform` |
| `qa_profile` | `pbr-v1` | 仅 `pbr-v1` |

示例：

```bash
curl --fail-with-body --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/uv/process" \
  -H "X-API-Key: $ASSET_API_KEY" \
  -H 'Idempotency-Key: li3d-chair-uv-attempt-001' \
  -H 'X-Request-ID: li3d-chair-uv-001' \
  -F 'asset=@chair.fbx;type=application/octet-stream' \
  -F 'metadata={"external_asset_id":"li3d:chair:uv:001","options":{"resolution":2048,"padding_px":10,"hard_edge_angle_degrees":75,"hidden_axis":"auto","texel_density_mode":"uniform","qa_profile":"pbr-v1"}}'
```

### 5.2 UV 质量与交付

服务端执行切缝、展开、统一 Texel Density、排版和可恢复 UV 修复，然后依次执行：

1. Blender 工程内 UV QA；
2. 导出 FBX；
3. 重新导入导出的 FBX；
4. FBX 回读 UV QA。

生产 Asset API 1.5.8 + Worker 1.2.4 已把 V2 质量决策收敛到 Asset API，并使用
`UV_QA_ENFORCEMENT=advisory`：

| 场景 | job status | warning | 五件套 |
| --- | --- | --- | --- |
| 双 QA 通过 | `SUCCEEDED` | 无 | 原子发布 |
| QA 未通过、`advisory` | `SUCCEEDED` | `UV_QUALITY_GATE_WARNING` | 仍原子发布 |
| QA 未通过、`strict` | `FAILED` | `UV_QA_FAILED` | 不发布 |
| 文件/身份/JSON/租约/SHA 完整性失败 | `4xx` / `FAILED` | 不是质量 warning | 不发布 |

但完整性门禁不随 advisory 放宽；非法/非对象 QA JSON、空制品、身份、租约或 SHA 错误仍硬失败。

成功（包括 advisory 告警成功）固定返回：

| kind | filename |
| --- | --- |
| `blend` | `<输入 stem>_PBR_UV.blend` |
| `fbx` | `<输入 stem>_PBR_UV.fbx` |
| `report` | `<输入 stem>_PBR_UV_report.json` |
| `qa` | `<输入 stem>_PBR_UV_QA.json` |
| `fbx_qa` | `<输入 stem>_PBR_UV_FBX_QA.json` |

advisory 告警写入 `options.qa_warning`：

```json
{
  "code": "UV_QUALITY_GATE_WARNING",
  "enforcement": "advisory",
  "failed_qa": ["blend", "fbx_readback"],
  "failures": ["blend: ...", "fbx_readback: ..."]
}
```

SSE 终态为 `details.event=asset.succeeded_with_warnings`。应用应显示“已交付 · UV QA 告警”，仍允许
下载全部五件套，不能把 warning 映射成失败、人工复核或仅日志交付。

## 6. 自动重拓扑

### 6.1 输入合同

```text
POST /api/v1/assets/retopology/process
Content-Type: multipart/form-data
```

| Form 字段 | 必填 | 合同 |
| --- | ---: | --- |
| `project` | 是 | **一个 `.blend` 文件**；其他格式返回 `422 ASSET_INPUT_INVALID` |
| `metadata` | 是 | 对象名、算法约束、目标面数、参考图声明和用户要求 |
| `reference_images` | 否 | 可重复 0～32 次；文件名集合必须与 `reference_views` 完全相等 |

参考图支持 `.png/.jpg/.jpeg/.webp`，每张必须能真实解码，默认最大 4,000 万像素。项目与参考图合计
默认最大 2 GiB。上传文件名必须是安全 basename，不能携带路径。

metadata 示例：

```json
{
  "external_asset_id": "li3d:crate:retopo:001",
  "options": {
    "high_object": "crate_high",
    "reference_object": "crate_reference_low",
    "low_object": "crate_current_low",
    "generated_low_object": "crate_generated_v001",
    "algorithm": "agent",
    "topology_style": "mixed",
    "topology_mode": "mixed",
    "target_faces": 1200,
    "preserve_sharp": true,
    "preserve_boundary": true,
    "planar_reduction": true,
    "planar_angle_threshold": 5.0,
    "preserve_hard_edges": true,
    "preserve_components": true,
    "allow_triangles": true,
    "allow_ngons": false,
    "render_resolution": 512,
    "max_repair_rounds": 1,
    "require_closed": false
  },
  "reference_views": [
    {"filename": "crate_front.png", "view": "front", "label": "正面"},
    {"filename": "crate_side.png", "view": "side", "label": "侧面"}
  ],
  "user_request": "高模决定轮廓，参考低模决定布线；保留孔洞、硬边和组件边界。"
}
```

### 6.2 参数硬范围

| 参数 | 默认值 | 允许值/规则 |
| --- | ---: | --- |
| `high_object` | 必填 | 1～128 字符，不含路径分隔符或 NUL |
| `reference_object` | 必填 | 同上 |
| `low_object` | 必填 | 同上 |
| `generated_low_object` | `GPUCTRL_Retopo_v001` | 必须以 `_vNNN` 结尾 |
| `algorithm` | `agent` | `agent / quadriflow / cleanup_existing` |
| `topology_style` | `mixed` | `mixed / quad_dominant / preserve_existing` |
| `topology_mode` | `mixed` | `mixed / quad_dominant` |
| `target_faces` | `null` | `null` 或 `50～5,000,000` |
| `preserve_sharp` | `true` | boolean |
| `preserve_boundary` | `true` | boolean |
| `planar_reduction` | `true` | boolean |
| `planar_angle_threshold` | `5.0` | `0.1～45.0` |
| `preserve_hard_edges` | `true` | boolean |
| `preserve_components` | `true` | boolean |
| `allow_triangles` | `true` | boolean |
| `allow_ngons` | `false` | boolean |
| `render_resolution` | `512` | `256 / 512 / 1024` |
| `max_repair_rounds` | `1` | `0～2` |
| `require_closed` | `false` | boolean |
| `user_request` | `null` | 最多 4,000 字符 |

`target_faces` 是优化目标，不是无条件精确命中的承诺。服务端不会为了命中数字而允许丢组件、破坏轮廓
或绕过完整性门禁。

### 6.3 提交示例

```bash
curl --fail-with-body --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/retopology/process" \
  -H "X-API-Key: $ASSET_API_KEY" \
  -H 'Idempotency-Key: li3d-crate-retopo-attempt-001' \
  -H 'X-Request-ID: li3d-crate-retopo-001' \
  -F 'project=@crate.blend;type=application/octet-stream' \
  -F 'reference_images=@crate_front.png;type=image/png' \
  -F 'reference_images=@crate_side.png;type=image/png' \
  -F 'metadata={"external_asset_id":"li3d:crate:retopo:001","options":{"high_object":"crate_high","reference_object":"crate_reference_low","low_object":"crate_current_low","generated_low_object":"crate_generated_v001","algorithm":"agent","topology_style":"mixed","topology_mode":"mixed","target_faces":1200,"preserve_sharp":true,"preserve_boundary":true,"planar_reduction":true,"planar_angle_threshold":5.0,"preserve_hard_edges":true,"preserve_components":true,"allow_triangles":true,"allow_ngons":false,"render_resolution":512,"max_repair_rounds":1,"require_closed":false},"reference_views":[{"filename":"crate_front.png","view":"front"},{"filename":"crate_side.png","view":"side"}],"user_request":"高模决定轮廓，参考低模决定布线；保留孔洞、硬边和组件边界。"}'
```

### 6.4 算法与所有权边界

- GPU Control 负责接入、队列、租约、Worker 选择、状态、制品完整性、SHA、可观测性和故障恢复；
- Retopology Skill/算法属于外部业务管线，GPU Control 不修改其 prompt、算法参数、模型或几何语义；
- RetopoFlow 是交互式 Blender 工具，不伪装成无人值守算法；
- `algorithm=agent` 可以提出建议，但 Worker 会根据组件、开放边界和非流形情况确定性选择或回退
  `quadriflow/cleanup_existing`，并把建议、实际算法和原因写入 manifest；
- 源高模、参考低模和当前低模均保持只读，不覆盖用户源对象。

## 7. 重拓扑 QA 与正式交付真值表

生产当前使用 `advisory`。这个开关只改变“几何质量未达标时是否交付”，不改变测量结果，也不放宽
完整性和源保护。

| 场景 | job status | delivery_ready | artifacts_role | 最终 BLEND/FBX |
| --- | --- | ---: | --- | --- |
| 严格 QA 通过 | `SUCCEEDED` | `true` | `delivery` | `kind=blend/fbx` |
| QA 未通过、生产 advisory | `SUCCEEDED` | `true` | `delivery` | 仍返回正式 `kind=blend/fbx`，并带告警 |
| strict 模式 QA 未通过 | `FAILED` | `false` | `diagnostic` | 仅 `candidate_blend/candidate_fbx`，不可当交付 |
| 完整性、身份、源保护或非法图片失败 | `4xx` / `FAILED` | `false` | `—` / `retained` | 不发布任何公开 artifact |

advisory 成功时，状态中的 `options.qa_warning` 示例：

```json
{
  "code": "RETOPOLOGY_QUALITY_GATE_WARNING",
  "enforcement": "advisory",
  "audit_passed": false,
  "topology_goal_met": false,
  "automatic_final_promotion_allowed": false,
  "failures": ["SIGNED_AUDIT_FAILED", "NGONS=1"]
}
```

SSE 终态事件的 `details.event` 为 `asset.succeeded_with_warnings`。应用必须显示“已交付 · QA 告警”，
不能显示“QA 通过”，但仍必须把正式 BLEND/FBX 提供给用户下载。

无论 strict/advisory，以下情况始终硬拒绝：

- 缺失、多余、空文件或错误文件名；
- JSON/schema 不合法；
- manifest 的 job/type/input SHA 或对象身份不一致；
- Agent plan 与 manifest 不一致；
- 源对象未保持；
- QA、quality gate 和自动交付字段互相矛盾；
- 四视图缺失、PNG 无法解码或像素超限。

## 8. 重拓扑制品合同

成功任务固定以这两个正式 kind 作为普通用户交付：

| kind | filename |
| --- | --- |
| `blend` | `retopology_final.blend` |
| `fbx` | `retopology_final.fbx` |

应用不能继续只查找 `candidate_blend/candidate_fbx`。成功任务还返回：

- `process_report`、`baseline_audit`、`audit`、`manifest`；
- `comparison`；
- `agent_plan`、`agent_prompt`、`agent_events`；
- `high/reference/generated × front/side/top/perspective` 共 12 张视图；
- 有真实 `reference_views` 时增加 `reference_images` 汇总图。

无参考图时基础为 22 件，有参考图时为 23 件。普通用户界面可以只突出最终 BLEND/FBX 和 QA 摘要，
其余内容放入“高级诊断”，但不能丢弃服务端证据。

## 9. 通用任务状态

首次提交和幂等重放都返回完整 job。核心字段：

```json
{
  "job_id": "UUID",
  "status_url": "/api/v1/assets/jobs/UUID",
  "events_url": "/api/v1/assets/jobs/UUID/events",
  "cancel_url": "/api/v1/assets/jobs/UUID/cancel",
  "external_asset_id": "li3d:crate:retopo:001",
  "job_type": "RETOPOLOGY_PROCESS_V1",
  "status": "QUEUED",
  "progress": 0,
  "stage": "QUEUED",
  "stage_message": "任务已进入资产处理队列",
  "timing": {
    "queue_position": 1,
    "estimated_start_seconds": 0,
    "elapsed_seconds": 0,
    "estimated_remaining_seconds": null,
    "last_progress_at": null
  },
  "source_filename": "retopology_input.zip",
  "input_sha256": "...",
  "options": {
    "high_object": "crate_high",
    "target_faces": 1200,
    "project_filename": "crate.blend",
    "project_sha256": "...",
    "reference_views": [],
    "user_request": "..."
  },
  "worker_id": null,
  "attempt_count": 0,
  "created_at": "2026-08-03T00:00:00+00:00",
  "started_at": null,
  "finished_at": null,
  "error": null,
  "delivery_ready": false,
  "review_required": false,
  "artifacts_role": "retained",
  "artifacts": []
}
```

当前状态路径：

```text
QUEUED → CLAIMED → RUNNING → SUCCEEDED / FAILED
                         ↘ CANCELLING → CANCELLED
```

可重试的 Worker 失败可能暂时回到 `QUEUED + RETRY_QUEUED`。当前 UV/自动重拓扑不使用人工
`WAITING_REVIEW`，`review_required` 固定为 `false`。应用不得把 95%、候选已生成或 SSE 断开当成完成。

终态后 `finished_at` 和 `timing.elapsed_seconds` 冻结。`estimated_start_seconds` 是动态估计，不是 SLA。

## 10. SSE 与取消

```text
GET /api/v1/assets/jobs/{job_id}/events
Accept: text/event-stream
Last-Event-ID: <上次收到的 sequence，可选>
```

事件名固定为 `asset-progress`，data 包含：

```text
job_id / status / stage / progress / message /
estimated_remaining_seconds / details / created_at
```

SSE 只负责低延迟显示；`GET job` 是最终权威。SSE 断开、应用退出、Worker 暂时离线或单步骤错误都不能
自动触发取消。

取消：

```text
POST /api/v1/assets/jobs/{job_id}/cancel
```

无 body。排队任务直接 `CANCELLED`；运行任务先进入 `CANCELLING`，在 Worker 安全点收敛为
`CANCELLED`。终态重复取消返回当前 job，不创建第二次取消。

## 11. Artifact 下载与 SHA

每个 artifact：

```json
{
  "id": "UUID",
  "kind": "blend",
  "filename": "retopology_final.blend",
  "content_type": "application/octet-stream",
  "size_bytes": 14798992,
  "sha256": "...",
  "download_url": "/api/v1/assets/jobs/UUID/artifacts/UUID"
}
```

下载后必须验证：

```text
SHA256(response body) == artifact.sha256 == X-Artifact-SHA256
```

任一不一致都视为交付失败，不得缓存或展示该文件。

## 12. 应用端状态映射

| 实际情况 | 应用文案/行为 |
| --- | --- |
| CA 缺失、SHA 不符或 TLS 握手失败 | “服务证书未信任”，提供安装/修复入口；不要称“等待服务授权” |
| HTTP `401/403` | “API 身份认证失败”，提示检查安全下发的 API Key |
| HTTP `200/202` | 已取得任务；进入轮询/SSE，不重复创建 |
| `QUEUED` 且未分配 Worker | “排队中”，展示 queue position/预计开始时间 |
| `RUNNING` | 展示 stage、progress、开始时间、Worker 和 ETA |
| `SUCCEEDED` 无 warning | “处理完成”，开放正式制品 |
| `SUCCEEDED` 有 `qa_warning` | “已交付 · QA 告警”；UV 开放五件套，重拓扑开放正式 BLEND/FBX |
| `FAILED` | 展示 `error.code` 和简洁摘要；原始日志折叠到高级诊断 |
| `CANCELLING/CANCELLED` | 明确区分“正在取消”和“已取消” |

应用可用性规则：

- CA/TLS 或 Asset API 不可达时，UV 与自动拓扑都不可提交；
- Codex 异常不能禁用 UV，也不能显示成 UV 授权失败；
- ComfyUI 忙不等于 Asset CPU Worker 忙；UV/自动拓扑与 GPU 推理独立排队；
- `/capacity` 是聚合提示，不能用它承诺某一个自动拓扑任务立即执行；
- 不实现人工批准/驳回按钮，最终事实只认服务端 job 状态和 artifacts。

## 13. 常见错误码

| HTTP/错误码 | 应用处理 |
| --- | --- |
| `401/403 AUTH_FAILED` | 停止重试，检查 API Key 或来源身份 |
| `409 CLIENT_IP_CONFLICT` | 改用独立 API Key |
| `422 ASSET_INPUT_INVALID` | 修正扩展名、metadata、对象名或未知字段 |
| `422 ASSET_EMPTY` | 拒绝 0 字节输入 |
| `413 ASSET_TOO_LARGE` | 减小输入或联系服务端调整受审限额 |
| `422 REFERENCE_IMAGE_INVALID` | 修复损坏/伪造图片 |
| `413 REFERENCE_IMAGE_TOO_LARGE` | 降低参考图像素 |
| `409 IDEMPOTENCY_CONFLICT` | 同一个 key 被用于不同请求；不要自动换 key 重提 |
| `409 EXTERNAL_ASSET_CONFLICT` | 业务 ID 已存在；查询原任务或生成新的业务版本 ID |
| `404 ASSET_JOB_NOT_FOUND` | 当前身份无权访问或 job 不存在 |
| `409 ASSET_NOT_COMPLETE` | 等待终态后再下载 |
| `404 ASSET_ARTIFACT_NOT_FOUND` | 刷新 job，使用最新 artifact ID |
| job `UV_QA_FAILED` | 仅 strict 模式或非质量类硬门禁失败；生产 advisory 下纯几何 QA 应为成功告警并交付五件套 |
| job `RETOPOLOGY_QUALITY_GATE_FAILED` | strict 质量失败，仅诊断；生产 advisory 正常情况下会改为成功告警交付 |
| job `BLENDER_EXECUTION_FAILED` | 保存 job/request ID，交由 GPU Control 管理端查高级诊断 |

## 14. 1.5.8 Asset API / Worker 1.2.4 分阶段部署边界

当前 Asset API/Worker 已把新鲜健康 Codex 探针精确门禁到 `RETOPOLOGY_PROCESS_V1` 的 Worker 领取：

- 只有 `AUTHENTICATED + HEALTHY + 未超过 3600 秒` 的 Linux Asset Worker 才领取自动重拓扑；
- UV 和独立拓扑审计不受该 Codex 门禁影响；
- 提交接口仍可返回 `202`，暂时没有合格 Worker 时任务保持 `QUEUED`，不是 `AUTH_FAILED`；
- Web 会区分 `HEALTHY/STALE/认证失败/探针失败`，Windows Baker 心跳不再覆盖 Linux Codex 状态。

这些能力已局部上线。三 Worker 镜像 revision 不同，但 Worker 相关源码与三项批准 Skill 文件 SHA
一致；统一 OCI image digest/SBOM 仍待归档。真实 PBR、UV warning、UV clean 和连续两笔重拓扑
canary 均已成功，但当前仍标记为 `DEPLOYED_NOT_ACCEPTED`。已部署修复包括：

- `UV_PROCESS_V2` 由 Worker 上传完整实测报告，再由 Asset API 按
  `UV_QA_ENFORCEMENT` 执行 strict/advisory；advisory 失败质量项不会隐藏五件套；
- Windows Baker 对 PowerShell 空 `ExitCode` 使用“日志成功 marker + 服务端制品完整性”的受限兼容，
  非零退出或缺少 marker 仍硬失败；
- Worker 启动时只为两个批准业务 Skill 建立精确子链接，保留 Codex 自有 `skills/.system`；inspect、
  probe、heartbeat 发现链接漂移时统一上报 `SKILL_MOUNT_INVALID`。

按 [82 号发布验收记录](82_2026-08-03_ASSET_FAILURES_UV_ADVISORY_AND_RELEASE_ACCEPTANCE.md)
完成控制面统一、API artifact 三重 SHA、统一 OCI image digest/SBOM、回滚和观察回填后，才能全量生产验收。

## 15. 最新测试证据与边界

- 六 API R8：120 VU、39,778 个 HTTP 请求、0 失败；
- 自动重拓扑：10/10 `SUCCEEDED`，每单 23 件制品，共 230 次下载和 SHA 校验通过；
- 前序 1.5.8 候选 `52ecad10…` 的源码回归：Python `315 passed / 6 skipped`；Web `16 passed`，
  类型、lint、格式和构建通过；该数字早于本轮 UV/PBR/Skill 修改，不能冒充最终候选全量结果；
- 本轮全量 unit `233 passed, 1 skipped`；全量 integration `116 passed, 5 skipped`；Ruff 全部通过；
  本轮相关 4 个文件 mypy 通过（不代表全仓 mypy 通过）；两份 Compose config 均可解析；
- 真实 PBR、UV warning、UV clean 与连续两笔重拓扑 canary 均已成功；数据库 artifact SHA 已记录，
  API body/header 三重下载校验仍待回填；
- 三节点 2026-08-03 Codex 探针快照均为 `AUTHENTICATED/HEALTHY`，但该快照不是永久 SLA；
- 尚未完成固定素材全部联合基准、完整故障矩阵、registry/SBOM 证据和连续七天观察，因此整体状态不是
  `FROZEN` 或 `PRODUCTION_ACCEPTED`。

## 16. 联调回执清单

应用团队回传时请填写：

```text
Li3D build/version:
Li3D source commit:
CA bundled path:
CA SHA-256 verified: YES / NO
API identity exchanged by secure channel: YES / NO
UV request ID / job ID:
UV five artifacts SHA verified: YES / NO
UV advisory warning rendered without hiding downloads: YES / NO / NOT DEPLOYED
Retopology request ID / job ID:
Retopology formal blend/fbx found by kind: YES / NO
qa_warning UI mapping verified: YES / NO
SSE reconnect with Last-Event-ID verified: YES / NO
Idempotent response-loss replay verified: YES / NO
Explicit cancel verified: YES / NO
Raw status JSON attached: YES / NO
Artifact SHA report attached: YES / NO
```

密钥、token 和认证文件不得附在 Markdown 回执中，必须通过安全渠道交换。
