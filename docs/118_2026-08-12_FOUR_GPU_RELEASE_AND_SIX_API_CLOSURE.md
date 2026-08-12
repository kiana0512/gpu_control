# GPU Control 1.5.13 四 GPU 发布、审计与六 API 收口报告

日期：2026-08-12（Asia/Singapore）

分支：`agent/four-gpu-4070ti-closure`

目标版本：GPU Control `1.5.13`、Blender Worker `1.4.48`
结论口径：五个 API 已有真实成功证据；Substance 烘焙因 Windows Agent 仍为 v6，被 v7 安全门禁阻止。因此本报告不得写成“六 API 全部生产接受”。

## 1. 结论摘要

本轮已将 4070Ti WSL2 纳入现有 4090、3090-A、3090-B 架构，并完成以下闭环：

- 四台 GPU 节点均可被控制面识别为 `ONLINE / ACTIVE`，各保留一个 GPU 槽位；
- 4070Ti 的 WSL2 指标不再依赖容器内 NVML，使用带 HMAC 的宿主代理持续上报真实利用率、显存、温度和功率；
- ImageClip 抠图、ModelView 局部重绘、PBR 粗糙度对齐到批准的生产工作流版本；
- 四台 Linux/WSL Blender Worker 对齐到 `asset-skills-auto-retopo-align-v3.0.25`；
- UV 和自动拓扑 CPU 任务仍与 GPU 保护窗口相互独立；
- 4070Ti 负责局部重绘响应优先，3090-B 保留唯一 Substance 烘焙能力；两个 15 分钟窗口都按“有受保护任务才续期、硬过期后恢复共享”执行；
- WebUI 已显示四台设备、四主机 Codex 探针、专用 GPU 策略、真实 WSL 指标和 CPU/GPU 槽位关系；
- 修复拓扑审计三处控制面漂移：当前 Worker 被错误排除、固定脚本 SHA 过期、Worker/API 仍使用旧审计参数/schema；
- 全量前端回归、Python 回归、静态检查和真实四节点 canary 证据见后文。

唯一未闭环项是 3090-B Windows 原生 Substance Agent。四个实例心跳新鲜，但运行版本是
`substance-baker-2026.08.03-v6`，控制面要求 `substance-baker-2026.08.12-v7`，所以它们被正确投影为
`DRAINING`。这是安全阻断，不是调度器卡死。

## 2. 四节点生产拓扑

| 物理节点 | Node ID | 地址/系统 | GPU | GPU 槽 | CPU Asset 槽 | 角色 |
|---|---|---|---:|---:|---:|---|
| 4090 控制中心 | `control-4090` | `10.3.34.11` / Linux | RTX 4090 24 GB | 1 | 2 | 控制面、普通共享 GPU、Overflow |
| 3090-A | `worker-3090-a` | `10.3.34.12` / Linux | RTX 3090 24 GB | 1 | 3 | 普通共享 GPU、CPU Asset |
| 3090-B | `worker-3090-b` | `10.3.34.14` / Windows + WSL2 | RTX 3090 24 GB | 1 | 4 | 普通共享 GPU、唯一 Windows Substance Baker、CPU Asset |
| 4070Ti | `worker-4070ti-animation-host-01` | `10.3.34.238` / Windows + WSL2 | RTX 4070 Ti 12 GB | 1 | 2 | 普通共享 GPU、局部重绘响应优先、CPU Asset |

生产数据流保持不变：客户端只访问 `https://10.3.34.11`；API/PostgreSQL 是任务真相来源，Scheduler
只给健康、兼容、可调度的物理 GPU 分配任务；Asset API 独立管理 CPU Worker 和 Windows Baker。
WSL NAT 地址不能成为节点身份，3090-B 和 4070Ti 均以固定 Windows 局域网地址、MAC、GPU UUID 和
Node ID 作为物理身份。

## 3. 固定调度规则

### 3.1 空闲共享

- ImageClip 抠图：4090、3090-A、3090-B、4070Ti 均可参与；
- ModelView 局部重绘：四台兼容 GPU 均可参与；
- PBR 粗糙度：四台兼容且有足够显存的 GPU 均可参与；
- 拆 UV、自动拓扑和拓扑审计：走独立 CPU Asset 槽，不被 GPU 专用窗口冻结；
- Substance PBR 烘焙：只能由 3090-B Windows 原生 Agent 执行。

