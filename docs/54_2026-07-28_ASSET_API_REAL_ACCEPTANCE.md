# 2026-07-28 UV / 拓扑 Asset API 真实验收记录

> **历史 V2 验收：** 本文记录初始审计接口。当前完整 V3 生成候选、三模型四视图、进度/ETA、
> SSE、人工审核和两机真实结果见
> [55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md](55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md)。

## 1. 验收结论

本次不是 Mock、不是手工改数据库、不是伪造 Worker，也没有使用程序生成的几何体。
真实执行链为：

`公开真实 3D 资产 -> multipart API -> PostgreSQL 持久化 -> HMAC Worker 心跳/领取 -> Blender 5.1.2 -> 固定哈希 Skill -> 原子交付 -> SHA-256 下载校验 -> Web UI 管理`

结论：

- UV V2 主链路通过，真实 GLB 完成展开、BLEND QA、FBX 导出与 FBX 回读 QA，
  5/5 交付物原子发布。
- 拓扑审计主链路通过。真实候选低模存在非流形边、松散几何与面朝向不一致时，系统
  正确返回 `WAITING_REVIEW`，未冒充最终游戏低模。
- 外部 API Key、幂等重放、幂等冲突、自动重试、最终失败、取消、管理审计、Worker
  心跳与控制面重启恢复均经过真实请求验证。
- Chromium 真实页面完成登录、读取任务、点击取消、确认弹窗、状态刷新；浏览器控制台
  错误为 0。
- 生产 GPU 后端未被替换、未重启、未迁移；测试运行于独立 PostgreSQL/Redis/API/
  Worker/Web 网络。测试结束时生产 `QUEUED/RUNNING` 均为 0，原服务继续 healthy。

## 2. 真实输入与来源

### 2.1 UV Golden Asset

- 来源：KhronosGroup `glTF-Sample-Assets` 的 `DamagedHelmet.glb`
- URL：`https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb`
- 大小：`3,773,916` bytes
- SHA-256：`a1e3b04de97b11de564ce6e53b95f02954a297f0008183ac63a4f5974f6b32d8`

### 2.2 拓扑 Golden Asset

- 来源：Stanford 3D Scanning Repository Bunny archive
- URL：`https://graphics.stanford.edu/pub/3Dscanrep/bunny.tar.gz`
- 归档大小：`4,894,286` bytes
- 归档 SHA-256：`a5720bd96d158df403d153381b8411a727a1d73cff2f33dc9b212d6f75455b84`

只用 Blender 将归档里的三个真实重建层级装入同一个 BLEND，未修改几何：

| 角色 | 原文件 | SHA-256 | 顶点 | 三角面 |
|---|---|---|---:|---:|
| high | `bun_zipper.ply` | `b1acc63bece78444aa2e15bdcc72371a201279b98c6f5d4b74c993d02f0566fe` | 35,947 | 69,451 |
| reference | `bun_zipper_res2.ply` | `8faa052bb08cf1625eec97b6508baceee11eec9cf28f83f6a9d4547ab15d7761` | 8,171 | 16,214 |
| current low | `bun_zipper_res3.ply` | `d628625d14fbe5deecb123536b515405c522984d43488cc24f91980eb75edadc` | 1,889 | 3,768 |

打包后的 `StanfordBunny_RetopologyAudit.blend` SHA-256：
`de170bdf3a30f35899d988d9b9d536af542f9149c64d2ce6d07ab3c375848e85`。

## 3. 固定执行环境

- Blender：`5.1.2`，build hash `ec6e62d40fa9`
- Worker Skill 版本：`asset-skills-2026.07.28`
- UV 执行脚本：
  - `unwrap_fbx.py`：`ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758`
  - `qa_uv.py`：`bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d`
- 拓扑审计脚本 `audit_pair.py`：
  `a6575902cfacd7b8106f9c887069d717a880d870fc48a6295431cdcf717a9dc4`
- 两个 `agents/openai.yaml` 均为 UTF-8，并纳入 `scripts/verify_asset_skills.sh`
  的固定 SHA 验证。

## 4. UV 真实 API 结果

