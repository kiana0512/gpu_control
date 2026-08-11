# 实施状态

最后更新：2026-08-11
版本：生产 API/Scheduler 1.5.12、Web 1.5.11-retopo-direct-v2 / Asset API
1.6.22-retopo-reliability-v1 / Worker 1.4.24-retopo-reliability-v1 / DB 20260810_0013；源码审计、
自动拓扑对齐包 v3.0.3 的高模坐标权威恢复、输出契约恢复、认证刷新持久化、逐项失败诊断、
triangle-soup 安全工作副本、
低模拓扑/UV 指纹保护和高低模 FBX 重导验证已完成真实生产任务。v3 同任务生成按批准合同不执行旧版
自动七方向复核，由用户检查最终外观。三台 Linux Worker 均为 `ONLINE`；各节点升级验证后均恢复过
`ACTIVE`，3090-B 在真实 Substance 烘焙期间会由 Asset API 临时置为 `DRAINING`，这是物理 GPU 互斥
保护，不是 Worker 升级失败。
真实抠图继续三卡并行，发布未重启三台 ComfyUI。第三次正式 100 VU 在
execute 前由用户取消，r7 为 0 请求、0 压测任务。综合发布见
`98_2026-08-10_GPU_CONTROL_1_5_11_AUDIT_RELEASE_AND_100VU.md`，高低模对齐热修复见
`99_2026-08-10_RETOPOLOGY_ALIGNMENT_V3_HOTFIX.md`，FBX 浏览器米制热修复见
`100_2026-08-10_RETOPOLOGY_FBX_BROWSER_METER_HOTFIX.md`，最终包围盒恢复 V2 与真实失败任务重试见
`101_2026-08-10_RETOPOLOGY_ENVELOPE_V2_HOTFIX.md`，最终纯变换交付和生产证据见
`102_2026-08-10_RETOPOLOGY_TRANSFORM_ALIGNMENT_AND_UV_DUAL_MODE.md`，Substance GLB 输入热修复见
`106_2026-08-11_SUBSTANCE_GLB_INPUT_HOTFIX.md`，Direct V2 源轴向锁定与视觉 QA 叠加证据见
`107_2026-08-11_RETOPOLOGY_SOURCE_AXIS_VISUAL_QA_HOTFIX.md`，进度/ETA/重试热修复见
`108_2026-08-11_RETOPOLOGY_PROGRESS_AND_RETRY_HOTFIX.md`，v3.0.0 当前生产发布见
`109_2026-08-11_AUTO_RETOPO_ALIGN_V3_RELEASE.md`，碎片高模/破面交付修复与 v3.0.2 发布见
`110_2026-08-11_RETOPOLOGY_FRAGMENTED_SOURCE_TOPOLOGY_HOTFIX.md`，UV 输入单位继承修复见
`111_2026-08-11_UV_SOURCE_UNIT_PRESERVATION_HOTFIX.md`，自动拓扑可靠性补丁见
`112_2026-08-11_RETOPOLOGY_OUTPUT_CONTRACT_AND_AUTH_RELIABILITY.md`。

## 2026-08-11 UV 交付继承源 FBX 单位热修复

- 项目 `a898d6c6-d01c-414a-a56e-3a6b9c29b38d` 的高模和拓扑低模都使用
  `UnitScaleFactor=1`；UV 交付适配器却固定输出 `100`。Blender 世界空间几何仍然对齐，但烘焙页的
  原始包围盒路径因此把低模缩小 100 倍，显示尺寸差 `99.0%`、中心偏移 `46.5%`。
- Worker `1.4.23-uv-source-units-v2` 读取输入 FBX 的单位合同，并只在 UV Skill 完成后按源单位重新导出；
  输入为 `1` 就交付 `1`，输入为 `100` 就交付 `100`，非 FBX 输入使用浏览器安全的米制默认值。
- 适配器不修改源模型、网格、拓扑、UV 或材质；全新 Blender 场景回读必须同时通过世界包围盒、对象
  结构和源单位门禁。输入单位不受支持时硬失败，不发布可能缩放错误的制品。
