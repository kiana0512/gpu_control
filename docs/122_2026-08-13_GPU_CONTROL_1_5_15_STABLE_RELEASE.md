# GPU Control 1.5.15 四 GPU 稳定版发布与验收记录

日期：2026-08-13  
时区：Asia/Shanghai / Asia/Singapore（UTC+08:00）  
发布分支：`agent/four-gpu-4070ti-closure`  
源码 revision：`917646957755cd0583768d412f72f94fd2cb6043`  
状态：`PRODUCTION_DEPLOYED / FUNCTIONALLY_ACCEPTED / STABILITY_MONITORING`

## 1. 发布结论

GPU Control `1.5.15` 是 1.5.14 Substance v7 恢复后的稳定性收口版本。它不修改 ImageClip、
ModelView 局部重绘或粗糙度的外部工作流、模型、提示词、推理参数、图拓扑和输出节点。

本版只修改 GPU Control 自有范围：

- 3090-B 烘焙软保护从 15 分钟缩短为 5 分钟；
- 4070Ti 局部重绘保护仍为 15 分钟；
- 旧 15 分钟 Substance 标签在滚动升级期间按 `started_at + 5 分钟` 钳制；
- 管理员可在完全空闲时立即解除 3090-B 烘焙软保护；
- 活动 Baker fence、待领取 reservation、recovery-required、活动 GPU/外来队列不能被手工解除绕过；
- CPU Asset 槽继续独立，拓扑与拆 UV 不受 GPU 保护窗口冻结；
- WebUI 同步展示 5 分钟规则与“解除烘焙保护”入口；
- 归档动画管家 `121` 修复回执，生产抠图只走 GPU Control，禁止本机静默回退。

## 2. 固定生产拓扑

| 节点 | Node ID | GPU | GPU 槽 | CPU Asset 槽 | 特殊职责 |
|---|---|---:|---:|---:|---|
| 4090 控制中心 | `control-4090` | RTX 4090 24 GB | 1 | 2 | 控制面、共享 GPU、Overflow |
| 3090-A | `worker-3090-a` | RTX 3090 24 GB | 1 | 3 | 共享 GPU、CPU Asset |
| 3090-B | `worker-3090-b` | RTX 3090 24 GB | 1 | 4 | 唯一 Windows Substance Baker、共享 GPU、CPU Asset |
| 4070Ti 动画主机 | `worker-4070ti-animation-host-01` | RTX 4070 Ti 12 GB | 1 | 2 | 局部重绘响应优先、共享 GPU、CPU Asset |

## 3. 调度状态机

### 3.1 4070Ti

局部重绘到达后刷新 15 分钟 `modelview-inpaint` 保护。若它正在处理 ImageClip，当前帧可安全中断、
回队列并改派其他 GPU；切换前必须释放模型缓存。没有新局部重绘时窗口硬过期并恢复共享。

### 3.2 3090-B

生产烘焙到达后刷新 5 分钟 `substance-bake` 软保护。已经开始的 ImageClip 帧自然完成，再由 Windows
Baker 取得物理 GPU。真实执行安全性由 reservation/fence/recovery 三层硬门禁保证，不依赖软计时器。

烘焙结束且不存在硬门禁时：

1. 5 分钟软窗口过期后自动恢复共享 GPU；或
2. 管理员在 GPU 节点页点击“解除烘焙保护/投入使用”，立即恢复普通接单。

任何手工操作都不能清除活动 Baker fence、未完成 reservation 或 recovery-required。

## 4. 外部工作流不变性

批准的三个 GPU 工作流仍为：

| 工作流 | 版本 | Pipeline SHA-256 | 最终输出 |
|---|---|---|---|
| ImageClip | `2026.08.12-c39ed0b-fp8-r1` | `07928d57852ed56ed37527960ec9955d867c0090456fda687fbcd12fecf1775c` | `SaveImage #102` |
| ModelView Inpaint | `2026.08.12-8c37f07-seedvr2-12g-r1` | 见 120 对接合同 | `SaveImage #9` |
| Roughness | `2026.08.12-d318bb39-roughness-12g-r1` | `8a5274...`（完整值见 120） | `PreviewImage #355` |