- Job ID：`c131ff60-ed2b-4708-a970-68f0940429bd`
- External Asset ID：`golden:khronos:damagedhelmet:uv:20260728:003`
- Worker：`asset-control-realtest`
- 最终状态：`SUCCEEDED`
- 尝试次数：1
- Blender 执行与发布耗时：约 `7.85s`
- 参数：`hidden_axis=y+`、`hard_edge_angle_degrees=75`、`resolution=2048`、
  `padding_px=10`

| kind | 文件 | 字节 | SHA-256 |
|---|---|---:|---|
| blend | `DamagedHelmet_PBR_UV.blend` | 3,736,442 | `582ade1d0638536bee5402075beb5ae4c95e21de256a8143e458ecb8c7434065` |
| fbx | `DamagedHelmet_PBR_UV.fbx` | 668,412 | `1694d27a9c97e386cc17d2d219552c9fa46792018a5bc56df04b1f3e262ee31b` |
| fbx_qa | `DamagedHelmet_PBR_UV_FBX_QA.json` | 1,006 | `a9fa566b723fc948a814ae6d50d5fa0100205b4d24052b89e7739cfe2f1c40f0` |
| qa | `DamagedHelmet_PBR_UV_QA.json` | 1,007 | `fa39afccdff1ddbf343800b7eed429c48a3145993849a572117b0bc0c7731c31` |
| report | `DamagedHelmet_PBR_UV_report.json` | 1,685 | `43568e442b2d535ae8470f65e9c13a970729f847037caf286580d7716866ce76` |

每个文件均验证：任务 JSON SHA = `X-Artifact-SHA256` 响应头 = 下载文件 SHA。

BLEND 与 FBX 回读 QA 都为 `passed=true`：

- flipped faces：0
- degenerate UV faces：0
- overlap triangle pairs：0
- out-of-0..1 loops：0
- stretch p95：`1.09363`
- texel density relative p10/p90：`0.80103 / 1.18072`

相同幂等键和完全相同请求重放返回 HTTP 200 和同一 Job ID；更改参数后重用同一
幂等键返回 HTTP 409 `IDEMPOTENCY_CONFLICT`。

## 5. 拓扑真实 API 结果

- Job ID：`4cb0ccf9-c52d-4aa9-bd2e-1dc20d332b05`
- External Asset ID：`golden:stanford:bunny:retopology:audit:20260728:001`
- Worker：`asset-control-realtest`
- 最终门禁状态：`WAITING_REVIEW`
- 尝试次数：1
- 交付物：`retopology_audit.json`、`retopology_manifest.json`

| kind | SHA-256 |
|---|---|
| audit | `ae49181cb8febd7be5adf54749a66d14939693d6f69e6c073e83dfb8e8ae2c7a` |
| manifest | `629638fd13a101cb21fabe8bd4b201ac55ad4563c4587a4ef6d43e5f69139dac` |

真实审计发现：

- 非流形边：103
- 松散顶点：2
- 朝向不一致边：10
- `audit_passed=false`

因此不允许自动发布最终低模，必须继续前、侧、顶、透视四视图与
high/reference/current-low 对照复核。

### 5.1 负向测试

用同一真实 BLEND，但把 `low_object` 指向不存在的 `missing_low_object`：

- Job ID：`a8414b4f-294a-4043-852c-1053f984f7b2`
- Worker 自动尝试：2 次
- 最终状态：`FAILED`
- 错误明确为 `Missing low object: missing_low_object`
- 交付物：0；未发布半成品。

## 6. Web UI 与管理测试

真实 Chromium 测试地址为隔离网关 `/asset-processing`：

1. 管理员真实登录。
2. 页面读取同一 PostgreSQL 中的 UV 与拓扑任务。
3. 真实排队 Job `921d06e6-9e9c-4e7f-ac0d-7529237948a0` 从“排队中”点击取消。
4. Element Plus 二次确认后，管理 API 返回 HTTP 200。
5. 页面刷新为“已取消”。
6. `audit_logs` 写入 `asset_job.cancel`，含 before/after、操作人、理由和 request ID。
7. 浏览器 console errors：0。

截图保留在测试主机：
`/tmp/gpu-control-asset-realtest/results/asset-processing-real-webui.png`。

## 7. Codex CLI 真实测试

不是 `--help` 探测，而是三次真实模型调用：

- UV Golden 结果通过严格 JSON Schema，返回 `accepted` 并逐项回显输入 SHA 与参数。
- 显式调用 `$blender-pbr-uv` 成功；修正其 `agents/openai.yaml` 从 GB18030 到 UTF-8
  后，Skill loader 不再忽略文件。
