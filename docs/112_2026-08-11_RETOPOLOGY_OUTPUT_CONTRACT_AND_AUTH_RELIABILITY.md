# 自动拓扑输出契约、认证刷新与门禁诊断可靠性补丁

日期：2026-08-11  
范围：GPU Control Asset API、Linux Blender Worker、Direct V2/v3 服务器集成层  
外部业务流水线：未修改 ImageClip、ModelViewCreator 的工作流、模型、参数或输出语义

## 1. 故障与根因

本轮复核了同一份 `ff_scene (1).fbx` 的两次真实失败：

- 任务 `6e5bb8a1-67b8-44a4-a9a0-a5893762cb38` 在 3090-B 返回
  `Codex completed but did not create a valid output Blend`。Codex 进程正常结束，但 Agent 没有把有效
  Blend 写到服务器约定的唯一文件名；旧适配器只给出笼统 Blender 错误，没有记录安全的文件清单。
- 任务 `b7cfb728-8e9a-4ac4-bfdd-74887d56f6d3` 在 4090 返回
  `Retopology v3 source-coordinate evidence gate failed`。旧 Worker 把几十个独立门禁压成一个布尔值，
  无法区分矩阵、中心、尺寸、手性、拓扑、UV、FBX 包围盒或结构回读中的具体失败项。
- 每个拓扑任务使用隔离 `CODEX_HOME`。Codex 在任务内刷新 OAuth 令牌后，旧 launcher 没有把新令牌
  安全写回节点私有持久目录；后续任务可能继续读取旧令牌。生产使用的是三节点各自的持久认证，
  不是共享的只读种子，因此不允许用一份账户文件覆盖三台机器。

精确输入在隔离环境重新执行后可以成功生成 8000 面低模，并通过 UV、闭合流形、源坐标恢复和 FBX
新场景回读。由此确认该输入本身可处理，主要问题是 Agent 输出契约的非确定性和集成层诊断不足，
不是应当放宽高低模对齐或拓扑质量门禁。

## 2. 修复

### 2.1 输出契约恢复

- 正式输出 `artifacts/generated.blend` 已有效时完全不介入。
- 仅在 Agent 已写出合法 `generation_report.json` 时，检查任务自身 `artifacts/` 的直属 Blend。
- 仅有一个有效候选，且它是报告声明路径或批准的兼容别名时，原样复制到正式输出名，并写
  `output_contract_recovery.json`。该动作不打开、不编辑、不重建模型。
- 候选缺失、位于输入/工作目录、越出任务目录或候选不唯一时继续硬失败，错误码为
  `RETOPOLOGY_OUTPUT_MISSING`。

### 2.2 节点私有认证刷新持久化

- 任务开始时记录持久认证 SHA-256；任务结束后仅在 Codex 确实改变任务认证文件时尝试回写。
- 回写目的地必须与本任务的认证源完全相同；拒绝符号链接和跨路径写入。
- 回写前后校验 JSON、认证模式、账户 ID、源文件并发变化和目标 SHA-256。
- 使用同目录临时文件和原子替换，权限固定为 `0600`；任何竞争或身份漂移都拒绝覆盖。
- 三台 Worker 继续保留各自独立的持久认证，不共享刷新令牌。

### 2.3 可执行诊断与失败策略

- 坐标/FBX 证据现在逐项返回稳定代码，包括矩阵、中心、尺寸、手性、拓扑、UV、FBX 高低模
  包围盒、低模结构和制品 SHA。
- Codex 认证失败、输出缺失、质量失败和坐标失败分别映射为独立公开错误；不再都显示成
  `BLENDER_EXECUTION_FAILED`。
- 认证失败和输出缺失不做盲目自动重建；认证探针不健康的 Worker 原有调度门禁继续阻止它领取
  Codex 拓扑任务。
- 滚动升级时，新 Asset API 只允许升级前已领取的 v3.0.2 任务按其任务内固定身份完成；新建任务固定
  使用 v3.0.3，旧 Worker 不能领取。任务身份与交付 manifest 不一致或不在批准窗口内仍硬拒绝。
