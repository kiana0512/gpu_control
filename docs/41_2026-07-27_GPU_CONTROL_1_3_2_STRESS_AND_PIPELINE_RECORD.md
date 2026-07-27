# GPU Control 1.3.2 → 1.3.3 三节点管线修复与真实压力记录

日期：2026-07-27（Asia/Singapore）
状态：稳定；60 单持续压力、1.3.3 进度修复、6/30 帧批量与高压后健康复核全部通过；全部档位均为真实 API、真实 ComfyUI、真实 GPU 推理
主交接：[40_GPU_CONTROL_MATTING_HANDOFF_V3.md](40_GPU_CONTROL_MATTING_HANDOFF_V3.md)

## 1. 固定环境

| 节点 | 地址 | 身份 | GPU | ImageClip commit | pipeline SHA |
|---|---|---|---|---|---|
| control-4090 | 10.3.34.11 | OVERFLOW/主控 | RTX 4090 | `721f7d68635ee36d45f545ce2c82037046147442` | `00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b` |
| worker-3090-a | 10.3.34.12 | PRIMARY | RTX 3090 | 同上 | 同上 |
| worker-3090-b | 10.3.34.4（MAC 固定身份） | PRIMARY | RTX 3090 | 同上 | 同上 |

- GPU Control：`1.3.3`（60 单压力基线为 1.3.2；批量进度热修复为 1.3.3）
- ComfyUI 镜像：`registry.local:5000/gpu-control/comfyui:projects-0.2.2`
- ImageClip 工作流：`2026.07.27-721f7d6-r1`
- ImageClip registry template SHA：
  `63e56d99bc125156016c544f26679406c84b3640123a8cec0ae762eb598c485c`
- ModelView 工作流：`2026.07.23-b22bb377`
- 三节点单卡并发均为 1；测试客户与生产客户独立归档，生产始终优先。

控制平面镜像：

| 镜像 | Image ID |
|---|---|
| `gpu-control-api:1.3.3` | `sha256:0d561fe9788557160f1997f6ada874e91dccd20370ed08ccd89c353393f19e6b` |
| `gpu-control-scheduler:1.3.3` | `sha256:689b1d990d25ac5197e25ba353fa597162db4caddf5803c4a5adeb5675f4096c` |
| `gpu-control-web:1.3.3` | `sha256:8fa91a5a593872fe3f108beba44958923e08c01237b56bcbacb070379b0566ee` |

## 2. 压测前发现并关闭的问题

### 2.1 ImageClip UI workflow 转 API 参数错位

首轮 3 个真实 ImageClip 请求均被 ComfyUI 400 拒绝。精确错误显示 KSampler 的
`sampler_name/scheduler/denoise/steps` 整体错位。根因是 UI workflow 在 seed 和
PrimitiveInt 后额外序列化 `control_after_generate` 值（例如 `fixed`），旧生成器把它当成
下一个业务输入。

修复：生成器读取实时 `/object_info`，识别布尔或字符串形式的
`control_after_generate`，消费但不写入 API prompt；同时拒绝未映射 widget 值。修复后的
prompt 在 3090-B 直接 `/prompt` 校验为 200，并完成真实推理。旧不可变版本停用，发布 `r1`。

### 2.2 ModelView Nunchaku 节点导入失败

10 单 7:3 首轮中，7 个 ImageClip 成功，3 个 ModelView 全部 HTTP 400。ComfyUI 返回
`missing_node_type: NunchakuFluxDiTLoader`。插件和模型实际都在镜像/宿主机中；失败原因是
`/opt/comfyui/models` 只读挂载，而插件初始化时尝试创建缺失的
`pulid/insightface/facexlib/ipadapter` 目录，导致整个 ComfyUI-nunchaku import 失败。

修复：三台宿主机预创建插件枚举出的空目录（并确认 `clip`），不修改任何模型文件；修复已
固化到 `bootstrap_common_ubuntu.sh` 和 `sync_models.sh`。三台重启后
`NunchakuFluxDiTLoader` 均存在，ModelView 模板所有 class type 与三台实时
`/object_info` 比较均为 PASS。

