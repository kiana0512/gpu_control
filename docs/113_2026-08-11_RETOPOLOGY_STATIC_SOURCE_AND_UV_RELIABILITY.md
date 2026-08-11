# 2026-08-11 自动拓扑静态源、UV 与确定性构建修复

## 1. 故障范围与根因

本次只修改 GPU Control 管理的自动拓扑接入、交付门禁和部署；没有修改 ImageClip、ModelViewCreator、
ComfyUI 工作流、模型、提示词、推理参数或输出语义。

生产 `3.0.3` 同时出现成功和失败的直接原因不是三台机器随机失效，而是输入格式走了两条不同路径：

- FBX 由正式包先准备为唯一、只读的 `SOURCE_HIGH`，并记录源哈希、世界矩阵和世界包围盒；
- GLB/GLTF/OBJ 仍由 Worker 的旧兼容入口保存成没有统一高模角色和源清单的普通 Blend。

因此同一个 Codex 生成器面对 GLB 时可能只写出 `build.py` 而未执行，也可能产生缺 UV 的低模。前者最终
表现为 `RETOPOLOGY_OUTPUT_MISSING`，后者在核心交付门只显示笼统的
`TOPOLOGY_VALIDATION_INVALID`。这解释了为什么 FBX 更容易成功，而两份新 GLB 分别以不同方式失败。

## 2. v3.0.4 修复

- FBX、GLB、GLTF、OBJ 全部由正式包导入并合并为唯一 `SOURCE_HIGH`；原文件 SHA、原世界包围盒、
  原矩阵和源网格统计写入 `li3d-retopology-static-source-v4` 清单。
- Worker 不再把 GLB/OBJ 预先压成无角色 Blend；只有缺少原始 Blend 文件签名的压缩 Blend 继续走兼容
  重存路径。
- 生成提示与技能合同明确要求实际执行构建脚本并检查输出 Blend/报告存在；只写计划或脚本返回
  `BUILD_SCRIPT_NOT_EXECUTED`。
- 低模在最终化时必须至少有一个非空 UV 层。缺 UV 直接返回 `MISSING_UV`，交付证据同时给出
  `TOPOLOGY_GENERATED_BLEND_PAIR_0_LOW_UV_MISSING`、Blend 回读和 FBX 回读对应的具体代码。
- 未放宽坐标、闭合流形、退化面、重复点面、长刺、穿插、面数、UV 或 FBX 回读门禁；坏模型仍拒绝发布。

发布身份：

- 自动拓扑服务器包：`3.0.4`
- 包清单 SHA-256：`a17c86ed58a052656846df77f80ef90aa7c18f4881913f18988cfecf15577188`
- Asset API：`1.6.23-retopo-static-input-v1`
- Blender Worker：`1.4.25-retopo-static-input-v1`
- Git 提交：`69409065116b07410037db586acf10b71eb57b10`

## 3. 测试证据

- 包完整性验证：`ok=true`、`package_version=3.0.4`、12 个技能文件、无错误。
- 定向拓扑/版本/交付门测试：`30 passed`。
- 新 Worker 镜像 Python 3.11 全部单元测试：`362 passed, 5 skipped`。
- 新 Asset API 镜像完整集成测试：`95 passed`。
- 两份原失败 GLB 在 Blender 5.1.2 中真实准备成功：
  - SHA `47d2afe1a8b1dd9a28bac11ead8075fff53d579b97dc1ebd2db3c7680a9cd0c5`，99,998 面；
  - SHA `13fbdb674baab09087aadb71bff66071ca908edb4b8319da29829c7ab30a4396`，99,980 面。
- 两份清单均只有一个 `SOURCE_HIGH`，源 SHA、世界尺寸和坐标保持不变；临时焊接仅发生在工作副本。

## 4. 生产滚动

Asset API 先在全体资产任务为 0 时滚动。三个 Linux Blender Worker 随后逐台执行
`DRAINING -> GPU/资产/活动租约为 0 -> 只替换 Blender Worker -> 包/心跳/Codex 探针验证 -> ACTIVE`。
滚动顺序为 `control-4090 -> worker-3090-b -> worker-3090-a`。

