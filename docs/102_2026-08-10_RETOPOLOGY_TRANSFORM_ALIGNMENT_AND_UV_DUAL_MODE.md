# 2026-08-10 自动拓扑纯变换对齐与 UV 双模式升级

## 1. 结论与范围

本次升级只改 GPU Control 管理的 Asset API、Blender Worker、制品门禁、部署配置和文档，不修改
ImageClip、ModelViewCreator、ComfyUI 工作流或模型参数。自动拓扑仍由 Direct V2/Codex 生成；生成
完成后新增一个强制的烘焙交付阶段。

拓扑后的高低模对齐严格执行 `blender-align-bake-models`：高模是唯一坐标标准，只允许平移、正确
旋转/轴向和一个整体缩放。对齐阶段禁止镜像、XYZ 非等比缩放、重拓扑、重建、Remesh、Voxel、
Decimate、Triangulate、Shrinkwrap、修改 UV 或替换低模。变换解烘焙到低模副本的顶点一次，交付
对象保持单位变换；用户原始高模和低模保留且隐藏，不覆盖、不删除。

如果纯变换不能证明高低模属于同一资产并在阈值内匹配，任务失败并保留诊断证据；服务端不会通过
改变拓扑来伪造“对齐成功”。数值通过也不能覆盖七方向视觉失败。

## 2. 处理顺序

1. Direct V2 完成原有低模拓扑；
2. 按面数识别角色，面数多者为高模、面数少者为低模；
3. 为交付创建世界坐标已烘焙的高低模副本，原对象保持不变；
4. 在对齐前执行独立 UV 阶段：已有有效 UV 时原样保留，否则使用明确选择的 UV 算法；
5. 通过表面采样、正确轴向候选和 trimmed similarity ICP 求解平移、正确旋转和统一缩放；
6. 对齐前后比较低模拓扑、UV、材质槽、顶点组和形态键指纹；任何变化立即失败；
7. 渲染前、后、左、右、顶、底、透视七个方向。高模为蓝色，低模是不透明亮橙实体和深色线框，
   禁止 X-Ray；
8. 独立 Codex 视觉 QA 检查尺寸、中心、旋转、非对称部件方向、错误镜像、长刺、折叠和可见穿插；
9. 分别导出米制 `bake_high.fbx` 与 `bake_low.fbx`，清空 Blender 场景后重新导入；
10. 回读验证包围盒、低模面数、有效 UV，以及 FBX 前后的顶点/面/Loop/UV/材质槽结构摘要；
11. 所有门禁通过后才原子发布完整制品。

UV 生成是独立的预对齐步骤，不属于“对齐”。现有 `legacy_pbr` 算法可能把非平面 N-gon 三角化以
获得可用 UV；报告会分别记录 UV 前后拓扑。纯变换对齐完成后不再修改拓扑或 UV。

## 3. UV 双模式 API

`POST /api/v1/assets/uv/process` 的 `metadata.options.algorithm` 支持：

| 值 | 当前行为 |
|---|---|
| 缺省或 `legacy_pbr` | 使用现有 PBR UV 流程；Linux Worker 明确声明并领取该算法 |
| `mof_low_seam` | 仅允许有许可证且预检通过的 Windows MOF Worker；当前没有此容量，提交前返回 `503 UV_MOF_CAPACITY_UNAVAILABLE` |
| 其他值 | 返回 `422 UV_ALGORITHM_INVALID` |

服务端不允许静默降级：请求 MOF 时不会改用 legacy，也不会把无法领取的任务写入队列。UV 的 report、
BLEND QA 和 FBX QA 均记录实际 `algorithm`，Asset API 完成门禁要求三份记录一致。

自动拓扑 `metadata.options.uv_algorithm` 当前只可实际执行 `legacy_pbr`。显式请求
`mof_low_seam` 会在上传落盘前返回 `409 UV_MOF_RUNTIME_UNAVAILABLE`，直到 Windows MOF Worker
完成许可证和预检接入。

## 4. 自动拓扑成功制品合同

成功的 `RETOPOLOGY_PROCESS_V2` 使用 `retopology_direct_delivery.v6`，发布：