### 2.3 工作流兼容性以前未核对实际 class inventory

历史兼容表只检查显存与标签，因此插件启动失败后节点仍可能显示 compatible。1.3.2 Scheduler
现在每 60 秒读取每台 ComfyUI 实时 `/object_info`，仅保存生产工作流涉及的 class inventory，
并逐节点/逐版本刷新兼容表。inventory 不可用、任一 class 缺失、显存不足或管线标签不一致均
fail closed。部署后两个启用工作流 × 三节点共 6 条兼容记录全部 `compatible=true/reasons=[]`。

### 2.4 压测统计误判

Nginx 会重建外部 `X-Request-ID`，旧压测器按请求 ID 前缀查询时出现“已接受但 observed=0”。
现改为只按 API 返回的精确 job ID 集合跟踪。压测成功条件也从“已接受并终态”收紧为：全部
`SUCCEEDED`、产物数等于请求数、逐文件 SHA/大小/PNG 解码通过，ImageClip 还必须含 Alpha；
任一任务失败都使整轮退出非零。

## 3. 已完成真实结果

### 3.1 ImageClip 三节点烟测

- 请求：3 个真实 ImageClip；并发提交 3。
- 分配：4090=1、3090-A=1、3090-B=1。
- 结果：3/3 `SUCCEEDED`，全部首尝试；3/3 最终 RGBA 校验通过。
- 报告：`smoke-r1-3-imageclip.json`。

### 3.2 ModelView 修复后三节点烟测

- 请求：3 个真实 ModelView；并发提交 3。
- 分配：4090=1、3090-A=1、3090-B=1。
- 结果：3/3 `SUCCEEDED`，产物校验 0 失败。
- 报告：`modelview-fix-smoke3.json`。

### 3.3 10 用户、10 单、ImageClip:ModelView=7:3

| 指标 | 实测 |
|---|---:|
| 接受/成功/首尝试 | 10/10/10 |
| ImageClip / ModelView | 7 / 3 |
| 4090 / 3090-A / 3090-B | 4 / 2 / 4 |
| 提交 P50 / P95 | 0.451s / 0.952s |
| 排队 P50 / P95 / max | 9.083s / 102.146s / 102.146s |
| 总耗时 P50 / P95 / max | 91.591s / 146.636s / 146.636s |
| ImageClip 执行 P50 / P95 | 44.490s / 91.515s |
| ModelView 执行 P50 / P95 | 7.200s / 9.250s |
| 产物校验 | 10/10，0 失败 |

报告：`mixed-r1-10-7to3-rerun.json`。

### 3.4 10 用户、30 单、ImageClip:ModelView=7:3

| 指标 | 实测 |
|---|---:|
| 持续时间 | 362.140s |
| 接受/成功/首尝试 | 30/30/30 |
| ImageClip / ModelView | 21 / 9 |
| 4090 / 3090-A / 3090-B | 13 / 9 / 8 |
| HTTP 202 / 限流重试 | 30 / 0 |
| 提交 P50 / P95 | 0.777s / 1.008s |
| 最大返回 queue_position | 27 |
| 排队 P50 / P95 / max | 169.375s / 326.955s / 340.833s |
| 总耗时 P50 / P95 / max | 197.989s / 352.186s / 358.903s |
| ImageClip 执行 P50 / P95 / max | 42.965s / 86.786s / 91.757s |
| ModelView 执行 P50 / P95 / max | 11.156s / 18.070s / 18.070s |
| 任务错误 / 产物错误 | 0 / 0 |

报告：`mixed-132-30-7to3.json`。

### 3.5 高压期间生产优先级验收

当仍有 23 个 test job 排队时，经公共同步图片 API 提交一个 ModelView 请求。系统自动识别
客户 `client_kind=production`，下一空闲槽立即分配到 3090-A：