四台节点的三个兼容矩阵共 12 条必须全部为兼容；不得通过改工作流规避 OOM或输出合同。

## 5. 动画管家联合恢复证据

动画管家交付的原文件已经按字节原样归档为：

`docs/121_2026-08-13_ASSETCLAW_GPU_CONTROL_1_5_14_FIX_RECEIPT.md`

SHA-256：`6036f895350115d7f5c325a255ef8cc6c4e3c1d3d650fd0bf07632009d63bd2b`

三个恢复批次在控制面最终均为成功：

| Batch | 帧数 | 终态 | 失败帧 |
|---|---:|---|---:|
| `8cc7a92b-59ed-4ba9-9025-83f58a28068d` | 97 | `SUCCEEDED 97/97` | 0 |
| `decc2eb4-c953-4e3b-a325-07f2cbd142d0` | 97 | `SUCCEEDED 97/97` | 0 |
| `8c5504e4-bb79-4b1d-9c04-6fe76c0ec9bb` | 44 | `SUCCEEDED 44/44` | 0 |

动画管家侧额外完成 `47 passed`，健康接口 `ok=true`；生产路由固定为 `gpu_control`，不得回退本机
秋叶 ComfyUI、hybrid 或本机 OOM fallback。

## 6. Substance 恢复与稳定性证据

- v7 最终 Agent SHA-256：
  `06fcb4cefb9aeb7e53693faf9f87a36a113324ba8df162738e416afdb9e4b399`；
- 四个 Windows Agent 均为 `substance-baker-2026.08.12-v7`；
- 原失败任务 `867d53b9-cfb6-49d5-b1d2-0007777e8072` 经 Admin Retry attempt 3 成功；
- 后续稳定性任务 `879ef49c-7ae4-4891-8b35-6d3f1b15a9d7` 成功；
- 本版发布前真实任务 `d1efeff5-e779-4d20-b1da-66b40b32fa82` 成功，耗时约 35 秒；
- 绝对 Python 路径、异步显存释放轮询和 ComfyUI 队列复查继续生效。

## 7. 自动化验证

| 验证 | 结果 |
|---|---|
| Python 全仓回归（最终代码） | `587 passed, 16 skipped`，284.50 秒 |
| 策略/发布/手工解除定向回归 | `11 passed` |
| 旧 15 分钟标签钳制与 5 分钟过期 | `2 passed` |
| Web 单元测试 | `18 passed / 5 files` |
| Web ESLint | `PASS / 0 warning` |
| Web 生产构建 | `PASS` |
| Ruff | `PASS` |
| Git diff whitespace | `PASS` |
| 动画管家客户端 | `47 passed` |

## 8. 稳定版镜像

所有镜像都由已推送的源码 revision `917646957755cd0583768d412f72f94fd2cb6043` 构建：

| 镜像 | 本地不可变 ID |
|---|---|
| `gpu-control-api:1.5.15` | `sha256:9771235bd4bd5933d133b6aa8154699c0f5251295e161ef805755fc8ff2952dc` |
| `gpu-control-scheduler:1.5.15` | `sha256:67c9f1fd5a36a856067d08a9bfaf6d7fdd9770590bfec3c9f87521e5df5ea290` |
| `unified-scheduler-asset-api:1.5.15` | `sha256:e800a0cde5e5277e5fe2cff42c8b9f33501cc82aa61517fd98c540716023ee4b` |
| `gpu-control-web:1.5.15` | `sha256:cbf44e1766404640719c69d90caec904fba9a0e51eb528371081fa8526766280` |
| `li3d/blender-worker:1.4.48` | `sha256:713cbf4258c91ac586a042421826374d3ff2f5edf5438e6885fb6667a3916ddf` |
| `gpu-control-node-agent:1.5.15` | `sha256:0bd52c863be5bb5fbaf8c87df08782faf618b9f84e199701fe497e28df3e96ab` |

五镜像合并归档：

