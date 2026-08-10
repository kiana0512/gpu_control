# ModelView 局部重绘交互式优先级与 1.5.12 发布记录

日期：2026-08-10  
状态：`DEPLOYED_LIVE_CANARY_PASSED`（不代表全系统 `PRODUCTION_ACCEPTED`）

## 1. 现场问题与根因

用户提交的两笔 `modelview-inpaint` 分别等待 `310.20s` 和 `307.21s` 后才被领取，实际 GPU 执行只需
约 `22.05s` 和 `47.09s`。当时三张 GPU 均在执行真实 `imageclip-rgba`，而局部重绘被创建为
`priority=normal / pinned=false`。队列中的旧批量帧经过优先级老化后达到最高普通等级，新局部重绘
必须同样等待约五分钟老化，因而错误地排在批量帧后面。

这不是 ModelViewCreator 工作流或模型速度问题；本轮没有修改其 Git 仓库、工作流 JSON、自定义节点、
模型、提示词、推理参数或输出语义。

## 2. 调度修复

- `/api/v1/services/modelview-inpaint` 由服务端强制使用 `critical + pinned`，客户端不能通过通用业务 API
  自行申请该内部优先级。
- 局部重绘会取得第一张释放的兼容 GPU 槽，旧批量抠图不能在它前面继续插单。
- 同一个局部重绘租户可使用最多三个现有 GPU 槽，不再被自动发现客户端的默认
  `max_running=1` 串行化。
- 策略是非抢占式：已经运行的真实抠图帧会先完成，不取消、不杀进程；没有局部重绘时三卡继续处理
  ImageClip，不永久空置专用 GPU。
- 数据库仍短暂记录 `QUEUED` 状态以保证持久化、审计和故障恢复。因此“无需排普通队列”的精确定义是：
  若三卡全忙，只等待第一张卡完成当前帧；若有兼容空闲卡，则直接领取。系统不会中断已运行任务来实现
  物理意义上的零秒等待。

## 3. 测试证据

- Ruff：修改文件全部通过。
- API 集成：`43 passed`。
- 数据库领取/调度集成：最终 `28 passed`，包含“租户已有活动任务时，pinned 局部重绘仍可使用第二张
  GPU”的回归。
- 公平性单元测试：`4 passed`。
- Compose 配置校验通过。
- 用户已取消正式压力测试，本轮没有生成压力流量。

## 4. 镜像、Git 与生产滚动

- 功能提交：`227f55b386ec38c0f0c9173165f2e8ef5bee2ed1`。
- 三卡并行补充提交：`093ae8b7966ae5beb86990c7881c11d4c24d4e51`。
- 两个提交均已推送 `origin/main`。
- API 镜像：`gpu-control-api:1.5.12`，本地镜像 ID
  `sha256:dbd2579dcf5e759a69508f4539cae6c37f3a2e1d467fa6cff9b8a38d756b7ac0`。
- Scheduler 镜像：`gpu-control-scheduler:1.5.12`，本地镜像 ID
  `sha256:931e0b689dee69d245a2f1c63f9f792a11e51d29c423567f1e85dead34c43a93`。
- 两个镜像的 OCI revision 均为
  `093ae8b7966ae5beb86990c7881c11d4c24d4e51`，构建包含 BuildKit provenance 与 SBOM attestation。
- API `/api/v1/version` 回报 `version/package_version/build_version=1.5.12`、revision 一致、
  `version_aligned=true`、`provenance_complete=true`。

滚动只重建 API/Scheduler 容器。三台 ComfyUI、Web、Asset API、Blender Worker 均未重启。滚动时三张
GPU 各有一笔真实 ImageClip 任务，任务持续执行；最终验收时统计到 `26 SUCCEEDED / 0 FAILED`。三个
节点最终保持 `ACTIVE / ONLINE / current_jobs=1`。

## 5. 真实局部重绘 canary

复用最近一笔已成功局部重绘的原输入和原参数，创建了一个不覆盖原任务的新 canary：

- Job：`2b77a00a-fb80-4194-a05a-4ea0b94e879c`。
- 提交现场：三卡满载，另有 20 条旧 `imageclip-rgba` 排队。
- 入库策略：`priority=critical / pinned=true`。
- `created -> claimed`：`27.941s`，被第一张释放的 `worker-3090-a` 领取；旧版本现场样本为
  `307.21s / 310.20s`。
- GPU 执行：`23.320s`；端到端：`57.011s`；HTTP `200`；输出 `897,749 bytes`；任务最终
  `SUCCEEDED / 100%`。
- canary 完成后仍有三条真实 ImageClip 正在三卡执行、20 条排队，节点保持 `ACTIVE / ONLINE`，没有
  业务失败或 ComfyUI 重启。

## 6. 回滚

把 `.env` 中 `APP_IMAGE_TAG` 和 `GPU_CONTROL_VERSION` 恢复为 `1.5.11`，将
`GPU_CONTROL_REVISION` 恢复为对应旧镜像 revision，然后只重建 `api scheduler`。回滚不需要重启三台
ComfyUI。回滚后新局部重绘会恢复旧的普通队列行为。
