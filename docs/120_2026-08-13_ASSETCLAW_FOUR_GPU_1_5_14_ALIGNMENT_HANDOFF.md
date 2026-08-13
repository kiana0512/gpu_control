# 动画管家 × GPU Control 四 GPU / 六 API 生产对接合同

日期：2026-08-13（Asia/Singapore）

接收方：AssetClaw 动画管家

提供方：GPU Control 统一调度中心

对接入口：`https://10.3.34.11`

文档状态：`FUNCTIONAL_RECOVERY_CONFIRMED / STABILITY_TESTING`

目标控制面版本：`1.5.14`

> 本文是动画管家当前应采用的最新对接基线。它替代旧文档中的三卡拓扑、ImageClip
> `SaveImage #25`、批次“只能完整成功或失败”以及 3090-B Baker v6 等旧口径。接口路径保持兼容，
> 动画管家不得直连任一 GPU 节点的 ComfyUI、WSL、Blender Worker 或 Substance Agent。

## 1. 当前结论与使用边界

当前四台物理 GPU 已纳入同一个生产调度域：

- ImageClip RGBA 抠图、ModelView 局部重绘和 PBR 粗糙度可在兼容的四台 GPU 间调度；
- Blender PBR UV、Direct V2 自动拓扑可在四台主机的独立 CPU Asset Worker 上调度；
- Substance PBR 烘焙只允许 3090-B 的 Windows 原生 Baker 执行；
- 4070Ti 对局部重绘有响应优先权，3090-B 对烘焙有唯一执行权；
- 两个 15 分钟保护窗口均为工作保持型窗口，不是固定休眠，不会在没有受保护任务时把设备锁死；
- GPU 工作流切换必须先排空队列、卸载模型并验证显存释放，CPU 任务不受该 GPU 窗口影响；
- ImageClip、ModelView 和 Roughness 的外部工作流语义没有被 GPU Control 修改；
- 3090-B Substance Baker v7 已由真实任务闭环，正式 1.5.14 镜像、LFS 与发布证据正在归档。

动画管家现在可以开始接口功能与稳定性测试。正式容量压测必须由双方使用隔离测试身份执行，不能用
破坏生产任务、强杀 ComfyUI 或制造真实 OOM 的方式取证。

## 2. 生产架构与物理拓扑

```text
动画管家
   |
   | HTTPS 443（统一身份、幂等、状态、SSE、制品下载）
   v
10.3.34.11  GPU Control / Nginx / API / Scheduler / Asset API
   |
   +-- GPU 推理调度 -----------------------------------------------+
   |      4090        3090-A       3090-B       4070Ti             |
   |      1 槽         1 槽          1 槽          1 槽             |
   |      抠图/重绘/粗糙度，按兼容性、保护窗口和公平队列选择         |
   |
   +-- CPU Asset 调度 ---------------------------------------------+
   |      2 槽         3 槽          4 槽          2 槽             |
   |      UV / Direct V2 自动拓扑 / 内部拓扑审计                    |
   |
   +-- Windows Substance ------------------------------------------+
          只在 3090-B；4 个 Agent 实例；共享同一物理 GPU 围栏
```

| 显示名称 | 固定 Node ID | 地址/系统 | GPU | GPU 槽 | CPU Asset 槽 | 固定职责 |
|---|---|---|---:|---:|---:|---|
| 4090 控制中心 | `control-4090` | `10.3.34.11` / Linux | RTX 4090 24 GB | 1 | 2 | 控制面、共享 GPU、Overflow |
| 3090-A | `worker-3090-a` | `10.3.34.12` / Linux | RTX 3090 24 GB | 1 | 3 | 共享 GPU、CPU Asset |
| 3090-B | `worker-3090-b` | `10.3.34.14` / Windows + WSL2 | RTX 3090 24 GB | 1 | 4 | 共享 GPU、唯一 Substance Baker、CPU Asset |
| 4070Ti | `worker-4070ti-animation-host-01` | `10.3.34.238` / Windows + WSL2 | RTX 4070 Ti 12 GB | 1 | 2 | 共享 GPU、局部重绘响应优先、CPU Asset |

WSL NAT 地址不是业务身份。动画管家只保存 GPU Control 返回的 `job_id`、`batch_id`、业务外部 ID 和
请求 ID，不保存或选择 Worker IP，不把任务绑定到某一张卡。