| kind | 文件 | 用途 |
|---|---|---|
| `blend` | `<stem>_GAME_LOW.blend` | 保留原对象和最终烘焙高低模的检查工程；文件名兼容冻结的 Li3D V6 正式模型识别合同 |
| `fbx` | `<stem>_GAME_LOW.fbx` | 最终低模，只传给烘焙引擎 |
| `high_fbx` | `<stem>_BAKE_HIGH.fbx` | 最终高模，只传给烘焙引擎 |
| `alignment_report` | `bake_alignment_report.json` | 角色、纯变换、指纹、七视图与米制导出证据 |
| `pair_validation` | `bake_pair_validation.json` | 全新场景 FBX 回读证据 |
| `alignment_views_zip` | `alignment_views.zip` | 初始与最终七方向图 |
| `initial_contact_sheet` | `alignment_initial_contact_sheet.png` | 对齐前检查总图 |
| `final_contact_sheet` | `alignment_final_contact_sheet.png` | 对齐后检查总图 |
| `visual_qa` | `bake_visual_qa.json` | 独立视觉门禁结果 |
| `visual_qa_events` | `bake_visual_qa_events.jsonl` | 视觉 QA Agent 事件证据 |
| 生成证据 | `generation_report.json`、`delivery_manifest.json`、`result.json`、Agent/Wrapper 事件 | Direct V2 来源和发布身份 |

烘焙端只能使用本次任务配对的 `_BAKE_HIGH.fbx` 和 `_GAME_LOW.fbx`，不能混用旧任务或用户另行上传
的文件。失败任务不发布半套正式结果。

## 5. 门禁与错误语义

- `TRANSFORM_ONLY_ALIGNMENT_REJECTED`：纯变换候选超出表面、中心、尺寸或方向门禁；不重建低模；
- `RETOPOLOGY_QA_FAILED`：UV、纯变换、七方向视觉或 FBX 回读任一步失败；
- `BLENDER_EXECUTION_FAILED`：脚本、Blender 或基础执行异常；
- `UV_MOF_RUNTIME_UNAVAILABLE` / `UV_MOF_CAPACITY_UNAVAILABLE`：MOF 未具备可执行容量；
- `UV_ALGORITHM_INVALID`：客户端提交未知算法。

原先因 `generated low dimensions do not match ... refusing to hide a different model with coordinate scaling`
在 92% 失败的旧坐标恢复器不再参与 Direct V2 v6 交付。新流程先求正确旋转/轴向，再求统一缩放和
中心，不再只比较未注册的尺寸，也不会用强制缩放掩盖不同模型。

## 6. 发布与回滚纪律

- Worker 启动时校验 `blender-pbr-uv`、`blender-retopology-compare-iterate` 和
  `blender-align-bake-models` 三个 Skill 的逐文件 SHA-256；漂移时拒绝启动或领取相关任务；
- Worker 代码同时固定后处理脚本和对齐脚本 SHA-256；
- 更新 Worker 前必须将节点置为 `DRAINING` 并确认没有运行中的 Asset Job；
- 只滚动 Asset Worker 和 Asset API，三台 ComfyUI 不重启；
- 回滚恢复上一版 Worker/Asset API 镜像及其匹配 Skill 包，不能混用 v5/v6 制品合同；
- 用户已取消本轮压力测试，因此本次发布不执行 100 VU，不得把离线回归写成压力测试结果。

## 7. 验证记录

发布候选必须完成以下检查后才能在本节写为通过：

- 三个 Skill 的冻结 SHA 清单校验；
- Python 编译、Ruff、相关单元和 Asset API 集成测试；
- Compose 渲染和镜像版本一致性；
- 真实 300,000 面高模 / 424 面 Direct V2 低模样本的独立 UV、纯变换、七视图、FBX 回读；
- 低模 FBX 严格 UV QA；
- 三节点零运行任务门禁、安全滚动、版本/心跳复核；
- 单笔真实拓扑 canary 或明确记录未执行原因。

### 7.1 发布前真实样本证据

用户提供的两个本地分享 ZIP 字节完全一致，SHA-256 为
`cd43391f3cb8e126ce3e428498a9eb7ceaf29ac5167526da10f0dac73a1d84fd`。运行时使用该包内原样
`blender-align-bake-models`；其中 `align_bake_models.py` SHA-256 为
`ea0588e81fa50772080bc19ff096ee29cb5b6dbc67cdb303b9d32cdbf6a99a78`。

未再次调用 Codex，直接用此前失败的真实生产样本执行后处理：

