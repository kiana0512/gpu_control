# 统一调度中心 Asset V3：UV / 重拓扑 API、进度合同与真实验收

文档日期：2026-07-28
软件版本：GPU Control `1.5.0`
接口版本：Asset V3
适用调用方：动画管家、资产生产 Agent、内部工具及人工运维台

本文是 UV 与重拓扑能力的当前唯一完整交接。`52`、`53`、`54` 号文档保留为实施历史；其中
“只审计、不生成候选”“没有四视图”“没有审核 API”等描述已经被本文替代。

## 1. 当前能力结论

- UV 和重拓扑使用独立 Asset API、Asset Job、Asset Worker 与 CPU 并发槽，不占用 GPU
  Scheduler/ComfyUI 的任务槽。
- 4090 与 3090-A 已使用同一份固定代码、Blender `5.1.2`、Codex CLI 与两套 Skill 做真实
  隔离执行；3090-B 的同版本 Asset Worker 已上线并持续心跳，但 B 的真实 UV/重拓扑 canary
  尚待下一维护窗口，不能把“Worker 在线”写成“业务验收通过”。
- UV 接受 FBX、OBJ、GLB/GLTF、BLEND，成功时一次性发布固定五件套。
- 重拓扑接受包含高模、参考低模、当前低模的 BLEND，并可同时上传 0～32 张多视角参考图；
  Worker 会生成版本化候选低模、FBX、前/侧/顶/透视图、基线/最终审计和 Agent 证据。
- 重拓扑完成执行后固定进入 `WAITING_REVIEW`，必须由管理员看过三模型 × 四视图后批准；
  未批准的候选不会冒充最终游戏低模。
- 长任务同时支持 GET 轮询和 SSE。轮询状态是最终事实，SSE 是低延迟提示并支持
  `Last-Event-ID` 断线续传。
- 每个上传、内部候选和最终 Artifact 都可追溯；下载时必须同时核对 JSON SHA、
  `X-Artifact-SHA256` 和本地文件 SHA。

## 2. 架构与任务隔离

```text
调用方
  └─ HTTPS /api/v1/assets/*
       └─ Asset API ─ PostgreSQL(asset_jobs/events/artifacts/workers)
            ├─ 4090 Asset Worker（CPU 并发槽）
            ├─ 3090-A Asset Worker（CPU 并发槽）
            └─ 3090-B Asset Worker（ONLINE，待真实资产 canary）

GPU 图片 API
  └─ GPU API / Scheduler / ComfyUI / GPU Job 槽
```

两个平面共享统一认证、PostgreSQL、审计、Nginx 和 WebUI，但不共享领取 SQL、租约、Worker
容量或执行槽。大量 UV/拓扑任务只会在 Asset 队列排队，不会阻塞抠图和局部重绘。

## 3. 认证、幂等与通用约定

生产入口：

```bash
BASE_URL=https://10.3.34.11
CA_FILE=/absolute/path/GPU_CONTROL_LAN_CA.crt
API_KEY='gpc_<prefix>_<secret>'
```

认证可使用 `X-API-Key`，或由系统按真实来源 IP 绑定客户。建议所有自动化调用都显式发送：

```text
X-API-Key: ...
Idempotency-Key: <业务唯一键>
X-Request-ID: <本次链路追踪 ID>
```

同一幂等键 + 完全相同输入/metadata 返回原 Job；同一幂等键但内容不同返回 HTTP `409`，调用方
不得用重试制造第二份资产。

提交成功返回 HTTP `202`，响应中直接给出：

```json
{
  "job_id": "uuid",
  "status": "QUEUED",
  "status_url": "/api/v1/assets/jobs/{job_id}",
  "events_url": "/api/v1/assets/jobs/{job_id}/events",
  "cancel_url": "/api/v1/assets/jobs/{job_id}/cancel"
}
```

## 4. UV API

### 4.1 提交

`POST /api/v1/assets/uv/process`，multipart 字段：

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `asset` | file | 是 | `.fbx/.obj/.glb/.gltf/.blend` |
| `metadata` | JSON string | 是 | external ID 和 UV 参数 |

