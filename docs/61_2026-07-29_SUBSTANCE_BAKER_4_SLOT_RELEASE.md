# 2026-07-29 Substance Baker 四槽发布与验收记录

## 发布结果

- Asset API：`unified-scheduler-asset-api:1.5.4`，healthy。
- WebUI：`gpu-control-web:1.5.4`，healthy。
- Windows Agent：`asset-worker-3090-b-windows-01`～`-04`，四个独立计划任务、四个持续心跳、每个 `max_concurrency=1`。
- WebUI：四实例聚合为一个“3090-B Windows Substance Baker”资源，显示 `当前占用/4`。
- 旧 `asset-worker-3090-b-windows` 心跳仅作为兼容记录保留，不参与新实例容量聚合。

## 并发与物理 GPU 安全

四个 Baker 进程允许并行执行，但不允许 Windows Baker 与 WSL ComfyUI 同时争抢 3090-B：

1. 第一个 Baker 任务进入时获取全局互斥锁、写入共享活动任务集合并暂停 WSL ComfyUI。
2. 后续 Baker 任务只加入集合，不重复停止服务。
3. 每个任务离开时从集合移除自身。
4. 最后一个任务离开时恢复 WSL ComfyUI，并以健康检查成功作为任务结果发布前置条件。

该机制既解除单槽吞吐限制，也避免四进程反复启停 WSL 推理服务。

## 真实请求验收

输入为真实低模 FBX、高模 FBX、Base Color、Roughness 和 Metallic；profile 为 `li3d-pbr-full-v2`，分辨率为 `512×512`。四个请求均通过 HTTPS、来源 IP 自动客户识别、独立 Baker 队列、Windows CLI、结果校验和原子发布全链路。

| 任务 ID | Worker | 状态 | 开始（UTC） | 完成（UTC） | 产物 |
|---|---|---|---|---|---:|
| `313a5f85-160d-482f-9562-5ca9faa24c5a` | `-03` | `SUCCEEDED` | `11:03:54` | `11:04:32` | 12 |
| `e7ea24e3-bfb9-4333-9c5c-9ecc611a2236` | `-04` | `SUCCEEDED` | `11:03:54` | `11:04:18` | 12 |
| `b54fd692-a311-457f-a8fc-effef888dab8` | `-03` | `SUCCEEDED` | `11:05:43` | `11:06:20` | 12 |
| `14ae0edb-1f64-4479-a598-8580bb118f82` | `-04` | `SUCCEEDED` | `11:05:43` | `11:06:07` | 12 |

每个任务最终产物均为：Base Color、Roughness、Metallic、AO、DirectX Normal、OpenGL Normal、World Normal、Curvature、Thickness、Position、`baker_result.json`、`baker.log`。

## 回归测试

- Web：lint、production build、3 个前端单元测试通过。
- Python：Ruff 通过；Asset API、Retopology Quality Gate、Roughness 工作流共 13 个测试通过。
- `git diff --check` 通过。
- 发布前资产执行队列为空；只滚动 Asset API 与 WebUI，未重启 GPU API 和调度器，未中断 GPU 生产任务。

## 回滚

如需回滚，只需把 `APP_IMAGE_TAG` 恢复为先前版本并单独重建 `asset-api`、`web`；Windows 侧可停用 `GPUControl-Substance-Baker-Agent-01`～`-04` 并恢复旧单实例任务。回滚前仍必须确认资产队列为空。