| 证据 | 结果 |
|---|---|
| 高模 / 原低模 | 300,000 faces / 424 faces |
| 独立 UV 阶段 | legacy PBR；112 个非平面面被三角化；最终低模 536 faces |
| 对齐变换 | 正确旋转、统一缩放 `0.9408865872`；无反射、无 XYZ 独立缩放 |
| 归一化表面误差 | `0.0638694`，通过 Direct V2 稀疏低模候选上限 `0.070` |
| 中心误差 | 约 `1.94e-17` |
| 最大尺寸误差比例 | `0.0478843` |
| 对齐指纹 | 652 vertices / 1,132 edges / 536 polygons / 2,192 loops 和 UV hash 前后一致 |
| FBX 回读 | 高低模中心/尺寸最大绝对差均不超过 `5.96e-8` 米 |
| FBX 结构 | 低模顶点/面/loop/面边数/UV/材质槽摘要在回读前后完全一致 |
| 低模严格 UV QA | 通过；越界、翻转、退化、重叠硬失败均为 0；P95 stretch `1.43469` |
| 源对象 | 原高低模指纹未变，均保留在 Blend 中并隐藏 |

该低模极其稀疏，最差正交方向固定视图轮廓 IoU 只有 `0.6731`，所以该数值只作提示，不能单独发布
任务。在线 Worker 仍必须通过独立 Codex 七方向视觉 QA；如果视觉判断低模本身不像，就会失败而不
重建或变形。此类低模的生成质量属于外部 Direct V2 拓扑生成器，不由对齐阶段静默修改。

当前发布前自动验证：全量测试 `523 passed, 12 skipped`；Asset API 集成专项 `92 passed`；
Worker/bootstrap/对齐专项 `55 passed`。Skill SHA 校验、Python 编译、Ruff、Compose 渲染和
`git diff --check` 通过。后处理脚本冻结 SHA-256 为
`d555d30824f7b822699543efe443de1395c8107428ff1e785770610f0f2f3b01`。

### 7.2 生产发布与真实 canary 证据

2026-08-10 已完成三节点安全滚动和真实任务验收：

| 项目 | 生产结果 |
|---|---|
| 源码 | 功能提交 `9802d9b9887ff03159ac86549c0fb19235ea07e0`；视觉 QA Schema 热修复提交 `39be5a3a7d9065e99f37b302c1dbee401171e269`，均已推送 |
| Asset API | `1.6.15-retopology-bake-align-v1`；image ID `sha256:04ac076cc205949e1f0af05d7d9e907e4b68c7e834d41e6084de7e12b7ed7f24`；healthy，0 restart |
| 三台 Asset Worker | `1.4.13-retopology-bake-align-v1`；三台 image ID 均为 `sha256:13524ea3f55068d5ff97291c43dd9726c922a93a6b84b94824e40d798b64f1e7`；ONLINE，Codex AUTHENTICATED/HEALTHY，0 restart |
| 节点状态 | `control-4090`、`worker-3090-a`、`worker-3090-b` 均为 `ACTIVE / ONLINE` |
| 真实 canary | Job `2fd36e99-77c8-40b9-addd-519134663421`，由 `asset-worker-3090-a` 执行，最终 `SUCCEEDED / 100%` |
| canary 面数与 UV | 高模 `100,000 faces`；原低模 `232 faces`；UV 独立阶段后的交付低模 `388 faces`；有效 `UVChannel_1`；低模面数小于高模 |
| canary 纯变换 | 正确旋转约 `(-0.00778°, -0.01614°, -0.00868°)`；统一缩放 `0.9905239440`；无镜像、无 XYZ 独立缩放、无直接物体变换复制 |
| canary 匹配 | 归一化表面误差 `0.024302`；中心误差约 `3.98e-17`；最大尺寸误差比例 `0.009463`；拓扑和 UV 指纹在对齐前后完全一致 |
| canary 几何门禁 | 0 退化面、0 非流形边、0 松散边/点、0 重复点/面、0 朝向不一致边 |
| canary FBX 回读 | 全新 Blender 场景重新导入通过；米制合同通过；低模中心/尺寸回读最大误差不超过 `5.03e-7 m` |
| canary 视觉门禁 | 前、后、左、右、顶、底、透视全部检查并通过；方向、非对称部件、轮廓与部件位置匹配；无错误镜像、长刺、可见穿插、折叠或坍塌 |
| 正式制品 | `_GAME_LOW.blend`、`_BAKE_HIGH.fbx`、`_GAME_LOW.fbx`、对齐/回读报告、两张总图、七方向 ZIP、视觉 QA 与生成事件均已原子发布 |