- 精确故障样本本地 Blender 5.1.2 回归：顶点 `2936`、边 `9000`、面 `6000`、循环 `18000`、UV 层
  `1`、材质槽 `1` 全部不变；高低模浏览器包围盒尺寸差约 `0.22%`、中心偏移约 `0.000005%`、
  轮廓比例差约 `0.13%`。单位 `100` 的回归样本也保持 `100` 且世界包围盒误差为 `0`。
- 三台 Worker 已逐台排空并滚动到统一镜像
  `sha256:02652e8b643eeb583a20523e7fc1f4c41f95255e5c18a07c0744479a3381ca45`；真实同样本 UV canary
  `b4b87366-0d77-4cd3-a310-2b4a7bc6ce23` 一次成功，单位 `1 / 1`、世界中心/尺寸误差 `0`、结构完全
  保持，生产 Three.js 读取后的尺寸/中心/轮廓差分别为 `0.220394% / 0.000005108% / 0.125617%`。

## 2026-08-11 自动拓扑 triangle-soup 与闭合流形交付 v3.0.2

- 精确故障高模是被 FBX 拆成 `38697` 个面分量的重复顶点 triangle soup；原算法直接降每个碎片，
  产生 `20308` 条开边、`41436` 条游离边和 `6058` 个重复点。
- 新源准备器只在临时工作副本上以严格容差焊接同位置顶点；原高模不变。精确问题副本移除
  `159802` 个重复点后成为 1 个闭合流形，同时保持 `300000` 面和原世界包围盒。
- 同一副本按原故障的 5% 比例降到 `15000` 面后，开边、游离边点、重复点面、退化面、非流形边和
  错误朝向全部为 0，1 个 UV 层保留；Blend 保存回读和高低模 FBX 新场景回读全部通过。
- finalizer 和 Asset API 对生成 Blend、保存回读、FBX 回读执行三层闭合流形硬门禁，失败件不发布、
  不自动重复建模。
- 生产 Asset API 与三台 Worker 已滚动到 v3.0.2，三节点包自检、Codex 与 RetopoFlow 探针均通过；
  真实抠图任务全部等待自然完成后才替换 Worker，三台 ComfyUI 未重启。

## 2026-08-11 自动拓扑与原坐标对齐包 v3.0.0

- 用户批准 ZIP SHA-256 为 `0a6e539a03e6dcecd9518c6fa592c112892f829717d2c768721463796a604138`；
  仓库正式 24 文件和镜像内包清单全部通过，Skill ID 为 `blender-auto-retopo-align`。
- Asset API 已升级为 `1.6.19-retopo-align-v3`，三台 Worker 已逐台排空并升级为
  `1.4.20-retopo-align-v3`；三台运行 Worker 镜像 ID 均为
  `sha256:0505e57d35fb83b4f9fc2fa271ebe48847f6099c6850426ce438a4e13016945d`，身份均为
  `ONLINE / AUTHENTICATED / HEALTHY`。
- 真实任务 `c836a9dc-ec36-498e-a1ed-0e962d8ed666` 一次成功：高模 300000 面，低模 18000 面且有
  1 个 UV 层；源矩阵和中心误差为 0、尺寸误差约 `0.3564%`，手性一致，FBX 新场景回读误差低于
  `1e-7`，低模结构完全一致。
- 正式交付 10 件制品全部非空并通过 SHA；报告证明没有 ICP、没有自动二次建模、没有修改低模拓扑
  或 UV，最终状态等待用户视觉检查。
- 三台 ComfyUI 的容器 ID、镜像、启动时间和 `RestartCount=0` 均保持不变；真实 ImageClip 任务先
  自然结束再替换对应 Worker，没有中断业务任务，也没有执行用户已取消的压力测试。
- 最终状态采集时 3090-B 正在执行一笔与本发布无关的真实 Substance 烘焙，节点按设计显示
  Asset API 自有 `DRAINING`；Linux v3 Worker 仍为 `ONLINE / HEALTHY / 0 jobs`。

