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

本节在安全滚动和同一故障输入真实回归完成后回填。滚动必须逐节点排空，确认无运行中的资产任务，
且不得停止、重启或清理任一 ComfyUI 实例。