三台 Worker 最终镜像均为
`sha256:bd1ba0e85b9c0f2cc168494ca17f7730767c811998acad96e1d519f6939469ad`；Asset API 镜像为
`sha256:f1332a7305922edb0ab31f8c55264f64424c060ae1f9c9e62640b8e78c409f1e`。三台 Worker 均报告
`ONLINE / AUTHENTICATED / HEALTHY` 和技能 `asset-skills-auto-retopo-align-v3.0.4`。

滚动期间正在运行的 ImageClip 任务全部先自然结束；三台 ComfyUI 容器的 ID、启动时间与
`RestartCount=0` 均保持不变。

## 5. 原故障输入回归

已通过生产正式 API 重新提交两份原失败 GLB，项目 SHA 与旧失败记录一致：

- `ddbdce33-458b-4b77-a5e2-3f4a7926289d`：项目 SHA `47d2afe1...`，由 `asset-worker-3090-a` 执行；
- `786c98a9-2885-4a76-a02e-d10a289d0729`：项目 SHA `13fbdb67...`，由 `asset-worker-3090-b` 执行。

第一次回归结果：

- `786c98a9-...` 一次成功，低模 54 面、高模 99,980 面，低模 UV 为 1；生成 Blend、Blend 回读和
  FBX 回读的低模边界边、非流形边、游离边点、重复点面、退化面与错误朝向均为 0；矩阵误差为 0，
  FBX 中心/尺寸回读误差约 `4.67e-8`。
- `ddbdce33-...` 失败且结构化诊断为 `BUILD_SCRIPT_NOT_EXECUTED`：Codex 已完成测量、shape-authority
  plan 和 9.7 KiB `build_once.py`，但回合结束前没有调用 Blender，因此没有生成 Blend。这证明仅靠提示词
  不能消除模型回合的最后一步波动。

## 6. v3.0.5 确定性构建收尾

服务器包装器在 Codex 正常结束但正式 Blend 不存在时，只接受任务根目录内唯一的
`build_once.py` 或 `build.py`。候选必须是普通文件、非符号链接、非空且不超过 2 MiB；候选歧义或超限
继续硬失败。服务器用 Blender `--factory-startup --disable-autoexec --python-exit-code 1` 执行这份既有脚本
一次，记录脚本 SHA、退出码、超时、Blend 和报告是否存在。它不生成第二版模型，也不重跑 Codex；完成后
仍经过同一 generation report、UV、闭合流形、坐标恢复和 FBX 回读门禁。

发布身份：

- 自动拓扑服务器包：`3.0.5`
- 包清单 SHA-256：`70934648f206af86bbaa2e954ed01962c57ac24c89a68d522049a446a4bd23b6`
- Asset API：`1.6.24-retopo-build-completion-v1`
- Blender Worker：`1.4.26-retopo-build-completion-v1`
- Git 提交：`babfb635e33321e98a4aa62afc6bfc957d3cc3c1`
- Worker 镜像：`sha256:58cab921d6e550c95626c3f936ff706f98e5c122057ec104cba1761c7c665281`
- Asset API 镜像：`sha256:c6450569d44fd2ca9adc63e51e078ec1d6e2c77d72947473019edd1effb0c7f9`

验证：定向合同测试 `26 passed`；新 Worker 镜像全量单元测试 `365 passed, 5 skipped`；新 Asset API
镜像完整集成测试 `95 passed`。生产再次逐台执行安全滚动，三台 Worker 均报告
`ONLINE / AUTHENTICATED / HEALTHY` 和技能 `asset-skills-auto-retopo-align-v3.0.5`；三台 ComfyUI
容器 ID、启动时间和 `RestartCount=0` 保持不变。

同一 SHA `47d2afe1...` 原失败 GLB 的 v3.0.5 正式 API 回归任务为
`341cecb0-1b3c-4c85-a623-c3d4fd40724c`。任务最终以 `BUILD_SCRIPT_NOT_EXECUTED` 失败；这暴露出
v3.0.5 运行时镜像中的报告字段仍可能把未实际生成的输出误判为存在，并直接推动了 v3.0.6 的真实
Blend/FBX 指标回读修复。

