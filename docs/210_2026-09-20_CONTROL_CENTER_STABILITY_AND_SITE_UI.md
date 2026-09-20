# 2026-09-20 控制中心稳定性与官网式 WebUI 重构

## 结论

本轮只修改 GPU Control 控制面、云实例生命周期和 WebUI；没有修改 ImageClip、ModelView、
ComfyUI 工作流 JSON、模型、采样器或业务参数。

- 原 AutoDL 5090 实例已更换为 RTX PRO 6000 Blackwell；SSH 隧道、ComfyUI 数据面和
  局部重绘路由均健康，控制面会从运行时遥测自动更新设备型号。
- 控制面重启后的云实例状态来自持久化 Provider inventory 与实时供应商快照，不依赖浏览器内存。
- WebUI 已从旧暗色侧栏后台整体重构为白色、横向业务导航、独立路由和按需下钻的产品站式控制台。
- 云实例电源、SSH、定时启停、批量操作、任务、节点、资产、Codex、告警、审计与日志能力均保留。
- Codex Worker 授权坚持每节点独立持久凭证；禁止复制任意节点的 `auth.json`。

## 5090 重启自愈修复

### 根因

远端 ComfyUI 启动脚本在 `flock` 子进程中继承了锁文件描述符，父进程等待健康检查时可能形成
自锁；Tunnel 只看端口而没有把远端 HTTP readiness 作为最终门禁。后续审计还发现 Provider
bootstrap 仍固定旧脚本 SHA，而 Tunnel 与仓库脚本已经更新。当前热运行不受旧 SHA 影响，
但下一次供应商开机时会在 checksum 门禁失败。

### 修复

- 远端启动命令关闭继承的锁 FD，保留进程与 HTTP 双重幂等检查。
- Tunnel 在开放数据面前直接验证远端 `/system_stats`，超时后关闭会话并有界重连。
- 当前脚本 SHA 固定为
  `2ce355754406f7cccf271668a11a907821620845774bf97a651f37d876f66d72`。
- Provider、Tunnel 与仓库脚本字节加入三方一致性测试。
- 新部署环境会独立生成 `PROVIDER_CONTROLLER_HMAC_SECRET`，不再保留 `CHANGE_ME`，也不复用
  JWT、Node Agent 或 Asset Worker 密钥。

## 控制面卡死与 502

- UI 卡死主因是长期 SSE 连接遗留 `idle in transaction` 会话以及数据库 dead tuples 膨胀。
- 终止的仅是确认无写事务的陈旧 SSE 会话；执行 `VACUUM ANALYZE` 后恢复。
- PostgreSQL 增加 `idle_in_transaction_session_timeout=5min`，asyncpg 会话也显式设置同一保护。
- SSE 生成器不再跨流生命周期持有数据库事务；Provider 同步写入节流并使用并发安全 upsert。
- RealESRGAN 502 来自 Controller 容器因 Docker IP 冲突停止；只重建 Controller 后恢复为 5/5 ready。

## WebUI 信息架构

顶层采用横向业务域导航，二级能力按需展开；每项仍是可直接访问和收藏的独立 URL：

| 业务域 | 路由 |
|---|---|
| 运行态势 | `/`、`/jobs`、`/analysis` |
| 算力与执行 | `/nodes`、`/cloud-servers`、`/asset-processing`、`/realesrgan`、`/codex` |
| 接入与策略 | `/workflows`、`/clients`、`/scheduling` |
| 可靠性 | `/alerts`、`/audit`、`/logs`、`/settings` |

交互重构包括命令面板、生产/测试口径隔离、Cloud 资源/紧凑视图、Codex 异常优先工作台、
RealESRGAN Worker/任务双索引以及移动端抽屉导航。当前角色从登录会话读取，不再固定显示
administrator。

发布阻断修复：Cloud 批量操作目标严格取“已选择 ∩ 当前可见”，筛选变化会剔除隐藏选择，
确认框列出实例名称；Jobs/Analysis 增加 request/scope generation，旧响应不能覆盖新口径；
Codex 零节点不再误报全部可接单，Inspector 不会指向筛选外节点；关键弹窗补齐 Escape、ARIA
与焦点恢复。

