# AutoDL 云服务器控制面与 5090 法线局部重绘验收

日期：2026-09-18

## 目标与边界

AutoDL 实例是独立的云 GPU 资源池，不并入现有五台物理服务器的“GPU 节点”清单。
控制台新增“云服务器”页面，负责实例盘点、运行指标、访问入口、开机、关机和按需 SSH
凭据。AutoDL Token 只允许存在于 Provider Controller 的只读 secret 文件中，不进入浏览器、
API 响应、日志、数据库或 Git。

本次同步的是已经批准的 `modelview-inpaint` 工作流，不修改其业务语义：

- 版本：`2026.09.18-refcontrol-normal-2step-r1`
- 四个输入：原图、材质参考图、mask、法线参考图
- 基础 LoRA 强度：`1`
- 法线 RefControl LoRA 强度：`0.8`
- `BasicScheduler`：`simple / 2 steps / denoise 1`
- 最终输出：节点 `29`，一张 `2048 × 2048` 图片

## 控制链路

```text
浏览器 /cloud-servers
  -> GPU Control API（JWT、operator 权限、审计、持久操作意图）
  -> Provider Controller（内部 HMAC、Redis nonce、防重放、写操作开关）
  -> AutoDL App / Pro API

PostgreSQL
  <- provider_instances：最近观测状态、期望状态、节点映射、调度资格
  <- provider_operations：幂等键、状态机、尝试次数、错误、完成证据
```

API 永远先提交 `provider_operations`，后台 reconciler 再执行外部开关机。API 进程重启后会
继续领取 `PENDING / WAITING / UNCERTAIN` 操作；超过 90 秒的 `IN_FLIGHT` 操作也会重新回读
云端状态后继续收敛。Provider Controller 的写操作在初次部署时默认关闭；完成只读盘点、
幂等操作、重启恢复和真实工作流 canary 后再显式开启。

关机时，如果云实例已经映射到调度节点，API 会先持久化 `DRAINING`，并检查
`current_jobs`、外部占用、陌生 ComfyUI 队列和活动 `NodeLease`。任一门禁未清空即拒绝关机。
过渡态或未知 AutoDL 状态不会触发电源写操作。

云服务器页面支持当前账号全部实例盘点（验收时共八台）、手动强制刷新、搜索/筛选、单台或批量开关机、一次性定时
开机/关机、操作阶段和耗时、AutoDL 原生定时关机时间以及按需 SSH。定时意图、执行状态和
派发证据都在 PostgreSQL 中；浏览器关闭或控制面重启不会丢失。

## 安全与部署约束

- Token 文件：`/srv/gpu-control/secrets/providers/autodl.token`，只读挂载到 Provider
  Controller；不通过环境变量传递真实 Token。
- Provider Controller 无宿主机公开端口，只加入 `backend` 网络；容器使用只读根文件系统、
  `cap_drop: ALL` 与 `no-new-privileges`。
- API 与 Provider Controller 之间每次请求签名覆盖 method、path、query、body、timestamp 和
  nonce；Redis `SET NX EX` 原子阻止重放。Redis 不可用时写操作 fail closed。
- SSH 凭据只在 operator 明确确认后按需读取，响应 `Cache-Control: no-store`，审计只记录
  host、port、username，Web 页面 60 秒后清除内存中的密码。
- 初次上线保持 `AUTODL_MUTATIONS_ENABLED=false`。全部实例盘点、运行中 5090 的幂等
  start、重启恢复和法线局部重绘 canary 均通过后，已于 2026-09-18 显式切换为 `true`。
  随后的真实定时关机、定时开机和自动恢复验收也已通过；从未执行释放、重装或清盘。
- 开机恢复只允许数据库中的 `bootstrap_profile=comfyui-6006-v1`。API 和 Web 不接受任意
  shell；Provider 执行器将该白名单 profile 映射为固定启动脚本。profile 只在首次开机写
  请求中发送，派发栅栏之后的重试一律是 GET 状态查询，不会重复开机或重复执行脚本。

## 5090 同步方式

测试实例：`app:pro-7894be501780`。同步使用严格 known-hosts 校验、可续传 SFTP、每个文件
SHA-256 校验和版本化暂存目录：

`/root/autodl-tmp/gpu-control/modelview-inpaint-normal-2step-r1`

