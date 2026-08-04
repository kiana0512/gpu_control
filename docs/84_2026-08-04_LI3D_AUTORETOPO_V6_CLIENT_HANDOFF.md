# Li3D 自动拓扑 V6 用户端对接手册

日期：2026-08-04  
接口：`POST /api/v1/assets/retopology/process`  
引擎契约：`retopology-v6`  
策略：`li3d-retopology-v6@6.0.0`  
策略 SHA-256：`e6781d6158a93e571c944f5913a600838fe28fc2edc38a3b1909f649f66f3d3d`

## 1. 本次用户端必须修改的内容

1. 隐藏并停止提交“目标面数”输入框、滑块和预设值。
2. 不再向用户展示“50–5000 面”等人工面数承诺。
3. 页面说明改为“系统根据高模结构、轮廓、形变与交付档位自动确定合理密度”。
4. 保留交付档位选择；默认使用 `next_gen_game_prop`。
5. 成功时只展示服务器正式发布的 BLEND、FBX 和必要报告。
6. V6 失败时不得把候选低模当作最终结果展示或下载。

用户端尚未升级时仍可发送旧 `target_faces`。服务器会忽略该字段并返回兼容告警
`DEPRECATED_TARGET_FACES_IGNORED`；该值不会转换成比例、预算、生成参数或 QA 门槛。

## 2. V6 核心语义

- 唯一形体权威是本次上传的高模；旧低模、当前低模和 bootstrap 低模都不参与决策。
- 面数由 Agent 自动确定，`budget_mode` 固定为 `automatic`。
- 每个任务只允许一个正式候选，内部最多执行两轮根因修正。
- 正式发布要求源文件 SHA 不变并同时通过八项门禁：
  - 源文件保护；
  - 拓扑完整性；
  - 六向轮廓；
  - 构造合理性；
  - 自适应密度；
  - 布线分布；
  - 着色质量；
  - 制品完整性。
- 任一门禁失败，任务状态为 `FAILED`，候选文件只在服务器隔离保留供管理员诊断，用户端无正式交付。

## 3. 提交方式

请求使用 `multipart/form-data`：

- `project`：高模文件，支持 `.fbx`、`.obj`、`.glb`、`.gltf`、`.blend`；
- `metadata`：下面的 JSON 字符串；
- `reference_images`：可选，最多 16 张，文件名必须与 `metadata.reference_views` 完全一致；
- Header `Idempotency-Key`：每次业务提交必须提供稳定且唯一的幂等键。

最小 `metadata`：

```json
{
  "api_version": "6.0",
  "external_asset_id": "li3d:asset-001:retopology:v6",
  "options": {
    "algorithm": "agent",
    "budget_mode": "automatic",
    "topology_style": "mixed_game_ready",
    "preserve_source": true,
    "preserve_sharp_edges": true,
    "preserve_boundaries": true,
    "delivery_profile": "next_gen_game_prop"
  },
  "reference_views": [],
  "user_request": "保留主要轮廓、开口、支撑关系与关键负空间"
}
```

允许的 `delivery_profile`：

- `next_gen_game_prop`
- `realtime_background_prop`
- `mobile_game_prop`

示例：

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${ASSET_API_TOKEN}" \
  -H "Idempotency-Key: li3d-asset-001-retopo-v6-001" \
  -F 'project=@/secure/input/high_model.blend;type=application/octet-stream' \
  -F 'metadata={"api_version":"6.0","external_asset_id":"li3d:asset-001:retopology:v6","options":{"algorithm":"agent","budget_mode":"automatic","topology_style":"mixed_game_ready","preserve_source":true,"preserve_sharp_edges":true,"preserve_boundaries":true,"delivery_profile":"next_gen_game_prop"},"reference_views":[],"user_request":"保留主要轮廓与关键负空间"};type=application/json' \
  https://10.3.34.11/api/v1/assets/retopology/process