首次线上 canary 在 98% 暴露 Codex Structured Outputs 不接受数组 `uniqueItems` 的兼容性问题；该字段
已删除并增加合同测试，形成 Worker `1.4.13`。热修复专项回归 `46 passed`，随后上述真实 canary
在最终镜像上成功，证明不是仅靠离线测试判定发布完成。

滚动期间还发现仅检查 GPU `nodes.current_jobs` 不足以代表 Asset CPU 队列空闲：一个已经被 Asset
Worker 领取的拓扑任务会被遗漏。该任务的租约机制已安全重新排队并在最终镜像上成功；后续维护门禁
必须同时确认节点 `DRAINING`、GPU Job 为 0、该节点 Asset Worker `current_jobs=0`，以及数据库不存在
该 Worker 的 `RUNNING` Asset Job 后才允许替换容器。

### 7.3 离线镜像归档

归档目录：`/srv/gpu-control/images/retopology-bake-align-v1-39be5a3/`。

| 归档 | 字节 | SHA-256 |
|---|---:|---|
| `li3d-blender-worker-1.4.13-retopology-bake-align-v1.tar.zst` | 688273392 | `a05c2af336d3f43573e22b04d928ce9607f1f09f2be56e341122828e19e89820` |
| `unified-scheduler-asset-api-1.6.15-retopology-bake-align-v1.tar.zst` | 92259136 | `2a286d98bf98a61495ddbfcb8eb2195e7d1e60a61306d13f2c3a0694c1d43355` |

本次没有重启或修改三台 ComfyUI、ImageClip、ModelViewCreator 及其工作流；真实抠图任务继续运行。
用户已取消压力测试，本发布执行了 0 条压力测试请求，不能把 canary 或回归测试描述为压测。

### 7.4 Li3D V6 正式 BLEND 文件名兼容热修复

Li3D 用户端按冻结的 V6 合同，以 `kind=blend` 且文件名后缀 `_GAME_LOW.blend` 识别正式 BLEND。
首次 bake-alignment 发布把相同 `kind=blend` 命名为 `_BAKE_ALIGNMENT.blend`，导致任务实际成功且
BLEND/FBX 都存在时，客户端仍只识别 FBX，并显示“交付不完整”。该问题只影响客户端分类和下载
展示，不影响已生成模型、SHA-256、UV、对齐或 QA。

- 修复提交：`fb7a07b94ac208a8bfde02c9e20b23db6948b26d`，已推送；
- Asset API：`1.6.16-retopology-v6-client-filename-v1`；image ID
  `sha256:4a5e4e7e25c6f1e12244ddbd891a0b23ff27dbffebaa1af95190e4b4c740559f`；healthy，0 restart；
- 新任务正式模型固定发布为同 stem 的 `_GAME_LOW.blend` 与 `_GAME_LOW.fbx`；BLEND 内容仍保留源对象、
  高低模 bake pair 与完整检查工程；
- 已成功任务 `2fd36e99-77c8-40b9-addd-519134663421` 的 `kind=blend` 展示名已在事务中兼容修正，
  文件路径、字节数 `18952129` 和 SHA-256
  `b628efd50a75eee56be46226a3ccbbb6c8180880357fb1ad09b5260a6e41b4b1` 均未改变；
- Ruff、Python 编译与针对性合同/集成测试 `23 passed`；
- 离线镜像：`/srv/gpu-control/images/retopology-v6-client-filename-v1-fb7a07b/`
  `unified-scheduler-asset-api-1.6.16-retopology-v6-client-filename-v1.tar.zst`，`92517334` bytes，
  SHA-256 `0f441ae61876904877a45f0acb85c631c0ec1222fdec3e1fc0f56a057a17b006`。

本次只滚动 Asset API；三台 Asset Worker 与三台 ComfyUI 均未重启。浏览器插件在当前会话不可用，
且 Li3D 用户端源码/URL 不在本仓库，因此没有虚报客户端页面自动化；服务端运行镜像合同、数据库两项
正式模型记录和 API 集成响应均已验证。