模型先传到 `.partial`，远端 SHA-256 匹配后才原子改名。安装时只创建可回滚符号链接；已有
目标会移动到 `/root/autodl-tmp/gpu-control/backups/`，不会被删除。AutoDL 公共存储中的 VAE
只有在 SHA-256 与批准文件一致时才复用。

## 任务数据面与路由结论

5090 已映射为 `autodl-5090-01`，通过仅 Scheduler 可访问的常驻 SSH 隧道提供 ComfyUI 数据
面。隧道不发布宿主机端口，严格校验 known-hosts，断线后指数退避重连。已提交任务保留原
`prompt_id`、Job、JobAttempt 和 NodeLease；短暂断线只回读该 prompt 的 history，绝不重复
提交，超过原工作流总超时才失败并释放租约。

隧道重连后不会仅凭 SSH 成功就开放数据面，而是先探测远端 `127.0.0.1:6006`。端口未就绪
时，每个 SSH 会话最多调用一次白名单恢复 profile；该 profile 只能映射到固定命令
`bash /root/autodl-tmp/gpu-control/start-comfyui.sh`，不接受来自 API、页面或 Provider 响应的
任意 shell 内容。固定脚本具备进程和 HTTP 双重幂等检查。6006 真正可连接后隧道才进入
ready，从而使实例重启、ComfyUI 进程未自动拉起以及 SSH 地址重新分配均不需要人工修复。

最终审计进一步把远端脚本纳入仓库和 Tunnel 镜像；2026-09-20 修复重连启动脚本的文件描述符
继承问题后，当前固定 SHA-256 为
`2ce355754406f7cccf271668a11a907821620845774bf97a651f37d876f66d72`。Provider、Tunnel 与仓库
脚本字节由同一回归测试三方校验，防止开机契约再次漂移。每次 SSH 建连后先经
SFTP 校验；内容缺失或漂移时写入固定临时文件、校验摘要、设置 `0700`，再通过
`posix_rename` 原子生效。Provider 开机命令和 Tunnel 恢复命令都先校验同一摘要。脚本使用
`flock`、锁内二次探测和完整 argv 进程匹配，避免开机 bootstrap 与 Tunnel 同时启动两份
ComfyUI；仅在精确匹配进程超过 180 秒仍不健康时才终止并重启。Tunnel 等待远端 ready 也
限制为 180 秒，超时会关闭 SSH 并重新连接，不会永久卡住；ready 判定必须由
`GET /system_stats` 返回 HTTP 200，单纯 TCP 端口打开不再算就绪。

GPU 节点页的“打开 ComfyUI”对云节点按 `management.node_id` 映射 Provider inventory 中的
官方 `service_6006` HTTPS 入口，不再把 Docker 私网地址
`http://autodl-5090-tunnel:16006` 交给浏览器。入口只允许 AutoDL/SeetaCloud 官方域名、无
URL userinfo 且绑定唯一；不满足条件时 fail closed。本地节点仍使用原有浏览器入口。

`modelview-inpaint` 当前生产路由为：先选所有健康、兼容、生命周期为 running 且可调度的
AutoDL 节点，按 GPU 总显存、可用显存和利用率排序，优先最强卡；云节点不可用、未就绪或
不兼容时，在同一轮候选选择中继续走原本的本地节点排序，不等待云机开机。其他工作流维持
原有策略。2026-09-18 完成自动冷启动和真实 API 验收后，5090 的 `test_only` 门禁已移除，
测试客户端及其临时密钥已禁用，云优先路由正式启用。

## 验证记录

- API/Provider 集成与单元回归：`61 passed`
- 白名单 bootstrap 专项（Provider、Controller、真实 reconciler POST → GET）：`19 passed`
- Migration `0016` upgrade/downgrade smoke：通过
- Web lint：通过
- Web 单元测试：`46 passed`
- Web production build：通过
- Python Ruff：通过
- API、Provider Controller、Web 容器构建与健康检查：通过
- 真实重启恢复演练：Provider 不可用时操作
  `f640bd89-8a1e-485d-8601-7eac9a177c19` 持久化为
  `PENDING / PROVIDER_CONTROLLER_UNAVAILABLE`；API 与 Provider Controller 重启后自动回读
  运行中的 5090，并收敛为 `CONFIRMED`。演练没有发出真实开机或关机写操作。