## 3. 六项公开生产能力

| # | 能力 | 提交入口 | 模式 | 可执行节点 | 成功结果 |
|---:|---|---|---|---|---|
| 1 | ImageClip RGBA 序列帧抠图 | `POST /api/v1/batches/imageclip-rgba` | 异步父批次 | 四台 GPU | RGBA PNG 成功子集 ZIP + Manifest |
| 2 | ModelView 局部重绘 | `POST /api/v1/services/modelview-inpaint` | 同步图片 | 四台兼容 GPU；4070Ti 冲突时优先 | 最终 PNG，响应头含 `X-Job-ID` |
| 3 | PBR 粗糙度 | `POST /api/v1/services/modelview-roughness` | 同步图片 | 四台兼容 GPU | 最终 Roughness PNG |
| 4 | Blender PBR UV | `POST /api/v1/assets/uv/process` | 异步 Job | 四台 CPU Asset Worker | FBX/报告等原子制品 |
| 5 | Direct V2 自动拓扑 | `POST /api/v1/assets/retopology/process` | 异步 Job | 四台 CPU Asset Worker | 低模 FBX/报告/视图等原子制品 |
| 6 | Substance PBR 烘焙 | `POST /api/v1/assets/bake/process` | 异步 Job | 仅 3090-B Windows | 贴图、`baker.log`、`baker_result.json` |

单张 ImageClip 仍兼容 `POST /api/v1/services/imageclip-rgba`，但动画序列必须使用父批次入口，不能逐帧
创建顶层同步请求。内部拓扑审计 `/api/v1/assets/retopology/audit` 是验收能力，不占公开六项名额。

## 4. 统一网络、身份与幂等规则

### 4.1 TLS

- 只访问 `https://10.3.34.11`；
- 客户端必须信任随交付提供的 `GPU_CONTROL_LAN_CA.crt`；
- 当前 LAN CA SHA-256：
  `ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b`；
- 禁止 `verify=False`、`curl -k` 或绕过证书校验；
- 禁止直连 `8188`、`9201`、`9202`、Windows SSH、PostgreSQL 或 Redis。

### 4.2 客户身份

生产推荐发送专用密钥：

```http
X-API-Key: gpc_<prefix>_<secret>
```

联调期可由真实来源 IP 自动识别客户，但 IP 改变后不能访问旧租户资源。创建、查询、SSE、取消和下载
必须使用同一客户身份。

### 4.3 必须持久化的关联键

| 字段 | 规则 |
|---|---|
| `external_batch_id` / `external_asset_id` | 动画管家生成；一个不可变业务 generation 永久唯一 |
| `Idempotency-Key` | 同一提交的网络重放必须复用；内容改变必须换 generation 和 key |
| `batch_id` / `job_id` | GPU Control 返回；后续状态、SSE、取消、下载的唯一服务端主键 |
| `X-Request-ID` | 建议每次 HTTP 动作唯一，格式如 `am-<业务ID>-<动作>-<序号>` |
| `X-Job-ID` | 同步图片 API 的执行身份；必须写入动画管家日志 |

同一租户下，同 key + 同内容返回原任务；同 key + 不同内容返回 `409 IDEMPOTENCY_CONFLICT`。创建
请求超时但结果未知时，必须用原文件、原 metadata/manifest、原 key 重放，不能先创建第二笔业务任务。

## 5. ImageClip 动画序列帧合同

### 5.1 创建请求

```http
POST /api/v1/batches/imageclip-rgba HTTP/1.1
Host: 10.3.34.11
X-API-Key: gpc_xxx
Idempotency-Key: assetclaw:episode01:shot010:matting:g1
X-Request-ID: am-shot010-g1-create-01
Content-Type: multipart/form-data

archive=<frames.zip; application/zip>
manifest=<UTF-8 JSON 字符串>
```

字段名固定为 `archive` 与 `manifest`。成功受理返回 HTTP `202`；幂等重放返回原 `batch_id`。

### 5.2 Manifest 1.0

```json
{
  "schema_version": "1.0",
  "external_batch_id": "assetclaw:episode01:shot010:matting:g1",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "episode_01/shot_010/frame_000001.png",
      "size_bytes": 4839201,
      "sha256": "64位小写十六进制"
    }
  ]
}
```

