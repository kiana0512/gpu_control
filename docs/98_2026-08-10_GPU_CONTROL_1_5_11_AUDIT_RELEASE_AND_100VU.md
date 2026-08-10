# GPU Control 1.5.11 全服务审计、发布与第三次 100 VU 压测

- 日期：2026-08-10
- 当前阶段：`DEPLOYED_NOT_ACCEPTED / 100VU_PENDING`
- 控制面候选：`1.5.11`
- Asset API 候选镜像：`1.6.12-retopology-coordinate-restore-v2`
- Linux Blender Worker 候选：`1.4.7-retopology-coordinate-restore-v2`
- 数据库候选迁移：`20260810_0013`
- 最终状态：五个镜像已构建并部署，真实六 API 压测和观察证据回填前保持 `NOT_ACCEPTED`

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
| 3090-B 满载时 `/object_info` 偶发超时被当成整机离线 | 节点反复上下线，真实序列分配明显少于另两台 | 核心健康与可选能力清单分离；清单失败保留缓存并退避 60 秒 |
| 3090-B 没有相对同型号原生节点的持续性能判断 | WSL/宿主异常时单帧慢 3～4 倍只能靠人工发现 | 最近 5 帧同分辨率中位数对比 3090-A；2 倍、持续 2 分钟告警，并监测掉线抖动 |
| 3090-B 只能看到“在线/离线”和任务耗时，缺少 WSL 内核直接证据 | 宿主异常要等任务变慢后才能确认 | 新增签名 `/v1/system-metrics`，采集 boot ID/uptime、load/CPU、内存/swap、CPU/内存/IO PSI；异常只告警、不影响调度资格 |
| 3090-B 无 DCGM，通用 `GPUHot` 不覆盖 WSL2 | B 的高温/功耗异常无法自动告警 | 签名 GPU 查询导出利用率、显存、温度、功耗/上限；新增 `WSLGPUHot` |
| 六 API 压测仍按退役 V1 的 22/23 件拓扑产物验收 | Direct V2 成功也会被测试工具误判失败 | 精确对齐 Direct V2 的 7 件正式产物 |
| 生产 test 历史超过 500 条后，旧 session 防重扫描饱和 | 正式压测在 0 请求阶段 fail closed，无法继续升压 | 新增规范 UUIDv4 精确碰撞查询，分别计数 GPU job、ImageClip batch、Asset job，不删除历史 |
| Blender 5.1 默认压缩合成 `.blend` | Direct V2 在执行前拒绝 Zstd 文件头 | 生成时显式 `compress=False`，生成器再次校验原始 `BLENDER` 签名 |
| Direct V2 压测件沿用审计用三角色场景 | 旧低模和参考模被误当成额外高模 | Direct V2 测试件只含唯一高模；三角色场景继续仅用于审计 API |
| Direct `.blend` 没有 FBX 专用 `source-manifest.json` | Codex 已生成有效 Blend，但报告别名无法补齐，任务在 92% 失败 | 拓扑结束后只读确认唯一源高模与最终低模，补齐报告后再执行现有坐标恢复 |
| 操作员同时中断外层脚本与 Locust | 无效压测虽然停止，但外层提前退出、未写完整 teardown/summary | 外层只向 Locust 转发 SIGINT并等待最多 360 秒完成范围清场 |
| Python/前端依赖存在已知安全公告 | 镜像/锁文件扫描失败 | 升级至修复版本；候选环境 `pip-audit` 与 `npm audit` 均为 0 |

旧 Windows `asset-worker-3090-b-windows` 数据库行保留兼容历史，但管理 API 按心跳超时投影为
`OFFLINE`，不计入容量；只有 `-01..-04` 四个 v6 实例计入 Substance 槽位。

## 4. 发布前验证

