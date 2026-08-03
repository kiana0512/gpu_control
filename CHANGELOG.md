# Changelog

## 1.5.8（候选）— 2026-08-03

- 三台 Asset Worker 改用各自独立、可写且持久化的 Codex home；真实探针、认证轮换、TLS 环境、
  超时回收和同节点调用串行化恢复，不再跨节点复用 refresh token。
- Codex 管理视图与领取门禁使用同一组新鲜度证据；只认精确 Linux Worker、在线心跳、有效认证和
  未过期真实探针，过期状态显示为 `STALE`，不再出现“页面健康但任务持续排队”。
- Substance Baker Agent v4 上报宿主级进程、Agent 代际和启动时间；长烘焙持续续租，租约歧义恢复
  同时要求零 Baker 进程与其后的 ComfyUI 空闲证据，签名 nonce 禁止重放。
- 每个 Substance 槽位增加全局单实例锁与持久作业互锁，Agent 重启、孤儿 Baker 或重复 Agent 均
  fail closed，不能造成同槽双领或提前恢复 3090-B。
- 新增迁移 `20260803_0012` 保存 Agent/进程证据；修复控制面部署脚本遗漏 `asset-api` 镜像的问题。
- 不修改外部 ImageClip/ModelView 工作流、模型、prompt、参数、图拓扑或输出语义；生产任务清零、
  v4 Agent 协调升级和真实 PBR canary 完成前保持候选状态。

## 1.5.7 — 2026-08-03

- 修复 Windows Substance Baker 等待当前 ComfyUI 帧时 reservation 续租未提交的问题；PBR 取得
  3090-B 下一轮优先权后，Scheduler 不会在 TTL 回滚窗口继续派入新帧，CPU Asset Worker 仍独立接单。
- Windows Baker 升级为精确 v3 身份：不调用 `/free`，不停止或重启 ComfyUI；每次烘焙前后硬校验
  ComfyUI 容器 `Id`、`StartedAt`、`RestartCount`、运行状态与健康状态。
- 连续性异常进入持久 `recovery_required` 闭锁，成功回执必须携带 no-explicit-eviction 和进程连续性
  证据；旧 v2 Agent fail closed，不能继续领取任务。
- Asset Web/API 展示真实 3090-B 共享关系、PBR 等待原因、Baker 进程槽位和下一轮预约；恢复、离线、
  管理员保留、外部 GPU 活动与未纳管队列不会误报为可切换烘焙。
- 版本、Compose 默认镜像和 release identity 门禁统一为 1.5.7；未修改任何外部 workflow、模型、
  prompt、采样参数或输出语义。

## 1.5.6 — 2026-07-30

- Retopology advisory 模式把通过完整性门禁的候选 BLEND/FBX 以正式 `blend`/`fbx` artifact 合同交付；几何 QA 告警与诊断证据保留，不再把用户需要的模型文件隐藏为诊断件。
- Scheduler advisory lock 改为专用 autocommit 连接并持续验证 backend/锁所有权，消除长期 `idle in transaction`；失锁后停止领取并执行受控接管恢复。
- Windows Substance Baker 与 ComfyUI 的 3090-B 物理 GPU 互斥改为持久 pending/fence/recovery 闭锁，加入生产优先、两阶段租约恢复、健康标签合并和统一锁序。
- 所有 Asset 完成入口在正式发布 artifact 前执行取消安全点，避免取消与完成并发时错误交付。
- 六 API 压测器区分同步端到端与异步 admission，自动恢复同步 Roughness 孤儿、执行严格范围清场，并支持有界极限压力的完整证据门禁。
- WebUI 延续已发布的任务/API/工作流分类、完整阶段时间、性能分析和可解释调度界面；本补丁从同一 source revision 重建四个控制面镜像，避免组件版本漂移。

## 1.5.5（候选）— 2026-07-30