- 写操作门禁上线验收：`AUTODL_MUTATIONS_ENABLED=true`；操作
  `2ba1673b-7e24-4b08-b543-edf81e9ebdad` 对运行中的 5090 请求 `running`，一次尝试即
  `CONFIRMED`，观测状态仍为 `running`，没有改变实例电源状态。
- 真实定时关机：计划到点后 `20.093 s` 收敛为 stopped；操作
  `6450f32d-fa27-4b14-beb1-0f29c2ed7dac`，首次写操作一次，后续 8 次均为只读状态查询；
  节点最终为 `DISABLED`。
- 真实定时开机：计划到点后 `8.159 s` Provider 操作确认，约 `53 s` ComfyUI 可访问，约
  `60 s` 节点恢复为 `ONLINE / ACTIVE`。全程未人工 SSH；固定启动脚本由 AutoDL 开机参数
  自动执行。
- 自动冷启动后的 5090 真实法线局部重绘：`PASSED`
  - 模型冷态：端到端 `95.315 s`，服务端 `94.333 s`，排队 `0.020 s`，GPU `86.574 s`
  - 模型热态：端到端 `18.209 s`，服务端 `17.948 s`，排队 `0.012 s`，GPU `13.829 s`
  - 两笔均由 `autodl-5090-01` 接单，返回节点 `29` 的 `2048 × 2048` PNG，传输 SHA-256
    校验一致
  - 证据目录：
    `/srv/gpu-control/jobs/deployment-autodl-5090-modelview-normal-20260918/cold-boot-automatic-canary`
- 正式生产客户端路由验收：`PASSED`。临时客户端切为 `client_kind=production` 后提交热态
  请求，`autodl-5090-01` 在 `0.013 s` 内接单，GPU `13.228 s`、服务端 `16.434 s`、端到端
  `17.179 s`；返回 `2048 × 2048` PNG 且 SHA-256 一致。验收后客户端及全部临时密钥已
  禁用。证据目录：
  `/srv/gpu-control/jobs/deployment-autodl-5090-modelview-normal-20260918/production-route-acceptance`

## 第二轮真实重启、自愈与性能复验

在数据库活动任务、活动租约和远端 ComfyUI 队列均为零后，执行了第二次真实关机/开机；
没有释放实例、重装系统、清盘或删除业务数据。

- 真实关机操作：`8df9e6f9-9650-465a-b6c9-b83e1d9ffa6c`
  - 操作创建后约 `10.644 s` 收敛为 `CONFIRMED / shutdown`。
  - 节点先进入 `DRAINING`，Provider 确认 stopped 后变为 `DISABLED / DEGRADED`。
- 真实开机操作：`87e081dc-0819-4a60-a577-44995270d814`
  - Provider 在约 `8.316 s` 内收敛为 `CONFIRMED / running`。
  - 固定隧道约 `59.109 s` 后重新访问 `/system_stats`，约 `62.506 s` 后节点恢复
    `ONLINE / ACTIVE`，容器健康状态恢复为 `healthy`。
  - 隧道日志真实记录 `remote_recovery_invoked`：SSH 恢复时 6006 尚未启动，固定白名单脚本
    自动拉起 ComfyUI，随后才记录 `tunnel_ready`。本次没有人工 SSH 或手动启动进程。

重启后通过公开的 `/api/v1/services/modelview-inpaint` 连续提交真实四输入 2048 任务：

- 模型冷态：端到端 `92.279 s`、服务端 `91.789 s`、排队 `0.011 s`、上传前段
  `5.428 s`、GPU `84.987 s`。
- 随后五个模型热态样本全部由 `autodl-5090-01` 完成，全部返回 `2048 × 2048` PNG 且响应
  SHA-256 与落盘文件一致：
  - 服务端平均 `17.535 s`，范围 `16.777–18.933 s`；
  - 调度排队平均 `0.018 s`；
  - 四输入上传平均 `3.826 s`，范围 `3.123–5.884 s`；
  - GPU 平均 `11.914 s`；
  - 结果下载平均 `0.704 s`。
- 上述复验后又有一笔真实生产客户端请求成功路由到 5090：服务端 `16.935 s`、排队
  `0.016 s`、上传 `3.076 s`、Comfy 执行窗口 `13.112 s`、下载 `0.600 s`。
