# 2026-07-30 六 API、120 VU 生产混合压测结果（r5/r7 历史与 R8 索引）

日期：2026-07-30

会话：`sixapi-20260730-r5`

状态：`r5 LOAD COMPLETED / CONTROL PLANE STABLE / ACCEPTANCE GATES PARTIAL`
发布状态：`DEPLOYED_NOT_ACCEPTED`

> 阅读提示：第 1～11 节保留 r5 当时的原始判断；第 12 节记录 1.5.6 上的 r7 复测；第 13 节只索引
> 独立 R8 最终记录。r7 已关闭
> r5 的业务阈值、生命周期和作用域清场缺口，但遥测相邻样本最大间隔以 `7,699 ms` 超过严格上限
> `7,500 ms`，故该轮仍以退出码 1 fail closed。R8 通过不能反向改写 r7 原始结果。

## 1. 最终结论

本轮已经按 `1 → 10 → 25 → 50 → 100 → 120 VU` 完成全部 31 分钟计划，六个业务 API
均形成真实服务端任务。GPU 三槽和 Asset 十三槽都曾全部占满，三张 GPU 都达到至少 90% 的
利用率样本。控制面在本轮观测窗口内保持稳定：Locust 共记录 `40,021` 次 HTTP 请求、`0` 次
失败，生产让路 watchdog 未触发，未观察到 Nginx 429/5xx、服务 OOM 或重启。

这不等于整体验收通过。结果生成器按合同以退出码 1 fail closed，原因有且只有以下三项：

1. 生命周期门禁未通过：登记 `181` 个任务，窗口内仅 `93` 个完成完整成功与产物校验；到点仍有
   `88` 个 ImageClip 父批次未完成，必须由 session teardown 取消；
2. 通用 submit P95 门禁未通过：同步返回最终图片的 ModelView Roughness 被纳入异步 admission
   的 `3,000 ms` 门槛，观测值为 `467,000 ms`；
3. 遥测完整性门禁未通过：得到 `378` 个有效五秒样本，理论期望值为 `380`，少两个尾部样本，
   因而 `telemetry.evidence_complete=false`。

因此必须把三个维度分开表达：负载计划已经完成，控制面在该窗口内稳定，但验收门禁仅部分通过。
本轮不能标记为 `FROZEN` 或 `PRODUCTION_ACCEPTED`，也不能替代 B1/B6/B30/B64/B97/B300、
`3×B97`、故障注入和连续七天观察。

## 2. 可审计身份与原始证据

| 项目 | 值 |
| --- | --- |
| 目标 | `https://10.3.34.11` |
| 环境 | `production` |
| 开始 | `2026-07-30T13:11:46.031916Z`（新加坡时间 21:11:46） |
| 结束 | `2026-07-30T13:43:27.271513Z`（新加坡时间 21:43:27） |
| 总历时 | `1,901.239 s` |
| 应用源码 revision | `7656aa68ebde9c95f5a41c52db3f066cae00e249` |
| 数据库 migration | `20260730_0011` |
| 完整恢复点 | `/srv/gpu-control/backups/20260730T122002Z-full`，`VERIFIED_FULL_PRE_WINDOW` |
| 结果目录 | `/tmp/gpu-control-load-results/sixapi-20260730-r5` |
| `summary.json` SHA-256 | `c9359292a07b708e449ce5bd6019027781afd8aff03c2d095404aabbd4505ac0` |
| `manifest.json` SHA-256 | `4af864acc64f46069e1c6937ad5d4f7e2c32b1af1e96a0e8fd1d5d1844f56be2` |
| `checksums.sha256` SHA-256 | `87bbfcf2d65fb876c118f3e3d89f9f7dba0b8f5bed259df1115459e598bb9d72` |

结果目录包含 `plan.json`、`preflight.json`、`records.json`、`summary.json`、
`teardown.json`、`events.jsonl`、`telemetry.jsonl`、Locust CSV/HTML/JSON、配置快照、
`manifest.json` 和 `checksums.sha256`。`events.jsonl` 有 `40,090` 行，
`telemetry.jsonl` 有 `378` 行。manifest 对每个交付文件固定 size 与 SHA-256；
`summary.json` 也明确记录 `secrets_recorded=false`。报告没有收录 API key、Admin token 或其他密钥。

