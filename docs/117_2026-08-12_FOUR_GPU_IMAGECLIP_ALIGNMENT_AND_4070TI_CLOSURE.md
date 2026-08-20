# 四 GPU ImageClip 对齐与 4070Ti 上线收口记录

日期：2026-08-12（Asia/Singapore）
范围：GPU Control 控制面、4090、3090-A、3090-B、4070Ti WSL2
外部管线原则：ImageClip 与 ModelViewCreator 只同步批准的上游原件，GPU Control 不修改工作流语义。

## 1. 最终拓扑

| 节点 | 节点 ID | 地址 | GPU | GPU 槽 | CPU Asset 槽 | 特殊职责 |
|---|---|---:|---|---:|---:|---|
| 4090 控制中心 | `control-4090` | `10.3.34.11` | RTX 4090 24 GB | 1 | 2 | 普通共享 GPU、控制面 |
| 3090-A | `worker-3090-a` | `10.3.34.12` | RTX 3090 24 GB | 1 | 3 | 普通共享 GPU |
| 3090-B WSL2/Windows | `worker-3090-b` | `10.3.34.14` | RTX 3090 24 GB | 1 | 4 | 唯一 Substance PBR 烘焙通道 |
| 4070Ti WSL2 | `worker-4070ti-animation-host-01` | `10.3.34.238` | RTX 4070 Ti 12 GB | 1 | 2 | ModelView 局部重绘优先；禁止 Substance 烘焙 |

四台在空闲状态均可执行 ImageClip 抠图、ModelView 局部重绘和 PBR 粗糙度。4070Ti 与 3090-B 的 15 分钟保护只限制 GPU 调度，CPU 拆 UV、拓扑、Codex/RetopoFlow 任务不受影响。

## 2. ImageClip 正确版本判定

2026-08-12 现场只读查询 GitLab：

- 仓库：`rd_center/ai_art/imageclip`
- 远端 `main`：`982d07224fcddf9dd6e5af3d4abe782fe6fe30fe`
- `ImageClip.json` 最近业务工作流提交：`c39ed0b3b637f0a1435bbe10e5a3acf6bfca07bd`
- 远端 `982d0722` 与 `c39ed0b3` 的 `ImageClip.json` 字节完全相同。
- 正确工作流 SHA-256：`d20f3206c0d103e32bc4df70cbc8f0dbd5b028ce58b2b4b07b1066a15bba2689`
- UI 图：28 个节点、34 条连线。

原三台部署的是旧提交 `691770cd6a59fd7c51391456fe900dc57a313233`，其 `ImageClip.json` SHA-256 为 `1932c2d2cf278c6ff538bf06d2d01039fe8855e1c831f57fde17695b05ecd5b4`，UI 图为 51 个节点、91 条连线。4090 页面中更复杂的 51 节点图是旧版本，不是正确的新版本。

对齐时四台先全部切换为 `DRAINING`，并确认 GPU Job、Asset Job 和四个 ComfyUI 队列均为零。4090、3090-A、3090-B 的旧文件分别备份为：

`/opt/imageclip/ImageClip.json.gpu-control-backup-20260812T1830`

随后原样同步远端正确文件。容器挂载内复核结果：四台 `/opt/comfyui/user/default/workflows/ImageClip.json` 的 SHA-256 均为 `d20f3206...`。复核后四台恢复 `ACTIVE`。

工作流文件不能单独发布。本轮同时以已跑通的 4070Ti 实装为唯一来源补齐另外三台依赖：

- 四台 `flux-2-klein-9b-fp8.safetensors` 大小均为 `9,433,061,528` 字节，SHA-256 均为 `865ba09f5b4c3cbd3468a4bd3acb9fcb2f8740c54317482f0bcd4ed1d3655cee`；
- 四台 `ComfyLiterals` 均为提交 `bdddb08ca82d90d75d97b1d437a652e0284a32ac`，通过只读 bind mount 注入；
- 四台 `/object_info` 均必须同时注册 `Int`、`NunchakuFluxDiTLoader` 和 `CherryHoldoutSimple`，并在 `UNETLoader` 枚举中出现 FP8 文件；
- 工作流 SHA、模型大小/SHA、custom-node Git SHA、节点类和模型枚举任一失败，节点必须维持 `DRAINING`，不得上线。

注意：ComfyUI 浏览器中已经打开的旧标签是浏览器会话副本，不会自动变成磁盘新文件。测试者应关闭旧 `ImageClip` 标签，刷新页面，再从工作流列表重新打开 `ImageClip`；以 28 节点和上述 SHA 为准。

## 3. 4070Ti 业务运行时

4070Ti 保持上游工作流不变，仅使用 GPU Control 部署层的显存驻留参数：

```text
--disable-dynamic-vram
--lowvram
--reserve-vram 3.0
--cpu-vae
--disable-async-offload
--disable-pinned-memory
```

这些参数不改变模型、节点、提示词、分辨率、采样参数、图拓扑或输出节点。已安装并校验：

- `flux-2-klein-9b-fp8.safetensors`
- 大小 `9,433,061,528` 字节
- SHA-256 `865ba09f5b4c3cbd3468a4bd3acb9fcb2f8740c54317482f0bcd4ed1d3655cee`
- ImageClip 输出为最终 RGBA；现场成功结果为 2109×1388，alpha 同时包含 0 与 255。
- ModelView 局部重绘与 PBR 粗糙度均已产生真实 PNG 成品；粗糙度峰值显存约 11.2 GB，未发生 OOM。

