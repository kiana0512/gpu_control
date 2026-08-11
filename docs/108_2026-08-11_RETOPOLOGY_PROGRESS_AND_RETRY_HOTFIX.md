# Direct V2 进度、ETA 与重试热修复（2026-08-11）

## 1. 现象与生产证据

多个真实拓扑任务在页面长时间显示 92%、97% 或 99%，Direct V2 阶段还显示“剩余约
116 分钟”。数据库事件、Worker 进程和临时交付文件联合证明：

- Worker 仍每 15 秒上报进度并续租，不是 Worker 离线或租约丢失。
- 4090 和 3090-A 分别有活跃 Blender/Codex 子进程；Codex 会在单次正式构建中执行测量、计划、
  Blender 构建和报告写入，复杂模型实测需要约 6–11 分钟。
- 旧 Worker 把 `CODEX_JOB_TIMEOUT_SECONDS=7200` 同时当作 ETA；这是两小时硬超时，不是正常预计。
- 旧进度每 15 秒固定增加八分之一阶段跨度，约两分钟就到达 92%/98%/99% 上限，即使子进程
  仍在实际计算。
- 质量 QA 失败后，Asset API 保留了上一尝试的 99%；第二次从头生成时又对进度取 `max`，
  因此整个第二轮都显示 99%。

现场任务 `70f21d3f-96d4-45a7-a8b7-0e056e641585` 和
`3f671a6f-28d1-4a32-a6c1-4541b0b41002` 最终不是超时：两轮都生成了候选，但候选被视觉或
纯变换门禁拒绝。其中一个候选表面误差比 `0.2330`、尺寸误差 `41.91%`，显然不能通过
坐标缩放伪装成同一模型；系统拒绝交付是正确行为，但第二轮重跑浪费了时间。

## 2. 修复

1. Direct V2 正常阶段估算改为 600 秒；7200 秒只保留为防孤儿进程的硬超时。
2. 进度根据本阶段实际经过时间增长，运行中最多到阶段边界的 95%；只有子进程真正退出后才进入
   下一阶段。
3. 经过时间超过正常估算时，ETA 返回未知，不显示 0 秒或硬超时剩余量。
4. 重试入队时将活动进度重置为 0，领取时设为 1；前一尝试的进度写入
   `previous_attempt_progress` 事件证据。
5. `RETOPOLOGY_QA_FAILED` 且已进入构建后门禁时禁止自动重跑；Worker 和 Asset API 双重强制。
   瞬时网络/Worker 错误仍可重试，且新尝试从真实进度开始。
6. 阶段文案不再说“保存后立即交付”，明确后续还有坐标对齐、UV、七方向检查和 FBX 回读。

## 3. 验证与发布

- 单元/集成回归：进度时间曲线、ETA 超窗口转未知、重试进度重置、旧进度事件保留、QA 失败不重试。
- Worker：`1.4.19-retopo-progress-v1`。
- Asset API：`1.6.18-retopo-progress-v1`。
- 代码回归：`41 passed, 4 skipped`；Python 编译、Ruff、Compose 配置和 `git diff --check`
  均通过。

### 3.1 正式发布证据

| 项目 | 结果 |
|---|---|
| Git | `cd6d22c8b0b1671f82e4cc53aec15b4ac62ecbfe`，已推送 `origin/main` |
| Worker 镜像 | `li3d/blender-worker:1.4.19-retopo-progress-v1`；镜像 ID `sha256:b51a947fd1552e9caa9368c40206d8b08bf8039b8c79591cd6a5a3525f85aecf` |
| Asset API 镜像 | `unified-scheduler-asset-api:1.6.18-retopo-progress-v1`；镜像 ID `sha256:d8debd14e63084d068d22ce6e45be18d99e4f4a920cb04d3a4ad3f6c663add22` |
| Worker 离线包 | `/srv/gpu-control/images/retopo-progress-v1-cd6d22c/blender-worker.tar.zst`；`690828772` bytes；SHA-256 `92749bba4aedc88d2316db21f27047f3160112b365db476256d60d33aec30bc5` |
| Asset API 离线包 | `/srv/gpu-control/images/retopo-progress-v1-cd6d22c/asset-api.tar.zst`；`92795656` bytes；SHA-256 `7de6e223a5a71952f5c405327afbc76e966a4712a690ee108d4a8edd29d671b0` |
| 三节点 | `control-4090`、`worker-3090-a`、`worker-3090-b` 均严格执行 `DRAINING -> 0 任务 -> 只替换 Blender Worker -> 三项探针健康 -> ACTIVE`；运行镜像 ID 一致 |
| 任务保护 | 发布前最后一个旧版本真实任务 `be09ab4a-7d3a-4d42-950f-623bd70ea147` 等待至 `SUCCEEDED / 100%` 后才开始替换；未中断用户任务 |
| ComfyUI / Baker | 三台 ComfyUI 容器身份、启动时间和重启次数保持不变；3090-B 四个 Windows Baker 槽保持 `ONLINE / 0 jobs / 0 processes` |
| 发布后状态 | 三节点 `ACTIVE / ONLINE / 0 jobs`；三台 Linux Worker 均为 `AUTHENTICATED / HEALTHY / RetopoFlow HEALTHY`；Asset API `healthy` |

本修复不需要以新任务改写或放宽拓扑输出门禁，发布后未主动占用生产槽位跑压力或破坏性 canary；
进度曲线、ETA、重试重置和 QA 不重试均由自动化专项覆盖，下一笔真实任务可直接验证页面行为。

本次不修改 Direct V2 的模型、prompt、网格构建算法或输出语义；不修改 ImageClip、ModelViewCreator、
ComfyUI 工作流、模型或参数。