```bash
MODEL=/data/chair.fbx
EXTERNAL_ID='asset:chair:uv:20260728:001'

curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/uv/process" \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: $EXTERNAL_ID" \
  -H "X-Request-ID: li3d-chair-uv-001" \
  -F "asset=@${MODEL};type=application/octet-stream" \
  -F 'metadata={"external_asset_id":"asset:chair:uv:20260728:001","options":{"hidden_axis":"y+","hard_edge_angle_degrees":75,"resolution":2048,"padding_px":10,"texel_density_mode":"uniform","qa_profile":"pbr-v1"}};type=application/json'
```

参数：

| 参数 | 默认值 | 范围 |
|---|---:|---|
| `hidden_axis` | `y+` | `x+/x-/y+/y-/z+/z-/auto` |
| `hard_edge_angle_degrees` | 75 | 1～179 |
| `resolution` | 2048 | 1024/2048/4096/8192 |
| `padding_px` | 10 | 2～128 |
| `texel_density_mode` | `uniform` | 当前固定 `uniform` |
| `qa_profile` | `pbr-v1` | 当前固定 `pbr-v1` |

本能力只处理 UV，不生成 Base Color、Normal、Roughness 等贴图，也不覆盖用户源文件。

### 4.2 UV 原子交付

输入 `chair.fbx` 只在全部门禁通过后发布：

```text
chair_PBR_UV.blend
chair_PBR_UV.fbx
chair_PBR_UV_report.json
chair_PBR_UV_QA.json
chair_PBR_UV_FBX_QA.json
```

BLEND QA 和导出 FBX 再导入 QA 必须都 `passed=true`，五项必须非空且命名正确；任何一项失败，
Job 都不会成为 `SUCCEEDED`，中间文件也不会进入 artifacts。

## 5. AI 重拓扑 API

### 5.1 输入合同

`POST /api/v1/assets/retopology/process`，multipart 字段：

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `project` | file | 是 | `.blend`，包含高模、参考低模、当前低模 |
| `metadata` | JSON string | 是 | 对象名、目标面数、候选版本与要求 |
| `reference_images` | repeated file | 否 | 0～32 张真实多视角参考图 |

参考图在 metadata 中按文件名声明语义。图片不会写进模型几何，只作为 Agent 规划与人工视觉复核证据。

```bash
PROJECT=/data/crate_retopology.blend
EXTERNAL_ID='asset:crate:retopology:20260728:001'

curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST "$BASE_URL/api/v1/assets/retopology/process" \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: $EXTERNAL_ID" \
  -H "X-Request-ID: li3d-crate-retopo-001" \
  -F "project=@${PROJECT};type=application/octet-stream" \
  -F 'reference_images=@/data/crate_front.png;type=image/png' \
  -F 'reference_images=@/data/crate_side.png;type=image/png' \
  -F 'reference_images=@/data/crate_top.png;type=image/png' \
  -F 'reference_images=@/data/crate_perspective.png;type=image/png' \
  -F 'metadata={"external_asset_id":"asset:crate:retopology:20260728:001","options":{"high_object":"crate_high","reference_object":"crate_reference_low","low_object":"crate_current_low","generated_low_object":"crate_generated_v001","algorithm":"agent","target_faces":3000,"preserve_sharp":true,"preserve_boundary":true,"render_resolution":512,"max_repair_rounds":1,"require_closed":false},"reference_views":[{"filename":"crate_front.png","view":"front"},{"filename":"crate_side.png","view":"side"},{"filename":"crate_top.png","view":"top"},{"filename":"crate_perspective.png","view":"perspective"}],"user_request":"高模决定最终轮廓，参考低模决定布线风格和面数；保留硬边与组件边界。"};type=application/json'
```

### 5.2 重拓扑执行证据与 23 项交付

真实带参考图任务发布 23 项：

- 版本化候选：`retopology_candidate.blend`、`retopology_candidate.fbx`；
- 数值证据：baseline audit、final audit、process report、manifest；
- Agent 证据：实际 prompt、结构化 plan、JSONL events；
- 三角色 × 四视图：high/reference/generated 的 front/side/top/perspective 共 12 张；
- `retopology_comparison.png` 三行四列总览；
- `reference_images.png` 用户参考图汇总。