冻结规则：

- `schema_version` 只能是字符串 `1.0`；
- `failure_policy` 请求值仍固定为 `all_or_nothing`，不能自定义 best-effort 参数；
- `parameters` 当前只能发 `{}`；
- 一批 1～5000 帧，ordinal 严格连续 `0..N-1`；
- 相对路径必须是 UTF-8 NFC、安全 POSIX 路径，禁止绝对路径、`..`、反斜杠和大小写折叠后重名；
- ZIP 内图片条目必须为 `ZIP_STORED`，条目集合与 Manifest 完全一致；
- `size_bytes`、输入 SHA-256 和 ZIP 真实字节必须一致；单帧最大 64 MiB；
- 输出保留相对目录和 stem，仅把扩展名替换为 `.png`；
- 禁止 callback、客户端指定节点、逐帧参数或未定义字段。

### 5.3 状态、SSE 与部分成功

```text
GET  /api/v1/batches/{batch_id}
GET  /api/v1/batches/{batch_id}/manifest?offset=0&limit=200
GET  /api/v1/batches/{batch_id}/events
POST /api/v1/batches/{batch_id}/cancel
```

父终态为 `SUCCEEDED`、`PARTIAL_SUCCESS`、`FAILED` 或 `CANCELLED`。SSE 用于低延迟提示，父状态 GET
始终是真相来源；SSE 断开不代表任务失败。建议状态轮询 3 秒，网络失败按 1、2、4、8、15、30 秒退避。

每帧最多实际执行三次；可重试帧应排除已经尝试过的物理节点。允许换节点重试的典型错误包括
`COMFY_TIMEOUT`、`JOB_TIMEOUT`、`GPU_OOM`、`COMFY_EXECUTION_ERROR` 和 prompt 提交前的可恢复传输错误。

`PARTIAL_SUCCESS` 是终态，不是失败或运行中。动画管家必须：

1. 下载唯一 `kind=result_archive`；
2. 校验归档 SHA、Manifest 和每张成功输出 SHA；
3. 只根据 `failed_items` 新建补算 generation；
4. 身份键使用 `ordinal + input_relative_path + input_sha256`，不能只按文件名；
5. 保留已验证成功帧，不覆盖、不删除、不重新计算；
6. 补算任务使用新的 external ID 和 Idempotency-Key。

部分结果 ZIP 只包含 `manifest.json` 与成功帧 `results/<output_relative_path>`，失败帧不会生成空白图、
复制旧图或伪造成功项。若 0 帧成功、工作流身份不符、Manifest/路径/装配完整性错误或结果 SHA/Alpha
不可信，父任务直接 `FAILED`，不发布结果包。

### 5.4 结果验收

每张抠图输出必须同时满足：

- PNG 可解码；
- 模式包含 Alpha，不能把 RGB 预览图当 RGBA；
- 输出相对路径与输入 ordinal/stem 唯一对应；
- Manifest SHA-256 等于下载体 SHA-256；
- `workflow_version`、`pipeline_commit`、`pipeline_sha256` 和 `output_node` 等于本文第 9 节身份。

## 6. 同步图片 API

### 6.1 通用调用

上传字段固定为 `image`。成功时 HTTP body 就是最终图片，响应头 `X-Job-ID` 用于审计：

```bash
curl --fail-with-body --show-error \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  -H 'Idempotency-Key: assetclaw-shot010-inpaint-g1' \
  -H 'X-Request-ID: am-shot010-inpaint-create-01' \
  -F 'image=@input.png;type=image/png' \
  -F 'prompt=修复蒙版区域的破损边缘' \
  --output result-inpaint.png
```

- ImageClip 单图：`/api/v1/services/imageclip-rgba`；
- 局部重绘：`/api/v1/services/modelview-inpaint`，`prompt` 可选；
- 粗糙度：`/api/v1/services/modelview-roughness`；
- 图片客户端总超时建议 1900 秒；连接超时建议 10 秒；
- 不能传入或覆盖服务端锁定的工作流参数、模型、节点和 Prompt 模板；
- 只有响应 `Content-Type` 为预期图片且 body 可解码时才能保存为业务结果。

