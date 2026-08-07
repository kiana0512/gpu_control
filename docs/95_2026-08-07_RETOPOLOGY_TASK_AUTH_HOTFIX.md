# 2026-08-07 Direct V2 任务私有认证热修复

## 现象与根因

一次三文件并行提交中，4090 与 3090-B 在 `RETOPOLOGY_DIRECT_V2_BUILD` 立即失败，错误为
`codex_runner_failed`；3090-A 的同批任务成功。Worker 健康探针均为
`AUTHENTICATED / HEALTHY`。

根因是 GPU Control 在创建任务私有 `CODEX_HOME` 时，从只读 bootstrap 挂载复制了已经过期的
`auth.json`，而不是从节点私有、可写且已成功轮换的持久化 `CODEX_HOME/auth.json` 复制。探针使用后者，
因此健康状态不能代表旧任务启动器的认证状态。

## 修复与发布

- 代码提交：`55a09d2780197549a6ba5a35a4e098b5596c7d1d`；
- Worker 镜像：`li3d/blender-worker:1.4.3-retopology-v2.3.0-auth`；
- 任务启动器现在从由 `codex_environment()` 验证过的节点私有 `auth.json` 为任务私有目录播种凭据；
- 4090 与 3090-B 均按 `DRAINING → 0 jobs → Blender Worker 替换 → 包与 Codex 验证 → ACTIVE` 滚动更新；
- 3090-A 未重启。三台节点恢复为 `ACTIVE / ONLINE / AUTHENTICATED / HEALTHY`。

没有复制、导出或记录认证内容；没有重启 ComfyUI、清理模型缓存，或修改外部工作流、Skill、模型、prompt、图拓扑与输出语义。

已终态失败的两项任务保持原诊断，不会自动重跑；调用方应以新的独立任务重新提交。其余同批成功任务保持可下载状态。