- 四个输入现为最多四路并发，但每一路仍保留 overwrite-safe 重试、完整 GET 回读、长度和
  SHA-256 校验；任一输入失败都不会提交 prompt。由于这条链路主要受共享上行带宽限制，
  与上线前样本相比没有观察到稳定的大幅收益，因此没有降低完整性校验，也没有继续引入
  跨任务远端缓存。
- GPU 一秒采样显示图计算阶段 SM 利用率持续 `97–100%`、功率约 `570–581 W`、最高温度
  `66 °C` 且无 thermal violation。这证明热态约 12 秒主要是真实 9B Q5 图计算，而非调度
  空转、模型重复加载或网络等待；若不改变工作流、模型或输出质量，不能把这段压缩成
  “无感”。
- 相同 2.36 MB 输出的只读传输基准：常驻 SSH 隧道中位数 `477.7 ms`（约 `4.71 MiB/s`），
  Provider 官方 HTTPS 入口中位数 `1193.8 ms`（约 `1.89 MiB/s`）。官方入口小请求 RTT 更低，
  但批量数据明显更慢，因此调度数据面继续保留 SSH 隧道；官方 HTTPS 只用于浏览器入口。

复验证据保存在：

- `output/autodl-5090-restart-speed-20260918`
- `output/autodl-5090-speed-repeat-20260918`
- `output/autodl-5090-gpu-profile-20260918`

复验结束后 `autodl-5090-canary-20260918` 已禁用，新增的三个临时 API Key 全部禁用。

## 生产镜像与代码审计

当前生产使用：

- Scheduler：`gpu-control-scheduler:1.5.23.post3-autodl-upload-parallel-r1-20260918`
- Web：`gpu-control-web:1.5.23.post3-autodl-access-r2-20260918`
- Provider Controller：`gpu-control-provider-controller:1.5.23.post3-autodl-bootstrap-r2-20260918`
- 5090 tunnel：`gpu-control-autodl-tunnel:1.5.23.post3-autodl-reconnect-r2-20260918`

上传并发、SSH 自愈与浏览器入口分别做了专项审计。上传仍使用每 Job 独立目录，不共享租户
输入；取消会传播到所有上传子任务，提交前重新锁库检查取消状态。SSH 自愈命令为编译时
白名单，凭据仅驻留内存且日志不记录命令输出或密码。浏览器入口强制官方 HTTPS 域名、拒绝
userinfo 和歧义绑定。审计后还覆盖了云 inventory 故障和部分成功场景，保证本地节点页面不
被 Provider 故障拖垮，云映射缺失时也不会退回 Docker 私网地址。

已知非阻断风险：四路上传中一路失败时会等待其他在途请求安全收敛后再返回，因此失败响应
可能比旧串行首错稍慢；需要持续观察 `UPLOADING p95`、`COMFY_TIMEOUT` 和
`COMFY_CONNECT_ERROR`。计划停机仍必须先清空运行任务、租约和远端队列；实例在任务执行中
被云平台强制断电时，ComfyUI 内存中的执行不能保证续跑。

最终部署时管理员已于 `2026-09-18 11:07 UTC` 从云服务器页面主动关闭 5090，操作
`0ffcd035-281a-4f89-9b3a-b5322367749b` 在约 `10.407 s` 后收敛为
`CONFIRMED / shutdown`。系统保持其 `stopped / DISABLED / DEGRADED` 状态，不为发布验收擅自
再次开机；节点在心跳超时后为 `OFFLINE / DISABLED`。新版 Tunnel 在实例关闭时 `/health/live=200`、容器保持 healthy，
`/health/ready=503` 正确表达远端未就绪；下一次管理员开机后会先自动同步上述摘要固定脚本，
再恢复 ComfyUI 和 Scheduler 数据面。该 r2 自动同步路径已通过 `22 passed` Tunnel/SFTP/
HTTP-ready/Compose 契约测试、`31 passed` Python 3.11 Provider/Tunnel/Compose 套件和
`9 passed` Provider Controller 测试；真实关机—开机链路由本节前述第二轮复验覆盖，但 r2
首次远端脚本替换将在下一次管理员开机时执行并留下 `remote_start_script_ready` 日志。
