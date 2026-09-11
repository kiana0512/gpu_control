# 5070 Ti Windows 运行端口接入记录

交付批次：2026-09-11。设备：`10.3.34.18`，Windows 用户 `lilithgames`，
WSL 发行版 `Ubuntu-22.04`，WSL 运维用户 `gpucontrol`。

本阶段已经完成四个运行端口的 Windows 转发和持续维护，SSH 接管链路保持正常。
这是 Windows 网络及恢复配置的验收；业务应用、模型、原生软件许可及真实任务分别验收。

## 变更范围

用户授权在新机部署全部环境。执行前已核对并归档维护方现场修订的初始化及代理脚本；
主代理审阅了具体增量脚本后确认执行。既有现场修订包括单地址数组索引修复，以及
Network Discovery 宽泛放行规则下的精确来源拒绝保护，本次均保留。

- 原 Windows SSH `22`、WSL SSH `2222 → 22` 保持。
- 新增 `8188 / 9100 / 9201 / 9301`，分别转发到当前 WSL IPv4 的同端口。
- 现有 `Management-BlockOtherSources` 拒绝规则只追加上述四个端口；来源集合不变。
  该集合覆盖除 `10.3.34.11` 外的全部 IPv4 地址，以及全部 IPv6 地址。
- 新增的运行端口允许规则仅匹配本机 `10.3.34.18` 与来源 `10.3.34.11`。
- 新配置放在 `C:\ProgramData\GPUControl\worker-5070ti-01\runtime-proxy\`，使用独立
  `proxy-config.json`、`proxy-ownership.json` 和日志。
- 新增 `GPUControl-worker-5070ti-01-WSL-RuntimeProxy` 登录常驻任务，复用原现场代理脚本。
  原管理任务及其 keepalive 始终保持运行。

原脚本在启动时读取配置，因此采用独立运行端口任务，避免修改原配置后旧进程继续维护旧端口集合。
没有重启 Windows、WSL、SSH 或 IP Helper，没有修改 DHCP、网卡或旧节点，也没有修改业务管线。

执行脚本：
[Apply-5070TiRuntimeProxy.ps1](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/Apply-5070TiRuntimeProxy.ps1)。
默认只检查，`-Apply` 才执行。首次调用遇到 Windows 默认脚本执行策略，在当前 PowerShell
进程使用与原任务相同的 `-ExecutionPolicy Bypass` 后续跑成功；未修改持久执行策略。

## 验证

- 代理维护记录覆盖 **186.4 秒**，超过两个 60 秒周期；运行日志没有 `repair_failed`。
- 五条代理均保持正确，采样 WSL 地址为 `192.168.255.166`；节点对外登记仍使用 Windows 地址。
- 两个维护任务均为 `Running`、`Interactive`、`Highest`，由发行版拥有者运行。
- 原管理任务进程 `6128`、keepalive 进程 `5244` 均保持；原任务 XML 完全一致。
- 原脚本、原配置、原 ownership 内容的 SHA-256 均保持一致。
- WSL boot ID 保持 `bd92343c-4da8-4d0c-abdc-1283794cb054`。
- 变更前后 Windows SSH 各 5/5 成功，WSL SSH 与非交互 sudo 各 5/5 成功；
  均使用专用私钥、固定 known_hosts、`StrictHostKeyChecking=yes` 和 `IdentitiesOnly=yes`。
- `ssh / docker / containerd` 均为 systemd `enabled`。
- 已核对生效防火墙规则及来源网段覆盖；本阶段未从其他物理来源发起负向联网探测。
- 业务服务尚处于独立部署阶段，未将 HTTP 未启动记为代理故障。

原运行脚本 SHA-256：
`01f695a3dafa393a8c8bc2eb3a262919b2cfc9d7ba4e217ce1446f569a7bb1b0`。

## 登录恢复及地址条件

当前两个任务均由 `lilithgames` 登录事件触发，任务保持 WSL 活跃并每分钟维护动态 NAT 地址。
Windows `sshd` 和 `iphlpsvc` 为自动启动服务。配置具备该用户登录后不再手工运行修复命令的条件。

任务使用 `InteractiveToken`，没有开机触发器；自动登录未启用，因此当前配置不承诺无人登录时
自动恢复 WSL 业务。未通过重启或注销实测恢复，也未保存或复制任何登录凭据。

物理网卡 MAC 为 `34-5A-60-4B-2B-3D`，链路为 1 Gbps，地址由 DHCP 服务器
`10.3.34.1` 提供。本机信息不能证明已有 DHCP 地址预约；本次未改变地址分配。

## 原生软件存在性

此阶段读取软件注册表、Program Files、常用 portable 目录及 D 盘根目录，未发现 Blender、
Ministry of Flat 或 Substance Automation Toolkit；常见 SAT 许可目录也不存在。
这只说明本次检查尚未找到安装及授权，不代表已验证任何原生业务能力。
没有复制旧节点许可证、用户档案或许可内容。

## 证据

- [变更前归档说明及哈希](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-before/README.md)
- [验收摘要](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-after/verification-summary.json)
- [完整末轮检查](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-after/verification-final.json)
- [变更后 SSH 稳定性](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-after/ssh-stability.json)

采样时间以证据中的原始 UTC 时间为准。归档包含实际脚本、无凭据配置、任务 XML、规则及日志；
没有私钥、密码、模型下载凭据或许可证内容。

## WSL 发行版故障恢复实测

2026-09-11 03:07–03:11 UTC，在新 GPU 节点 `DRAINING`、GPU 和资产任务数均为 0、
ComfyUI 队列为空且无设备授权会话后，通过 Windows SSH 仅执行一次
`wsl --terminate Ubuntu-22.04`。没有手动启动 WSL，也没有重启 Windows。
原登录用户的维护任务使发行版自动恢复；terminate 返回后的首次被动 WSL SSH 探测即成功。
这个结果不等同于零秒恢复时间，也未连续采样 terminate 命令执行期间的 Windows SSH。

恢复后 Windows/WSL SSH 均成功，WSL 非交互 `sudo` 正常；`ssh`、`docker`、`containerd`
均为 active/enabled。五条端口代理恢复正确，ComfyUI 与 Real-ESRGAN 健康；
`8188/system_stats`、`9100/metrics` 和 `9301/health/live` 返回 200，
需要签名的 `9201/health` 与 `9301/internal/v1/ready` 对未签名请求返回 401。
新 GPU 与资产节点均重新 ONLINE、任务数为 0；新 GPU 继续保持 DRAINING。
从 WSL 使用已安装 LAN CA 访问控制端 HTTPS `/healthz` 返回 200。

WSL 内核 boot ID 保持不变，发行版 systemd PID 1 已重新启动；重启后的 PID 1
启动 ticks 为 `5111541`。NAT 地址仍为 `192.168.255.166`，因此本次不证明地址变化场景。
Windows 整机重启、注销及无人登录恢复仍未实测，前文的交互用户登录条件继续适用。

- [恢复验收摘要](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/wsl-recovery/summary.json)
- [恢复前检查](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/wsl-recovery/before.json)
- [恢复后检查](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/wsl-recovery/after.json)

## Windows 时间服务修复

恢复检查发现 Windows 壁钟约落后 14 小时 19 分钟，而 WSL 与控制端时间正确。
设备为工作组成员，未加入域；原有 NTP 来源是 `time.windows.com,0x9`，没有时间服务策略覆盖，
W32Time 原为停止、手动启动。主代理批准将该服务改为自动启动并向既有来源重新同步。

已将 W32Time 设置为 Automatic 并启动。时间服务事件 52 记录自动修正 `51,528` 秒偏差，
既有来源的三次 stripchart 偏差为 `+9.6～11.3 ms`。首次大幅修正后的查询暂为未同步；
再次普通 `w32tm /resync` 后成功，2026-09-11 03:18:05 UTC 的最终状态为
Leap `0`、Stratum `5`，来源仍为 `time.windows.com,0x9`。
没有手工设时钟、修改 NTP 来源、相位修正阈值或域策略，也没有重启 Windows。

- [时间修复前配置](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/wsl-recovery/windows-time-before.json)
- [既有来源连通性与事件](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/wsl-recovery/windows-time-diagnosis.json)
- [最终同步确认](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/wsl-recovery/windows-time-confirm.json)

## 5070 Ti 显存验证记录的有效范围

独立审查控制端提交 `13ef0a129759404c2313bc7d7a31827c1244eea8` 后确认：
`validated_vram_profiles` 仅由控制端登记；节点心跳不能提交或覆盖这个标签。
放宽显存门槛前须精确匹配节点 ID、工作流 key/version、模板 SHA-256 和 GPU UUID，
验证状态必须为 PASSED，证据及 runtime profile SHA 格式必须有效；缺少或不匹配的记录
回退到工作流原显存门槛，原有 class 与标签检查保留。

当前实现对 `runtime_profile_sha256` 只检查摘要格式，尚未与节点实际运行镜像、启动参数和模型
指纹实时比对。因此，既有实测结果仅覆盖当时获准且固定的 runtime；以后更换镜像、启动参数、
模型或相关运行配置时，必须先撤销受影响的验证记录，重新做真实任务验收后再登记。
不能因 GPU UUID 和模板未变就把旧的 16 GB 验证自动沿用于新 runtime。