本轮绑定的镜像 digest：

- API：`sha256:762dc15ebc72ba8825906a0716e781f9a8d9ec29f0e81793b820489faba3ec43`；
- Scheduler：`sha256:6abbaa1ed6a9238109dfa2d6f6fb3804804f73366d5944bd3562331511cf206d`；
- Asset API：`sha256:52c8c96e79074b086884afd4b72a10c4fe6a79479f0a6552721a042fdd96aec6`；
- Web：`sha256:80f8651621d2264ce00500180a19fbf6ceaad9887ef4adc44983b67a4341f0bf`。

工作流只做身份校验，没有在本轮修改外部业务工作流：

- `imageclip-rgba`：version `2026.07.30-691770c-r1`，pipeline commit
  `691770cd6a59fd7c51391456fe900dc57a313233`，pipeline SHA-256
  `00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b`，
  output node `SaveImage #25`；
- `modelview-roughness`：version `2026.07.29-d318bb39-roughness-v1`，pipeline commit
  `d318bb392040e2d5f6bbd10ae61d832d36d3cb4a`，pipeline SHA-256
  `8a52740b90ac47e77919b460a0e35241c94d91fde035effb3285600642e2ea38`。

## 3. 负载模型与生产前置门禁

计划使用 12 个隔离 test client/tenant 身份轮转，未把凭据写入结果。六 API 权重为：

| API | 权重 | 资源 |
| --- | ---: | --- |
| `imageclip_batch` | 42% | 三节点 GPU |
| `modelview_roughness` | 23% | GPU，同步响应 |
| `uv_process` | 12% | CPU Asset Worker |
| `retopology_audit` | 8% | CPU Asset Worker |
| `retopology_process` | 10% | CPU Asset Worker |
| `substance_bake` | 5% | 3090-B Windows fenced GPU Asset Worker |

阶段完整执行如下：

| VU | spawn rate | hold |
| ---: | ---: | ---: |
| 1 | 1/s | 60s |
| 10 | 2/s | 120s |
| 25 | 5/s | 180s |
| 50 | 10/s | 300s |
| 100 | 20/s | 600s |
| 120 | 10/s | 600s |

生产预检发生在任何 VU 生成之前，并确认：

- GPU 与 Asset 既有活动任务均为 0；
- 三个 GPU 节点均为 `ONLINE + ACTIVE`、槽位 `0/3` 使用、workflow 身份一致；
- 三个 Linux Asset Worker 和四个 Windows Substance Worker 在线，共 `13/13` 槽空闲；
- 12 个 API 身份全部为 `client_kind=test` 且 admission 可用；
- 固定素材、scenario、CA、生产窗口、变更标识、恢复点和确认令牌全部通过 fail-closed 检查。

在到达可执行的 `resume2` 之前有四次启动尝试被本地门禁拒绝：分别是容器入口错误、只读备份
权限不足、wrapper 依赖路径错误，以及直接恢复时遗漏 scenario/fixture 环境。四次均未开始业务
负载，不计入本报告的 40,021 次请求。该历史用于说明门禁真实生效，不应合并成服务端失败率。

## 4. 六 API 覆盖与任务结果

| API | 已登记 | 窗口内成功 | admission | 产物验证 |
| --- | ---: | ---: | --- | --- |
| `imageclip_batch` | 88 | 0 个父批次 | `88 × 202` | 到点后 88 个进入 session teardown |
| `modelview_roughness` | 20 | 20 | `20 × 200` | 20 个最终图片下载并校验 |
| `uv_process` | 27 | 27 | `27 × 202` | 135 个产物下载并校验 |
| `retopology_audit` | 21 | 21 | `21 × 202` | 42 个诊断产物下载并校验 |
| `retopology_process` | 21 | 21 | `21 × 202` | 每任务 23 个，共 483 个产物下载并校验 |
| `substance_bake` | 4 | 4 | `4 × 202` | 每任务 12 个，共 48 个产物下载并校验 |

