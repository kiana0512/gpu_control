# 2026-07-23 RTX 3090-B 与三节点联调验收记录

## 1. 最终结果

3090-B 已按 3090-A 的固定版本完整复制并接入主控。4090、3090-A、3090-B 已完成三卡同时
`ACTIVE`、10 个独立 API 客户并发提交 10 个真实图片请求的轻量验收：10/10 成功、三张卡
全部参与、没有重试、丢单或失败。复杂持续压力测试留到后续单独执行。

| 节点 | 地址 | 主机名 | MAC | GPU UUID | 测试分配 |
|---|---|---|---|---|---:|
| `control-4090` | `10.3.34.11` | `lilithgames2` | `58:11:22:c1:66:63` | 4090 本机固定卡 | 4 |
| `worker-3090-a` | `10.3.34.13` | `lilithgames1` | `18:c0:4d:9f:13:13` | `GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c` | 3 |
| `worker-3090-b` | `10.3.34.14` | `lilithgames3` | `2c:f0:5d:76:7b:70` | `GPU-092a5184-5857-d196-5df2-efa9503368aa` | 3 |

工作节点发生 DHCP 地址变化时，MAC 和 GPU UUID 是节点唯一身份约束；Node Agent 主动心跳
更新当前 IP，Scheduler 反向 HMAC 校验身份，Prometheus 从数据库动态发现新地址。仍应让
网络管理员为三台主机配置 DHCP 保留。

## 2. 3090-B 部署验收

- Ubuntu `22.04.5 LTS`，内核 `6.8.0-134-generic`，RTX 3090 24 GiB，驱动
  `580.159.03`。
- Docker `29.6.2`、Compose `5.3.1`、NVIDIA Container Toolkit `1.19.1`；CUDA 容器
  正确识别 RTX 3090。
- 镜像 `registry.local:5000/gpu-control/comfyui:projects-0.2.2` 的 Image ID 为
  `sha256:bb8c76cfb0bf18c1caff7cfe2a758a9ec1e049543180f117d75af2e94d73a325`。
- 离线归档 SHA-256 为
  `97c5e8f73fd189a29b59ac7c6a851f9278fe53bb641c118fd20baec22c027ddc`。
- `/opt/imageclip` HEAD 为 `bb243808a6bd43055ad92c1071b2ea949b1d9ea1`；
  `/opt/modelviewcreator` HEAD 为 `b22bb377d200d10ae1af565494674fdfb53580dc`。
- ImageClip 4 个模型、ModelViewCreator 5 个模型，共 9 个模型全部通过 SHA-256；模型同步
  实测约 112 MB/s，已接近 1 GbE 链路上限。
- ImageClip 23 种 API 节点缺失 0；ModelViewCreator 18 种 API 节点缺失 0；容器
  `pip check` 通过。
- ComfyUI、Node Agent、node-exporter、DCGM exporter、Alloy 全部运行；8188、9201、
  9100、9400 只允许主控访问，Prometheus 的 B 节点两个目标均为 `up`。
- NVIDIA persistence mode 已由 `gpu-control-nvidia-persistence.service` 固化为开机启用。

## 3. 3090-B 真实任务证据

在只允许 B 接单的阶段，用同一张 1920×1080 PNG 连续调用 ModelView：

| Job ID | Node | API 总耗时 | 数据库执行耗时 | 缓存路径 | 结果 |
|---|---|---:|---:|---|---|
| `2ae9c17a-a785-428b-819c-4aae2fc46947` | B | 15.152s | 14.653s | 冷切换，先释放旧模型 | 200 / SUCCEEDED |
| `62dc7a1a-4363-43c4-b10a-3bf95e7d53a3` | B | 8.127s | 7.762s | 同工作流热缓存复用 | 200 / SUCCEEDED |

两份返回均为有效 1920×1080 PNG。日志分别出现 `executor.memory_released` 与
`executor.memory_cache_reused`，证明优化不是只改显示值。

## 4. 动态调度与显存策略

Scheduler 在不改变公共 API 和任务状态机的前提下增加最佳努力热缓存亲和：

1. 从公平队列中观察队首工作流。
2. 优先选择 `labels.warm_workflow` 相同且当前有空槽的节点。
3. 热节点忙、离线、保留或不兼容时立即回退到正常最久未分配排序，不等待热节点。
4. 同一节点连续执行相同工作流时保留 ComfyUI 模型；工作流变化时调用 `/free` 后再加载，
   避免 ImageClip 与 ModelView 共存造成 24 GiB 3090 OOM。
5. Comfy 执行错误会清除热缓存标签，下一单不错误假设缓存仍可用。

该策略只影响节点排序，不改变 API 路径、客户公平性、幂等、配额、审计和持久队列。

## 5. 10 客户、10 请求、三卡轻量验收