ComfyUI Manager 对局部重绘所列的 5 个“缺少模型”是 Manager 下载索引误报，而非执行运行时缺失。4070Ti 容器可见且工作流枚举已选中：`flux-turbo-alpha-8tp.safetensors`、`clip_l.safetensors`、`t5xxl_fp16.safetensors`、`ae.safetensors` 和 `Nunchaku-svdq-int4_r32-flux.1-fill-dev.safetensors`；对应节点类均已注册，真实局部重绘任务已成功输出。不得点击 Manager 的“全部下载”覆盖已批准模型。

## 4. 调度保护定案

1. 4070Ti 检测到新的局部重绘时刷新 15 分钟 GPU 专用窗。
2. 如果 4070Ti 正在抠图，当前抠图帧可安全中断并重新入队到其他兼容 GPU；切换前执行队列排空、模型卸载和显存校验。
3. 3090-B 检测到烘焙时刷新 15 分钟 GPU 专用窗；若正在抠图，必须完成当前帧后才切换。
4. 3090-B 是唯一 Substance 烘焙节点；4070Ti 不声明、不领取烘焙能力。
5. 保护窗只由新的对应任务刷新。到期且没有新的局部重绘/烘焙时自动解除，节点重新参与普通 GPU 调度，禁止形成永久不接单状态。
6. 所有 GPU 工作流切换前均执行 `/free`、释放模型/节点缓存并校验 VRAM；排空失败时阻断新 GPU Job，避免 OOM 导致节点失联。

## 5. 任务中遥测修复

故障根因是 ComfyUI 在加载大模型或采样时可能短暂阻塞 HTTP 探测；旧 Scheduler 把单次探测超时直接解释为整机离线，Web 因而清空温度、利用率和显存。

修复后：

- ComfyUI 探测与 Node Agent 遥测分离。
- ComfyUI 忙但 Node Agent 可达时为 `DEGRADED`，继续显示 Node Agent 实测 GPU 指标。
- ComfyUI 与 Agent 同一轮短暂失败时，保留最近一次遥测并进入三分钟确认窗；不会因一次超时显示离线。
- 连续三分钟无任何有效遥测才显示 `OFFLINE`，真实断电/断网仍会被识别。

已构建并部署：

| 镜像 | 不可变本机 image ID |
|---|---|
| `gpu-control-api:1.5.12-four-gpu-routing-v1` | `sha256:ebb361a6e6e7f90927f2d210324751419a0275b78971c558fe2148e26a551905` |
| `unified-scheduler-asset-api:1.6.47-four-gpu-specialization-v1` | `sha256:4168a00ec6bd8c1fe02db4eee074cdfb387712a0c02e7260591c304dcd4b733a` |
| `gpu-control-scheduler:1.5.12-agent-health-fallback-v2` | `sha256:0d1e71f4ba0d5b993e9bee9b1da009af3280f6a67ec26157ce32e125f6a7e0ce` |
| `gpu-control-web:1.5.12-live-agent-metrics-v3` | `sha256:4693a718c5dc2711d3da15688819e1139e135943402f3b4f2d864f3b6e6ac276` |
| `gpu-control-node-agent:1.5.12-4070-canary-v1` | `sha256:40de3760637e2fc92fa3b17ae93af46338c99c2f5bb778a325cf7c9bbe9d7db0` |

最小已部署离线包：`output/deploy/gpu-control-1.5.12-four-gpu-20260812/control-plane-and-node-agent-images.tar.gz`。完整候选离线包另包含 API、Asset API 和 Blender Worker；同目录保存 SHA-256 文件。生产当前仅热更新 Scheduler/Web，API/Asset API 候选须在烘焙、拓扑与 UV 回归全部通过后再滚动替换。

## 6. 测试入口与验收顺序

测试前硬刷新 GPU Control Web；关闭 ComfyUI 旧 ImageClip 标签后重新打开。按以下顺序执行，任一步失败停止后续业务压测：

1. 四台 Web 卡片均存在，状态为 ONLINE/DEGRADED，温度、利用率、功率、可用显存和槽位均非空。
2. 4070Ti ImageClip：执行中连续观察 Web，任务不得变为 OFFLINE；输出必须为最终 RGBA。
3. 4070Ti 工作流切换：ImageClip → 局部重绘 → 粗糙度，每次切换核对队列清空、模型释放和显存恢复。
4. 4090、3090-A、3090-B 各执行一笔 ImageClip、一笔局部重绘和一笔粗糙度。
5. 四台 Linux/WSL Asset Worker 各执行拆 UV 与拓扑；检查 Worker ID、成品、审计和 Codex/RetopoFlow 探针。
6. 3090-B 单独执行 Substance 烘焙回归；不得让 4070Ti 领取烘焙。
7. 构造局部重绘与抠图冲突，验证 4070Ti 抠图中断重排和 15 分钟硬过期。
8. 构造烘焙与抠图冲突，验证 3090-B 当前帧完成后切换和 15 分钟硬过期。

回滚工作流时必须先 DRAINING 并确认队列为空，再从对应备份文件恢复；不得在运行中的 ComfyUI Job 上覆盖工作流。