## 7. v3.0.6 真实网格指标回读

v3.0.5 的服务器补跑使“已写脚本但未执行”的任务可以继续，但生成报告里的 `faces`、`triangles`
和 `uv_layers` 仍来自生成代理自行填写。为防止报告声称有 UV、实际 Blend/FBX 却没有 UV，v3.0.6
把这些字段降为提示信息；正式结果一律由 Blender 对生成 Blend、保存后 Blend 和 FBX 重新导入对象
实测，并用实测值回写报告。真实缺 UV 仍以 `MISSING_UV` 硬失败，不降低质量门禁。

发布身份：

- 自动拓扑服务器包：`3.0.6`
- Asset API：`1.6.25-retopo-report-reconciliation-v1`
- Blender Worker：`1.4.27-retopo-report-reconciliation-v1`
- Git 提交：`5c4166c487753950bc17f8748dc3b8998bfcd800`

同一原始 GLB（SHA `47d2afe1...`）的正式回归任务
`6cb2b0b8-52d4-43ea-b2f1-d96f60dfda71` 在第 2 次尝试成功，低模 952 面、1,888 三角形、
一个 UV 层，报告的指标权威来源为 `blender_generated_blend_and_fbx_readback`。第 1 次尝试没有发布
模型，但留下了确定性证据：服务器发现并执行了唯一 `build_once.py`，Blender 返回
`RuntimeError: SOURCE_HIGH missing`。

## 8. v3.0.7 补跑时加载工作场景

第 1 次尝试的日志证明剩余故障不在模型、不在设备，也不是随机超时：服务器使用
`--factory-startup --python build_once.py` 启动了空场景，而生成脚本依赖准备阶段写入工作 Blend 的
只读 `SOURCE_HIGH`。因此脚本本身有效，但在空场景中必然找不到输入对象。

v3.0.7 的唯一行为变化是：服务器仍只执行 Codex 已生成的同一份、通过路径/大小/符号链接检查的
`build_once.py` 或 `build.py`，但先把准备好的工作 Blend 作为 Blender 启动文件打开，再运行脚本：

```text
blender --background --factory-startup --disable-autoexec <working.blend> \
  --python-exit-code 1 --python <generated-build-script>
```

完成证据增加工作 Blend SHA-256。服务器不生成第二版模型、不修改生成脚本、不重跑 Codex；生成物
仍必须经过真实网格指标、UV、闭合流形、坐标恢复和 FBX 回读门禁。

发布身份：

- 自动拓扑服务器包：`3.0.7`
- 包清单 SHA-256：`6c964cae8530b3d5e2bccfde2af485be90480fb1e08850cb4e3aaf0a9ec33162`
- Asset API：`1.6.26-retopo-build-context-v1`
- Blender Worker：`1.4.28-retopo-build-context-v1`
- Git 提交：`2f354e44584c9a834973dd5e3939314b38681419`
- Worker 镜像：`sha256:7f77ab9fe505b2c43e3edb81a23ab42211d2b79959b536696d6899af9bac9730`
- Asset API 镜像：`sha256:bcf81672621560e87388528fdbcd6cee6a285f87f381bd47923a803aba609b23`

定向测试 `27 passed`，Ruff 检查通过，正式包验证通过（12 个技能文件）。生产按
`control-4090 -> worker-3090-a -> worker-3090-b` 逐台执行排空和替换；每台当前业务任务自然结束后才
替换 Blender Worker。三台最终均为 `ONLINE / AUTHENTICATED / HEALTHY`、技能
`asset-skills-auto-retopo-align-v3.0.7`，并恢复 `ACTIVE`。三个 ComfyUI 业务容器未被替换或重启。

## 9. v3.0.7 原始 GLB 生产回归