### 3.2 4070Ti 局部重绘优先

局部重绘任务到达时刷新 4070Ti 的 15 分钟 `modelview-inpaint` 保护窗口。若 4070Ti 正在执行
ImageClip 帧，则只中断并重新排队该帧，其他节点可重新领取；之后先等待队列排空并调用 ComfyUI
缓存释放，再加载局部重绘模型。这个规则不修改 ImageClip 或 ModelView 工作流。

窗口不是固定休眠：没有新的局部重绘任务时，硬过期后 4070Ti 立即恢复 ImageClip、粗糙度和普通
局部重绘共享调度。WebUI 会把过期标签视为无效，不会继续显示“保护中”。

### 3.3 3090-B 烘焙优先

生产烘焙任务到达后，3090-B 不再领取新 ImageClip 帧；已经开始的 ImageClip 帧必须自然完成，随后
排空 ComfyUI、释放模型缓存，再把物理 GPU 交给 Windows Baker。新的烘焙会刷新 15 分钟窗口；没有
受保护任务且到达硬过期时间后，3090-B 恢复普通 GPU 调度。

不得为了烘焙抢占而杀死当前 ImageClip 帧，也不得让过期标签永久阻止普通任务。CPU Asset Worker
始终独立于这一物理 GPU 围栏。

### 3.4 OOM 防护

- 每台物理 GPU 固定一个受控 GPU 槽；
- 工作流家族切换必须先排空 ComfyUI 队列，再执行 `/free` 并轮询权威 VRAM 计数；
- 释放超时或 VRAM 恢复失败时 fail-closed，不加载下一模型家族；
- 4070Ti 的生产工作流最低显存合同统一为 12 GB，不通过改采样、分辨率、模型或输出节点来规避显存；
- 预览或中间节点不能代替批准的最终输出节点。

## 4. 外部工作流对齐（只读同步）

GPU Control 没有修改 ImageClip 或 ModelViewCreator 的提示词、参数、图结构、模型语义或后处理。
这里只部署并验证用户批准的精确版本。

| API | 版本 | Git/Pipeline SHA | Template SHA-256 | 最终输出 | 最低 VRAM |
|---|---|---|---|---:|---:|
| ImageClip RGBA | `2026.08.12-c39ed0b-fp8-r1` | commit `c39ed0b3b637f0a1435bbe10e5a3acf6bfca07bd`; pipeline `07928d57852ed56ed37527960ec9955d867c0090456fda687fbcd12fecf1775c` | `ce4d23c39363d489e48a32a20295bf83837829651f10e46a537de635031c6846` | `SaveImage #102` | 12 GB |
| ModelView 局部重绘 | `2026.08.12-8c37f07-seedvr2-12g-r1` | `8c37f07` | `df13ca08…ccd` | node `9` | 12 GB |
| PBR 粗糙度 | `2026.08.12-d318bb39-roughness-12g-r1` | commit `d318bb39`; pipeline `8a527…` | `bbc80…` | node `355` | 12 GB |

ImageClip 当前使用批准的 FP8 diffusion model：
`diffusion_models/flux-2-klein-9b-fp8.safetensors`。四台节点的工作流兼容性检查均为 true；工作流中
使用的 class inventory 由 Node Agent 现场读取，而不是 WebUI 硬编码。

## 5. 4070Ti WSL2 指标与在线状态

WSL2 的 Docker 容器在 GPU 忙碌时无法稳定通过 NVML 返回温度和功率；将其直接作为心跳健康依据会
造成“任务一运行，WebUI 就离线”。本轮增加：

- `scripts/wsl-node-agent-proxy.py`：校验控制面 HMAC 请求并把操作转发到容器 Node Agent；
- `apps/node_agent/systemd/gpu-node-agent-wsl-user.service`：WSL 用户级自启动模板；
- 9202 为容器后端，9201 为宿主代理统一入口；
- 指标由 WSL 可用的 `/usr/lib/wsl/lib/nvidia-smi` 采集，Node Agent 心跳与指标探针彼此独立；
- 指标暂时缺失不会把正在工作的节点投影为离线。

