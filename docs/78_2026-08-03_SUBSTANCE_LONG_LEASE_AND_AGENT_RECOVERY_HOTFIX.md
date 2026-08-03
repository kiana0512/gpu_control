# Substance 长烘焙续租与 Windows Agent 自恢复热修复

日期：2026-08-03（Asia/Singapore）  
代码提交：`d5391bf`  
变更范围：GPU Control Windows Substance Baker Agent；不修改外部业务 workflow、模型、参数或输出语义。

## 1. 事故与根因

生产作业 `804f2b12-3f7b-4e99-a84e-159b0df202b1` 在
`BAKING_TEXTURE_TRANSFER / 15%` 运行超过 300 秒后，被控制面标记为
`SUBSTANCE_LEASE_EXPIRED_RECOVERY_REQUIRED`。事件时间线证明租约恰好在最后一次进度后的
300 秒到期；旧 Agent 在同步等待 `substance3d_baker.exe` 时不能发送进度或续租，因此大型
`li3d-pbr-full-v2` 烘焙会被误判为 Worker 丢失。

随后四个计划任务均以 `0xC000013A / STATUS_CONTROL_C_EXIT` 结束。Task Scheduler 同期只有
action completed 事件，没有显式 stop 事件，Windows 也没有关机或重启。旧任务使用可见的
`InteractiveToken` PowerShell 且只有 AtLogOn trigger；交互控制台/会话收到 console-control
退出后，`RestartOnFailure` 没有重新拉起 Agent。

3090-B 物理设备、WSL2、ComfyUI 和批准的业务 pipeline 均未损坏。节点进入 `DRAINING` 是
控制面对不确定 Baker 租约的恢复闭锁，不是设备关机。

## 2. 修复内容

1. 原生 Baker 命令改为异步进程等待；每 60 秒向现有 `/progress` 端点续租，并同时上报
   Worker heartbeat。
2. 续租请求设置 5 秒连接、20 秒总超时，并执行 3 次有界重试；取消请求会终止当前 Baker
   进程，避免继续生成未授权产物。
3. 五条生产 Baker 命令路径全部携带 job、lease、stage、progress 和预计剩余时间。
4. 仍严格校验原生进程退出码与 `Bake finished successfully` 标记；没有用预览或中间产物替代
   正式输出。
5. 计划任务使用隐藏 PowerShell，增加每分钟自恢复 trigger，保留
   `MultipleInstances=IgnoreNew`，开启 `StartWhenAvailable`，并禁用电池/桌面 idle 自动终止。
6. 安装器启动四个实例后逐一确认连续处于 `Running`；任一实例未稳定运行则安装失败闭锁。

Adobe 授权仍是用户级身份，因此计划任务继续使用 `InteractiveToken`。用户完全注销后需在下次
登录恢复；改成无人登录 Windows Service 需要单独确认 Adobe 授权身份和密钥 ACL，不在本次
变更内。

## 3. 生产发布过程

发布前通过审计 Admin API 将 `worker-3090-b` 置为 `DRAINING`，保留正在运行的 ImageClip
帧自然完成。随后在同一短窗口确认：

- 3090-B GPU 活动作业为 0；Asset 非终态作业为 0；四个 Baker `current_jobs=0`；
- `substance3d_baker.exe` 无残留进程；
- ComfyUI 队列为空；
- ComfyUI 身份为
  `95acf7b332f27a169c1c4de9a10b209b3bf1dd4773d2e95a3935fefcdd7cb01d`，
  `StartedAt=2026-08-03T06:37:16.542537267Z`、`RestartCount=0`、`running/healthy`。

只替换并重新注册四个 Windows Baker Agent。未停止、启动或重启 ComfyUI，未调用模型释放，
未清理缓存。Agent 脚本生产 SHA-256：

`2b26484daf1df17968f48660a3d17f7c742317792bdfc29af25bd46f5e52f42b`

安装后四个 Worker 于 `2026-08-03 08:34:05Z` 全部恢复
`ONLINE / current_jobs=0 / substance-baker-2026.08.03-v3`。ComfyUI 的 ID、StartedAt、
RestartCount 和健康状态前后完全一致。随后通过审计 Admin API 恢复 3090-B `ACTIVE`；下一条
真实 ImageClip 作业已重新分配到该节点并正常推进，恢复/排空标签均为空。

## 4. 验证

| 验证项 | 结果 |
| --- | --- |
| Baker、计划任务和 Codex 探针定向单元测试 | `19 passed` |
| Asset API 完整集成回归 | `34 passed` |
| Ruff | 通过 |
| `git diff --check` | 通过（仅既有 PowerShell CRLF 属性提示） |
| 目标 Windows PowerShell 5.1 最终脚本解析 | Agent / Installer 均 `PARSE_OK` |
| Windows trigger/settings 对象构造 | `P3650D` / `StartWhenAvailable=True` |
| 四个计划任务安装验证 | 全部 `RUNNING / RECOVERY=1m` |
| ComfyUI 进程连续性 | ID、StartedAt、RestartCount、健康前后相同 |

没有在真实队列运行时发起压力测试或 PBR canary。下一次真实 PBR 作业需重点观察至少一次超过
300 秒的阶段持续产生续租证据；若没有自然长作业，只能在用户明确批准的空闲窗口执行受控 canary。

原失败作业保持终态，避免静默重复执行；调用方应在确认输入仍有效后以新的幂等键重试。