- job：`d717330a-4345-49a4-94bd-97af65793ba8`
- 排队：12.292s；执行：7.971s；HTTP 总耗时：21.123s；HTTP 200。
- 返回：768×768 RGB PNG，11609 bytes。
- SHA-256：`f240ea5d06e0111a1b1881792b8385bc89298375d749e6fed274dcd3e1f97722`。
- test 队列没有被误归入 production，生产请求也没有等待测试队列清空。

1.3.3 的 30 帧 test 批次占满三卡时再次提交无 API Key 生产请求，结果一致：

- job：`80a5adb6-6dc4-4580-9445-feb29e8c2069`
- 自动识别：`client_kind=production`；分配：`control-4090`；首尝试成功。
- 排队：7.179s；执行：13.107s；HTTP 总耗时：21.129s；HTTP 200。
- 返回：768×768 RGB PNG，11,609 bytes。
- SHA-256：`6b8bf9328f8098d6d6dab32f29c103cd929b2eaf299421f57de9fb959da80062`。

## 4. 自动化回归

- Python pytest：76 passed。
- Ruff：通过。
- strict mypy：23 source files，无问题。
- Web lint：通过；Prettier：通过；Vitest：3 passed；生产构建：通过。
- `npm audit --omit=dev`：0 vulnerabilities。构建日志中的 16 high 均不在生产依赖集合中。

## 5. 20 用户、60 单持续高压（ImageClip:ModelView=7:3）

本轮通过 20 个独立 `client_kind=test` 客户，以 30 并发、目标 30 RPS 瞬时提交 60 个真实任务；
工作流顺序使用固定随机种子打散。所有请求经过生产 Nginx、API、PostgreSQL、Scheduler 和真实
ComfyUI/GPU 推理，没有绕过任何控制面组件。

| 指标 | 实测 |
|---|---:|
| 持续时间 | 741.807s |
| 接受/成功/首尝试 | 60/60/60 |
| ImageClip / ModelView | 42 / 18 |
| 4090 / 3090-A / 3090-B | 25 / 19 / 16 |
| 最终 HTTP 202 / 提交失败 | 60 / 0 |
| Nginx 429 后自动退避重试 | 39 次，最终无损接收 |
| 提交 P50 / P95 | 1.343s / 4.104s |
| 最大返回 queue_position | 57 |
| 排队 P50 / P95 / max | 319.648s / 676.321s / 689.824s |
| 总耗时 P50 / P95 / max | 362.638s / 688.823s / 734.835s |
| ImageClip 执行 P50 / P95 / max | 42.991s / 90.183s / 90.785s |
| ModelView 执行 P50 / P95 / max | 12.106s / 19.525s / 19.525s |
| 任务错误 / 产物错误 | 0 / 0 |
| 生产队列采样 | 全程 0；test 与 production 未混淆 |

工作流切换成本（同一节点本轮平均执行时间）：

| 节点 | 未切换 | 切换后 |
|---|---:|---:|
| 4090 | 17 单 / 23.333s | 8 单 / 37.292s |
| 3090-A | 14 单 / 30.294s | 5 单 / 61.271s |
| 3090-B | 9 单 / 37.548s | 7 单 / 56.827s |

这说明缓存亲和策略有效但仍有可量化的切换成本；系统在切换时先释放上一工作流显存，因此没有
OOM、错误产物或错误复用。限流重试来自单一压测源地址的突发提交，客户端指数退避后全部收到
唯一 job ID，数据库没有重复任务。

报告：`mixed-132-60-7to3.json`。

## 6. 批量序列帧验收

首个真实 6 帧批次暴露了父任务进度回退：ComfyUI 在内部节点切换时会从新的局部进度重新计数，
旧 Scheduler 直接覆盖子任务进度，导致父任务从 49.5% 降回 38.6%。推理和结果没有出错，但
这会误导 API 调用方。1.3.3 将子任务进度与父任务聚合进度都改为单调值，并在验收器中把
“任意一次进度下降”设为整轮失败条件。

1.3.3 修复后 6 帧复验：