正式 API 任务 `67eacbbc-3a1c-416c-82fe-b3bbd3ae2aa5` 使用同一原始 GLB，项目 SHA 为
`47d2afe1a8b1dd9a28bac11ead8075fff53d579b97dc1ebd2db3c7680a9cd0c5`，请求记录的服务器包版本为
`3.0.7`、包 SHA 为 `6c964cae...`。任务由 `asset-worker-3090-a` 第 1 次尝试一次成功，没有失败重试，
发布 10 个带 SHA-256 的交付/证据文件。

最终实测：

- 原高模 99,998 面；低模 918 面、1,836 三角形、944 顶点、一个 UV 层；低模面数显著低于高模。
- 生成 Blend、保存后 Blend 和全新 FBX 重新导入三阶段均通过；低模边界边、多面非流形边、游离边、
  游离点、重复点、重复面、退化面、错误朝向边和微碎片计数全部为 0。
- 对齐矩阵误差为 0，中心误差比例约 `1.25e-8`，尺寸误差比例约 `0.01479`；轴向行列式符号与高模
  一致，没有错误镜像。
- FBX 回读通过且低模结构完全匹配；低模中心/尺寸回读误差约 `6.23e-9`。
- 对齐模式为 `source_matrix_restore`，高模是唯一坐标标准；`topology_or_uv_edited=false`、
  `topology_uv_unchanged=true`，原始拓扑和 UV 没有被对齐步骤改写。
- 交付状态为 `generated_for_user_inspection_aligned`。自动数值和结构门禁已通过，最终视觉语义仍由用户
  检查确认，不把数值通过冒充视觉验收。

本次生产任务由 Codex 自己完成了构建，不需要触发服务器补跑。此前 `SOURCE_HIGH missing` 的精确补跑
分支由 `tests/unit/test_retopology_output_contract_recovery.py` 的 11 项测试覆盖，其中命令断言工作 Blend
必须位于 `--python` 之前，并核对 `working_blend_sha256`；`11 passed`。

## 10. v3.0.8 生成低模重复点与退化面修复

生产任务 `f9484be3-a60d-47a1-99da-f2aa08a3858d` 和
`395ba1dc-74af-4c59-9cc0-90e4d5ff2e06` 使用完全相同的原始 FBX（项目 SHA-256
`b26f4303857a1cf24c810f4e6b53df4bd98de852870d770f2d4a1d898e08cb17`），分别在 v3.0.7 的
3090-B 和 3090-A 上失败。两次服务器构建证据都证明 Blender 正常打开了工作 Blend、执行了 Codex
生成的 `build_once.py`，但生成脚本的曲线/封口/合并收尾留下精确重合端点与零面积面：第一次为
`duplicate_vertices=16 / degenerate_faces=16`，第二次为 `12 / 12`。源 FBX、训练技能静态文件和
`SOURCE_HIGH` 均未被修改；正式拓扑门禁正确拒绝了两个坏候选，因此没有发布破面模型。

v3.0.8 把构建残留清理固定在同一次低模生成脚本内，顺序为所有 modifier、曲线转网格和对象 join
之后、UV 创建之前。清理范围只能是新生成低模：按包围盒对角线推导数值容差，只合并完全重合或数值
容差内的点，dissolve 退化边，删除零面积面和随之产生的游离边点，然后重算法线。禁止修改
`SOURCE_HIGH`，禁止宽距离焊接、Decimate、remesh 或用第二版模型冒充清理。最终闭合流形、UV、坐标
恢复和 FBX 新鲜导入门禁没有放宽。

为避免一次动态生成波动让整项任务在约 40% 直接终止，只有最终对齐/FBX 门禁之前的
`RETOPOLOGY_QA_FAILED` 可以进入一次有界新尝试。`CODEX_AUTH_FAILED`、输出缺失、坐标不匹配，以及
70% 之后的最终对齐、拓扑保持或 FBX 回读失败仍禁止自动重试。拒绝候选不会作为交付发布。

发布身份：