### 5090 局部重绘专用调度与可读性更新

- `autodl-5090-01` 持久化 `workflow_allowlist=["modelview-inpaint"]`，只允许完整四输入
  ModelView 局部重绘；粗糙度、单视图、单视图局部重绘、ImageClip 及其他工作流均不能领取。
- 兼容性刷新会把白名单外工作流写为 `compatible=false` 并记录策略原因；抢单事务再次应用同一
  白名单，因此即使存在陈旧的 `compatible=true` 行也会拒绝，控制面重启后仍然有效。
- 上线时确认 5090 无活动任务后临时置为 `DRAINING`，部署 API/Scheduler 后才恢复 `ACTIVE`，
  两次状态与策略变更均写入 `audit_logs`。
- 云服务器页面新增“任务范围”，5090 明确显示“仅局部重绘”。
- WebUI 使用舒适密度：整体提高导航、正文、表格、状态、表单和登录页尺寸；宽度达到
  1800 CSS px 的 2K/4K 工作站会同步放大导航高度、控件、行高和卡片节奏，而不是只放大标题。

### r6 统一视觉与导航修复

- 删除旧暗色、紫色渐变和页面各自为政的卡片色，所有业务路由统一为白色工作面、浅蓝网格背景、
  单一蓝色强调色和一致的边框/阴影层级；性能洞察页最后一组深色组件也已迁移。
- 顶部业务导航改为单一受控展开状态，不再由多个 `:focus-within` 状态叠加；点击路由后立即收起，
  不会再遮挡告警、任务或节点页面。
- Vue Router 与主滚动容器都在跨路由时恢复顶部位置，任务页不再继承上一个页面的滚动位置。
- 4K、DPR 2 浏览器验证覆盖 `/`、`/jobs`、`/analysis`、`/nodes`、`/cloud-servers`、
  `/codex`、`/realesrgan`、`/alerts`：8 个页面均无运行时错误、无横向溢出。

### r13 顶部导航与可读性收敛

- 一级业务域、二级页面路由和页面内数据范围改为三种明确不同的控件；二级路由不再使用容易与
  “生产/测试”混淆的灰色胶囊，而是完整高度的文字导航轨道，当前项用字重与 3 px 下划线定位。
- 2048×1080 实机计算样式为一级导航 16 px、二级导航 16 px、生产/测试范围 14 px；任务主标题
  17 px、服务名 16 px、表格正文 16 px，不再被异步路由 CSS 恢复成低对比度小字。
- 首页头部高度压缩，生产/测试和同步状态保留在页面操作区；路由切换仍使用原有 Vue Router，
  所有独立 URL、权限和功能均保留。
- 真实浏览器截图保存为
  `output/webui-studio-r11-20260920/dashboard-r13-2048x1080.png`，任务表可读性截图保存为
  `output/webui-studio-r11-20260920/jobs-r12-2048x1080.png`。

### r15 AutoDL ComfyUI 直达入口

- GPU 节点页会把 Provider 返回的官方 `service_6006` 地址与节点持久标签中的浏览器工作区
  fragment 组合，且只允许 HTTPS 和 AutoDL/SeetaCloud 官方域名；格式异常的 fragment 不会进入 URL。
- 节点数据与云实例映射现在并行请求、共同完成后再解除页面加载状态，消除了页面首次进入时
  “打开 ComfyUI”先于云映射返回、必须点击第二次的竞态。
- PRO 6000 节点按钮明确显示“打开云端 ComfyUI”，15 px/720 字重、170 px 实测宽度；真实浏览器
  点击生成的 URL 与供应商控制台给出的地址完全一致，使用 `_blank` 和 `noopener,noreferrer`。
- 节点标签 `comfyui_browser_fragment` 已持久化，控制面、Provider Controller 或服务器集群重启后
  不依赖前端内存恢复。配置动作已写入 `audit_logs`，审计记录不保存 fragment 本身。