源对象不被覆盖。候选必须使用新对象名和新版本；如果需要下一轮，应由提交方在用户端完成复核并
按冻结后的客户接口创建新 Job，旧候选、旧审计和旧图片全部保留。

### 5.3 审核门禁

执行完成状态为 `WAITING_REVIEW`，进度 95%。提交方用户端应展示并检查：

1. 高模形状与轮廓；
2. 参考低模布线风格和面数；
3. 新低模四视图轮廓；
4. N-gon、非流形、松散几何、法线、破面等审计；
5. 候选 BLEND/FBX 和 manifest；
6. 源输入 SHA 与 Agent 证据。

批准后才发布；驳回则进入 `REVIEW_REJECTED`。统一调度后台只显示状态、诊断、制品清单和 SHA，
不提供人工批准/驳回按钮。当前客户侧“复核决定回传”公开接口仍待双方冻结并安全发布；在该接口
完成前，调用方只能读取 `WAITING_REVIEW` 证据，不能调用管理接口或把候选冒充最终交付。

## 6. 查询、双向进度和 ETA

### 6.1 轮询

```bash
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -H "X-API-Key: $API_KEY" \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID"
```

重要字段：

```json
{
  "status": "RUNNING",
  "worker_id": "asset-worker-v3-3090-a",
  "progress": 82,
  "stage": "RETOPOLOGY_RENDERING",
  "stage_message": "正在渲染高模、参考低模与生成低模四视图",
  "timing": {
    "elapsed_seconds": 96,
    "estimated_remaining_seconds": 120,
    "last_progress_at": "2026-07-28T...Z"
  }
}
```

建议 2～5 秒轮询并加抖动。ETA 是动态估算，不是完成承诺；状态、进度、stage、Worker 和 Artifact
以 PostgreSQL 轮询结果为最终事实。

### 6.2 SSE

```bash
curl --no-buffer --fail-with-body \
  --cacert "$CA_FILE" \
  -H "X-API-Key: $API_KEY" \
  -H "Last-Event-ID: 0" \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID/events"
```

每个事件有递增 `id`。断线后携带最后消费的 `Last-Event-ID` 重连；不要仅依赖 SSE 判断最终成功，
终态后再 GET 一次 Job 并下载 artifacts。

### 6.3 状态语义

| 状态 | 含义 |
|---|---|
| `QUEUED` | 已持久化，等待 Asset Worker；不是“尚未分配丢失” |
| `CLAIMED` | Worker 已获得租约 |
| `RUNNING` | Blender/Codex/QA 正执行，查看 stage 与 ETA |
| `WAITING_REVIEW` | 重拓扑执行完成，等待提交方用户端人工复核 |
| `REVIEW_REJECTED` | 候选被驳回，可创建下一版本 |
| `SUCCEEDED` | UV 或人工批准后的最终交付可下载 |
| `FAILED` | 失败关闭；读取 error 并按幂等键决定重试 |
| `CANCELLED` | 已取消，不发布半成品 |

## 7. 取消和 Artifact 下载

```bash
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -X POST -H "X-API-Key: $API_KEY" \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID/cancel"
```

```bash
curl --fail-with-body --silent --show-error \
  --cacert "$CA_FILE" \
  -H "X-API-Key: $API_KEY" \
  -o result.bin \
  "$BASE_URL/api/v1/assets/jobs/$JOB_ID/artifacts/$ARTIFACT_ID"
sha256sum result.bin
```

## 8. 并发与调度行为

- 每个 Asset Worker 的 `max_concurrency` 是独立 CPU 槽；一个 Worker 可以并发处理多个不同模型。
- Worker 只在 CPU/内存和当前槽位允许时领取；心跳丢失不会让 Job 无迹可寻，租约超时后按状态机恢复。
- UV 与重拓扑可无序混合进入同一 Asset 队列；Job 仍按自身 API 类型、阶段和 Artifact 合同执行。
- Asset Worker 使用 `CUDA_VISIBLE_DEVICES` 空值，避免它与 ComfyUI 争抢 GPU。
- 4090、3090-A、3090-B 可同时领取不同 Job；WebUI 只展示真实心跳。B 在完成真实资产 canary
  前虽可保持 Worker ONLINE，但验收记录必须明确区分“已注册”和“真实任务通过”。