验收运行 ID：`20260723T104513Z`。工具：
`scripts/three_node_ten_client_smoke.py`。测试为 10 个独立客户分别持有独立 API Key，
同时提交 5 个 ImageClip 与 5 个 ModelView 请求；每卡仍严格为一个执行槽。

| 用户 | 工作流 | Job ID | 节点 | API 耗时 | 尝试 | 结果 |
|---:|---|---|---|---:|---:|---|
| 01 | ImageClip | `8e235929-021f-4bc0-88b1-d7b8e9c154bd` | A | 56.404s | 1 | SUCCEEDED |
| 02 | ModelView | `e85ef786-b4e4-4d56-aac9-6cbdbc019305` | B | 95.857s | 1 | SUCCEEDED |
| 03 | ImageClip | `7cc2b78a-cb88-4e8f-a1a1-f0dcd2433941` | B | 68.673s | 1 | SUCCEEDED |
| 04 | ModelView | `885ae218-c572-43a7-bd4e-c4df2b4f6b2c` | B | 10.352s | 1 | SUCCEEDED |
| 05 | ImageClip | `61c60a96-9d7e-4d94-8265-a49c62455929` | 4090 | 31.369s | 1 | SUCCEEDED |
| 06 | ModelView | `b00baf80-404a-476d-b0e4-6f82813a5d32` | A | 75.788s | 1 | SUCCEEDED |
| 07 | ImageClip | `bc017700-1c94-44ee-b2ae-7247d36a0670` | A | 130.960s | 1 | SUCCEEDED |
| 08 | ModelView | `867c4601-afad-4cb4-9e51-80b37b66331a` | 4090 | 75.785s | 1 | SUCCEEDED |
| 09 | ImageClip | `25ee78d8-708f-4530-be0a-ad7e7cab549f` | 4090 | 55.676s | 1 | SUCCEEDED |
| 10 | ModelView | `57ff99bc-44bf-4e10-882c-ca5750d32083` | 4090 | 81.727s | 1 | SUCCEEDED |

总墙钟 130.968 秒，成功 10/10，分配为 4090/A/B = 4/3/3。所有响应均为 HTTP 200、
`image/png`、进度 100%，每个任务只有一次尝试。冷切换和排队耗时会包含在同步 API 的总
耗时中；因此单个用户看到的 130.960 秒并不代表单卡纯推理耗时。

## 6. 失败、重试与 OOM 修复

任务 `f66fe2f0-e77d-40a7-9843-c4074dc9ff67` 首次失败的真实原因是 A 上残留 ImageClip
模型后加载 ModelView，在 `NunchakuFluxDiTLoader` 触发 CUDA OOM。手工重试一度排队不动，
原因是 `node_leases.job_id` 唯一约束与重试创建新租约冲突并导致 Scheduler 重启。

修复后：重试复用原租约行并刷新 token；每次尝试都有独立结束状态；管理端重试清理旧
node/prompt/时间/进度但把旧错误保留在审计中；Comfy 错误包含具体节点、节点类型和消息；
`/free` 的 HTTP 200 空响应按成功处理。该任务第 4 次尝试最终 SUCCEEDED，Prompt ID 为
`a8e58c22-35a1-498c-b142-92cf87c66a94`。

## 7. GPU 指标显示修复

此前显存由 ComfyUI 每 5 秒刷新，但 `nodes.gpu_util_percent` 没有生产写入路径，因此 Web
利用率长期显示 0%。现增加 HMAC 保护的 `GET /v1/gpu-metrics`：Node Agent 调用
`nvidia-smi` 返回利用率和显存，Scheduler 每 5 秒采集并写入 PostgreSQL。部署后对三台
Agent 的请求全部 HTTP 200；首次验收采样为 4090 25%、空闲 A/B 0%。

任务结束后利用率回到 0% 是正常状态；显存仍被占用是 ComfyUI 热缓存，不是泄漏。节点页
曾在 18:47:00 显示 A 为 1/1，而 18:47:33 总览显示 0/1，是最后一单在两次刷新之间完成，
并非状态不一致。

## 8. 自动回归结果

- 调度、重试、Comfy `/free` 和 API 集成：30 passed。
- 增加 GPU 指标签名接口后，Node Agent、调度、数据库领取和 API 集成：29 passed。
- 上传前最终全量 Python：Ruff 全通过、Mypy 22 个源码模块无问题、单元与集成测试
  62 passed。
- 前端 ESLint、Prettier、Vitest、TypeScript 和 Vite 生产构建全部通过，2046 modules
  transformed。
- 两套 Compose 配置检查通过；三节点真实页面显示三台 ONLINE/ACTIVE，ComfyUI 按钮使用
  各节点动态 `base_url`。

## 9. 运行模式说明

三卡验收期间三台均设为 `ACTIVE`。日常若希望 4090 只做控制面，将 `control-4090` 改回
`RESERVED`；若希望三卡持续接单，保持当前 `ACTIVE`。默认部署文档仍采用 4090
`RESERVED` 的保守策略，切换操作会进入审计日志。
