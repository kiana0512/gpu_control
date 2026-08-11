# 自动拓扑与原坐标对齐 v3.0.0 发布记录

日期：2026-08-11

状态：已部署。Asset API 为 `1.6.19-retopo-align-v3`，三台 Blender Worker 为
`1.4.20-retopo-align-v3`；真实自动拓扑任务已完成十件套原子交付。

## 1. 批准输入

- 原始 ZIP：`blender-auto-retopo-align-server-package-v3.0.0.zip`。
- ZIP SHA-256：`0a6e539a03e6dcecd9518c6fa592c112892f829717d2c768721463796a604138`。
- 包版本：`3.0.0`。
- Skill ID：`blender-auto-retopo-align`。
- 正式文件：24 个，其中 Skill 文件 12 个。
- `manifest/FILES.sha256` 全部通过，`server/verify_package.py` 返回 `ok=true`。

仓库内 `resources/retopology-direct-v2/` 已按 ZIP 原样替换；目录差异只排除了运行校验产生的
`__pycache__`/`.pyc`，正式文件及字节哈希不变。

## 2. 服务器执行语义

公共 API 路由和 `engine_contract=retopology-direct-v2` 保持不变，内部批准包升级为 v3：

1. 高模既是形状依据，也是唯一坐标依据。
2. Codex 只生成一次低模；低模声明为 `source_high_local`，坐标权威为
   `high_object_matrix_world`，禁止展示平移。
3. 同任务生成的低模不运行 ICP，不执行自动七视图 Agent 复核，不因复核结果自动生成第二版。
4. 技能包自带 finalizer 只恢复源矩阵/中心，保留生成低模的拓扑和 UV；不执行 Decimate、remesh、
   重建、三角化或 UV 修改。
5. finalizer 输出对齐 Blend、高模 FBX、低模 FBX和对齐报告，并用新 Blender 导入两个 FBX，验证
   中心、尺寸、低模结构和手性。
6. 坐标或回读异常统一返回 `RETOPOLOGY_COORDINATE_MISMATCH`，不自动重新建模。
7. 成功状态为 `generated_for_user_inspection_aligned`；服务只证明坐标恢复、拓扑/UV 未被 finalizer
   修改和 FBX 回读通过，不伪造自动视觉验收或米制单位声明，最终外观仍由用户检查。

## 3. 原子交付合同

Worker 必须一次上传以下 10 件制品，Asset API 对每件非空、文件名、大小和 SHA-256 做硬校验：

- `bake_alignment.blend`
- `bake_low.fbx`
- `bake_high.fbx`
- `bake_alignment_report.json`
- `generation_report.json`
- `delivery_manifest.json`
- `result.json`
- `source_manifest.json`
- `agent_events.jsonl`
- `wrapper_events.jsonl`

客户端兼容文件名继续使用 `<stem>_GAME_LOW.blend`、`<stem>_GAME_LOW.fbx` 和
`<stem>_BAKE_HIGH.fbx`。Asset API 只有在下列条件全部成立时才原子发布：

- API 任务、输入 ZIP、Worker 三方包版本/ZIP SHA 完全一致；
- generation report 的三个坐标声明完整且精确；
- result 声明 v3 Skill、源矩阵恢复、无自动复核/重试，且四个 bake 文件哈希一致；
- alignment report 为 `li3d-auto-retopo-align-v1`，矩阵/中心误差不超过 `1e-5`，尺寸误差不超过
  `0.15`，手性一致；
- 高低模 FBX 新导入误差不超过报告的 `1e-5`，低模结构完全一致；
- 源文件 SHA 在执行前后不变。

## 4. 滚动与兼容安全

- 新 Worker 注册版本：`asset-skills-auto-retopo-align-v3.0.0`。
- Asset API 只把 v3 新任务分配给上述精确版本；旧 Worker 在滚动窗口内不能领取 v3 任务。
- 发布前必须确认没有 `QUEUED / CLAIMED / RUNNING` 的 Asset 任务。
- 先更新 Asset API，再逐台排空和替换 4090、3090-A、3090-B Worker；每台恢复前验证
  `ONLINE / AUTHENTICATED / HEALTHY`。
- 不停止、不重启三台 ComfyUI，不修改 ImageClip、ModelViewCreator 或任何外部工作流、模型、提示词、
  参数和输出语义。

## 5. 本地验证

- ZIP/仓库清单：全部 24 个正式文件一致。
- 包自检：通过。
- Python compile：通过。
- Ruff：通过。
- Unit：`344 passed, 5 skipped`；跳过项依赖 Blender NumPy 或可选运行环境。
- Asset API integration：`95 passed`。
- v3 定向契约组：`106 passed, 5 skipped`。
- 未执行用户已取消的压力测试，也未注入生产 GPU 流量。

## 6. 回滚

回滚时先停止新任务进入并确认活动任务为 0，然后同时恢复：