| 门禁 | 结果 |
|---|---|
| Python 全量测试 | `516 passed, 11 skipped, 0 failed` |
| Ruff | 通过 |
| mypy strict | 43 个源码文件通过 |
| Web Vitest | 18/18 通过 |
| Web ESLint / Prettier / vue-tsc / production build | 通过 |
| npm audit（完整依赖） | 0 vulnerabilities |
| pip-audit（候选业务环境，pip 26.2.1） | 0 known vulnerabilities |
| Compose / Shell / diff | 通过 |
| Alembic 0013 | 独立 PostgreSQL 完整 upgrade → downgrade → upgrade 通过；测试库已删除 |
| 三节点连通 | ComfyUI、Node Agent、Node Exporter、3090-A DCGM、PostgreSQL、Redis 全通过 |
| 三 Worker 拓扑包身份 | 镜像 ID 与三份校验清单 SHA 完全一致 |
| 三个 `/object_info` | HTTP 200 |
| WSL 性能/系统状态探针针对性回归 | 21 passed；mypy strict、Ruff、Prometheus 14 rules 通过 |

本轮发布准备期间，真实 118 帧 ImageClip 序列成为生产保护门禁。旧 Scheduler 的现场日志显示
3090-B 在 GPU 满载时持续出现 `COMFY_HEALTH_FAILED`，分配量明显低于另外两台；随后 Windows/WSL
界面也无法正常显示，确认当时同时存在健康误判放大与真实宿主异常。用户重启 B 时中断的 1 帧由
调度器自动跨节点重试成功，最终批次 `118/118 SUCCEEDED`、失败 0。结果 ZIP 为 269,787,187 bytes，
SHA-256 `447376625bd420b477450eec7bae62194adfad61fdf2b07cd626fadfd9669e58`，ZIP 完整性通过，包含
118 个 PNG 与 1 个 manifest。

同一批 1080×1440 输入的稳定性能证据：4090 P50 `19.06s`、原生 3090-A P50 `30.59s`、WSL2
3090-B 重启并预热后 P50 `33.15s`，B 比 A 慢约 8.4%，可接受；B 重启前 P50 `128.62s`，属于
不可接受的异常状态。真实数据只读运行新探针得到最近 5 帧 A=`30.636983s`、B=`33.084579s`、
比值 `1.079890`、`anomaly=0`。新增回归同时要求 `/system_stats` 与 `/queue` 正常、仅
`/object_info` 超时时节点保持 `ONLINE`、旧能力清单不丢失且立即进入退避。

重启后在 B 上只读实测深度状态：内核 `6.18.33.2-microsoft-standard-WSL2`，boot ID
`a7faf7c8-b7d8-40ac-bf86-cf153fb53473`，64 个可见 CPU，MemAvailable 约 60.4/65.8 GB，swap 使用
0，CPU/内存/IO PSI avg10 均为 0；Node Agent、Node Exporter、8188 和 9201 正常。真实任务满载采样
GPU 89%、82°C、354.8/370W，低于持续高温阈值但已接近 WSL 功耗上限。

随后第二批真实 118 帧 `e99ec54a-8765-4b95-9dc3-ee3ad7495c6d` 也在候选代码测试期间完成：
`118/118 SUCCEEDED`、失败 0、活动租约最终归零。结果 ZIP 为 270,059,647 bytes，SHA-256
`264c5ee529e40f5e72affba27e30f2e419cbb39bb6dac530a84f3432fb6aec8a`；ZIP 完整性通过，包含 118 个
PNG 与 1 个 manifest。这证明离线测试和文档工作没有干扰三节点真实调度。

候选发布前实机状态：三 GPU 节点、三 Linux Worker、四 Windows Baker 在线，任务、批次、Asset 作业、
活动租约均为 0；三台 ComfyUI 健康且 RestartCount=0。滚动后 API、Scheduler、Asset API、Web 和三台
Blender Worker 均运行候选镜像且 RestartCount=0；三台 ComfyUI 未因本轮发布重启。三 GPU 节点已恢复
`ACTIVE / ONLINE`，发布后任务与租约再次归零。

生产数据库已由 `20260803_0012` 升级到 `20260810_0013`。三台 Blender Worker 的源码、包和镜像身份
一致；三台外部 ImageClip pipeline hash 均保持
`00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b`，没有修改外部仓库内容。
Prometheus 已热加载 14 条规则；3090-B 的 system probe、GPU 温度/功耗和性能探针均有实时指标，规则
加载过程没有重启 Prometheus。

