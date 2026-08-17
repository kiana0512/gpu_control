# 4070 Ti HAGS 重启恢复记录

## 变更目的

4070 Ti Windows 开启“硬件加速 GPU 计划”后按维护窗口重启。重启前确认节点 GPU 槽位为
`0/1`，旧 ImageClip 批次已取消，因此重启没有中断生产帧。

## 实际故障

Windows 固定地址 `10.3.34.238` 重启后可以 Ping，RDP `3389` 也已监听，用户确认 WSL2
已经启动；但控制机访问以下端口全部超时：

- `2222`：Windows portproxy 到 WSL SSH `22`；
- `8188`：Windows portproxy 到 WSL ComfyUI；
- `9201`：Windows portproxy 到 WSL Node Agent。

因此故障不在 GPU Control 调度器，也不是 WSL2 未启动，而是 WSL2 重启后动态 IPv4 变化，
Windows 旧 `portproxy` 目标没有被 Watchdog 及时刷新。旧的 S4U Watchdog 任务启动后立即退出，
无法可靠托管属于交互用户的 WSL 发行版。控制面正确把 4070 标为 `OFFLINE`，
没有向不可达节点派发任务；4090、3090-A、3090-B 继续处理新校色版本任务。

## 恢复方法

在 4070 Windows 的管理员 PowerShell 中执行：

```powershell
Start-ScheduledTask -TaskName "GPUControl-4070-WSL-Watchdog"
Start-Sleep -Seconds 5
netsh interface portproxy show v4tov4
```

若计划任务不存在，则执行正式幂等脚本：

```powershell
powershell -ExecutionPolicy Bypass -File C:\ProgramData\GPUControl\Update-4070WslProxy.ps1
```

仓库中的权威脚本是 `scripts/Update-4070WslRuntimeProxy.ps1`。它只维护固定 Windows 地址
`10.3.34.238` 上的 `2222/8188/9100/9201`，目标为当前 WSL IPv4；防火墙来源仍严格限制为
控制机 `10.3.34.11`，不会临时开放整个局域网。

## 验收门禁

恢复后必须全部满足：

1. `2222/8188/9201` 从控制机可达；
2. WSL 内 `ssh`、`docker`、`containerd` 为 `active`，用户级
   `gpu-node-agent-wsl-proxy.service` 为 `active/enabled`，Node Agent 容器健康；
3. ComfyUI `/system_stats` 与 Agent `/v1/identity` 成功；
4. 控制面节点状态为 `ONLINE / ACTIVE / 0/1`；
5. 使用 `2026.08.17-c39ed0b-colorfix-r1` 在 4070 实跑一帧，最终节点为 `SaveImage #109`，
   返回 8-bit RGBA PNG；
6. 4070 继续参与 ImageClip、粗糙度等兼容任务，但仍硬禁止 24 GiB 局部重绘。

## 永久修复要求

`GPUControl-4070-WSL-Watchdog` 必须由安装 Ubuntu 的同一 Windows 用户以最高权限运行，
同时配置“系统启动后”和“用户登录时”触发，并每分钟校正一次映射。不能改成看不到用户发行版的
`SYSTEM` 账户。下一次验收包含真实 Windows 重启，确认不需要人工刷新 portproxy。

## 本次恢复实测

维护方已用新版开机持久化 Maintainer 替代失效的旧 S4U 任务，并恢复 IP Helper/端口映射。
控制机随后实测：

- `2222`、`8188`、`9201` 可达；未签名访问 9201 返回 `401`，认证边界正常；
- WSL `6.18.33.2-microsoft-standard-WSL2`，`ssh/docker/containerd` 均为 `active`；
- `gpu-node-agent-wsl-proxy.service` 为 `active/enabled`；
- Node Agent 容器 `healthy`，ComfyUI 容器 `healthy`；
- GPU 身份为 `NVIDIA GeForce RTX 4070 Ti / GPU-70c028e4-dd91-4337-8f96-29daa437d1c3`；
- 控制面恢复为 `ONLINE / ACTIVE / 1/1` 并立即参与新校色批次。

恢复后的生产任务 `26437498-cdca-4f34-83c5-93756e95a134` 使用
`2026.08.17-c39ed0b-colorfix-r1`，耗时 `21.97 s`；ComfyUI history 的唯一输出为 `#109`。
产物是 `768×768`、8-bit、RGBA PNG，SHA-256 为
`d0f4c72ece5bb8a415b7a78463f27d7f9ec3e919338f3170891c0eaae0e3a38d`。