- 官方入口的 HTML 和 ComfyUI API 均返回 200。独立冷浏览器审计发现远端 ComfyUI 1.51.10
  会加载约 250 个前端资源；25 秒时仍有 30 余个脚本等待供应商代理传输，50 秒后 Vue/Canvas 已
  挂载但 splash 仍未解除且 WebSocket 尚未建立，继续等待后 splash 消失并显示完整画布和工具栏。
  因此按钮和地址问题已修复，而供应商入口超过 50 秒的首次冷加载不能误报为控制中心故障。
  节点页验收截图为
  `output/webui-studio-r11-20260920/nodes-pro6000-comfyui-r15-2048x1080.png`。
- 完成可用性验证后供应商强制刷新显示实例已变为 `stopped`，节点也已收敛为
  `OFFLINE/DISABLED`；此时官方 8443 地址返回 404 属于关机状态的预期结果。本次没有未经授权
  自动开机或产生新的云 GPU 费用。

### r16 PRO 6000 与 Codex 运行时解耦

- PRO 6000 是局部重绘云推理节点，不安装、不注册也不要求配置 Linux Asset Worker 或 Codex CLI。
- 节点持久标签已设置 `codex_runtime_enabled=false`，同时继续保留
  `cloud_scheduling_enabled=true`；配置变更写入 `audit_logs`。
- Codex 工作台只统计适用的 Codex 节点。PRO 6000 不再出现在运行时列表、异常数量、认证数量或
  探针延迟中；云服务器、GPU 节点、局部重绘调度和云端 ComfyUI 入口均不受影响。
- 适用性使用显式能力标记，并为历史 AutoDL 节点保留 `provider=autodl` 的安全默认排除逻辑，
  不依赖 `5090` 或 `PRO 6000` 设备名称。以后供应商换卡也不会重新产生 Codex 误告警。

## AutoDL 5090 局部重绘延迟审计（历史基线）

对最近 3 条热启动、无重试的 `modelview-inpaint` 任务逐阶段核对，端到端耗时分别为
23.027 秒、19.629 秒和 18.817 秒，平均 20.491 秒。平均拆分如下：

| 阶段 | 平均耗时 | 占比与结论 |
|---|---:|---|
| 排队 | 0.043 秒 | 可忽略，调度器和云优先路由不是瓶颈 |
| 四输入上传与 SHA 回读校验 | 5.786 秒 | 约 10.69 MiB 输入跨公网发送后又被完整读取一次 |
| 5090 GPU 执行 | 13.169 秒 | 三次稳定在 13.10–13.21 秒，属于当前工作流推理主体 |
| 结果下载与持久化 | 1.488 秒 | 返回约 2.73 MiB 结果并落盘 |

证据同时表明：全部上传均为第一次成功且 `verified=true`，没有断线重连、重试、限流或模型重载；
历史 GPU profile 的 SM 利用率为 97–100%、功耗 570–581 W、最高 66°C，因此不是显卡降频或
配额问题。一次 104.645 秒历史样本属于冷启动模型加载；5090 现在只允许局部重绘，其他工作流
无法再驱逐该缓存。

不改变工作流质量的下一步优化是：在云端就地计算输入 SHA 并只返回摘要，取消 10.69 MiB 的
校验回读，预计可减少约 1–4 秒；随后加入按租户和 SHA 隔离的输入缓存，以及实例启动后的受控
预热门禁。GPU 本体约 13 秒若还要明显下降，必须评估模型、分辨率、采样或推理后端变更，不能
在控制面里伪装为网络优化。

## RTX PRO 6000 替换、缓存修复与真实测速

2026-09-20，供应商实例 `app:pro-7894be501780` 保持不变，但实际 GPU 已从 RTX 5090
替换为 `NVIDIA RTX PRO 6000 Blackwell Server Edition`。数据库主键
`autodl-5090-01` 为避免破坏任务、租约、兼容性和审计外键而保留；它不再作为设备型号来源。
Scheduler 每次健康探测都会读取 ComfyUI `/system_stats`，清理 `cuda:0` 和 allocator 装饰后，
自动更新：