发布前完整备份位于 `/srv/gpu-control/backups/20260810T033212Z-full`，格式、SHA 清单、数据库 dump、
Git bundle、敏感配置和前后两次 zero-work gate 均通过离线校验。该目录含敏感配置，保持 root-only；
后续仍需制作加密异地副本。

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

首次正式启动 session `ae607325-580d-4147-b13c-e8a64fb88e45` 在 preflight 阶段发现历史 test GPU
记录已达到 500 条上限，压测器按设计以 exit 2 停止：业务请求 `0`、创建任务 `0`、残留任务 `0`，
七容器身份在退出前后保持一致。该结果不是服务压测失败，而是旧防重查询无法在截断历史上证明 UUID
未被使用。修复后控制面只读接口按精确 session 前缀直接在数据库计数三类持久命名空间；任一计数非零
仍拒绝执行，历史数量本身不再造成假阻断。专项验证为 `124 passed, 1 skipped`，Ruff 通过。

精确碰撞修复源码 `969b535645e05be32597a9a86d1510cd84febd51` 推送后，重新从该提交构建全部
五个镜像；没有混用先前 `04e281cd` 镜像。第二套候选位于
`artifacts/control-plane/1.5.11-r2/release-parts`，组合归档 SHA-256 为
`4cb3836718c6060d9785a6a92eba9fe3b3c8039a5ac93e4fedba13f3ae21b7dd`。7 个分片的本地 SHA、
Git LFS 指针、上传结果和 `git lfs fsck` 均通过，候选与 live receipt 在提交
`2cbfd1632c759cf729e68d7f6f25fd19a03b038f` 推送到 `origin/main`。

生产重新滚动时先将三节点置为 `DRAINING`，确认运行中任务、Asset 作业、活动租约和三台 ComfyUI
队列为 0，再按 Asset API、3090-B Worker、3090-A Worker、control-4090 Worker、API、Web、
Scheduler 的单服务顺序替换。滚动窗口中出现 1 个新的排队 GPU 任务；它没有被 Worker 领取，未被
取消或修改，节点恢复 `ACTIVE` 后由 control-4090 正常完成。三台 ComfyUI 的容器 ID、启动时间和
RestartCount 全程不变。滚动后新精确碰撞接口实机返回 HTTP 200、空 session 计数 0；最终任务、
租约再次归零。

第二次正式启动 session `4878d60c-56ab-4d4a-a56f-39b2ab608b67` 通过生产、备份、身份和碰撞
preflight；到 10 VU 时 7 个 Direct V2 提交均在执行前拒绝。测试件由 Blender 5.1.2 以 Zstd
压缩保存，文件头为 `28 b5 2f fd`，而已批准 Direct V2 合同只接受原始 `BLENDER` 签名。该轮随即
停止，没有继续制造无效流量。原外层脚本因同时收到 SIGINT 退出 130，随后按该轮原始 preflight
时间边界执行范围恢复：尝试 9、收敛 9、最终作用域验证通过，GPU/Asset 活动作业和租约全部为 0；
证据保存在该结果目录的 `manual-recovery.json`。

修正签名后，两次真实单任务 canary 分别为
`ecaa6740-e703-43ca-8899-b1f26b5860d4` 和
`37aa5735-62c9-488e-b1bc-6a6fe64e163c`，均已通过输入签名并执行到 92%，随后稳定暴露报告兼容缺口。
进一步使用只含唯一高模的合规测试件复现任务
`4b5c3c38-da31-47b8-8d87-b6238ec2888f`，仍得到
`generation report has no asset records`，证明问题不是拓扑、多模型歧义或节点偶发异常。根因是直接
`.blend` 不生成 FBX 路径使用的 `source-manifest.json`，旧兼容器因此没有调用已有的 Blend 只读
交付检查。补丁只在 Codex 成功并保存结果后读取源/结果 Blend，唯一确认高低模名称并规范化报告；
之后仍由坐标恢复 v2 决定“未移动则原样交付、移动则只平移恢复”。针对性回归
`96 passed`、Ruff 与 strict mypy 通过。