- 自动拓扑服务器包：`3.0.8`
- 包清单 SHA-256：`5211a7e772d8a2944bf42ea81c498a8f0414d7f8a5a3f9352a09785808624424`
- Asset API：`1.6.27-retopo-generated-cleanup-v1`
- Blender Worker：`1.4.29-retopo-generated-cleanup-v1`
- Git 提交：`4610efa942ef4cd8c3d269675591a31f0c81d073`
- Worker 镜像：`sha256:6b20d52f5b121066ef28e62aa22a2765c3370ecab4ff79b511f9a8254a31b11a`
- Asset API 镜像：`sha256:6aaf914485ce490ecf076bf87c90bcea1a0d3b243d8ca9e8f6ffe6b5a834f5b0`

Ruff 检查通过；27 项定向单元测试通过；在 Python 3.11 正式 Worker 镜像中验证了重试分类；3 项正式
Asset API 容器集成测试覆盖早期 QA 有界重试、70% 后不重试和重试进度复位。生产部署时先让两台
v3.0.7 拓扑任务自然结束，再替换 API 和三台 Blender Worker；每台均核对
`ONLINE / AUTHENTICATED / HEALTHY` 和技能 `asset-skills-auto-retopo-align-v3.0.8` 后恢复 `ACTIVE`。
ComfyUI 和外部 ImageClip/ModelView 工作流没有修改、替换或重启。

## 11. v3.0.8 生产回归

同一原失败 FBX 的首次正式回归任务为 `79fad66b-fa06-4921-b8b5-325d319934ef`，由 3090-B 在第 1 次
尝试成功完成，没有消耗有界重试。发布低模 1,040 面、2,080 三角形、1,072 顶点和一个非空 UV 层，
低于高模 149,999 面。生成 Blend、保存后 Blend 和新鲜 FBX 重新导入三个阶段中，低模边界边、多面
非流形边、游离边点、重复点面、退化面和错误朝向边均为 0；FBX 结构完全匹配。

坐标恢复模式为 `source_matrix_restore`，矩阵误差与中心误差均为 0，尺寸误差比例约
`0.01398`，高低模行列式符号一致；FBX 回读低模中心/尺寸误差为 0。对齐报告明确记录
`topology_or_uv_edited=false`、`topology_uv_unchanged=true`，说明最终坐标恢复没有再次修改拓扑或 UV。

部署期间另一个不同的真实用户任务 `53521ef7-1b68-42c9-ac0d-2892e3a56d66` 由 3090-A 在第 1 次
尝试成功：低模 1,106 面、2,260 三角形、1,174 顶点、一个 UV 层；三个阶段的全部低模拓扑缺陷计数
为 0，矩阵/中心误差为 0，尺寸误差比例约 `0.01309`，FBX 回读结构与中心/尺寸均通过。

## 12. v3.0.9 形体门禁与七方向证据

v3.0.8 已消除生成构造残留，但它仍只能证明低模拓扑健康、UV 存在、坐标和 FBX 回读一致；如果生成器
做出了一个干净但轮廓错误的低模，旧门禁可能误发布。离线重复回放同一原失败 FBX 时，两份候选符合
高模外形，另一份候选的最大轴尺寸误差约 `8.94%`、表面 P95 误差约 `5.15%`。因此 v3.0.9 在全新
导出的高低模 FBX 上增加确定性形体门禁：最大轴尺寸误差不超过 `3%`，低到高与高到低的 P95 表面
距离均不超过高模对角线的 `4%`。不符合的候选进入一次有界重试，门限不会为了提高成功率而放宽。

每份可发布交付必须同时生成并保存前、后、左、右、顶、底和透视七张不透明橙色实体线框证据，以及
`views.json`。这些证据与 `bake_pair_validation.json` 一起绑定到交付清单 SHA-256。原高模仍是唯一
形体与坐标标准；生成、对齐和验收不覆盖源文件。

发布身份：