## 2026-08-11 Direct V2 进度、ETA 与重试热修复

- 页面 92%/99% 不等于死锁：Worker 持续每 15 秒续租，但旧进度在 8 次心跳后就到达阶段上限；
  Direct V2 还把 7200 秒硬超时当成 ETA，所以显示“剩余约 116 分钟”。
- 新 Worker 使用 600 秒正常估算窗口和基于实际经过时间的阶段进度；超过正常窗口后 ETA 改为未知，
  不再把 2 小时安全上限冒充预计时间。
- 重试任务的活动进度重置为 0/1，不再继承上一次的 99%；上一轮进度保留在事件证据中。
- 高低模质量门禁已经证明候选不是同一可视资产时，不再从头执行第二轮昂贵生成；网络或 Worker
  瞬时故障仍保留自动重试。这不放宽对齐、UV、七视图或 FBX 回读门禁。

## 2026-08-11 Direct V2 源轴向与视觉 QA 热修复

- 失败任务 `2b68690b-d616-438f-97e5-503369807c9b` 已定位为拓扑完成后的视觉 QA 拒绝；Worker、Codex
  认证和生成阶段均正常。第二轮低模中心、尺寸、UV 与 FBX 回读均通过，但通用 ICP 对细长手柄加入
  约 `0.46°` 微旋转，且独立 reviewer 同时看到了预对齐展示图，放大为左右/顶底轮廓误判。
- Direct V2 交付优先锁定高模原轴向，只搜索整体等比缩放和中心平移；该候选必须通过原有表面、中心、
  尺寸、非镜像门禁，否则才回退通用 proper-rotation ICP。没有复制高模对象变换，也没有 XYZ 非等比缩放。
- 仅在保留并隐藏原始低模之外的 `BAKE_LOW` 副本上三角化 4 个 N-gon、统一闭合壳体朝外法线并生成
  UV；严格审计结果为 320 面、384 triangle-equivalent、0 N-gon、0 退化面、0 非流形边、0 负体积壳体。
- 视觉 reviewer 新增确定性轮廓叠加图（绿=重合、蓝=仅高模、橙=仅低模），只以最终七视图和叠加图
  判定；初始移开展示图继续保留作审计证据，但不得作为最终失败依据。数值仍不能覆盖真实视觉错位。
- 同一失败样本 Blender 5.1.2 复测：旋转严格 `0°`、中心误差 `0`、三轴尺寸误差最大约 `1.05%`、
  高模 100000 面/低模 320 面、低模 UV 有效、全新场景 FBX 重导通过；原候选 Blend SHA 未改变。

## 2026-08-11 Substance Baker GLB 输入热修复

- 一键烘焙 422 已定位为 Asset API 旧白名单只接受 FBX/OBJ；请求未入队，和对齐、UV、Worker 或
  Substance 执行失败无关。
- 生产 `substance3d_baker.exe 15.1.0` 已对真实 GLB 原生解析成功，Baker mesh 合同扩展为
  `FBX / OBJ / GLB`；不转换模型，不修改坐标、拓扑、UV 或材质，仍拒绝 `.blend` 和外链式 `.gltf`。
- 真实 GLB 高低模 canary `20412247-a0c4-44b7-8acf-97dd1b40a73d` 在
  `asset-worker-3090-b-windows-01` 一次成功，5 秒发布 AO、DirectX 法线、日志和结果 JSON。
- Asset API `1.6.17-substance-glb-input-v1` 已健康上线，镜像
  `sha256:64b13ef572589bca38703040b0ae8d65c91e5c8156efa1a6cc90a2e83093891b`；发布前后均为
  0 活跃任务，7 Worker / 13 槽位全部可用，其他服务和三台 ComfyUI 未重启。

## 2026-08-11 拓扑低模 UV 后 FBX 米制单位热修复

- 任务 `d7fdc2e9-8d26-4947-bf8b-7538f9850ddf` 的拓扑交付低模正确使用
  `UnitScaleFactor=100 / OriginalUnitScaleFactor=100`；后续 UV 任务
  `24c999b1-18cb-40b4-8802-1c6a1810c8a9` 使用 Skill 默认 FBX 导出后变成 `1 / 1`。浏览器按原始
  坐标加载时因此把低模放大约 100 倍，烘焙页显示尺寸差 `9838.4%`。