同步 HTTP 超时不等于任务失败。先记录 `X-Request-ID`，使用同一 Idempotency-Key 重放；不得绕过统一
入口去 ComfyUI 队列中查找或重复点“运行”。

## 7. UV、自动拓扑和烘焙异步合同

### 7.1 通用异步状态

三项 Asset API 成功提交均返回 HTTP `202`：

```json
{
  "job_id": "UUID",
  "status": "QUEUED",
  "status_url": "/api/v1/assets/jobs/UUID",
  "events_url": "/api/v1/assets/jobs/UUID/events",
  "cancel_url": "/api/v1/assets/jobs/UUID/cancel",
  "timing": {
    "queue_position": 1,
    "estimated_start_seconds": 0
  }
}
```

```text
GET  /api/v1/assets/jobs/{job_id}
GET  /api/v1/assets/jobs/{job_id}/events
POST /api/v1/assets/jobs/{job_id}/cancel
```

制品 URL 以状态响应中的 `artifacts[]` 为准。下载后必须比较响应元数据 SHA-256、响应头 SHA-256 和
下载 body SHA-256；三者不一致时拒绝发布。

### 7.2 Blender PBR UV

```bash
curl --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/uv/process' \
  -H 'Idempotency-Key: asset-chair-uv-001' \
  -F 'asset=@chair.fbx' \
  -F 'metadata={"external_asset_id":"asset:chair:uv:001","options":{"hidden_axis":"y+","hard_edge_angle_degrees":75,"resolution":2048,"padding_px":10}};type=application/json'
```

UV 与拓扑使用独立 CPU Asset 槽；即使 4070Ti 正处于局部重绘保护或 3090-B 正在烘焙，其他 CPU 槽
仍应继续接单。调用方不得把 WebUI 的 GPU `1/1` 误解为 CPU Worker 已满。

### 7.3 Direct V2 自动拓扑

```bash
curl --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/retopology/process' \
  -H 'Idempotency-Key: asset-crate-retopo-001' \
  -F 'project=@crate.blend' \
  -F 'reference_images=@crate_front.png' \
  -F 'metadata={"external_asset_id":"asset:crate:retopo:001","options":{"high_object":"crate_high","reference_object":"crate_reference_low","low_object":"crate_current_low","generated_low_object":"crate_generated_v001","target_faces":3000}};type=application/json'
```

对象名必须与 `.blend` 内实际对象完全一致。动画管家只上传合同输入，不调用 Worker 本地 Codex、
RetopoFlow 或 Blender 插件。

### 7.4 Substance PBR 烘焙

3090-B 是唯一烘焙设备；4070Ti 不具备、也不会获得 Substance 烘焙路由。

```bash
curl --fail-with-body --show-error \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST 'https://10.3.34.11/api/v1/assets/bake/process' \
  -H 'Idempotency-Key: asset-chair-bake-001' \
  -F 'low_mesh=@chair_low_uv.fbx;type=application/octet-stream' \
  -F 'high_mesh=@chair_high.fbx;type=application/octet-stream' \
  -F 'base_color_texture=@chair_basecolor.png;type=image/png' \
  -F 'roughness_texture=@chair_roughness.png;type=image/png' \
  -F 'metallic_texture=@chair_metallic.png;type=image/png' \
  -F 'metadata={"external_asset_id":"asset:chair:bake:001","options":{"profile":"li3d-pbr-full-v2","resolution":2048,"texture_cache_mb":32768}};type=application/json'
```

`li3d-pbr-full-v2` 必须提供低模、高模、Base Color、Roughness 和 Metallic；`cage_mesh` 可选。低模
必须已有有效 UV。允许的几何输入为 FBX、OBJ、GLB；`.blend` 和依赖外部文件的 `.gltf` 不属于合同。

GPU fencing 固定顺序：停止给 3090-B 分配新推理帧，等待已开始的 ImageClip 帧自然完成，确认
ComfyUI 队列为空，调用 `/free`，最长 30 秒轮询显存释放且每 0.5 秒复查队列，显存门禁通过后才启动
Windows 原生 Baker。门禁失败时 fail-closed，不能同时运行 ComfyUI 模型和 Baker。

## 8. 固定调度与 15 分钟保护规则

### 8.1 空闲时四卡共享