- 自动拓扑服务器包：`3.0.9`
- 包清单 SHA-256：`bc0120433f98ad0115870ba760e663f75e515ce8e5b592e9d330af515c7ad232`
- 分发 ZIP SHA-256：`8861df62443552b6ba8acc4810d810564ad66dc859f82b33cc4c5d0e1225c9dc`
- 初始 Asset API：`1.6.28-retopo-shape-view-gate-v1`
- 初始 Blender Worker：`1.4.30-retopo-shape-view-gate-v1`
- Git 提交：`af8c20f41beaa0813cc9d8abf779f03743235887`

## 13. 十二产物发布合同、单机 Codex 槽位与租约修复

v3.0.9 Worker 首次正式运行时正确上传了 12 个产物，但 Asset API 仍只允许旧的 10 个产物，因而在
94.5% 以 HTTP 422 拒绝了新增的 `bake_pair_validation.json` 与 `alignment_views.zip`。这不是 Blender
失败。Asset API `1.6.29-retopo-shape-artifacts-v1` 已把两项证据设为必需，并验证报告门限、拓扑/UV
事实、ZIP 精确成员、每张 PNG 和清单哈希后再原子发布。

随后三任务并发暴露出 Worker 本地 `accepts_codex_jobs` 上报的竞态：3090-A 瞬间误报槽位空闲时，可能
再次领取一个 Codex 拓扑，而 3090-B 仍空闲。Asset API `1.6.30-retopo-durable-codex-slot-v1` 改用数据库
中同 Worker 的 `CLAIMED/RUNNING/CANCELLING` Codex 任务作为权威兜底；一台机器始终只允许一个 Codex
拓扑，但其余 CPU 容量仍可领取 UV 或纯 Blender 审计任务。

控制面更新期间又出现 502/短暂断线。旧 Worker 把一次进度心跳的连接错误当成 Blender 执行失败并
终止仍在运行的子进程，最终在控制面显示 `ASSET_LEASE_EXPIRED`。Worker
`1.4.31-retopo-resilient-leases-v1` 对连接错误、HTTP 429 和 HTTP 5xx 只跳过当次心跳并保留实时子进程；
控制面恢复后继续续租。确定性的 4xx 过期租约或取消仍立即停止，禁止无主交付。最终发布身份：

- Asset API：`1.6.31-retopo-resilient-leases-v1`
- Blender Worker：`1.4.31-retopo-resilient-leases-v1`
- Git 提交：`f93dcbd7c49c4308a06ad224185727dcd30c240a`
- Asset API 镜像：`sha256:2f6d960d33b9b4528d26cdfa5ebf2868ca95dcfbed1a36637f16a292f743f7c5`
- Worker 镜像：`sha256:8aec1994983aa8d04552186174bde7d02a59691ad77a9488e2fafadc72197de3`
- API 归档 SHA-256：`3b7258e66ce0dc676ac7b84eb5bde3b95898e727ebf2e6aa7b556360474e0986`
- Worker 归档 SHA-256：`a604f1cb5585fe24cc68c5ea4bc198b7953c0957003d56e23728c43507650dbf`

正式镜像中的定向槽位、租约、十二产物、版本和交付测试为 `23 passed`，Ruff 全部通过。生产部署只在
三台节点均 DRAINING 且业务任务自然结束后替换 Blender Worker；三台 ComfyUI 容器没有替换或重启。
最终三台 Worker 均为 `ONLINE / AUTHENTICATED / HEALTHY`、技能
`asset-skills-auto-retopo-align-v3.0.9`，随后恢复 ACTIVE。

## 14. v3.0.9 生产真实回归

同一原失败 FBX（SHA-256 `b26f4303857a1cf24c810f4e6b53df4bd98de852870d770f2d4a1d898e08cb17`）
的任务 `0bc969e0-91ba-4a4d-ae6e-e112e34ff0ec` 在第 2 次有界尝试成功，并通过新的 12 产物发布合同：

- 高模 149,999 三角面；低模 812 三角面、418 顶点、一个 UV 层；
- 低模 6 个组件，退化面、边界边和非流形边均为 0；
- 中心误差约 `9.60e-9`，最大轴尺寸误差 `2.245%`；
- 低到高 P95 `3.361%`，高到低 P95 `2.140%`；
- FBX 新鲜回读、拓扑/UV 保持、七方向 PNG 与 SHA 绑定全部通过。

