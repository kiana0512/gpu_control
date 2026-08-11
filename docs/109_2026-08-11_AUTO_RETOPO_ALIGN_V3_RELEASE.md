# 自动拓扑与原坐标对齐 v3.0.0 发布记录

日期：2026-08-11

状态：代码与技能包已校验，目标发布为 Asset API `1.6.19-retopo-align-v3`、三台 Blender Worker
`1.4.20-retopo-align-v3`。生产运行证据在滚动完成后回填到本文末尾。

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

待滚动完成后回填：Git revision、镜像 ID、三台 Worker 实例、包自检、队列状态、ComfyUI 连续性和
最小真实交付验证。