- 没有专用任务时，抠图、局部重绘和粗糙度按兼容性、公平队列与缓存亲和分配到四台 GPU；
- 保护策略不是永久机器绑定；空闲时局部重绘也可以分配到 4090、3090-A 或 3090-B；
- CPU UV/拓扑始终按独立 Worker 槽正常调度。

### 8.2 4070Ti 局部重绘优先

局部重绘与 ImageClip 冲突时，4070Ti 只中断并重新排队当前 ImageClip 帧，然后排空缓存并优先执行
局部重绘。被重新排队的帧可由其他健康 GPU 领取；父批次和其他帧不取消。

局部重绘到达时刷新 15 分钟保护。窗口内如果还有局部重绘，4070Ti 不接新的普通 GPU 单；如果没有
新的局部重绘，窗口硬过期后立即恢复共享，不能继续因为旧标签而拒绝抠图/粗糙度。

### 8.3 3090-B 烘焙优先

烘焙到达或形成排队时，3090-B 停止领取新 ImageClip 帧，但已经开始的一帧必须自然完成，随后排空
ComfyUI 并进入 Baker。烘焙排队会刷新 5 分钟窗口；没有新烘焙且窗口过期后恢复共享 GPU 接单。

如果 Baker 已结束、没有待领取烘焙、没有恢复门禁且 GPU 已空闲，管理员在 GPU 节点页点击
“解除烘焙保护”（或从排空状态重新点击“投入使用”）会立即恢复普通 GPU 接单，不必等待 5 分钟。
活动 Baker fence、待领取 reservation 和 recovery-required 都不能被该按钮绕过。

### 8.4 动画管家必须如何理解“排队”

- `QUEUED` 不等于四台设备离线；可能在等待兼容版本、物理 GPU 围栏、唯一 Baker 或公平调度；
- 动画管家不得因为 WebUI 显示某卡 0% 就把任务重提到另一业务 ID；
- 状态与任务归属以 API 为准，GPU 利用率是遥测，不是任务状态真相；
- 如果父任务长时间未分配，提交 `batch_id/job_id + X-Request-ID` 排障，不要直连节点人工运行工作流。

## 9. 批准的外部工作流身份

GPU Control 只部署并验证用户批准版本，不修改图结构、模型、提示词、采样参数、分辨率或输出语义。

| 能力 | 生产版本 | 上游/管线身份 | API Template SHA-256 | 唯一最终输出 | 最低显存 |
|---|---|---|---|---:|---:|
| ImageClip RGBA | `2026.08.12-c39ed0b-fp8-r1` | commit `c39ed0b3b637f0a1435bbe10e5a3acf6bfca07bd`; pipeline `07928d57852ed56ed37527960ec9955d867c0090456fda687fbcd12fecf1775c` | `ce4d23c39363d489e48a32a20295bf83837829651f10e46a537de635031c6846` | `SaveImage #102` | 12000 MiB |
| ModelView 局部重绘 | `2026.08.12-8c37f07-seedvr2-12g-r1` | commit `8c37f07b0a8ed87a94f4159c173d3d2e03a20b61` | `df13ca08fab5a20cb57aaa07ef81d78ca1e9aaa3e541715e4ba03eeb3bf86ccd` | `SaveImage #9` | 12000 MiB |
| PBR 粗糙度 | `2026.08.12-d318bb39-roughness-12g-r1` | commit `d318bb392040e2d5f6bbd10ae61d832d36d3cb4a`; pipeline `8a52740b90ac47e77919b460a0e35241c94d91fde035effb3285600642e2ea38` | `bbc80df2c8fc8eb148c671ea1b97a3d77ed2bd1f64593701c68d3bba1438d42f` | `PreviewImage #355` | 12000 MiB |

ImageClip 固定使用批准的 FP8 diffusion model
`diffusion_models/flux-2-klein-9b-fp8.safetensors`。旧 `SaveImage #25`、旧 Q6_K 或 UI 中手工保存的
不同工作流不能作为动画管家生产身份。

## 10. 当前生产验收证据

### 10.1 四 GPU ImageClip

四个 118 帧父批次共 472 帧全部成功、0 失败；节点最终分布为 4090 228、3090-A 108、3090-B 92、
4070Ti 44。输出均通过 RGBA PNG、路径和 SHA 校验，证明父批次可在四张物理卡分布式领取。

### 10.2 ModelView、Roughness、UV、拓扑