```

密钥只能通过安全配置注入，不能写入源码、Markdown、浏览器日志或截图。

## 4. 旧客户端兼容窗口

服务器仅兼容已知旧字段，并把兼容行为写入任务 `options.deprecated_fields_ignored` 和事件告警。

| 旧输入 | V6 行为 |
|---|---|
| `target_faces` | 删除并告警，不参与任何预算或 QA |
| `algorithm=quadriflow/cleanup_existing` | 归一为 `agent` 并告警 |
| `topology_style=mixed/quad_dominant/preserve_existing` | 归一为 `mixed_game_ready` 并告警 |
| `preserve_sharp` | 归一为 `preserve_sharp_edges` |
| `preserve_boundary` | 归一为 `preserve_boundaries` |
| V5 bootstrap/low/object selector 字段 | 删除并告警，不作为形体权威 |
| 未知字段 | `422 ASSET_INPUT_INVALID`，拒绝请求 |

兼容窗口只保证旧调用不会因已知字段立即中断，不保证旧面数或旧算法语义继续生效。用户端应尽快停止发送这些字段。

## 5. 状态轮询和取消

创建成功返回 `202`，其中包含：

- `job_id`
- `status_url`
- `events_url`
- `cancel_url`
- `job_type=RETOPOLOGY_PROCESS_V2`
- `status=QUEUED`

轮询：

```text
GET /api/v1/assets/jobs/{job_id}
```

事件：

```text
GET /api/v1/assets/jobs/{job_id}/events
```

取消：

```text
POST /api/v1/assets/jobs/{job_id}/cancel
```

用户端应按服务器状态渲染，不得根据本地倒计时自行判定完成。终态只有：

- `SUCCEEDED`：八门禁全部通过，正式交付已发布；
- `FAILED`：未通过门禁或执行失败，无正式低模可交付；
- `CANCELLED`：用户或管理员已取消。

## 6. 成功交付

成功响应中的 `artifacts` 至少包含正式模型：

- `<源文件名>_GAME_LOW.blend`，`kind=blend`
- `<源文件名>_GAME_LOW.fbx`，`kind=fbx`

还会包含执行计划、QA、七视图接触表、线框表、manifest 和 result 等证据。用户端默认突出显示正式 BLEND/FBX，把技术证据放入“高级诊断”。下载后建议校验每个 artifact 的 SHA-256。

`delivery_ready` 只有在 `status=SUCCEEDED` 时为 `true`。不得用进度 100%、本地文件名或候选文件存在作为交付判断。

## 7. 失败展示

V6 QA 失败时：

- 页面显示服务器 `error.code`、`error.message` 和 `stage_message`；
- 不显示“下载候选 FBX/BLEND”；
- 不显示“人工确认后也可使用”；
- 不提供客户端绕过、管理员确认发布或 advisory 发布按钮；
- 可以提示用户调整高模输入、参考图或交付档位后用新的幂等键重新提交。

对 V6 任务，`artifacts_role=isolated_diagnostic` 表示诊断已由服务器隔离保留，普通用户端收到的 `artifacts` 为空。

## 8. 推荐页面文案

标题：`自动拓扑 V6`  
说明：`导入高模，系统将根据结构、轮廓和交付档位自动确定合理密度，并在八项质量检查通过后发布正式低模。`  
执行中：`Agent 正在分析高模并生成唯一正式候选。`  
QA 中：`正在执行七视图、拓扑、构造、密度、布线、着色与制品完整性检查。`  
成功：`正式低模已通过全部质量门禁，可下载 BLEND 与 FBX。`  
失败：`本次候选未通过全部质量门禁，未发布正式低模。`

## 9. 用户端验收清单

- 页面不存在可编辑目标面数控件；
- 网络请求不含 `target_faces`；
- 高模支持五种约定格式；
- 0–16 张参考图的声明与上传文件名一致；
- 重复点击使用同一个幂等键，不创建第二个任务；
- `RETOPOLOGY_PROCESS_V2` 能正确分类和展示；
- 成功时 BLEND/FBX 可下载且 SHA 正确；
- 八门禁任一失败时不展示任何候选模型下载；
- 取消、刷新和断线重连后仍以服务端任务状态为准；
- 日志、埋点和错误页面不泄漏 Token 或内部文件路径。

## 10. 权威来源

本手册与以下 V6 交付内容对齐：

- `validation-report.md`
- `09_服务器部署操作手册.md`
- `Li3D_AutoRetopo_Server_Deployment_V6.zip`
- 冻结策略 `config/retopology-policy-v6.json`
- 请求、计划和结果 Schema
- V6 正式 Agent、独立 QA Prompt 与 Skill

若旧文档与上述 V6 交付冲突，以本次 V6 冻结策略和 Schema 为准。V5 文档只用于回滚和历史任务解释。