- 原有严格要求保持不变：源高模只读、低模面数更少且有 UV、无退化/开边/非流形/游离/重复几何、
  高模为唯一坐标权威、FBX 必须新场景回读通过。

## 3. 发布身份与验证

- 自动拓扑服务器包：`3.0.3`
- 包清单 SHA-256：`6125113a5e703cd288a4265f381031baf125b262c9eddbddfa57cc05d9e36647`
- Asset API：`1.6.22-retopo-reliability-v1`
- Blender Worker：`1.4.24-retopo-reliability-v1`
- 定向单元测试：`32 passed, 4 skipped`
- 全部单元测试（Python 3.11）：`357 passed, 5 skipped`
- Asset API 集成测试（Python 3.11）：`95 passed`
- 包完整性校验：通过，24 个包文件与清单一致

## 4. 生产滚动与真实回归

2026-08-11 已完成生产滚动。滚动顺序为 `worker-3090-b`、`control-4090`、`worker-3090-a`；
每个节点均先进入 `DRAINING`，确认 GPU 任务、资产任务和活动租约均为 0 后，只重建对应 Blender
Worker，再通过包完整性、心跳和 Codex 探针后恢复 `ACTIVE`。3090-A、3090-B 与 4090 的 ComfyUI
实例均未停止、重启或清理，滚动前后的容器 ID 未变化，重启次数为 0。

生产最终状态：

- 三个 Linux Blender Worker 均为 `1.4.24-retopo-reliability-v1`，镜像
  `sha256:bc6710503290552ee10a8aaa5bb35a3a9930a370d4d68d842c871dd49b28e7b1`；
- 三个 Worker 的技能均为 `asset-skills-auto-retopo-align-v3.0.3`，状态均为 `ONLINE`，
  `codex_auth_status=AUTHENTICATED`、`codex_probe_status=HEALTHY`；
- Asset API 为 `1.6.22-retopo-reliability-v1`，镜像
  `sha256:04c6f4d31c876f826fe8ff32c566f3c2584e01334f2c353e7733456b9aeb35be`，健康检查通过；
- 三台 Worker 内执行 `verify_package.py` 均返回 `ok=true`、`package_version=3.0.3`，无缺失文件。

随后通过生产公开入口重新提交原始故障模型，未使用隔离替代物：

- 回归任务：`1ec9c3ad-b093-4002-80f3-7e41b13ca9a5`；
- 原始项目 SHA-256：`b26f4303857a1cf24c810f4e6b53df4bd98de852870d770f2d4a1d898e08cb17`；
- 执行节点：`asset-worker-3090-b`；
- 执行时间：2026-08-11 11:32:52 UTC 至 11:38:13 UTC，约 5 分 21 秒；
- 终态：`SUCCEEDED`，一次领取、无重试，10 项制品均已登记 SHA-256。

真实交付证据：

- 高模 149,999 面；低模 1,168 面、1,200 点、1 个 UV 层，低模面数严格少于高模；
- 低模开边、非流形边、游离边、游离点、重复点、重复面、退化面、方向不一致边和微碎片均为 0；
- 坐标恢复模式为 `source_matrix_restore`，高模为唯一坐标权威，未使用 ICP，未在对齐阶段编辑
  拓扑或 UV；矩阵误差为 0、中心误差为 0、尺寸误差为 1.4345%，手性前后保持正向；
- Blend 保存回读与高低模 FBX 新场景回读全部通过；低模 FBX 结构完全一致，低模中心/尺寸回读误差为 0；
- 正式低模 FBX SHA-256 为
  `ed0088a0771b45744a51e8317e6da5df5050cb367a29d938278ea7133cedf5db`，对齐 Blend SHA-256 为
  `63197df8f31af9ea8d99a9b6890a85ae64f147a3b238151bc90b896892cde591`。

因此，同一输入在旧 v3.0.2 上出现的笼统坐标门禁失败已不再复现；v3.0.3 已真实完成低模生成、
源坐标恢复、UV/拓扑硬门禁、FBX 回读和最终制品发布。