- 4070Ti 局部重绘 canary `328b0f8d-d1ee-452f-bbff-4f93440cfab7` 成功；
- 粗糙度 canary `2573e7fa-1768-4ed1-beba-8f2bd34ce0ae` 成功；
- UV 已有 11 个真实成功任务，四台 CPU Worker 均有领取；
- Direct V2 自动拓扑四节点各有真实成功；独立拓扑审计 12/12 成功。

### 10.3 3090-B Substance Baker v7

真实任务 `867d53b9-cfb6-49d5-b1d2-0007777e8072` 经受控 Admin Retry 生成 attempt 3，并由
`asset-worker-3090-b-windows-03` 成功执行：

| 证据 | 结果 |
|---|---|
| 任务终态 | `SUCCEEDED / SUCCEEDED` |
| 开始/结束（UTC） | `2026-08-13 02:30:00.579710` / `02:30:37.798564` |
| Agent 版本 | `substance-baker-2026.08.12-v7` |
| 最终 Agent SHA-256 | `06fcb4cefb9aeb7e53693faf9f87a36a113324ba8df162738e416afdb9e4b399` |
| Baker | Adobe Substance 3D Baker `15.1.0` |
| GPU fencing | 队列为空、模型已卸载、空闲 23222/24576 MiB、比例 0.9449 |
| ComfyUI 连续性 | 未重启，进程身份连续 |
| Baker 执行 | 10 条命令、成功标记存在、退出码有效 |
| 制品 | 12 个、34,775,544 bytes、12 个唯一 SHA-256 |
| 结束后 | 四个 v7 Agent 在线健康，0 当前任务，0 Baker 进程 |

12 个制品包括 AO、Base Color、Curvature、Metallic、Normal DirectX、Normal OpenGL、Position、
Roughness、Thickness、World Normal、`baker.log` 和 `baker_result.json`。该任务证明 Python 路径修正、
最长 30 秒异步显存释放轮询、队列复查、原生 Baker 启动和控制面结果接收均已闭环。

## 11. 状态、错误与重试矩阵

| 场景 | 动画管家动作 |
|---|---|
| HTTP 连接超时/断线，是否受理未知 | 原文件 + 原 body + 原 Idempotency-Key 重放 |
| `429 RATE_LIMITED` | 尊重 `Retry-After`，指数退避；不并发复制同一任务 |
| `409 IDEMPOTENCY_CONFLICT` | 停止自动重试，检查 key 是否错误复用于不同内容 |
| Batch `RUNNING` 且已有 failed count | 等待父终态；其他帧和换节点重试仍在继续 |
| Batch `PARTIAL_SUCCESS` | 下载成功子集，仅对 `failed_items` 建新 generation 补算 |
| Batch/Job `FAILED` | 保存错误码、request/job/batch ID；只按明确可重试合同重提 |
| SSE 中断 | 回到 GET 状态接口；不能判失败或取消 |
| Asset Job `QUEUED` | 继续查询同一 job；烘焙只能等待 3090-B |
| 用户取消 | 调用正式 cancel API 并保存业务操作者、原因、Request ID 和最终状态 |
| `GPU_OOM` | 不在客户端降低模型/分辨率或改工作流；由集群换节点/围栏处理 |

稳定错误码包括 `RATE_LIMITED`、`INPUT_INVALID`、`WORKFLOW_NOT_FOUND`、
`IDEMPOTENCY_CONFLICT`、`SERVICE_TIMEOUT`、`GENERATION_FAILED`、`GPU_OOM` 和对应 Asset 错误码。
任何排障单至少携带时间、入口、外部 ID、Idempotency-Key、`X-Request-ID` 和 `job_id/batch_id`。

## 12. Codex、状态探针与 WebUI 口径

- 四台 Node Agent 的在线状态、GPU 指标与任务槽位分别采集；指标暂时缺失不能把正在工作的节点判离线；
- 4070Ti 温度、功率、利用率、显存通过 WSL 宿主签名代理采集，不由 WebUI估算；
- 四个 CPU Asset Worker 独立上报 Codex CLI、认证、RetopoFlow、Skill 版本和槽位；
- 3090-B 另有四个 Windows Baker Agent 心跳与原生 Baker 进程探针；
- WebUI 的“运行中”只表示节点在线可调度；GPU `1/1`、CPU `x/y`、Baker `x/4` 必须分别理解；
- 动画管家不解析 WebUI DOM 作为机器接口，所有业务状态只取公开 API。

