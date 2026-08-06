# 2026-08-06 自动拓扑 v2.3.0 多 FBX 服务端接入

## 结论

GPU Control 已把用户批准的
`blender-retopology-compare-iterate-server-package-v2.3.0.zip` 作为冻结算法包接入候选版本。
现有公开创建接口、任务状态、事件、取消和制品下载路径保持不变。

多 FBX 不在一个 Worker 租约内串行执行。Li3D 用户端一次拖入多个 FBX 后，为每个文件提交一个
独立任务；GPU Control 调度器根据健康且已认证的 Worker 数并行执行。

## 冻结身份

| 项目 | 值 |
| --- | --- |
| 上游包版本 | `2.3.0` |
| 上游 ZIP SHA-256 | `d86f218d2194bd6260a491da66f89b8954a72ef8e5309c0ff1062c639d8f6ec4` |
| Skill SHA-256 | `03dff7efe9ffac9a365a0b81637bc3065fd4fe7259c67a9d2eb4ebf697e450aa` |
| Engine contract | `retopology-direct-v2` |
| Worker 候选镜像 | `li3d/blender-worker:1.4.2-retopology-v2.3.0` |
| Asset API 候选镜像 | `unified-scheduler-asset-api:1.6.8-retopology-v2.3.0` |

## 服务端实现

- `POST /api/v1/assets/retopology/process` 仍接收一个 `project`，不破坏现有调用方；
- 新任务输入清单固定为 `retopology_input.direct-v2`，同时记录包版本和包 SHA；
- 请求幂等哈希包含 Direct V2.3.0 包身份，旧算法任务不会被误重放成新算法任务；
- FBX 由上游 v2.3.0 `prepare_fbx_source.py` 归一为一个 `SOURCE_HIGH`，同一资产的多个 Mesh
  不会被拆成多个用户任务；
- OBJ、GLB、GLTF 等已有单文件调用继续使用 GPU Control 兼容归一化后进入相同单文件入口；
- 上游 `one_click_retopology.py` 和 Skill 内容保持原样；GPU Control 仅通过独立启动器把只读生产
  Codex 身份复制到任务私有 `CODEX_HOME`；
- Worker 不调用上游串行 `batch_retopology.py`，避免一个批次长期占用一个租约并扩大故障域；
- 每个成功任务继续发布独立 BLEND、FBX、SHA、generation report、delivery manifest 和 Agent 事件。

## Li3D 对接

Li3D 先读取 `GET /api/v1/assets/version` 的 `retopology` 字段确认服务端身份，然后按
[多 FBX 并行用户端对接](90_2026-08-06_RETOPOLOGY_V6_MULTI_FBX_PARALLEL_CLIENT_HANDOFF.md)
为每个文件生成独立 `external_asset_id` 和 `Idempotency-Key`。

客户端上传并发建议为 3，但实际建模并发始终由健康 Worker 数决定。页面刷新后必须使用已经获得的
`job_id` 恢复状态，不能重复上传。

## 验证回执

- 上游 `verify_package.py`：通过；
- 上游清单 17 个文件 SHA：通过；
- Python 编译：通过；
- 目标单元测试：`16 passed`；
- Asset API 版本与 Direct V2.3.0 创建合同集成测试：`2 passed`；
- Ruff 目标文件检查：通过；
- 控制面与 GPU 节点 Compose 解析：通过；
- Worker 镜像内包校验与启动设置检查：通过；
- Asset API 镜像内 Direct V2.3.0 身份检查：通过。

## 发布保护

本文记录的镜像当前是候选版本。滚动发布前必须检查真实任务为零；先更新 Asset API，再逐台将
Worker 置为 `DRAINING`、确认 `current_jobs=0` 后替换并恢复 `ACTIVE`。不得重启 Scheduler、Web、
ComfyUI 或 Windows Substance，也不得清理 ComfyUI 模型缓存。