- 显式调用 `$blender-retopology-compare-iterate`，对真实 Bunny 结果返回
  `WAITING_REVIEW / audit_passed=false / final_promotion_allowed=false`，并要求四视图复核。

测试期间还真实发现并修正了 Codex 输出 Schema 不支持 `uniqueItems` 的兼容性问题；
最终 Schema 调用通过。

## 8. 本次真实测试发现并修复的问题

1. **PostgreSQL 父子写入顺序**：SQLAlchemy 在没有 ORM relationship 时先插入
   `asset_idempotency_keys`，触发 Job 外键 409。修复为先 `await db.flush()` 父 Job，
   再插入幂等记录。
2. **Worker 遇 API/DNS 短暂中断直接退出**：修复为 1~30 秒有界指数退避，保留在途
   Blender 子进程，控制面恢复后自动恢复心跳和领取。
3. **Worker 故障日志过量**：退避期间只记录异常类型和下一次重试秒数，不刷完整堆栈。
4. **CLI Skill 编码错误**：UV `agents/openai.yaml` 从 GB18030 无损转换为 UTF-8，
   并把两个 Skill 的 agent YAML 纳入固定 SHA 校验。
5. **统一 Web UI 只能看不能管**：增加运维确认取消、真实状态刷新和审计落库。

## 9. 自动化与构建验证

- `tests/integration/test_asset_api.py` + `tests/integration/test_api.py`：`25 passed`
- Python `py_compile`：通过
- `git diff --check`：通过
- Vue TypeScript + Vite 生产构建：通过
- Skill 固定 SHA 校验：通过（8/8）
- 真实 Web UI Playwright：通过

最终隔离验收镜像：

| 镜像 | Image ID | 大小 |
|---|---|---:|
| `gpu-control-api:1.4.0-realtest` | `sha256:1cf455cc1cfd38d1984c0e3fa43a549fdd736ef86a7780c51f621f1fb33670ae` | 88,662,401 |
| `gpu-control-asset-api:1.4.0-realtest` | `sha256:a1afae75b476862aac26496a44c2fcd2b3096bde04a1d274bd381718aa67752c` | 88,566,833 |
| `gpu-control-blender-worker:1.4.0-realtest` | `sha256:00eca1601bff2f76b5fdc6aff8ed5277bedf0f9487ec45971bf95eb13ae38408` | 687,539,403 |
| `gpu-control-web:1.4.0-realtest` | `sha256:6a4b0de3b64cc57b87b2362e3c2d01607b0915e915074df521f2934025ba066d` | 21,339,921 |

这些标签是隔离验收标签，不应直接当成正式发布标签。

## 10. 明确边界与尚未声称的能力

当前拓扑接口是**真实拓扑审计与人工复核门禁**，不是全自动重新拓扑生成器：

- 它接受已经包含 high/reference/current-low 的 BLEND。
- 它不会凭空生成一个新的游戏级低模。
- 它不会把数值审计通过等同于视觉和烘焙通过。
- 当前原子交付只有 audit + manifest；四视图属于必须执行的人工复核项，尚未作为 PNG
  交付物由 API 自动渲染。

在自动拓扑生成、四视图渲染与人工审批回写完成前，不得把 `WAITING_REVIEW` 当成
`SUCCEEDED`，也不得向下游发布所谓“最终低模”。

## 11. 正式上线状态

本次没有把隔离测试数据或公开 Golden Asset 放入生产库，也没有在生产 `/srv` 写入模型。
正式生产库当前仍未执行 `20260727_0006_asset_processing`，正式 Asset API/Worker 也未启用；
这是有意保留的发布边界，不应把“真实隔离验收通过”误写成“生产已上线”。

正式上线前必须：

1. 生成独立的 32+ 字符 `ASSET_WORKER_HMAC_SECRET`，不得复用 node/API/JWT secret。
2. 备份生产 PostgreSQL。
3. 再次确认 GPU Job 与 Batch 均无 `QUEUED/RUNNING/CANCELLING`。
4. 执行 Alembic 0006。
5. 部署正式 Asset API、Web、管理 API 与至少一个 Blender Worker。
6. 用调用方自己的真实模型做一次 canary，不把本记录的公开 Golden Asset 复制进生产。