- `display_name=AutoDL RTX PRO 6000 Blackwell Server Edition`
- `labels.gpu_model=NVIDIA RTX PRO 6000 Blackwell Server Edition`
- `labels.gpu_model_source=comfy_system_stats`
- `total_vram_mb=97250`

因此后续供应商再次换卡时，无需手工修改节点名称或重建数据库记录；UI 可见名称跟随真实运行时
硬件更新。Web 中原有“控制 5090 实例”和 5090 搜索示例也已改为通用云 GPU 文案。

### r3 缓存误命中根因与 r4 修复

第一次 PRO 6000 任务在四个输入文件上传前失败。远端缓存命令原为：

```text
... && mv "$temporary" "$destination"; trap - EXIT
```

实例替换后远端缓存本来不存在，前面的 `stat/cp` 正确失败，但末尾成功执行的 `trap - EXIT`
覆盖了 shell 退出码，Tunnel 错误返回 HTTP 200；Scheduler 因而把不存在的四个目标文件当成
缓存命中，ComfyUI `/prompt` 以 `prompt_outputs_failed_validation` 拒绝。随后旧逻辑把确定性的
HTTP 400 当成不确定提交，额外等待约 35 秒确认 queue/history，造成“云卡很慢”的假象。

r4 将清理 trap 与复制、SHA 校验、原子 rename 放进同一个 AND-list，并引入
`receipt_version=2`。Scheduler 只信任版本化原子回执；旧回执会独立回读验证，缺失时执行真实
上传。确定性的 4xx 提交拒绝也会立即失败，不再进入 35 秒模糊结果确认。生产验证用已丢失缓存
执行 materialize，返回 `404 {"status":"miss"}`，不再出现伪命中。

### PRO 6000 实测结果

通过公开 `/api/v1/services/modelview-inpaint` 同步接口，使用法线输入的四图 2048×2048
工作流连续执行两次；未改变工作流 JSON、模型、采样、分辨率或输出语义。

| 阶段 | 首轮冷模型/冷缓存 | 第二轮热模型/热缓存 |
|---|---:|---:|
| 调度排队 | 0.083 秒 | 0.051 秒 |
| 调度领取至 GPU 开始 | 4.816 秒 | 0.498 秒 |
| 其中四输入上传/缓存物化至提交 | 4.695 秒 | 0.381 秒 |
| PRO 6000 GPU 原生执行 | 86.176 秒 | 8.406 秒 |
| GPU 完成至下载、校验和持久化完成 | 2.564 秒 | 1.648 秒 |
| 数据库端到端 | 93.636 秒 | 10.600 秒 |
| 客户端请求总耗时 | **93.832 秒** | **10.768 秒** |

首轮四个输入均为 `cache_hit=false`，以 `atomic_promotion_sha256` 上传并重建远端缓存；第二轮
四个输入全部为 `cache_hit=true`，以 `atomic_materialize_sha256` 在云端就地物化。原来约
5.786 秒的四图上传与完整 SHA 回读，在热缓存时降为 0.381 秒。首轮 86.176 秒主要是模型
首次加载，不是调度或网络；热态 PRO 6000 的工作流 GPU 本体为 8.406 秒。

两次结果均为 2048×2048 PNG，分别为 2,311,898 和 2,328,314 字节；响应头 SHA-256 与
落盘文件重新计算的 SHA-256 完全一致。测试后已禁用新旧两组临时 canary client 及其 API key。
客户端报告保存于
`output/autodl-pro6000-cache-r4-20260920/scheduled-canary-client-report.json`。

### r5 SSH 流控优化与复测

结果 PNG 通常为 2–12 MiB，而 Paramiko 默认接收窗口只有 2 MiB。一次 2.3 MiB 输出就会在
传输中途耗尽信用并等待公网往返确认。Tunnel r5 将接收窗口提高到 16 MiB、最大 SSH packet
提高到 64 KiB、双向 relay 块提高到 1 MiB；仍使用阻塞背压、流式 SHA-256 和原子持久化，未启用
对 PNG 无收益的 SSH 压缩，也没有更改工作流或输出语义。

对同一 2,311,898 字节输出连续读取，SHA-256 始终为
`abc8cfe4f7cb9603ef2e55c3e034ce7c1b55568b238453932cd8d853f4964002`：