当前 Asset Worker 基线：Codex CLI `0.146.0-alpha.3.1`、`AUTHENTICATED / HEALTHY`、RetopoFlow
`3.4.11 / HEALTHY`、Skill `asset-skills-auto-retopo-align-v3.0.25`。这些探针供运维判断，不改变六 API
请求格式。

## 13. 动画管家稳定性测试清单

建议按顺序执行并逐项保存 request/job/batch ID 与 SHA：

1. TLS/身份：使用 LAN CA 和正式客户身份访问健康入口；
2. ImageClip 小批次：提交 4～8 帧，验证 RGBA、路径、SHA、父状态和四卡可分布；
3. ImageClip 稳定批次：提交真实序列，验证断线重连、幂等重放和结果 ZIP；
4. 失败帧补算：使用隔离夹具验证 `PARTIAL_SUCCESS` 与 `failed_items`，不破坏生产工作流；
5. 局部重绘：在抠图有负载时提交，验证 4070Ti 优先响应与被打断帧重新排队；
6. 保护过期：15 分钟没有新局部重绘后，确认 4070Ti 自动恢复普通接单；
7. 粗糙度：验证四卡共享和最终 `#355` 图片；
8. UV/拓扑：GPU 保护期间并行提交 CPU 任务，确认独立槽位继续运行；
9. 烘焙：只提交一笔真实 3090-B 任务，验证 fencing、Baker 日志和全部制品；
10. 烘焙排队：确认烘焙到达后 3090-B 不接新抠图，但已开始帧自然完成；
11. 指标：负载期间核对四节点利用率、显存、温度、功率和任务槽，不用遥测替代任务终态；
12. 稳态恢复：全部任务结束后确认四 GPU 重新共享、CPU Worker 在线、Baker 进程归零、无活动告警。

通过标准不是“页面看起来在线”，而是请求身份、状态机、最终输出、SHA、节点审计、模型切换和恢复
状态全部一致。

## 14. 双方变更控制

以下变更必须由 GPU Control 提供新的版本身份和对接回执后，动画管家才能启用：

- 公开路径、multipart 字段、Manifest/schema、状态或错误码语义变化；
- ImageClip/ModelView/Roughness 工作流版本、管线 SHA、Template SHA 或最终输出节点变化；
- UV/拓扑/Bake 的 metadata schema、profile 或制品清单变化；
- 节点职责、唯一 Baker、15 分钟保护或失败帧重试策略变化；
- TLS CA、认证方式、限流或上传大小变化。

动画管家不得通过客户端参数覆盖 GPU Control 锁定的模型、Prompt、采样、分辨率、图拓扑或输出节点。
GPU Control 也不会在没有用户明确批准的情况下修改 ImageClip/ModelView 外部业务管线。

## 15. 本轮交付与后续发布状态

截至本文生成时：

- 六项能力均已有真实功能成功证据，Substance v7 真实 Retry 已成功；
- 3090-B 最终 Agent SHA 已固定为 `06fcb4ce…e4b399`；
- GPU Control `1.5.14` 的正式版本对齐、Docker 镜像、离线归档、Git LFS、Git 提交和完整探针/综合
  测试证据仍在同一发布流程中生成；
- 动画管家对接路径和请求合同不会因此次控制面发布改变；
- 在最终发布回执补齐前，可以做功能与稳定性测试，但不得把暂存镜像 ID 写成长期生产基线；
- 最终发布回执将补充精确 Git revision、正式镜像 ID、离线归档 SHA-256、LFS 对象和测试汇总。

关联记录：

- `118_2026-08-12_FOUR_GPU_RELEASE_AND_SIX_API_CLOSURE.md`：四节点与六 API 全量审计；
- `119_2026-08-13_SUBSTANCE_V7_PYTHON_PATH_HOTFIX.md`：v7 热修、Retry 与制品证据；
- `84_2026-08-05_PARTIAL_SUCCESS_AND_FAILED_FRAME_REPAIR_HANDOFF.md`：部分成功与失败帧补算合同；
- `13_PUBLIC_API_GUIDE.md`：公共入口快速示例。