- Worker `1.4.17-uv-fbx-meter-contract-v1` 在批准 UV Skill 完成后增加 GPU Control 交付适配器：只从
  已生成的 UV Blend 重新导出 FBX，固定米制 `FBX_SCALE_UNITS`，不修改 Skill、源文件、拓扑、UV、
  材质或对象结构；随后在全新 Blender 场景回读，并把单位、中心、尺寸和结构证据写入正式报告。
- 精确故障 Blend 实测为 `100 / 100`，中心和三轴尺寸回读误差均为 `0`；顶点 `2605`、边 `4844`、
  面 `2313`、循环 `7635`、UV 层 `1`、材质槽 `1` 在导出前后完全一致。
- 生产 canary `b5364345-8c95-41d5-bc9e-dbae3b2d800f` 使用同一份问题低模，经真实
  `/api/v1/assets/uv/process` 一次成功，正式五件套原子发布，单位 `100 / 100`、中心/尺寸误差 `0`。
- 三台 Worker 均已逐台排空并滚动到镜像
  `sha256:20ee34af498af5955835d176e4f228a813bf4599dc0490a6cd77bd0603d41f8e`，新实例均为
  `ONLINE / AUTHENTICATED / HEALTHY`；三台 ComfyUI 的容器 ID、启动时间和重启次数未改变。
- 旧错误 UV 制品保持不可变，不覆盖、不删除；项目需要重新执行一次 UV 才会获得已修复的新 FBX。
  完整证据见 `105_2026-08-11_UV_FBX_METER_UNIT_HOTFIX.md`。

## 2026-08-11 自动拓扑交付退化几何热修复

- 失败任务 `437c4d37-047d-4963-a1cf-fa24926dbca5` 已定位为 98% 拓扑后处理门禁
  `UV-prepared low has invalid geometry`；拓扑生成、Codex 认证和 Worker 心跳均正常。
- `1.4.16` 只在保留源低模之外的 `BAKE_LOW` 交付副本存在零面积面时执行有界清理：先消解零长度边，
  再删除仍为零面积的面及由此产生的悬空边点；不合并邻近点、不重网格、不减面、不重建。
- 清理在 UV 前执行，UV 步骤若引入退化面则再执行一次有界清理；正常模型为严格 no-op，原始高模、
  原始低模、正常拓扑、已有有效 UV 和材质均保持不变。之后仍必须通过低模面数、UV、纯变换对齐、
  七方向视觉检查和 FBX 全新场景回读，未放宽任何门禁。
- 功能提交 `98b66d957536f0215d631377da3920c4b5c6409f` 已推送；三台 Worker 已滚动到相同镜像
  `sha256:dd9cff402f5d13d26ab6829e9bc74269a791850b55d12555e330b5e672835b50`，脚本 SHA-256 为
  `917ca181f7239dc82c24b205bdb68d7babd223daa621b550f41340a55c8f680b`，均为
  `ACTIVE / ONLINE / AUTHENTICATED / HEALTHY`。滚动期间未重启三台 ComfyUI。
- Python 3.11 契约专项 `23 passed, 3 skipped`，Ruff 通过；Blender 5.1.2 实测验证退化面被清理、
  正常面与有效 UV 保留、正常模型几何/UV 指纹不变。完整发布证据见
  `104_2026-08-11_RETOPOLOGY_DEGENERATE_DELIVERY_HOTFIX.md`。
- 原失败任务 `437c4d37-047d-4963-a1cf-fa24926dbca5` 已在输入 SHA 不变、历史事件保留的条件下执行
  一次管理员确认重试。原退化几何错误不再出现，证明本热修复覆盖了原故障点；新生成低模随后因
  `surface_error_ratio=0.0831222 > 0.070` 被纯变换对齐门禁拒绝，未发布错误制品。该剩余问题属于
  拓扑生成形状差异，不能靠坐标变换或放宽门禁伪装为成功。