已登记任务合计 `181`，完整成功并通过 artifact 合同的任务为 `93`。六 API coverage 的
`missing=[]`、`passed=true`。窗口内没有业务失败终态、retry、recovery 或 artifact contract failure；
但 88 个父批次需要 teardown，所以生命周期总门禁仍是 `passed=false`。

### 4.1 Retopology QA advisory 与正式交付

本轮再次确认“QA 告警不阻断交付”已经落到正式 artifact 合同，而不只是返回日志：

- `retopology_process` 21/21 为 `SUCCEEDED`；
- 每个任务返回 23 个产物，合计 483 次下载均校验响应体与 SHA-256；
- 正式交付集合包含 `blend/retopology_final.blend` 和
  `fbx/retopology_final.fbx`；诊断 JSON、prompt、comparison 等继续保留，但不能代替这两个模型文件；
- advisory 只放宽几何质量判定，文件完整性、SHA、manifest/输入身份和正式 kind 仍为硬门禁。

上线前的单任务证据仍可交叉核对：`retopology_final.blend` 为 `14,798,992 bytes`，SHA-256
`0dd443337087e30bb1fd2929cf6715c82460a1bb13b7c43c745c89a2c0757f6f`；
`retopology_final.fbx` 为 `95,692 bytes`，SHA-256
`8d254b5f3aaea5f13b73b2b2f1bf9b2ed2147e6fac7f6bc0c69f014ab81058f7`。
这组单任务字节身份与 r5 的 21 组 synthetic 任务是两类证据，不能把前者的大小/SHA 套到后者。

## 5. HTTP、排队与端到端指标

### 5.1 控制面 HTTP

| 指标 | 观测值 | 门槛 | 结论 |
| --- | ---: | ---: | --- |
| HTTP requests | 40,021 | — | 完成 |
| HTTP failures | 0 | failure rate ≤1% | 通过，0.00% |
| 聚合 P50 | 12 ms | — | 记录 |
| 聚合 P90 | 25 ms | — | 记录 |
| 聚合 P95 | 36 ms | — | 记录 |
| 聚合 P99 | 150 ms | — | 记录 |
| 最终 Locust 当前 RPS | 28.60 | — | 记录，不等同业务完成吞吐 |
| poll 最差 P95 | 35 ms | ≤1,500 ms | 通过 |
| artifact download 最差 P95 | 39 ms | ≤30,000 ms | 通过 |
| retry rate | 0.00% | ≤5% | 通过 |

按总请求/总历时计算的区间平均约为 `21.05 req/s`。业务完整成功吞吐为
`0.048915 tasks/s`，约 `2.935 tasks/min`；后者只统计完整成功任务，不能用 HTTP poll RPS
替代。

除同步 Roughness 外，其余 submit P95 均远低于 3 秒：ImageClip `1,100 ms`、UV `270 ms`、
Retopology Audit `260 ms`、Retopology Process `260 ms`、Substance `260 ms`。Roughness 的 POST
会等待 GPU 推理并直接返回最终图片，其 submit P95 为 `467,000 ms`、平均
`100,178.854 ms`。这暴露的是压测指标分类与同步 API 合同不匹配；在拆分“请求到最终图片”与
“admission”指标并重跑前，不允许手工豁免 3 秒门禁。

### 5.2 业务排队与端到端

| 指标 | P50 | P90 | P95 | P99 |
| --- | ---: | ---: | ---: | ---: |
| queue | 0.768s | 47.087s | 150.050s | 526.803s |
| completed total | 34.223s | 163.138s | 176.541s | 569.441s |

scenario 的混合过载 queue P95 门槛为 900 秒，本轮通过；但 P90 `47.087s` 不满足动画管家
B97 合同中的排队 P90 ≤5 秒。两者测试目的不同，本轮不能作为 B97 联合速度目标已通过的证据。

## 6. GPU、Asset 槽位与节点分布

遥测每 5 秒采样，`378/378` 样本结构有效，所有预期节点和 worker 都有样本。