- 对齐动画管家优化后的 V4.1 合同：父批次固化 ImageClip 身份快照、完整阶段时间、节点/attempt 性能证据，并在 create、父 GET、分页 manifest 和最终 ZIP manifest 返回同一身份。
- 封闭批次子任务取消和产物旁路；父批次完整 `SUCCEEDED` 前，父/子结果均不可读取，节点中断也不能绕过持久化父取消操作。
- 父取消改为显式 API Key、稳定幂等键、持久 operation 和完整审计；公共 cancel POST 受理回执使用 `CANCEL_REQUESTED`，持久父 GET 在收敛期间仍使用 `CANCELLING`；没有合法取消 operation 的终态不会被补写成合法 `CANCELLED`。
- 新增从生产基线 `20260729_0010` 升级的候选迁移 `20260730_0011`；该迁移尚未在生产执行，必须随完整 1.5.5 drain、备份和回滚门禁发布。
- 调度器遍历全部可用节点寻找兼容任务，避免首个不兼容节点造成队头阻塞；prompt 提交使用持久确定性 client ID，崩溃恢复只做对账，不重复 POST。
- API、Scheduler、Asset API、Web 和节点代理增加统一版本/源码 revision 证据；候选镜像带 OCI labels，发布校验器要求 clean commit、registry digest 和与 manifest 绑定的 SBOM。
- 新增根目录和 Web 专用 `.dockerignore`，排除 `.git`、LFS 归档、缓存、运行时数据与本地密钥，缩小候选镜像构建上下文并避免把宿主数据带入镜像。
- 未修改 ImageClip 工作流、模型、提示词、参数、图拓扑或输出语义；本候选版本在联合故障注入、固定素材基准和灰度完成前不标记为生产验收。

## 1.3.3 — 2026-07-27

- 修复批量父任务与单帧任务进度在 ComfyUI 内部节点切换时回退的问题；任务进度和聚合进度现在均为单调值。
- 新增真实批量序列帧验收器，强制校验幂等重放、父子聚合、目录顺序、文件名、SHA-256、PNG 解码与 Alpha 通道。
- 完成 20 个隔离测试客户、60 个真实 7:3 混合任务持续高压，60/60 首次执行成功、60/60 产物通过。

## 1.3.2 — 2026-07-27

- 修正 ComfyUI UI workflow 中 `control_after_generate` 控制值导致的 API
  KSampler 参数错位，发布 ImageClip `2026.07.27-721f7d6-r1`。
- 节点健康检查定期采集实际 ComfyUI class inventory；工作流兼容性现在对缺失
  节点类型 fail closed，避免任务提交后才发现插件未加载。
- 补齐只读模型挂载下 ComfyUI-Nunchaku 启动所需的空模型目录，并固化到
  Ubuntu 初始化与模型同步脚本。
- 压测器改为按 API 返回 job ID 追踪，不受 Nginx 请求 ID 重写影响；任一推理
  失败、产物缺失或校验失败都会使整轮压测失败。

## 1.3.1 — 2026-07-27

- 将 ImageClip 生产工作流升级到远端 `main` 提交 `721f7d6`（v4.3），API 模板只保留最终 `SaveImage #25` 的祖先子图。
- 节点签名心跳新增 ImageClip Git 提交和确定性内容哈希；调度领取时实时硬校验，不一致节点不能接抠图任务。
- 修复三节点 NVIDIA 内核/用户态版本不一致导致 GPU 利用率和显存指标失真的问题。

## 1.3.0 — 2026-07-27

- 新增真实客户与压力测试客户分类；管理台总览、任务和 API 客户按类型隔离展示。
- 调度器保证兼容的真实业务优先领取 GPU，测试任务仅使用真实租户并发限制之外的空闲算力。
- 修正预计清空时间的在线槽位计算，三台 ACTIVE GPU 均计入实时吞吐能力。
- 调度策略页升级为三节点实时视图，展示当前执行槽位、节点健康、已启用工作流和实际分配规则。
- 新增可复现的 7:3 ImageClip/ModelView 混合真实 GPU 压测工具、产物 SHA/图片/Alpha 校验和容量报告。

## 1.2.0 — 2026-07-24

- 新增 ImageClip RGBA 序列帧批次 API：严格 manifest、ZIP_STORED、逐帧大小/SHA-256/图片校验与租户幂等。
- 新增父批次、帧、事件和批次产物持久化；内部帧任务分布到三台 GPU，支持有界投喂、重试、取消和重启恢复。
- 结果仅在全量帧 PNG/Alpha、路径、顺序和 SHA-256 校验通过后原子发布，失败不暴露部分结果。
- Web 任务列表以一个父批次展示，逐帧进度、节点分布、错误和完整结果归档统一置于详情页。
- 新增动画管家 V2 接口交接、生产部署记录和批次安全/幂等/隔离/归档测试。

## 1.0.0-rc1 — 2026-07-21

- 全量替换旧单机 SQLite/React/进程管理架构。
- 新增 PostgreSQL 任务真相、asyncio 单主调度、3090 主池与 4090 Reserve/Overflow。
- 新增可复现 ComfyUI 镜像、Fake ComfyUI、Node Agent、gpuctl 与三机部署脚本。
- 新增 FastAPI 公共/管理 API、RBAC、审计、回调、工作流注册和安全存储。
- 新增 LiClick 风格 Vue 管理台以及 Prometheus/Grafana/Loki/Alloy/Alertmanager。
- 新增空机部署、灾备、故障和 30 项验收文档。