现场已观察 4070Ti 在 GPU 负载下真实利用率、显存、温度、功率变化，并确认 WebUI 保持
`ONLINE`。当前空闲快照为约 12 GB 总显存、34°C、15.9 W；这是时点数据，不是性能上限。

正式 Node Agent `1.5.13` 滚动后再次通过带签名的 `/v1/gpu-metrics` 实测：利用率 0%、空闲显存
9678 MiB / 总显存 12282 MiB、34°C、9.9 W、功率上限 285 W；代理 `/health/ready` 返回 ready，
容器 `RestartCount=0`。温度和功率来自 WSL 宿主 `nvidia-smi`，不是 WebUI 估算值。

Windows/WSL 重启后的持久化仍需要 IT 在 4070Ti 上执行一次：

```powershell
wsl.exe -d Ubuntu -u root -- loginctl enable-linger gpucontrol
```

随后应验证 `loginctl show-user gpucontrol -p Linger` 返回 `Linger=yes`。

## 6. 真实功能证据

### 6.1 ImageClip 抠图

四个父批次 g1～g4，每批 118 帧，总计 472 帧，全部成功、0 失败；归档、输出 SHA 和 472 张 RGBA
PNG 均已校验。分配结果：4090 228 帧、3090-A 108 帧、3090-B 92 帧、4070Ti 44 帧。这同时证明
动画管家的父批次状态、帧级重试、最终 `SaveImage #102` 输出兼容和四机分布式领取均可用。

### 6.2 ModelView 局部重绘

4070Ti 真实 canary `328b0f8d-d1ee-452f-bbff-4f93440cfab7` 第一次执行成功；输出为
2048×1052 RGB PNG，SHA-256 `305970ed…`。该任务还验证了 12 GB 模型路径映射、缺失模型诊断和
局部重绘专用窗口。

### 6.3 PBR 粗糙度

真实 canary `2573e7fa-1768-4ed1-beba-8f2bd34ce0ae` 成功；当时 4070Ti 处于局部重绘保护，任务按
兼容性路由到 3090-B。输出为 1024×1024 RGB PNG，SHA-256 `4aa7a870…`。

### 6.4 Blender PBR UV

11 个真实 UV 任务成功，分布为 4090 2、3090-A 3、3090-B 4、4070Ti 2。共下载 55 个原子制品并
校验响应 SHA、文件头和 SSE 历史。测试工具中 UV-only 模式误访问空 reference root 的问题也已修复。

### 6.5 自动拓扑与独立审计

自动拓扑 `RETOPOLOGY_PROCESS_V2` 四节点各一次成功：

| Job | Worker | 结果 |
|---|---|---|
| `094a940c-312f-4d53-b247-3e9fc94a978a` | `asset-control-4090` | SUCCEEDED |
| `33db0340-d07d-4007-a308-19a0b3966b20` | `asset-worker-3090-a` | SUCCEEDED |
| `53c484a2-a0ab-4a3e-8cee-4c479c639c0c` | `asset-worker-3090-b` | SUCCEEDED |
| `1dabb667-6cd7-4822-bd35-9fe204f6f158` | `asset-worker-4070ti-animation-host-01` | SUCCEEDED |

独立审计首次暴露三层控制面漂移：当前 v3.0.25 Worker 被 claim 过滤、Worker 内置脚本 SHA 仍指向
旧值、调用和回传仍按旧 `--reference`/schema v2。修复后提交 12 个真实审计，12/12 成功，四台均有
真实领取，其中 4090 4、3090-A 5、3090-B 1、4070Ti 2。固定脚本实际 SHA-256 为
`bbc9990a045284be799df2f56f29b4a52f066c923eda0c65f2a88fe2d3128f1b`。

### 6.6 Substance PBR 烘焙

未通过本轮最终 canary。任务 `db6b2eb1-4f21-4c4f-88d9-38156133e0c9` 因四个 Windows Agent 仍为
v6 而未获领取，随后被安全取消。控制面和候选 v7 包没有绕过门禁。

IT 需要在 Windows 用户/管理员 PowerShell 中执行：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File D:\GPUControl\candidate-closure-20260812-v7\Install-GPUControlSubstanceAgent.ps1 `
  -InstallRoot D:\GPUControl\agent `
  -InstanceCount 4 `
  -ConfirmNoActiveBakes
```