最终镜像部署后的三机并行真实任务为 `8a1a40c9-8b2e-43c6-8e81-659a649c6e67`、
`72897f68-1aff-4b65-bf43-29c0a28f4e33` 和 `685eb2e2-4e57-4756-99ed-6f97aba71df6`。领取结果严格为
4090、3090-A、3090-B 各一条。运行中执行了 8 秒受控 Asset API 中断，三台 Worker 均记录瞬时连接
错误但保留实时生成进程；控制面恢复后进度继续增长，没有再次出现租约假失败。最终
`8a1a40c9-...` 在第 2 次尝试成功，发布低模 1,042 面、2,060 三角形和一个 UV 层。另两条在第 2 次
分别以 15 条和 12 条低模边界边被闭合流形门禁拒绝，均未发布坏候选。这说明租约修复有效，但生成
脚本仍需要显式闭合意外开口，直接推动了 v3.0.10。

## 15. v3.0.10 生成低模闭合构造修复

v3.0.10 要求生成脚本在 UV 前完成闭合构造：实体端面必须封口，成对环必须桥接；只有组件内部、简单且
明显属于意外残留的边界环才允许使用 `bmesh.ops.holes_fill`。真实开口必须保留内外壁和口沿，禁止用
大面盖住。清理后重新实测并要求生成低模 `boundary_edges=0`；高模仍保持只读，形体、UV、FBX 回读和
七方向门禁均未放宽。

发布身份：

- 自动拓扑服务器包：`3.0.10`
- 包清单 SHA-256：`9996f9fdbeafbcedcecbca74e58e29e1ffaaf8f03164ba672ea52d7297871e46`
- Asset API：`1.6.32-retopo-closed-build-v1`
- Blender Worker：`1.4.32-retopo-closed-build-v1`
- Git 提交：`cf424c4f237039af87dbf3e40b18a8ddd68a03a9`
- Asset API 镜像：`sha256:91ba803922da44ebff33761730f8d877326f7f7cff0a3458640ba6ef9d6ee0e2`
- Worker 镜像：`sha256:216b091765136adc24220d9c5ae5ac18addace3a6b204454f7ddea434ae347ac`
- 分发 ZIP SHA-256：`52da6d560ff3b2551981c2b08819361cc9dcd0ebce9d22cda9758211ee54c928`

使用同一原失败 FBX 的三机真实回归任务为 `3fab35c1-0f32-4ef5-b86f-42eece4fbc5d`、
`ac7d47b3-2c0f-454a-9ac9-bee41c9e9441` 和 `a6bb2a9b-8921-4fad-8c0a-bfa6596223ae`。所有首次和
重试候选的边界边、退化面和非流形边均为 0，证明旧的 9/12/15 条开放边问题已经消除。
`3fab35c1-...` 第一次成功：高模 149,999 三角形，低模 6,000 三角形、2,996 顶点、一个 UV 层；
最大轴尺寸误差约 `0.10%`，双向 P95 表面误差均低于 `0.07%`，七方向和 FBX 回读通过。另两条均因
形体尺寸或表面误差被正确拒绝，而不是因破面失败；拒绝候选没有发布。复盘发现第 2 次尝试没有被传入
Worker，因而可能重复第一次低细节语义代理，这推动了 v3.0.11。

## 16. v3.0.11 有界重试方法分流与三机真实验收

v3.0.11 把数据库中的权威 `attempt_count` 加入 Worker 领取合同，并由 Worker 传给服务器入口的
`--attempt-number`。第 1 次仍优先语义重建；只有形体/拓扑 QA 允许的第 2 次才要求更换方法，禁止重复
同类低细节代理。满足安全条件时，第 2 次在全新 `SOURCE_HIGH` 副本上采用受控直接降面并做确定性
BMesh 收尾与 Smart UV；否则改用逐组件混合构造。源高模始终只读，最终 3% 尺寸、4% 双向 P95、
闭合流形、UV、七方向和 FBX 新鲜回读门禁没有放宽。