## 2026-08-10 ModelView 局部重绘交互式优先级

- API/Scheduler `1.5.12` 已将 `modelview-inpaint` 固定为服务端 `critical + pinned`；它仍使用持久队列
  留痕，但不会再等待普通批量抠图的五分钟优先级老化，第一张兼容 GPU 释放后立即优先领取。
- 同一局部重绘客户端不再受默认单任务槽限制，最多使用当前三卡调度容量；不抢占或取消已经执行中的
  真实任务，也不永久闲置一张专用卡。
- 生产滚动期间三张 GPU 上的真实 ImageClip 任务未中断；最终真实局部重绘 canary 在 20 条旧抠图排队、
  三卡满载时只等首张卡释放，`27.941s` 被领取、`57.011s` 端到端成功。同期 ImageClip 累计成功 26、
  失败 0；三台 ComfyUI 未重启。完整记录见
  `103_2026-08-10_MODELVIEW_INPAINT_INTERACTIVE_PRIORITY.md`。

## 2026-08-10 拓扑对齐统一缩放双门禁搜索 v2

- 最终提交 `849bedfd839214fa6f87a526a672918b4e12e61e` 已推送；三台 Worker 使用完全相同的
  `1.4.15-retopology-uniform-scale-search-v2` 镜像
  `sha256:deb9db0b4d3a7d5edd2259d8378a22356bc8801ed91ef116b5a0c49b2d135b27`，0 restart。
- 保持旋转与中心后，只搜索解析可行区间内的统一缩放；表面、中心、尺寸、无反射门禁不放宽，禁止
  XYZ 非等比缩放、镜像、拓扑/UV 修改和重建。
- 真实旧版失败候选从尺寸 `0.1426283` 改善为 `0.0777227`，最终表面 `0.0695762`，26 个候选中
  只有 7 个全门禁通过；拓扑/UV 指纹不变。另一真实坏候选仍被拒绝。
- 完整回归基线 `525 passed, 12 skipped`；最终 v2 专项 `10 passed`。三节点均
  `ACTIVE / ONLINE`，Codex 探针健康；滚动期间两条真实抠图任务均成功，三台 ComfyUI 未重启。

## 2026-08-10 拓扑后纯变换对齐生产发布

- 功能提交 `9802d9b9887ff03159ac86549c0fb19235ea07e0`、最终热修复提交
  `39be5a3a7d9065e99f37b302c1dbee401171e269` 均已推送；三台 Worker 使用完全相同的最终镜像
  `sha256:13524ea3f55068d5ff97291c43dd9726c922a93a6b84b94824e40d798b64f1e7`。
- 真实 canary `2fd36e99-77c8-40b9-addd-519134663421` 已 `SUCCEEDED / 100%`：高模 100,000 面，
  交付低模 388 面并有 UV；纯旋转/平移/统一缩放、七方向视觉 QA、米制高低模 FBX 全新场景回读
  全部通过，完整 v6 制品已发布。
- 全量回归 `523 passed, 12 skipped`，最终 Schema 热修复专项 `46 passed`；首次线上 QA Schema 失败
  已修复并由最终真实 canary 覆盖。
- Asset API、三台 Asset Worker、三台调度节点均在线；Codex 探针健康。三台 ComfyUI 未重启，
  ImageClip/ModelViewCreator 及工作流未修改。用户取消压力测试，本轮没有发出压测请求。
- Li3D V6 用户端把 `_BAKE_ALIGNMENT.blend` 误归入高级诊断的问题已在提交
  `fb7a07b94ac208a8bfde02c9e20b23db6948b26d` 修复：正式 `kind=blend/fbx` 重新使用同 stem 的
  `_GAME_LOW.blend` / `_GAME_LOW.fbx` 冻结合同；既有成功 canary 的展示名已原位兼容，模型字节和
  SHA-256 未变。Asset API 1.6.16 已健康上线，针对性测试 `23 passed`。

## 2026-07-30 GPU Control 1.5.5 速度稳定性候选