## 9. WebUI 管理

入口：`/asset-processing`，左侧名称为“UV / 重拓扑”。

- 顶部可在“GPU 推理任务 / CPU 资产任务”两个平面之间切换；
- 按提交 API 分类：全部、UV、拓扑审计、AI 重拓扑；
- 按状态分类：排队/执行、等待复核、已交付、异常/取消；
- 支持任务 ID、external ID、客户、文件名和 Worker 搜索；
- 顶层一个模型只占一行，内部阶段与 5/23 项制品全部在详情中，不刷屏；
- 详情显示 Worker、stage、stage message、耗时、ETA、输入 SHA、提交 API 和下载 SHA；
- 重拓扑详情显示状态、诊断、制品清单和 SHA；三模型四视图由提交方用户端读取并执行人工复核，
  调度后台不提供批准、驳回或创建下一版本按钮。
- GPU 与资产任务列表均分页显示，默认 20 条，可切换 50/100；表格与操作文字已按生产大屏统一放大。
- 资产页可直接跳转到“API 客户 → 统一服务调用方式”，复制 UV/重拓扑 cURL 或 Python 示例。

## 10. 真实验收结果

### 10.1 两机混合验收

真实输入：Khronos DamagedHelmet GLB、Stanford Bunny 三层级 BLEND、真实 Bunny 四视图参考图。

| Job 类型 | 数量 | Worker | 结果 |
|---|---:|---|---|
| UV | 2 | 4090 + 3090-A | 2/2 `SUCCEEDED`，每单 5 artifacts |
| AI 重拓扑 | 2 | 4090 + 3090-A | 2/2 `WAITING_REVIEW`，每单 23 artifacts |

UV 用时 10/12 秒；重拓扑用时 137/152 秒。全部 Artifact 完成 JSON/header/body 三重 SHA
校验；全部 SSE id 单调且无重复。

### 10.2 最终代码 3090-A Canary

- Job：`b2e66de5-0853-4fad-88b5-d07f215e467f`
- Worker：`asset-worker-v3-3090-a`
- 状态：`WAITING_REVIEW`
- 尝试：1
- 用时：159 秒
- SSE 事件：19
- Artifact：23/23
- comparison SHA-256：`daea5db5d0efa8a61abeefeeb50e646f56d819db3d94a66b8a9e2f5b86e33a6c`

执行阶段真实经过：领取、Agent 规划、基线/最终审计、候选导出、四视图渲染、原子上传、等待复核。

### 10.3 自动化和浏览器

- API + Asset API 集成测试：`26 passed in 40.76s`；
- Vue TypeScript + Vite production build：通过；
- Chromium：登录、两任务平面切换、API 分类、状态筛选、真实比较图、23 artifacts 全部通过；
- 浏览器结果：2 个 UV、4 个重拓扑，console errors `0`；
- 最终分页/API 示例浏览器验收：GPU 默认 20 条、资产默认 20 条、UV/重拓扑示例完整，console errors `0`；
- 最终截图 SHA-256：`0dd49740d53325e726840d73edb4511ec2082a5df5603dad48e7dc7beae242e1`。

## 11. 本轮真实测试发现并修复的问题

1. Worker ID 使用下划线会被 API 422 拒绝：启动配置现在提前按统一正则失败关闭。
2. 运行中只热替换脚本会造成进程内固定 SHA 与文件不一致：Worker 正确拒绝执行；发布流程要求
   代码与固定 SHA 一起滚动重启。
3. Blender 5.1.2 headless 不提供 Workbench：渲染器按实际能力回退到 Eevee，固定灯光、材质、
   相机和世界色；不修改候选几何。
4. 初版透视图尺度偏小/高模过暗：相机距离按对象尺寸确定，并只调整复核渲染材质与灯光；
   不改变源模型或业务拓扑。