| Tunnel | `/view` 中位数 | 稳定样本范围 |
|---|---:|---:|
| r4 默认 2 MiB window | 0.3091 秒 | 0.3068–0.4071 秒（另有一次 0.8734 秒抖动） |
| r5 16 MiB window | **0.2690 秒** | **0.2671–0.3017 秒** |

中位数下降 13.0%。随后真实提交一次法线版四输入 2048×2048 局部重绘：输入缓存与提交
0.351 秒，Comfy 原生日志从 `execution_start` 到 `execution_success` 为 8.622 秒，数据库端到端
12.102 秒。结果为 2,343,895 字节 PNG，SHA-256 为
`81effa074ec75c8620be36758741c75a8d616dc8407970767db7d27417766f5d`。本次公网抖动使下载、校验
和持久化阶段为 2.449 秒，所以不能把孤立 `/view` 的 13% 直接等价为整单提速；当前真实热态
物理下限仍是约 8.5–8.6 秒原生推理，加上约 0.35 秒输入准备及可变结果回传。报告和结果保存于
`output/autodl-pro6000-flow-control-r5-20260920/`，临时客户端已再次禁用。

### r19 ComfyUI 稳定直连与云优先复核

供应商 `:8443` 页面依赖浏览器会话授权，实例替换或重启后可能返回 HTTP 403；这不代表
ComfyUI 或调度数据面离线。控制中心现在通过既有常驻 SSH 隧道提供独立的 LAN TLS 入口：

```text
https://10.3.34.11:16006/
```

入口只绑定控制中心 LAN 地址并限制 `10.3.34.0/24`，不向浏览器返回 AutoDL Token、SSH
账号或密码；HTTP、上传和 WebSocket 均由 Nginx 透明转发。GPU 节点页和云服务器页的
`ComfyUI 直连` 会在新标签页打开该入口，不再跳转会 403 的供应商工作区。

发布后实测根页面与 `/system_stats` 均为 HTTP 200，WebSocket `/ws` 完成 HTTP 101 升级；
当前远端版本为 ComfyUI `0.35.0`，队列为空。节点持久标签
`comfyui_browser_proxy_port=16006` 已写入数据库，审计 request ID 为
`autodl-comfyui-direct-20260920`。

云优先策略同时复核通过：`autodl-5090-01` 的真实硬件已动态识别为
`NVIDIA RTX PRO 6000 Blackwell Server Edition`，仍只允许 `modelview-inpaint`，
`cloud_scheduling_enabled=true`、`current_jobs=0`。定向测试证明兼容且运行中的最强云卡优先；
云卡不兼容或不可用时才按原本地排序回退，不会把粗糙度或其他工作流发到该云节点。

## 验证

- Web：ESLint、`vue-tsc`、Vite production build 通过。
- Web：Vitest 9 files / 57 tests 全部通过；覆盖控制中心 ComfyUI 直连、官方入口降级、恶意
  fragment/端口拒绝，以及
  AutoDL 推理节点从 Codex 运行时工作台排除的能力契约。
- Web：`vue-tsc --noEmit` 通过；Vite production 镜像构建通过。
- Nginx：`nginx -t` 通过；直连根页面和 `/system_stats` 为 HTTP 200，WebSocket 为 HTTP 101。
- 调度：云优先、云不兼容本地回退、节点工作流白名单三项定向集成测试通过。
- 部署契约：5 项通过，覆盖 LAN 绑定、独立网络、WebSocket 与流式请求配置。
- Git LFS：`git lfs fsck` 通过，当前分支无待推送 LFS 对象；本轮未把模型、测试输出或镜像
  tar 加入仓库，继续只版本化源码、文档、摘要与镜像身份。
- Web：`npm audit` 为 0 vulnerabilities。
- 浏览器：2040×1069 CSS viewport、DPR 2 实测 8 个核心路由；全部 HTTP 200、无 runtime
  exception、无页面级横向溢出，导航宽度为完整视口而非旧侧栏。
