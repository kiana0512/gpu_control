# GPU Control 1.5.11 全服务审计、发布与第三次 100 VU 压测

- 日期：2026-08-10
- 当前阶段：`SOURCE_AUDITED / CANDIDATE_BUILD_PENDING`
- 控制面候选：`1.5.11`
- Asset API 候选镜像：`1.6.12-retopology-coordinate-restore-v2`
- Linux Blender Worker 候选：`1.4.7-retopology-coordinate-restore-v2`
- 数据库候选迁移：`20260810_0013`
- 最终状态：镜像、部署、真实六 API 压测和观察证据回填前保持 `NOT_ACCEPTED`

本文是本轮综合升级的唯一事实记录。任何未回填的项目都不能提前写成通过。

## 1. 范围与不变项

本轮只修改 GPU Control 自有控制面、调度、Worker 接单、可观测性、数据库维护、发布工程和压测工具。
没有修改 ImageClip、ModelViewCreator、外部 Git 仓库、workflow JSON、模型、custom node、prompt、
推理参数、采样步骤、分辨率、图拓扑或最终输出节点。

重拓扑保持 Direct V2 和坐标恢复 v2：拓扑完成后只校准低模平移；如果坐标未变化则原样交付；
如果变化则恢复到高模中心，并对正式 FBX 做回读校验。没有修改拓扑算法、网格、旋转、缩放、高模或
烘焙合同。

## 2. 拓扑与 CPU 并发结论

每台 Linux Worker 有多个普通 CPU/Blender 槽，但只有一个进程级 Codex 执行槽。继续保持：

| 节点 | 普通 CPU 槽 | Codex 拓扑槽 |
|---|---:|---:|
| control-4090 | 2 | 1 |
| worker-3090-a | 3 | 1 |
| worker-3090-b | 4 | 1 |
| 合计 | 9 | 3 |

因此三台机器最多同时执行 3 个 Codex 拓扑任务，不在同一台机器并发多个 Codex。修复前，一个拓扑
占用 Codex 后会让该 Worker 停止领取所有任务，剩余 CPU 槽闲置；1.5.11 新增向后兼容的
`accepts_codex_jobs` claim 能力位，只屏蔽第二个 Codex 拓扑，UV 和 Blender 审计仍可填满剩余槽位。

## 3. 审计发现与修复

| 发现 | 影响 | 修复 |
|---|---|---|
| `.env` 仍使用旧版 1.5.9 默认值 | 重建会意外降级 | 全部运行/示例/Compose 默认值对齐候选版本 |
| `.env` 把 3090-A 写成旧地址 10.3.34.13 | 官方连通检查误报离线 | 对齐实时心跳与 `nodes.yaml` 的 10.3.34.12 |
| 3090-B WSL 没有 DCGM `:9400`，Prometheus 仍宣告目标 | 持续 Critical NodeDown 假告警 | WSL 默认只宣告 Node Exporter；显式标签可重新启用 DCGM |
| 3090-A 缺少 Nunchaku 元数据探测所需空 checkpoints 目录 | `/object_info` 报 FileNotFoundError | 只补空目录；未改模型、工作流或 custom node |
| Asset Worker 心跳表 8 行、约 419 万次更新、约 78 MB | 频繁 vacuum 与持续膨胀 | 0013 移除无收益心跳索引、启用 HOT/fillfactor 与合理阈值 |
| Codex 槽占用时整台 Worker 停止 claim | UV/审计浪费剩余 CPU 槽 | Codex 与普通 CPU admission 分离，仍严格一机一个拓扑 |
| 六 API 压测仍按退役 V1 的 22/23 件拓扑产物验收 | Direct V2 成功也会被测试工具误判失败 | 精确对齐 Direct V2 的 7 件正式产物 |
| Python/前端依赖存在已知安全公告 | 镜像/锁文件扫描失败 | 升级至修复版本；候选环境 `pip-audit` 与 `npm audit` 均为 0 |

旧 Windows `asset-worker-3090-b-windows` 数据库行保留兼容历史，但管理 API 按心跳超时投影为
`OFFLINE`，不计入容量；只有 `-01..-04` 四个 v6 实例计入 Substance 槽位。

## 4. 发布前验证