| GPU 节点 | 利用率 P50 | P90 | P95 | 最大 | ≥90% 样本占比 | 最低空闲显存 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `control-4090` | 97% | 100% | 100% | 100% | 53.44% | 1,192 MiB |
| `worker-3090-a` | 99% | 100% | 100% | 100% | 65.61% | 2,111 MiB |
| `worker-3090-b` | 90% | 97% | 97% | 99% | 53.44% | 2,079 MiB |

- GPU used slots peak `3/3`，available slots minimum `0`；三节点槽位 P50/P90/P95 均为 100%；
- GPU queue depth peak `499`；
- Asset used slots peak `13/13`，available slots minimum `0`，Asset queue depth peak `9`；
- 三个 Linux Asset Worker 的槽位最大占用均为 100%，四个 Windows Baker 槽也都各自达到 100%；
- 后端没有暴露可信 CPU utilization，因此本报告只声明 CPU/Asset 槽位被打满，不伪造 CPU 百分比。

任务分布：

| 平面 | control/4090 | 3090-A | 3090-B |
| --- | ---: | ---: | ---: |
| Roughness 成功任务 | 8 | 7 | 5 |
| ImageClip batch 节点分配记录 | 54 | 36 | 17 |
| Linux Asset Worker 成功任务 | 24 | 14 | 31 |

四个 Windows Baker Worker 各完成 1 个 Substance 任务。ImageClip 的 `54/36/17` 是
`summary.json` 中的批次节点分配记录，不等同于 teardown 后的成功子帧数；清场数据库最终记录为
`104 SUCCEEDED + 424 CANCELLED` 子帧。

## 7. 网关、控制面与生产让路

在本轮观测窗口和结尾核对中：

- Nginx 业务流量未出现 429 或 5xx；独立 heartbeat/capacity/admin zone 未被业务流量耗尽；
- API、Scheduler、Asset API 没有结构化 error 事件，控制面容器无 OOM、无重启；
- 120 VU 现场快照中，API/Scheduler/Asset API/Nginx/PostgreSQL/Redis CPU 约为
  `19.59% / 13.98% / 2.52% / 1.37% / 47.67% / 0.46%`；
- PostgreSQL 约使用 `27/100` 连接，未发现 blocker 或未授予锁；
- watchdog 全程 `triggered=false`，没有外来真实生产任务需要让路。

这些结论只覆盖本次 31 分钟运行和紧随其后的清场检查。它们不构成七天稳定性观察，也不证明
任意高于本计划的无界并发安全。

## 8. 有界清场与孤儿 Roughness 补清理

Locust 到达固定时限后，88 个已登记但未完成的 ImageClip 父批次由 session-scoped teardown
逐个取消：`88/88` 首次请求即返回 HTTP 200，无 teardown failure。清场后核对：

- 88 个 ImageClip 父批次全部为 `CANCELLED`；
- 子帧 `424 CANCELLED / 104 SUCCEEDED`；
- active lease 为 0；
- GPU active、Asset active、GPU queue/running 均为 0；
- 三个 GPU 槽位 `3/3` 空闲，Asset 槽位 `13/13` 空闲。

此外发现 21 个本轮 test tenant 的 Roughness 服务端任务没有进入内存 session registry。原因是该
接口同步等待最终图片，Locust 强制结束正在等待的请求时还没有收到 `X-Job-ID`。首次管理面核对为
`18 QUEUED + 3 RUNNING`；执行定向补清理时 7 个已自然完成。补清理脚本只匹配：

- `client_kind=test`；
- 本轮精确 tenant allowlist；
- `workflow=modelview-roughness`；
- `created_at >= 2026-07-30T13:11:46Z`。

剩余 `14/14` 个任务取消均返回 HTTP 200。没有按时间范围、全租户或全队列执行广泛取消，也没有
触碰真实生产任务。这个缺口必须在 harness 中修复：同步任务 ID 应在请求中断时仍可恢复，或采用
服务端 session/external ID 查询补录；不能把人工定向补清理当成长期正常流程。

## 9. 本轮发现的稳定性/公平性问题