- 本轮最终源码提交：`59b35d319d84715489dedbd22d81bc56719f57c8`；最终离线测试汇总：Python 188 passed / 0 failed；Ruff、mypy（34 个源文件）、compileall、Control/Node Compose 均通过；PostgreSQL 17.5 隔离 SQL 通过；Web Vitest 8/8、mocked Playwright 20/20、lint/format/typecheck/build 通过；plan-only 0 HTTP。
- 父批次状态/装配已用批量最新 artifact 查询替代逐帧 N+1；feeder 按系统、租户、批次和单轮预算
  公平有界投喂，只锁定实际选择项。
- 单任务创建与 batch feeder 统一采用 `global admission → sorted tenant locks → recount`，关闭跨租户并发
  越过全局队列上限的窗口。
- materialize 遇到 commit 结果不确定时，从新会话按精确 job ID 对账；查询失败或任一记录存在时保留
  输入目录，不冒险删除服务端已提交任务依赖的文件。
- Node Agent 的 GPU 型号为可选遥测，探测失败不阻断心跳；父批次逐节点帧数、attempt、GPU 时间、
  P50/P95、改派和证据完整性已形成候选实现。`straggler_ratio` 使用合同 03 的分数公式和 `<=0.15`
  口径；动画管家 06 的 `<=1.15` 是 `1 + ratio` 倍率展示，需由对方确认。
- WebUI 源码候选已在 `e726b93c45b8dbdffc9b013024aff6703967d866` 形成，已完成 Vitest 8/8、
  mocked Playwright 20/20、lint、format、typecheck 和 build；生产 Web 仍为 1.5.4，本轮未热更新。
- 六 API 100+ VU 工具、分阶段计划、真实素材合同、只读预检、双重执行门禁和 session 清理已准备；
  本轮没有启动 Locust，也没有向生产发送压测请求。
- 动画管家第三轮输入 SHA 为
  `f0a46e701022185397d5c1574f90e58cd33ccde785f8ba63c75e15f95d2f2da9`。其声明的 65 号旧回执
  SHA `40938b4884ba788f509fd0a4942ee10630962825a861bcad744bb85bbe383047` 与仓库当前历史文件 SHA
  `d2b5d3c2c908447f3beeee23b0d47f349da90a7d181589ef2fe9a2f907d05bb8` 分别保留，不互相改写。
- 第三轮输入中的 `bundle_index.json` SHA 有 65 个十六进制字符，且实际 index、六个 bundle 和生成器
  尚未传输；状态为 `PENDING_INPUT_CORRECTION_AND_TRANSFER`。离线 validator 已 21/21 通过，但没有
  声称真实素材 SHA 匹配。
- 1.5.5 打包工具代码已准备；四镜像构建、SBOM/provenance、registry push、Git LFS 上传、rollback
  镜像实测和生产数据库 `0010 → 0011` 均未执行。
- 完整交接和双方下一输入见
  `69_2026-07-30_ASSETCLAW_1_5_5_SPEED_STABILITY_PREJOINT_RECEIPT.md`。

## 2026-07-28 3090-B Windows / WSL2 GPU 上线

- `worker-3090-b` 已按物理 MAC `3c:7c:3f:a5:b0:4f`、GPU UUID
  `GPU-092a5184-5857-d196-5df2-efa9503368aa` 和固定 Windows IP `10.3.34.14` 登记；WSL NAT
  地址不作为节点身份。
- 节点当前为 `ONLINE / ACTIVE / PRIMARY`，ComfyUI healthy；Node Agent、SSH、Docker、
  containerd、node_exporter 均 active。
- 真实生产 HTTPS API 已在 B 完成 ImageClip RGBA 与 ModelView 局部重绘，均 `SUCCEEDED`，最终
  PNG 可解码并完成 SHA-256 核对；三节点同时 ACTIVE 的正常调度又把 ModelView 分配到 B。
- B 的 `/srv/comfyui/runtime` 已与容器 uid/gid 10001 对齐，修复首次上传 500；没有修改或重启
  ImageClip/ModelViewCreator 工作流。