- Asset API：`1.6.18-retopo-progress-v1`；
- Blender Worker：`1.4.19-retopo-progress-v1`；
- `BLENDER_SKILL_VERSION=asset-skills-retopology-v2.3.0`；
- 旧 `resources/retopology-direct-v2` 包和旧包 SHA。

不能只回滚 Worker 或只回滚 Asset API，否则精确 skill/package 身份门禁会让 Worker 合理地拒绝接单。

## 7. 生产证据

### 7.1 Git、镜像与离线归档

| 项目 | 生产证据 |
|---|---|
| 源码 revision | `10b1b3e5c720b9a4a193b37c55e5751ae51f1d3c`，已推送 `origin/main` |
| Asset API | `unified-scheduler-asset-api:1.6.19-retopo-align-v3`；image ID `sha256:b1866c4a00d70ad8a0014be44d4a35a0eb48ec36a30e77284c43818c11b47635` |
| Blender Worker | `li3d/blender-worker:1.4.20-retopo-align-v3`；三节点统一 image ID `sha256:0505e57d35fb83b4f9fc2fa271ebe48847f6099c6850426ce438a4e13016945d` |
| Asset API 归档 | `/srv/gpu-control/images/auto-retopo-align-v3-10b1b3e/asset-api.tar.zst`；`91842486` bytes；SHA-256 `e3d9542c96fb2131e11fe458f955c74ee7ae039cddb71c3275dc23229a64d7d7` |
| Worker 归档 | `/srv/gpu-control/images/auto-retopo-align-v3-10b1b3e/blender-worker.tar.zst`；`683317994` bytes；SHA-256 `dbc6167d7a6f7597f1fba4e7d882fbf5c139f66706abfbbdf3bd6970c379223b` |

两个 3090 节点均先校验归档 SHA 再载入同一 Worker 镜像；3090-B 的 WSL2 环境没有 `zstd`，因此从
控制机解压后通过 SSH 直接送入 `docker image load`，最终 image ID 与另外两台完全一致。

### 7.2 安全滚动与服务连续性

- 严格按 `control-4090 -> worker-3090-a -> worker-3090-b` 顺序执行
  `DRAINING -> GPU/Asset current_jobs=0 -> 只替换 Blender Worker -> 包自检/探针 -> ACTIVE`。
- 发布窗口内三台各有真实 ImageClip 工作；均等待自然完成后才替换对应 Worker，没有取消或中断。
- 三台节点在各自升级验证后均恢复 `ACTIVE`；三台 Linux Worker 最终均为 `ONLINE`，Skill 身份精确为
  `asset-skills-auto-retopo-align-v3.0.0`，Codex 为 `AUTHENTICATED / HEALTHY`，RetopoFlow 为
  `HEALTHY`。
- 三台 ComfyUI 运行 image ID 仍为
  `sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea`，容器 ID、启动时间和
  `RestartCount=0` 均未改变；没有执行 stop/restart/free，也没有修改外部工作流、模型或参数。
- Asset API `/health/live` 返回 `{"status":"live"}`。仓库和三台运行 Worker 的
  `server/verify_package.py` 均返回 `ok=true / package_version=3.0.0 / skill_file_count=12`。
- 发布证据回填时 3090-B 又收到一笔真实 Substance 烘焙，因此节点被 Asset API 自有物理 GPU fence
  临时置为 `DRAINING`；Linux v3 Worker 仍 `ONLINE / HEALTHY / 0 jobs`。该动态 drain 不能由发布
  脚本强行解除，烘焙结束后由 Asset API 自动恢复。

### 7.3 真实 v3 自动拓扑交付

真实任务 `c836a9dc-ec36-498e-a1ed-0e962d8ed666` 由 `asset-control-4090` 一次执行成功：

| 验证项 | 结果 |
|---|---|
| 时间 | 2026-08-11 07:50:27Z 开始，07:54:16Z 完成，约 3 分 49 秒 |
| 面数与 UV | 高模 `300000` 面；低模 `18000` 面、`1` 个 UV 层，低模面数严格更少 |
| 坐标恢复 | `alignment_mode=source_matrix_restore`；矩阵误差 `0`、中心误差 `0`、尺寸误差 `0.0035643859` |
| 方向/手性 | 高低模 determinant sign 均为 `+1`，没有镜像 |
| FBX 回读 | `pass=true`；高模误差 `4.15695e-08`、低模误差 `1.03924e-08`，低模结构完全一致 |
| 低模保护 | `icp_used=false`、`topology_or_uv_edited=false`、`topology_uv_unchanged=true` |
| 展示/复核 | `opaque_yellow`；无自动后生成复核、无自动第二轮，等待用户视觉检查 |
| 原子交付 | 10/10 件制品、10 种唯一 kind、全部非空并逐件 SHA-256 校验通过 |

本次未执行用户已取消的压力测试。真实任务证明的是服务器端生成、坐标恢复、拓扑/UV 指纹保护、FBX
回读和原子交付合同；按 v3 规则，最终形状和外观仍由用户在下载后检查，服务端不伪造视觉通过。