### 9.1 Substance 在连续 GPU 队列下可能饥饿

四个 Substance 任务最终全部成功并各交付 12 个已验证产物，但排队时间分别为：

- `526.803s`；
- `353.185s`；
- `47.087s`；
- `47.955s`。

根因是 Windows Baker 只有在 claim 后才把物理 `worker-3090-b` 置为 DRAINING；当 GPU 队列持续
有任务时，ComfyUI 可在 Baker claim 前连续占用同一张卡。四个 Windows worker 本身健康，最终均
领取并完成任务，因此这不是 worker 离线或产物失败，而是物理 GPU fence 的待处理公平性缺口。

源码工作区在 r5 当时已有“生产 Substance pending reservation + 到期释放”的候选修复，并坚持真实
生产优先，test 流量不能覆盖 production reservation。该候选在 r5 时不属于当轮镜像；此后已完成
全量测试并随 1.5.6 的 `310a44c` 镜像部署，当前事实见 74、75 号记录。Asset queue ETA 排除 stale
worker 的修复也随同部署。

### 9.2 Scheduler advisory lock 留下 idle transaction

压测高峰的只读数据库诊断发现一个来自 Scheduler 的连接处于 `idle in transaction`，持续约
`2,552s`，并保留 `backend_xmin`。当时没有 blocking PID 或 ungranted lock，未影响本轮请求，
但长期持有会拖住 vacuum horizon。代码路径指向 Scheduler 为持有 session advisory lock 而长期
保留的 `AsyncSession`。

修复必须在保持“全局只允许一个 Scheduler”语义的前提下，把 advisory lock 放到专用 autocommit
连接或等价的可证明生命周期中；不能简单 commit 后把 session-level lock 所在连接交回池中。
相关修复在 r5 时仍处于源码审查/离线测试，不属于 r5 稳定结论；此后专用 AUTOCOMMIT 锁连接、
leader epoch 和旧 leader 写入 fence 已完成真实 PostgreSQL 回归并随 1.5.6 部署。

### 9.3 压测 harness 的两个证据缺口

- 同步 Roughness 被通用 async submit P95 门禁误分类，并在强停时可能留下未登记服务端任务；
- 理论 380 个遥测样本只落盘 378 个，尾部采样与结果汇总的关闭顺序需要收敛。

这些缺口在 r5 时尚未进入生产。后续 harness 已拆分 async submit 与 sync end-to-end 指标、增加作用域
恢复并改为基于观测窗口的显式最终采样；r7 结果见第 12 节。任何复测仍必须 fail closed，不能通过
调大门槛、忽略孤儿任务或伪写遥测来获得绿色结果。

## 10. 验收矩阵

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| 全部六个 API 有真实服务端任务 | PASS | `missing=[]` |
| 达到 120 VU 且执行完整 stage | PASS | 1,901.239s |
| HTTP failure rate ≤1% | PASS | 0/40,021 |
| poll P95 ≤1.5s | PASS | 35ms |
| artifact P95 ≤30s | PASS | 39ms |
| queue P95 ≤900s | PASS | 150.050s |
| retry rate ≤5% | PASS | 0% |
| 三张 GPU 均达到 ≥90% | PASS | 100% / 100% / 99% peak |
| GPU 与 Asset 槽位达到满载 | PASS | 3/3、13/13 |
| Retopology 正式 FBX/BLEND 可交付 | PASS | 21/21，483 个产物下载校验 |
| 所有登记任务在窗口内成功并有产物 | FAIL CLOSED | 93/181；88 teardown |
| 通用 submit P95 ≤3s | FAIL CLOSED | 467,000ms；同步 Roughness 口径不匹配 |
| 遥测样本完整 | FAIL CLOSED | 378/380 |
| 压测后任务、lease、槽位清零 | PASS | 含 14 个孤儿 Roughness 定向补清理 |
| B97 三节点速度合同 | NOT TESTED | 本轮不是固定 B97 A/B |
| 故障注入与七天观察 | NOT TESTED | 后续联合验收 |

## 11. 后续动作与发布边界