- Python changed scope：`apps/api`、`apps/asset_api`、`packages/gpu_control_core` Ruff 通过。
- AutoDL/环境契约：10 项相关单测通过。
- 缓存回执、旧回执降级、确定性 4xx、动态 GPU 身份及既有上传路由：66 项定向单测通过。
- 5090 工作流白名单：5 项定向兼容性、陈旧缓存抢单和云优先/本地回退测试通过。
- 全仓库非 Blender 广测：809 passed、16 skipped；其余包括历史 MOF hidden-seam fixture 错误和
  当前 MOF-only 路由与三项 legacy PBR 测试的未版本化契约冲突。本轮没有修改外部 UV/MOF
  算法来掩盖该冲突。

Web r2 发布时已有一条真实任务在 `autodl-5090-01` 运行；只滚动 Web 容器后该任务继续处于
RUNNING，API 与 Scheduler 全程 healthy，证明页面发布没有干扰数据面。

## 镜像与回滚

当前生产镜像：

- Web：`gpu-control-web:1.5.23.post4-studio-ui-r19-20260920`
  - manifest digest：`sha256:1069b589522deee8a4f4d92ad7628f1f311ca8aecb854ea48d9d3cb7554bc316`
- Scheduler：`gpu-control-scheduler:1.5.23.post4-pro6000-identity-r6-20260920`
  - manifest digest：`sha256:8e9fe616bce287f667420fb1feb7d20f1fa761a395812cec86acdf24691d5b52`
- Tunnel：`gpu-control-autodl-tunnel:1.5.23.post4-flow-control-r5-20260920`
  - manifest digest：`sha256:d66a550f100bf5caf66c1fe958c99e535d374eef578b0afbdd52f68c953c61f6`
- API：`gpu-control-api:1.5.23.post4-event-wakeup-r1-20260920`
- Provider Controller：`gpu-control-provider-controller:1.5.23.post4-autodl-contract-r1-20260920`

以下为本日早期历史发布，保留用于审计与回滚定位：

- Web：`gpu-control-web:1.5.23.post4-webui-site-r6-unified-20260920`
  - image digest：`sha256:46716abf200a5cdbc639464c65ad41f58f5e5199985736862b521c7e75ae9dd3`
- API：`gpu-control-api:1.5.23.post4-inpaint-only-r1-20260920`
  - image digest：`sha256:0c6be0395ef55d400f60a63c9362d094673651bcfe861607864cb15f0ea4014b`
- Scheduler：`gpu-control-scheduler:1.5.23.post4-inpaint-only-r1-20260920`
  - image digest：`sha256:809e2d36e5451a8bffb57ffc93aacf243a9ecf1515884d7e3219c8f112821d42`
- Asset API：`unified-scheduler-asset-api:1.5.59-sse-stability-r1-20260920`
- Tunnel：`gpu-control-autodl-tunnel:1.5.23.post4-reconnect-r1-20260920`
- Provider Controller：`gpu-control-provider-controller:1.5.23.post4-autodl-contract-r1-20260920`
  - image digest：`sha256:e320eb4e101e5f5b50a76343641a1b43fe39af72406d50eaa17148531da26096`

Web 回滚只需把 `.env` 的 `WEB_IMAGE_TAG` 恢复为上一个已验收版本
`1.5.23.post4-studio-ui-r16-20260920`，再执行：

```bash
docker compose --env-file .env -f deploy/control-plane/compose.yaml up -d --no-deps web
```

## Codex 授权边界

- 4090、4070 Ti：Worker 在线，但各自 refresh chain 已过期。
- 5070 Ti：Worker 在线，但此前设备授权未完成，持久目录中没有 `auth.json`。
- AutoDL PRO 6000：仅属于云端局部重绘推理链路，不属于 Codex Runtime，不需要 Linux Asset
  Worker 或 Codex 授权，已从 Codex 工作台及其异常指标排除。

三台现有本地 Worker 必须逐台完成 device auth。完成后 Worker 会在约 60–90 秒内自动执行真实模型
探针并恢复 `AUTHENTICATED/HEALTHY`，无需直接改数据库或重启 Worker。