补丁源码 `3583023db112a684a757fa2f1a10fec5fcd47463` 推送后，第三套候选从同一 SHA 重新构建五个
镜像，位于 `artifacts/control-plane/1.5.11-r3/release-parts`；组合归档 SHA-256 为
`e51c810aed37a274b6dd349c10cfa874be5ce9e473dfe42f55594ea80afde705`，7 个分片 SHA、gzip、
OCI/Docker config 身份和 `git lfs fsck` 均通过，候选提交
`0be5ac28cb59f86256e5782f5f122c86850e9cdc` 已推送。三节点按零任务门禁全部 DRAINING，依次替换
Asset API、3090-B Worker、3090-A Worker、control-4090 Worker、API、Web、Scheduler，再恢复
ACTIVE。三 Worker 均为 `ONLINE / AUTHENTICATED / Codex HEALTHY / RetopoFlow HEALTHY / 0 jobs`；
所有新容器 RestartCount=0，三台 ComfyUI 的容器 ID、启动时间与 RestartCount 保持不变。

## 6. 安全发布顺序

1. 等待受保护的 118 帧真实序列完成并校验下载产物；再次确认 GPU/Batch/Asset/lease 为 0，生成并
   验证本窗口 full backup。
2. 提交并推送源码，五个镜像必须绑定同一完整 Git SHA。
3. 构建、扫描、打包并推送 API、Scheduler、Asset API、Web、Blender Worker。
4. 执行 0013；在 Worker 心跳暂停的受控窗口压缩 `asset_workers`，验证 8 行完整保留。
5. 先升级 Asset API；新 API 接受老 Worker 缺省字段。
6. 按 3090-B → 3090-A → control-4090 逐台 Drain、确认 0 任务、只更新 Blender Worker、恢复。
7. 更新 API、Web、Scheduler；只更新目标服务，不重启三台 ComfyUI，不清模型缓存。
8. 验证三 Worker 同镜像、同源码、同 Skill/包 SHA、同 Codex 健康边界；Prometheus 假目标消失。
9. 写入并推送 candidate/live deployment receipt，保持 `DEPLOYED_NOT_ACCEPTED`。
10. 执行六 API canary 与正式 100 VU；清场、观察、结果 SHA 和分析全部通过后再决定验收状态。

## 7. 已部署证据与待回填项

| 项目 | 状态 |
|---|---|
| 镜像源码 Git SHA | `3583023db112a684a757fa2f1a10fec5fcd47463`；已推送 |
| 候选归档 Git/LFS | `0be5ac28cb59f86256e5782f5f122c86850e9cdc`；`1.5.11-r3` 7/7 LFS 已推送，`git lfs fsck` 通过 |
| 五镜像构建与本地不可变 ID | `VERIFIED / DEPLOYED`；API `cc377158`、Scheduler `8f7d2490`、Asset API `e35fa7d2`、Web `e647fc83`、三 Worker `072b4175`；组合归档 SHA-256 `e51c810aed37a274b6dd349c10cfa874be5ce9e473dfe42f55594ea80afde705` |
| Registry digest / SBOM | `PENDING_REGISTRY_PUSH / PENDING_PINNED_SBOM_GENERATOR`；不得宣称 strict release accepted |
| live deployment receipt | `artifacts/control-plane/1.5.11/deployment/live-deployment-receipt.json`；blob SHA-256 `61c75c6cdb98a97e4e4a8d1f232ef1843d9c1bf3350b1264ddcdf841e5b02b36`；状态 `DEPLOYED_NOT_ACCEPTED` |
| full backup 与离线完整性校验 | `PASS`；`/srv/gpu-control/backups/20260810T033212Z-full` |
| 0013 生产迁移 | `PASS`；当前 revision `20260810_0013` |
| Asset API / 三 Worker / API / Web / Scheduler 滚动 | `PASS`；目标容器 RestartCount 均为 0 |
| 三节点、四 Baker、Codex、Prometheus 发布后验证 | `PASS`；三节点 ACTIVE/ONLINE，14 条规则已加载 |
| 六 API canary | 三次诊断任务已定位并修复 Direct Blend 报告兼容缺口；新镜像部署后成功 canary `PENDING` |
| 100 VU 原始结果、阈值、清场与分析 | `PENDING` |
