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