候选已复制到该目录；候选 Agent SHA-256 为 `91f83e0b…`。升级后必须看到 `-01`～`-04` 四个实例均为
`ONLINE`、`skill_version=substance-baker-2026.08.12-v7`，再提交真实烘焙 canary 并校验全部制品 SHA。

## 7. WebUI 与 Codex 探针

WebUI 已包含：

- GPU 节点页固定展示四台节点，不把临时指标缺失等同于离线；
- 4070Ti/3090-B 专用策略、剩余保护分钟和硬过期后的共享状态；
- 调度说明页明确“空闲四卡共享、4070Ti 局部重绘优先、3090-B 烘焙唯一、CPU 不冻结”；
- Codex 运行中心文案和真实数据均为四主机；
- 资产处理页继续显示四个 CPU Worker 和唯一 Windows Baker 通道。

四个 Linux/WSL Asset Worker 当前均上报：Codex CLI `0.146.0-alpha.3.1`、
`AUTHENTICATED / HEALTHY`、RetopoFlow `3.4.11 / HEALTHY`、Skill
`asset-skills-auto-retopo-align-v3.0.25`。探针是每台物理主机独立认证，不复制其他节点的认证文件。

## 8. 测试、代码审计和负载门禁

已完成的测试结果：

- Web：5 个测试文件、18 项测试通过；ESLint 0 warning；Prettier 通过；Vue/TypeScript/Vite 生产构建通过；
- 变更相关负载门禁与夹具：`91 passed, 0 failed`；Asset API schema v2/v3 参数化集成测试 2/2 通过；
- 最终全量 Python：`586 passed, 15 skipped, 0 failed`，耗时 282.86 秒；
- Ruff 全仓通过；`git diff --check` 通过；关键脚本可编译；
- 四节点真实审计 12/12 成功；
- Web 生产容器健康并由 HTTPS 入口返回 200。

新场景 `tests/load/scenarios/six_api_100_20260812.yaml` 定义 1→10→25→50→100 VU、总时长 1260 秒、
公开六 API 混合、失败关闭和清场门禁。公开六项固定为：ImageClip 抠图、ModelView 局部重绘、
PBR 粗糙度、Blender PBR UV、Direct V2 自动拓扑和 Substance PBR 烘焙；拓扑独立审计属于内部
验证合同，保留 12/12 真实成功证据，但不挤占用户可见的六项综合压测名额。由于正式场景要求六 API
容量完整、源码 revision 已发布、运行镜像与 release evidence 完全一致，而 Windows Baker v7 尚未上线，
本轮没有伪造或强行执行“六 API 正式
100 VU 通过”。计划文件可以审计，但正式综合压力必须在 v7 canary 成功后运行。

最终回归前还发现旧负载场景把内部 `retopology_audit` 当作第六项，导致用户可见的
`modelview_inpaint` 没进入综合集合。现已同时修正场景、Locust 调度器、会话碰撞检测、异常清场、
制品校验、示例夹具和离线合成夹具；机器可读断言确认 API 恰为上述六项、权重总和 100、峰值 100 VU、
总时长 1260 秒。旧六项 YAML 仍只保留解析兼容，不会被当前正式场景选中。

## 9. Docker、Git 与清理

正式镜像必须绑定本报告对应的完整 Git revision，不能把 `working-tree` 热修复镜像冒充正式归档。
发布过程先提交并推送功能 revision，再从该干净 revision 构建镜像；镜像证据允许由后续仅文档提交回填，
但 OCI label 中的 source revision 必须始终指向实际代码提交。

代码 revision 已推送为 `94022a699b12a5928597664d0ecffdcee582d1b7`，分支
`agent/four-gpu-4070ti-closure`，Draft PR：<https://github.com/kiana0512/gpu_control/pull/1>。