| 指标 | 实测 |
|---|---:|
| batch ID | `8a04626d-6067-4089-bc71-f8fbce05b51b` |
| 创建 / 幂等重放 | HTTP 202 / HTTP 200，同一 batch ID |
| 状态 / 持续时间 | `SUCCEEDED` / 90.567s |
| 进度采样 | 31 次，全程单调 |
| 4090 / 3090-A / 3090-B | 2 / 2 / 2 帧 |
| 校验帧 | 6/6 |
| 结果 ZIP 大小 | 3,029,028 bytes |
| 结果 ZIP SHA-256 | `894da1e734af68e3ca3eadac1a0f92de5992f316c575fb4386101040eec7b976` |

逐帧强制校验：结果 ZIP 文件集合恰好等于 `manifest.json + results/...`，ordinal 连续，输入
相对路径和 SHA 与请求一致，输出保留原目录和 stem，输出 SHA 与 manifest 一致，全部可解码
为带 Alpha 的 PNG。报告：`batch-133-6-monotonic.json`。

1.3.3 真实 30 帧单父任务档位：

| 指标 | 实测 |
|---|---:|
| batch ID | `f5aae773-abb3-4b58-8cf4-78d6bab306c2` |
| 创建 / 幂等重放 | HTTP 202 / HTTP 200，同一 batch ID |
| 状态 / 持续时间 | `SUCCEEDED` / 391.823s |
| 进度采样 | 79 次，全程单调 |
| 4090 / 3090-A / 3090-B | 13 / 9 / 8 帧 |
| 校验帧 | 30/30 |
| 结果 ZIP 大小 | 15,026,388 bytes |
| 结果 ZIP SHA-256 | `2c04e9131f25819135ebda57ddd9c8e32b05c14430d429e4d95935706fcfa087` |

批次物化使用有界窗口；采样中最多同时 3 个运行、约 9 个内部排队，其余保持 pending，避免单个
超大批次一次灌满全局队列。数据库最终只有 1 条 `job_batches` 父记录和 30 条带 `batch_id` 的
内部 job；`/admin/jobs` 明确排除 `Job.batch_id IS NOT NULL`，再合并父记录，因此任务列表只显示
一行，30 帧只在详情接口展开。报告：`batch-133-30-monotonic.json`。

## 7. 高压后健康与三节点一致性

- API、Scheduler、Web 为 `1.3.3` 且 healthy；PostgreSQL、Redis、Nginx、Prometheus、
  Grafana、Loki、Alertmanager 均正常。
- 三台 ComfyUI `/system_stats` 均 HTTP 200；三节点 `ONLINE / ACTIVE / current_jobs=0`，无活动
  队列，GPU 利用率回落到 0%。
- 4090 重新 fetch 后 `HEAD == origin/main == 721f7d68635ee36d45f545ce2c82037046147442`；
  3090-A/B HEAD 相同。
- 三台签名心跳的 ImageClip pipeline SHA 均为
  `00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b`。
- 当前工作树已通过 `sync_node_source.sh` 同步到 A/B，关键文件指纹均为
  `65634b60030c3c8f2389019a5831a353535514750ce93c1020d2a11f5acb7e82`。
- 三台 `gpu-node-agent` 均升级至 `1.3.3`，systemd active，`/health/ready` 全部通过。
- 1.3.3 部署后 API/Scheduler 无 warning、error、Traceback 或 Exception。

## 8. 1.3.3 镜像归档

- 归档：`/srv/gpu-control/images/gpu-control-control-plane-1.3.3.tar.gz`
- 内容：`gpu-control-api:1.3.3`、`gpu-control-scheduler:1.3.3`、`gpu-control-web:1.3.3`
- 大小：149,808,385 bytes
- SHA-256：`56de4da49b5db7e41b85686980bdec4af6c959d1ef49bcc073956c31accea729`
- 恢复：`gzip -dc gpu-control-control-plane-1.3.3.tar.gz | docker load`

归档不包含 `.env`、数据库、任务、模型或证书。ComfyUI 仍使用已验收的
`registry.local:5000/gpu-control/comfyui:projects-0.2.2`，没有重复封装模型层。