发布身份：

- 自动拓扑服务器包：`3.0.11`
- 包清单 SHA-256：`8140b5b2359e4ea5542b533fe697992668b45b10a2496d9084c8e52a2e398f2c`
- 技能文件 SHA-256：`81a55d39d737eca4ac7c57e492cadc4cdb13104fde5e41c16e47c84d574611c0`
- Asset API：`1.6.33-retopo-retry-method-v1`
- Blender Worker：`1.4.33-retopo-retry-method-v1`
- Git 提交：`f85cb238e9d3073a67c4793c5185e0be4137a03c`
- Asset API 镜像：`sha256:d2a5b47892cf7bc092f6030b0130763f642d535d085b527e0bc682ffdb8ab421`
- Worker 镜像：`sha256:b43279c21b46e68ec4ae6c8e8f5f88b3cbb88eb516354b1327677669aa4a93c7`
- 分发 ZIP SHA-256：`fc99a79923b3bc2b862b02f299dea8b1ae44a31fbfe08123093fd7902cc41175`
- Worker 归档 SHA-256：`7dd7e748f133a87148fba0c89087e96fb8dff80bfc93b376744a7e2294962a61`
- Asset API 归档 SHA-256：`3d4cd7fa4b70d93d7b3619df8f06d4c72824a5ff9a7036e2fcea8493cc626d1e`

发布前 52 项定向单元/集成测试通过，12 项输出合同测试复跑通过，Ruff 与正式包验证通过。生产部署在
三节点 `DRAINING` 且 `jobs=0 / asset_jobs=0` 后只替换 Asset API 与 Blender Worker；三台 ComfyUI
容器未替换或重启。部署后 4090、3090-A、3090-B 均为 `ACTIVE / ONLINE / AUTHENTICATED / HEALTHY`，
技能统一为 `asset-skills-auto-retopo-align-v3.0.11`。

同一原失败 FBX（SHA-256 `b26f4303857a1cf24c810f4e6b53df4bd98de852870d770f2d4a1d898e08cb17`）
的三机并行真实任务全部成功：

- `c0bcc8dd-0c12-4023-b0bb-487ce79510da`：3090-A，第 1 次成功，384 秒；低模 1,322 三角形、
  679 顶点、13 个组件、一个 UV 层；尺寸最大误差约 `0.0013%`，低到高/高到低 P95 分别约
  `2.617% / 1.647%`。
- `d2755f28-be01-468e-ba97-dbb35727ed52`：3090-B，第 1 次因尺寸误差 `12.0%` 被拒绝；实际进程
  进入 `--attempt-number 2`，更换为 `controlled_direct_reduction` 后成功，591 秒。低模 18,000
  三角形、8,996 顶点、一个组件和一个 UV 层；尺寸最大误差约 `0.0018%`，双向 P95 均低于 `0.025%`。
- `b341ddc7-11a3-4007-9932-2d30b7d2dbda`：4090，第 1 次因尺寸误差 `3.167%` 被门禁拒绝；
  `--attempt-number 2` 换方法后成功，635 秒，低模与上述受控降面结果一致。

三条正式交付均为 12 个带 SHA-256 的文件。生成 Blend、保存后 Blend 与全新 FBX 重新导入三阶段中，
低模边界边、退化面、非流形边、重复点面、游离边点和错误朝向边均为 0；UV 层均为 1，低模三角形数
显著低于高模 149,999。坐标恢复模式均为 `source_matrix_restore`，矩阵误差为 0，未错误镜像，且
`topology_or_uv_edited=false / topology_uv_unchanged=true`。前、后、左、右、顶、底和透视七张不透明
橙色线框叠加图均已逐张人工检查，箱体、顶盖、把手、附件方向和整体轮廓一致。第 1 次被拒绝的两个
候选也均为 `boundary_edges=0 / degenerate_faces=0 / nonmanifold_edges=0`，说明拒绝原因只剩形体质量，
而 v3.0.11 的方法分流已把它们恢复为可发布结果。