| 镜像 | 本地/部署 image ID |
|---|---|
| `gpu-control-api:1.5.13` | `sha256:83e1f86f1fdf1e2d130d688f735634f72ec4b87e5652b53c1500704be993bb84` |
| `gpu-control-scheduler:1.5.13` | `sha256:0322ad3d50331dd5c27bc68e38a5ec88203455a7ec951dd928bfcb6c1b571720` |
| `unified-scheduler-asset-api:1.5.13` | `sha256:29a56671d8edf7642a51f822e392bd8c8db64766802aad0393adf8532881f84f` |
| `gpu-control-web:1.5.13` | `sha256:23c13b92b94ea6aa844baefe8b7cfb4bc3315d4ea79a0cae8a9df9bfc550e9f5` |
| `li3d/blender-worker:1.4.48` | `sha256:c3e20f206889fbb8fcdf2d9532b68130dc41ffa6fcc3ff0be2a6bf4e0d382698` |
| `gpu-control-node-agent:1.5.13` | `sha256:1f8d1a43df8e7ba62875e438717355d0e81fc0434ccc753c3f6fae79fe55b093` |

五镜像组合归档大小 837019163 字节，SHA-256
`f57ac79e6ec937f8ca3d060f81d4ea72d6ffee03b724462f23b2dc3d1cbbf0d9`；Node Agent 独立归档
大小 89212344 字节，SHA-256 `32f2ad35bff9a5ef00f0408517847f99dca9ea7fbef43fe8bcf4c200c8d93450`。
拆分件和离线 provenance 位于 `artifacts/control-plane/1.5.13/release-parts/`。五镜像的 Docker/OCI
config 全部逐项一致，OCI revision label 全部绑定上述代码提交；离线 provenance 已验证。

六 API 负载运行器的公开集合修正发生在正式运行镜像构建之后。该模块仅由离线压测脚本和测试导入，
生产 API、Scheduler、Asset API、Web、Worker 与 Node Agent 均不导入它，因此不重启或重打生产镜像；
压测执行时必须同时记录运行镜像 revision `94022a6…` 与后续负载运行器 Git revision，不能混写为同一
制品身份。

零活动任务门禁确认后，API、Scheduler、Asset API、Web 和本机 Worker 已滚动到正式 tag；三台远端
Blender Worker 的运行 image ID 也全部等于 `c3e20f…`。4070Ti Node Agent 已滚动到 `1.5.13`，
最终容器健康。HTTPS 首页返回 200，四个 Asset Worker 均为 `ONLINE`、0 当前任务，Codex/RetopoFlow
探针均为 `HEALTHY`。

边界：本轮没有 registry push，因此 registry manifest digest 仍为 pending；打包使用
`PENDING_PINNED_SBOM_GENERATOR`，不能宣称 registry-bound SBOM 已完成。这不影响本地/LFS 离线镜像
恢复，但仍阻止 `PRODUCTION_ACCEPTED`。

允许清理的范围：本轮明确创建的 `/tmp` 镜像归档、临时 env 副本、Pytest/Ruff/Mypy 缓存和 dangling
image。禁止删除模型、ComfyUI output、生产任务归档、PostgreSQL/Redis 数据或执行
`docker system prune -a`。

## 10. 明日验收顺序

1. WebUI 检查四台节点 `ONLINE / ACTIVE`，4070Ti 温度/功率/利用率/显存有真实值；
2. 提交小型四帧 ImageClip，确认不同物理节点领取并交付 RGBA；
3. 在 ImageClip 运行时提交局部重绘，确认 4070Ti 安全让出抠图帧、清缓存并优先响应；
4. 确认 15 分钟无新局部重绘后 4070Ti 自动恢复普通 GPU 接单；
5. 提交 UV 和自动拓扑，确认 CPU 槽不受 GPU 保护影响；
6. IT 完成 Windows v7 后提交真实 Substance canary；
7. 仅当六个 API canary、release binding、备份和清场全部通过时，执行正式 100 VU 场景；
8. 归档 summary、明细 CSV、任务 ID、镜像 digest、Git revision 和所有 SHA。

## 11. 不得误判的边界

- 4070Ti 不具备 Substance 烘焙能力；
- 3090-B 是唯一烘焙节点，但烘焙优先不等于永久禁止普通推理；
- CPU 拓扑/UV 与 GPU 专用窗口独立；
- WebUI 的 `1/1` 是 GPU 槽，Asset Worker 的 2/3/4 槽另算；
- 本轮没有改 ImageClip/ModelView 的业务管线语义；
- 五 API 成功不能写成六 API 正式通过；Windows v7 和正式综合压力仍是明确门禁。