5. 旧 WebUI 仍显示硬编码候选节点：当前只展示真实 Asset Worker 心跳；3090-B 未心跳时不会出现。
6. 任务分类不够显眼：统一任务入口新增 GPU/CPU 平面切换、API 分类、状态分类和搜索。
7. 3090-A 因 DHCP 从 `10.3.34.13` 漂移到 `10.3.34.12`：按唯一 MAC
   `18:c0:4d:9f:13:13` 和 hostname `lilithgames1` 双重确认后更新节点地址。

## 12. 3090-B 当前迁移状态

3090-B 当前唯一物理身份为 MAC `3c:7c:3f:a5:b0:4f`、GPU UUID
`GPU-092a5184-5857-d196-5df2-efa9503368aa`、Windows IP `10.3.34.14`。Windows 原生用于后续
Substance/烘焙；WSL2 用于 ComfyUI 与 UV/重拓扑，不能把两者登记成两台物理 GPU。

当前已完成：固定地址上报、Node Agent、ComfyUI 真实双工作流验收、Blender Worker 镜像同步、
Blender `5.1.2`、Skill `asset-skills-2026.07.28-v3` 和 4 个 Asset 槽心跳。尚未完成：B 的真实
UV/重拓扑 canary、客户侧人工复核回传接口、Windows 原生烘焙 Worker。完整事实、Job ID、SHA 和
回滚方式见 `57_2026-07-28_3090_B_WINDOWS_WSL2_GPU_ACCEPTANCE.md`。

## 13. 生产发布与回滚

发布前：确认 GPU 与 Asset 在途任务均为 0、备份 PostgreSQL、记录当前镜像 ID。执行 migration
`20260728_0007` 后，依次更新 API、Asset API、Web、4090 Asset Worker，再启用 3090-A Worker。
本次发布只短暂重启控制 API 与 Scheduler 以装载新版本和修正后的节点地址；ComfyUI 未重启、外部
ImageClip/ModelViewCreator 工作流未修改。

### 13.1 2026-07-28 生产落地结果

- Alembic：`20260728_0007`；发布前 PostgreSQL 备份 SHA-256：
  `5db6aaa3528c642aa94c26109cc5b91eb0f4ad19dad3622a188ebcb7be54b057`；
- `gpu-control-api:1.5.0`：`sha256:0e8cad452ebae3313a7c2f344d2b96541ff2709898b8e8018842d88c27f87ccd`；
- `gpu-control-scheduler:1.5.0`：`sha256:49acd3433943ccbc691f81c88e36dd591f2c26d4bd26cd062155012477734dac`；
- 当前运行 `gpu-control-web:1.5.0`：`sha256:0a0ef7a476d1d737b284571eb99eb22d0429262204fd4cc41790bc244c831558`；
- 当前运行 `unified-scheduler-asset-api:1.5.0`：`sha256:85c248e57f03fd5be75022fa8592c9d388872b1cbd0ecf0e92366b647e13c5cf`；
- `li3d/blender-worker:1.1.0`：`sha256:c43941fb6dd4bbb68eec89eacd92c42e73d86677daa505d9980e7e4a1c0065a6`；
- 离线归档：`/srv/gpu-control/images/unified-scheduler-1.5.0-images.tar.gz`，SHA-256
  `598f25a0a9100b9ebd1e87d084eb3be31e2168ac1b624768260619abc3fbfac8`；
- 4090 Worker：ONLINE，2 槽；3090-A Worker：ONLINE，3 槽；3090-B Worker：ONLINE，4 槽；
  均为 0 个在途资产任务。B 的真实资产 canary 仍需补做；
- 生产 API、Asset API、Scheduler、Web、Nginx、PostgreSQL、Redis 均健康；ComfyUI 保持 healthy。

回滚时先停止新的 Asset 提交和领取，等待/取消 Asset Job，再回滚 API/Web/Worker 镜像。数据库迁移
保留向前兼容表；禁止在尚有租约的 Worker 上直接替换脚本。

## 14. 所有权边界

GPU Control 只管理 API、队列、租约、进度、传输、审计、WebUI、Blender 执行器和发布门禁。
ImageClip、ModelViewCreator 的工作流 JSON、模型、提示词、图拓扑和输出语义不在本功能范围内，
本次没有修改这些外部业务管线。