以下是 r5 时点提出的后续动作；其中控制面修复和 r7 复测已有进展，最终状态以第 12 节及 74、75 号
记录为准：

1. 把当前 `/tmp` 结果目录按 `checksums.sha256` 复核后归档到受控、持久存储；在完成前不要删除
   原始目录；
2. 完成同步 Roughness registry/指标分类和尾部遥测关闭顺序修复，补齐离线单测；
3. 完成 Substance pending reservation、stale worker ETA 与 Scheduler advisory lock 修复的全量
   测试；只在零活动任务窗口构建新镜像、记录 digest 并受控部署；
4. 重跑相同 r5 scenario，要求三项 fail-closed 门禁全部关闭且清场无需人工补录；
5. 再执行动画管家固定 B1/B6/B30/B64/B97/B300、1/2/3 节点 A/B、`3×B97` 和故障注入；
6. 通过灰度和连续七天观察后，双方才可共同决定是否签署 `FROZEN / PRODUCTION_ACCEPTED`。

本报告不授权修改 ImageClip、ModelViewCreator、Retopology Skill 的 workflow JSON、模型、prompt、
图拓扑、采样参数或输出语义。所有未部署候选修复必须继续留在 GPU Control 所有权边界内。

## 12. 1.5.6 上的 r7 复测

### 12.1 结果边界

会话 `sixapi-20260730-r7` 在生产 1.5.6 控制面上完成同样的
`1 → 10 → 25 → 50 → 100 → 120 VU` 有界升压。结果生成器仍以退出码 1 fail closed，但这次业务
阈值、六 API 覆盖、生命周期、生产让路、作用域恢复和清场全部通过；唯一未通过项是遥测相邻样本
最大间隔 `7,699 ms`，比 `7,500 ms` 的严格上限多 `199 ms`。因此准确状态是
`LOAD COMPLETED / BUSINESS GATES PASS / TELEMETRY GAP FAIL CLOSED / DEPLOYED_NOT_ACCEPTED`，不能
写成整体验收通过。

### 12.2 核心结果

| 指标 | r7 结果 | 门禁 |
| --- | ---: | --- |
| HTTP 请求 / 失败 | `39,776 / 0` | PASS |
| HTTP P50 / P95 / P99 | `11 / 37 / 170 ms` | 记录 |
| 已登记 / 已验证成功任务 | `184 / 99` | 有界生命周期 PASS |
| 六 API 登记数 | ImageClip `85`、Roughness `13`、UV `29`、Retopo Audit `15`、Retopo Process `25`、Substance `17` | coverage PASS |
| async submit P95 | `1,400 ms` | `≤3,000 ms`，PASS |
| Roughness sync E2E P95 | `354,000 ms` | `≤600,000 ms`，PASS |
| poll / artifact P95 | `42 / 41 ms` | PASS |
| queue P95 | `548,885 ms` | `≤900,000 ms`，PASS |
| failure / retry rate | `0% / 0%` | PASS |
| teardown | `120/120` accepted、`120/120` settled；作用域恢复 `35` 个未登记任务 | PASS |
| production watchdog | `triggered=false` | PASS |

r7 共有 `184` 个任务进入服务端，其中 `99` 个在窗口内完成并通过产物合同；剩余任务全部被限定在
本轮精确 test tenant、开始时间和业务身份范围内恢复并安全清场。因此有界压力模式的生命周期判定为
通过，不再把“所有任务必须在固定加压窗口内自然完成”误作唯一成功条件，也没有广泛取消其他租户。

### 12.3 资源饱和与稳定性

- 三张 GPU 峰值均为 `100%`；利用率 P50/P95 分别为 4090 `98%/100%`、3090-A
  `99%/100%`、3090-B `42%/97%`，三节点饱和目标通过；
- GPU 槽位峰值 `3/3`、队列峰值 `485`；Asset 槽位峰值 `13/13`、队列峰值 `20`；
- Substance fence/recovery 在真实负载中两次使 3090-B 进入受控 DRAINING/OFFLINE 观察状态，并在
  Windows 物理 GPU 使用结束后自动恢复 ONLINE/ACTIVE；没有手工解锁，也没有跨平面并发占卡；