| 门禁 | 结果 |
|---|---|
| Python 全量测试 | `500 passed, 12 skipped, 0 failed` |
| Ruff | 通过 |
| mypy strict | 60 个源码文件通过 |
| Web Vitest | 18/18 通过 |
| Web ESLint / Prettier / vue-tsc / production build | 通过 |
| npm audit（完整依赖） | 0 vulnerabilities |
| pip-audit（候选业务环境，pip 26.2.1） | 0 known vulnerabilities |
| Compose / Shell / diff | 通过 |
| Alembic 0013 | 独立 PostgreSQL 完整 upgrade → downgrade → upgrade 通过；测试库已删除 |
| 三节点连通 | ComfyUI、Node Agent、Node Exporter、3090-A DCGM、PostgreSQL、Redis 全通过 |
| 三 Worker 拓扑包身份 | 镜像 ID 与三份校验清单 SHA 完全一致 |
| 三个 `/object_info` | HTTP 200 |

候选发布前实机状态：三 GPU 节点、三 Linux Worker、四 Windows Baker 在线，任务、批次、Asset 作业、
活动租约均为 0；三台 ComfyUI 健康且 RestartCount=0。Scheduler 的 RestartCount=2 是历史值，当前
健康、无 OOM；发布后必须确认没有新增重启。

## 5. 第三次真实 100 用户压力模型

场景为 `tests/load/scenarios/six_api_100_20260810.yaml`，只到 100 VU，不进入 120 VU。阶段：

| VU | spawn rate | 平台期 |
|---:|---:|---:|
| 1 | 1/s | 60s |
| 10 | 2/s | 120s |
| 25 | 5/s | 180s |
| 50 | 10/s | 300s |
| 100 | 20/s | 600s |

权重来自 2026-08-03 至 2026-08-10 的生产创建量，并给零自然流量的拓扑审计保留 5% 覆盖：

| API | 权重 |
|---|---:|
| ImageClip RGBA batch | 29% |
| ModelView Roughness | 4% |
| UV Process | 12% |
| Retopology Audit | 5% |
| Retopology Direct V2 | 35% |
| Substance Bake | 15% |

35% 拓扑流量会有意触发三 Codex 槽的排队极限；这用于量出真实队列延迟，不通过在单机并发多个 Codex
来伪造容量。测试采用 `bounded_stress`，生产优先门禁和 watchdog 保持启用，只能清理本 session 的
任务。HTTP、提交、同步端到端、poll、artifact、queue 和 retry 七类阈值全部 fail closed。

## 6. 安全发布顺序

1. 再次确认 GPU/Batch/Asset/lease 为 0，生成并验证本窗口 full backup。
2. 提交并推送源码，五个镜像必须绑定同一完整 Git SHA。
3. 构建、扫描、打包并推送 API、Scheduler、Asset API、Web、Blender Worker。
4. 执行 0013；在 Worker 心跳暂停的受控窗口压缩 `asset_workers`，验证 8 行完整保留。
5. 先升级 Asset API；新 API 接受老 Worker 缺省字段。
6. 按 3090-B → 3090-A → control-4090 逐台 Drain、确认 0 任务、只更新 Blender Worker、恢复。
7. 更新 API、Web、Scheduler；只更新目标服务，不重启三台 ComfyUI，不清模型缓存。
8. 验证三 Worker 同镜像、同源码、同 Skill/包 SHA、同 Codex 健康边界；Prometheus 假目标消失。
9. 写入并推送 candidate/live deployment receipt，保持 `DEPLOYED_NOT_ACCEPTED`。
10. 执行六 API canary 与正式 100 VU；清场、观察、结果 SHA 和分析全部通过后再决定验收状态。

## 7. 待回填证据

| 项目 | 状态 |
|---|---|
| 源码 Git SHA / origin main | `PENDING` |
| 五镜像 ID / registry digest / SBOM | `PENDING` |
| full backup 与 verify-only | `PENDING` |
| 0013 生产迁移与表压缩 | `PENDING` |
| Asset API / 三 Worker / API / Web / Scheduler 滚动 | `PENDING` |
| 三节点、四 Baker、Codex、Prometheus 发布后验证 | `PENDING` |
| 六 API canary | `PENDING` |
| 100 VU 原始结果、阈值、清场与分析 | `PENDING` |