- 文件：`gpu-control-control-plane-1.5.15-images.tar.gz`
- 大小：`837037754` bytes
- SHA-256：`1c5193adb7d537fab34238623496b0c4a0604ab6baa6896e2568259a627461f5`
- LFS：7 片，最大单片 128 MiB。

Node Agent 归档：

- 文件：`gpu-control-node-agent-1.5.15-image.tar.gz`
- 大小：`89245100` bytes
- SHA-256：`908df1d70284a4257d8fdb116a3840960d8f6f1f4dea3f0953ef631dce0a323d`
- LFS：1 片。

离线 OCI provenance 已验证；registry push、registry manifest digest 和 digest-pinned SBOM generator
仍明确为 `PENDING`，不得把离线 OCI digest 冒充 registry digest。

## 9. 发布后运行态

- API、Scheduler、Web、Asset API 已在 GPU/Asset 安全窗口中滚动为 `1.5.15`，健康检查通过；
- `GET /api/v1/version` 返回 package/build/version 三者均为 `1.5.15`，revision 精确匹配，
  `version_aligned=true`、`provenance_complete=true`；
- `GET /api/v1/assets/version` 返回 package/build `1.5.15`、revision 精确匹配，
  `version_aligned=true`、`provenance_complete=true`；
- HTTPS Web 与 Asset capacity 接口均返回 HTTP 200；
- 4070Ti Node Agent 已滚动为 `1.5.15`，状态 `ONLINE/ACTIVE`，温度、功率、显存、利用率继续上报；
- 4090、3090-A、3090-B、4070Ti 四个 CPU Asset Worker 均为 `ONLINE`，Codex CLI 均为
  `0.146.0-alpha.3.1`，认证与真实探针均为 `HEALTHY`；
- 3090-B 四个生产 Baker 均为 `substance-baker-2026.08.12-v7`，进程探针 `HEALTHY`、
  发布检查时活动 Baker 为 0；
- 3090-B 发布时发现一个由旧 15 分钟标签遗留的空闲 `DRAINING`。在确认无活动 GPU Job、
  无 reservation、无 fence、无 recovery-required、无外来队列后，已通过正式 Admin mode API
  恢复为 `ACTIVE`；没有直接修改数据库；
- 4090/3090-A/3090-B 的旧 Node Agent 仍为 `1.5.11`，因为宿主 Python 3.10 不满足当前包的
  Python 3.11 下限；它们的心跳、GPU 遥测和 Codex/Asset 探针均正常。不得在生产窗口内强行升级
  Python，后续应在逐节点维护窗口升级；
- `li3d/blender-worker:1.4.48` 正式镜像已构建归档；当前三台既有 Linux Worker 不在有生产任务时
  强制替换，待逐节点 CPU Asset 空窗滚动；这不改变本版 CPU API 合同和已通过的真实任务；
- 四台 ComfyUI 不因本次控制面发布被替换或重启。

## 10. Git 与 LFS

- 源码提交：`917646957755cd0583768d412f72f94fd2cb6043`；
- 镜像归档提交：`da318325a209d6a81b0291bff4a82a289acefb18`；
- 分支：`agent/four-gpu-4070ti-closure`；
- Draft PR：`https://github.com/kiana0512/gpu_control/pull/1`；
- 8 个 LFS 对象（约 926 MiB）已上传，远端分支已包含镜像归档提交；
- `git lfs fsck`、`git lfs status` 和 LFS dry-run 均以退出码 0 完成。

## 11. 发布边界

本记录中的“稳定版”表示功能、回归、真实业务 canary、镜像可复现性、控制面部署和基础探针已闭环。
它不等于已经完成连续七天观察，也不等于 registry/SBOM 供应链签署完成。长时 100 VU 测试只有在
无生产任务的受控窗口才能运行；生产任务始终优先，未运行时不得伪报通过。

发布收口期间生产方持续提交 CPU Asset 任务，因此没有为了凑测试结论中断真实作业，也没有在任务
执行中替换 Blender Worker。100 VU/21 分钟混合压测、registry push、registry digest 与 SBOM 签署
仍列为后续受控维护项，不计入已完成证据。