- 生产 watchdog 未发现外来任务；整轮无 HTTP 失败、业务失败、retry 或 recovery 失败；清场后
  GPU/Asset 活动任务、租约和队列均为 0。

### 12.4 遥测 199 ms 边界偏差

`telemetry.jsonl` 有 `378` 个有效样本，序号连续、显式 final sample 存在、全部 GPU/Worker 资源样本
齐全且无无效样本。唯一失败发生在第 377 个正常样本与第 378 个 final sample 之间：最大间隔
`7,699 ms`，超过允许的 `7,500 ms` 共 `199 ms`；其他正常采样间隔最大约 `5,004 ms`。根因是停止
流程先终止正在进行的采样，再写 final sample，而不是服务端稳定性或监控数据缺失。

源码提交 `682b2c3` 已让停止监听器先等待当前采样结束，再在超出有界 grace 时兜底终止，并补充 AST
回归；相关 load harness 测试为 `42 passed`。该修改是 r7 后产生的代码证据，不得将 r7 原始
`sampling_evidence.passed=false` 改写为 true；必须由后续独立运行生成新的机器报告。

### 12.5 原始证据

| 项目 | 值 |
| --- | --- |
| 开始 / 结束 | `2026-07-30T15:40:38.286046Z` / `2026-07-30T16:13:36.346554Z` |
| 结果目录 | `/tmp/gpu-control-load-results/sixapi-20260730-r7` |
| `summary.json` SHA-256 | `9a26cdb1ce537ca4704817160d9b997caffdc86679c56871e18646152e0af1e6` |
| `manifest.json` SHA-256 | `18183af989406ea4d07ca9f02fe720e873e5ef05551bd22eff586f80feaba222` |
| `checksums.sha256` SHA-256 | `3cacc4832ac33f87d155133df4dd592ddacf6e8bdb75548b3d60b1a70a50d69b` |

全目录已按 `checksums.sha256` 复核。原始报告保留 r7 的非零退出与遥测失败，不通过编辑 Markdown
掩盖。该轮仍不能替代固定 B1/B6/B30/B64/B97/B300、1/2/3 节点同素材 A/B、`3×B97`、完整故障
注入和连续七天观察。

## 13. R8 独立复测：机器门禁全部通过

会话 `sixapi-20260730-r8` 在同一生产 1.5.6 控制面重新完成最高 120 VU 的六 API 有界压力，执行
进程退出码为 `0`。本轮 `39,778` 个 HTTP 请求、失败 0，登记/验证成功任务 `151/66`；六 API
coverage、七项阈值、生产 watchdog、有界生命周期、作用域恢复、三卡饱和与遥测证据均通过，
teardown 为 `120/120 accepted`、`120/120 settled`。

R8 共生成 `379` 个有效、0 个无效遥测样本，sequence 连续且包含显式 final sample；相邻样本最大
间隔 `5,004 ms`，低于严格上限 `7,500 ms`。这是一份新的独立机器结果，关闭了 R7 的尾部采样门禁，
但不会反向修改本文件第 12 节保留的 R7 退出码 1 与 `7,699 ms` 历史事实。

R8 持久结果目录为 `/srv/gpu-control/load-results/sixapi-20260730-r8`；`summary.json` SHA-256 为
`1463154198a005415d285f7567d2382b6922d17e873a6c95010eac704e4bcf57`，`manifest.json` 为
`ada21d3c083aefb080ab269bff6523f2ea949c6893adb08e439754547beb2ea0`，外层
`checksums.sha256` 为
`c5ae4401befecdef85702f076a24c9e902a7543d826c3c6ff62b980b425380de`。完整阈值、硬件、Substance
fence/recovery、Scheduler lag 观测边界和全部关键 SHA 见
`76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md`。

R8 的 `BOUNDED_STRESS_ACCEPTED` 仍不等于固定 B97、完整故障矩阵或七天生产验收，总体发布状态保持
`DEPLOYED_NOT_ACCEPTED`。
