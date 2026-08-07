# 2026-08-07 3090-B Direct V2.3 认证与并行恢复记录

## 结果

自动拓扑的三个 Linux Asset Worker 已恢复为可调度状态。每个 Worker 对 Codex 拓扑任务保持单并发，
因此多文件的实际并行拓扑容量恢复为三个独立任务：4090、3090-A、3090-B 各一个。

| Worker | 节点模式 | Worker 状态 | Skill 身份 | Codex |
| --- | --- | --- | --- | --- |
| `asset-control-4090` | `ACTIVE` | `ONLINE` | `asset-skills-retopology-v2.3.0` | `AUTHENTICATED / HEALTHY` |
| `asset-worker-3090-a` | `ACTIVE` | `ONLINE` | `asset-skills-retopology-v2.3.0` | `AUTHENTICATED / HEALTHY` |
| `asset-worker-3090-b` | `ACTIVE` | `ONLINE` | `asset-skills-retopology-v2.3.0` | `AUTHENTICATED / HEALTHY` |

## 3090-B 修复

3090-B 的旧运行时凭据已被撤销，真实探针确认错误为
`refresh_token_invalidated` / `token_expired`；旧 `1.4.0-retopology-v6-merged`
Worker 还会将内网 CA 注入 Codex 子进程，造成公网 `UnknownIssuer`。只检查
`codex login status` 不足以发现该问题，必须以真实只读 `codex exec` 为准。

在节点私有的持久化 `CODEX_HOME` 完成设备码重新认证后，真实请求返回 `AUTH_OK`。没有复制、导出或
记录任何 `auth.json`、access token 或 refresh token。

随后执行单节点安全滚动：

1. 通过管理 API 将 `worker-3090-b` 设为 `DRAINING`，审计主体为 `codex-operator`；
2. 等待 GPU 与 Asset Worker 活动作业均为零；
3. 预置并启用 `li3d/blender-worker:1.4.2-retopology-v2.3.0`，OCI revision 为
   `3ce03966ac05e2566269db5b6442b49237414bbd`；
4. 备份节点 Compose 与受控环境文件后，仅更新 Blender Worker 的镜像、版本和 Skill 身份；
5. 验证 `verify_package.py`、新鲜 Worker 心跳与真实 Codex 探针；
6. 通过管理 API 将节点恢复为 `ACTIVE`。

控制面中两次 mode 变更均已写入 `node.mode.change` 审计日志。ComfyUI 未停止、重启或重建；没有清理模型
缓存，也没有修改 ImageClip、ModelViewCreator 或任何外部工作流、模型、prompt、图拓扑与输出语义。

## 验证边界

本次验证证明认证、Worker 运行身份、Direct V2.3 包和调度资格已恢复。多文件调用方仍必须按
`one_file_per_job` 合约创建独立任务；不要在同一 Worker 内启动多个 Codex 拓扑任务。完整业务资产的
BLEND/FBX 交付验收应在后续受控真实任务中进行。