- B 的 Blender Worker 已 `ONLINE`，Blender 5.1.2、Skill `asset-skills-2026.07.28-v3`、4 个独立
  CPU 槽；真实 UV/重拓扑 canary 留待下一维护窗口，不能提前写成业务验收通过。
- Web 已修复资产终态持续计时和窄抽屉显示；管理员人工复核按钮已从调度后台移除，复核应在用户端
  完成。客户侧复核决定回传接口仍待安全发布。
- 详细证据见 `57_2026-07-28_3090_B_WINDOWS_WSL2_GPU_ACCEPTANCE.md`。
- 已生成未部署的 `1.5.0-r1` 五镜像归档，大小 `826519963` bytes，SHA-256
  `0c68057f66f2c143f203f54b98533e1fb419a8df0f70ad7704646836b1521ccb`，由 Git LFS 分发。

## 2026-07-28 V4 批量抠图修复

- 已确认动画管家未取消 `assetclaw:VID_9D9EB9ACE6A1:matting:g1`；旧 Web 的“取消中”来自服务端
  将单帧失败错误映射为取消状态。
- 已定位 ordinal 34 的主控源 PNG 有效，而 3090-A 的 ComfyUI 输入文件为 0 字节；根因是旧上传
  重试使用 `overwrite=false` 且未回读验证远端最终字节。
- 新实现强制 `overwrite=true`，每次上传后回读并校验 size/SHA-256，完全一致后才提交 prompt；
  零字节遗留覆盖修复单元测试通过。
- 新父任务语义隔离失败帧并继续其他帧；只有明确取消请求才能进入 `CANCELLING`，失败批次最终
  `FAILED` 且不发布部分结果。
- 原父任务按原 batch ID、原 child job ID 和原 ordinal 恢复；4090 与 3090-A 已同时继续执行。
- 当前活动生产队列未排空，因此永久修复尚未滚动替换生产 API/Scheduler；不得提前写成已上线。
- 当前完整接口与联合验收合同见 `56_GPU_CONTROL_MATTING_HANDOFF_V4.md`。

## 1.2.0 生产增量

- 两台 3090 和一台 4090 均已接入、在线并完成真实三卡分配；节点以 node_id、MAC 和 GPU UUID
  保持身份，心跳可更新 DHCP 地址。
- ImageClip/ModelView 单图 API、Web、Scheduler、PostgreSQL、Redis、Nginx 和监控栈已在生产验证。
- 动画管家 ImageClip RGBA 序列帧批次已完成：严格 ZIP/manifest、父子任务、有界投喂、三卡
  调度、重试/取消/恢复、结果校验和原子 ZIP 发布。
- Web 顶层只显示一个批次父任务，帧级路径、节点、重试和错误只在详情分页展示。
- 对外冻结合同为 `38_GPU_CONTROL_MATTING_HANDOFF_V2.md`，部署证据为
  `39_2026-07-24_BATCH_MATTING_DEPLOYMENT_RECORD.md`。

## 状态定义

- `已实现`：仓库存在完整实现，不是目录壳或 TODO。
- `本机已测`：Windows/SQLite/Fake ComfyUI 或浏览器环境真实运行通过。
- `现场待测`：必须在 Ubuntu、Docker、NVIDIA、PostgreSQL 或真实业务材料下执行。
- 只有“现场已测”才能写入生产验收通过；本文件当前没有虚报该状态。

| 范围 | 已实现 | 本机已测 | 现场待测 |
|---|---|---|---|
| PostgreSQL 持久队列/迁移 | 是 | 空库迁移到 0002、SQLite 集成测试 | Ubuntu PostgreSQL 17、SKIP LOCKED 实例竞争 |
| asyncio Scheduler | 是 | 领取、租约、恢复、取消、超时、100 并发 | 三机长稳、真实 WS/历史/下载 |
| 3090 优先/4090 溢出 | 是 | Guard、事务后复核、动态设置测试 | 真实 GPU 利用率/显存/时段/sentinel |
| API/幂等/租户 | 是 | 100 同 key 只产生一个 job、跨租户拒绝 | Nginx HTTPS 下真实客户调用 |
| 工作流注册 | 是 | API 格式、bindings、节点兼容、启用测试 | 真实 Export Workflow (API) 与模型 |
| ComfyUI 统一镜像 | 是 | Dockerfile/锁文件静态验证 | 主控构建、三机 image ID、真实启动 |
| Node Agent/后台运维 | 是 | HMAC、防重放、固定命令测试 | systemd/sudo/Docker start-stop-restart |
| 管理台 | 是 | lint、format、Vitest、build、浏览器登录/页面/移动端 | 现场 HTTPS、真实节点操作 |
| Loki/Alloy/监控/飞书 | 是 | 配置静态检查、告警 API 测试 | 三机日志、Prom targets、飞书实发 |
| 初始化/部署脚本 | 是 | 参数/生成文件单测、Python 静态检查 | 三台 Ubuntu 顺序执行 |
| 文档/PDF | 是 | Markdown/PDF 生成和渲染检查 | 操作者按 PDF 完成现场签字 |

## 当前验证结果

- 2026-07-28 本轮针对性回归：Comfy 上传完整性、Node Agent 混合节点身份、Asset API 来源 IP
  自动客户共 `17 passed in 9.65s`（一次性容器、测试凭据、仓库只读挂载）。
- 本轮 Python 源码编译、5 个部署 Shell 脚本 `bash -n` 与 `git diff --check` 通过。
- 当前 Web production build 通过并已只替换 Web 容器；API、Scheduler 与三台 ComfyUI 未因此重启。

- `python -m ruff check .`：通过。
- `python -m mypy packages apps/api/src apps/scheduler/src apps/node_agent/src`：通过，23 个源文件。
- `python -m pytest -q`：66 passed；批次列表混合排序修复后相关集成 17 passed。
- 前端 `npm run lint`：通过。
- 前端 `npm run format:check`：通过。
- 前端 `npm test`：2 passed。
- 前端 `npm run build`：通过；`npm audit` 为 0 vulnerabilities。
- 浏览器：管理员真实登录生产 API；父批次只显示 1 行，3 帧详情、路径、三节点和 artifact SHA
  可见，内部子任务未出现在顶层，控制台 0 error。
- 部署配置：12 个 YAML 文件全部解析通过。
- Alembic：生产 PostgreSQL 已到 `20260724_0004 (head)`。

## 为当天落地完成的关键修复

1. 一条命令生成主控 `.env`、两个 worker env、三节点清单、Prometheus 实际 IP 和初始管理员密码。
2. ComfyUI 模型统一挂载到默认 `/opt/comfyui/models`；镜像固定 commit 和依赖，三机导入同一 tar。
3. PostgreSQL/Redis/Grafana/Loki 等改用 Docker 命名卷，避免空机首次启动的 UID 写权限冲突；job/runtime 目录对齐容器 UID 10001。
4. Node Agent systemd 允许固定 sudo 命令和 Docker Unix Socket，后台启动/停止/重启不再被沙箱配置阻断。
5. 导入工作流自动生成三节点兼容性，没有兼容节点不能启用。
6. 动态 4090 配置由 Scheduler 每轮从数据库读取，后台保存后真实生效。
7. 管理台补充启动/停止、任务取消、诊断包、工作流 JSON 导入、真实 dashboard 指标。
8. Alertmanager webhook 鉴权，告警先持久化再异步发送飞书。
9. 上传 staging、事务幂等、request_id 一致性和维护 DRAINING 协议已补齐。
10. 新增 `docs/28_TODAY_DEPLOYMENT_MANUAL.md`，严格按三机实际执行顺序编排。

## 现场首日执行入口

只读这一份即可开始：`docs/28_TODAY_DEPLOYMENT_MANUAL.md`。打印/离线版本为仓库根目录 `GPU_CONTROL_成品部署联调与核心逻辑手册.pdf`。

现场完成后，在本表新增日期、主机名、命令、结果和操作者，不覆盖本机测试记录。
