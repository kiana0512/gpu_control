# RTX 5070 Ti Windows 服务器预处理手册

日期：2026-09-10　｜　交付阶段：主机准备与 SSH 接通　｜　接收方：新 5070 Ti 设备维护人

> 2026-09-11 更新：此文保留初次预处理流程。20260910 的 DOCX、HTML、目录及 ZIP 为历史包；最新可执行包见 [20260911 发布目录](https://github.com/kiana0512/gpu_control/blob/agent/four-gpu-4070ti-closure/artifacts/control-plane/5070ti-20260911/README.md)。新包含已验证的转发与防火墙修复，阅读文件为 README.md，不再附 DOCX/HTML。该节点已完成部署，现有主机勿重复初始化；状态以 [最终验收](https://github.com/kiana0512/gpu_control/blob/agent/four-gpu-4070ti-closure/docs/5070TI_DEPLOYMENT_ACCEPTANCE_20260911.md) 为准。

## 1. 本次要完成什么

请先把新机器准备到“4090 主控可以使用公钥，通过 SSH 管理 Windows 和 WSL Ubuntu”的状态。之后由 GPU Control 维护方远程安装锁定的容器运行环境、分发模型和工作流、接入调度并验收。

本次操作只针对新增 5070 Ti，不需要操作已有 4090、3090-A、3090-B、4070 Ti。

| 项目 | 本次目标 |
|---|---|
| GPU Control 主控 | `10.3.34.11`，HTTPS `443` |
| 新节点标识 | 暂定 `worker-5070ti-01`；只用于新机文件/任务命名，尚未登记生产 |
| 新机 Windows 地址 | 由维护方回填实际固定/保留地址，不能套用旧机 `.14` 或 `.238` |
| 操作系统方式 | Windows 宿主 + WSL2 Ubuntu 22.04 + 后续 Docker Engine |
| Windows 管理 | 新机 Windows IP 的 TCP `22`，仅主控来源、公钥认证 |
| WSL 管理 | 新机 Windows IP 的 TCP `2222` → WSL TCP `22`，仅主控来源、公钥认证 |
| 本阶段完成标准 | 驱动正常、Ubuntu/systemd 正常、磁盘/网络信息齐全、两层 SSH 就绪、回执完整 |

**做到第 9 节回执后即可交接。暂不安装业务容器、不复制模型、不注入集群密钥、不登记 ACTIVE、不运行生产任务。**

## 2. 准备包与操作账号

将准备包完整解压到新机，例如：

```text
C:\GPUControl-5070Ti-Prep
```

包内文件：

| 文件 | 用途 |
|---|---|
| `README.md` | 20260911 更新后的预处理说明；DOCX/HTML 仅存在于历史初版包 |
| `Initialize-GPUControlWindowsNode.ps1` | 分阶段检查、Windows SSH、WSL SSH、登录后恢复配置 |
| `Update-GPUControlWslProxy.ps1` | 维护 WSL 存活及动态 IP 的端口转发 |
| `gpu-control-5070ti-management.pub` | 4090 为新机准备的独立 SSH 公钥 |
| `GPU_CONTROL_LAN_CA.crt` | 主控 HTTPS 的公开 CA 证书 |
| `SHA256SUMS.txt` | 文件校验清单 |

私钥只保存在 4090，不在此包内。不要索取或发送私钥、Windows 密码、API Key、许可证文件。

**从头到尾使用同一个 Windows 账号安装 Ubuntu、配置 WSL、注册恢复任务。** 打开该账号自己的“管理员 Windows PowerShell”。如果 UAC 要求切换为另一个管理员账号，请先记录两个账号并反馈，避免把 Ubuntu 安装到错误用户下面。

现网 4070 Ti 曾因恢复任务使用 S4U、无法访问正确用户的 WSL 而离线。本包采用“发行版拥有者登录后运行”的任务，保持同一用户身份；**Windows 重启后需要这个账号登录，不能把它当成无人登录也已通过恢复验收。**

在管理员 Windows PowerShell 中进入目录：

```powershell
Set-Location C:\GPUControl-5070Ti-Prep
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Initialize-GPUControlWindowsNode.ps1 -Stage Inspect |
    Tee-Object -FilePath .\5070ti-preflight.txt
```

`Inspect` 不安装、不修改配置。`-ExecutionPolicy Bypass` 仅作用于这个脚本进程；若被企业组策略阻止，记录报错交给本机 IT，不修改全局策略。

## 3. 硬件、驱动、网络和磁盘

### 3.1 收集实际硬件

在 Windows PowerShell 执行并保存输出：

```powershell
whoami
Get-CimInstance Win32_Processor |
    Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
Get-CimInstance Win32_ComputerSystem |
    Select-Object Name, @{N='PhysicalRAM_GiB';E={[math]::Round($_.TotalPhysicalMemory/1GB,1)}}
Get-NetAdapter | Select-Object Name, Status, MacAddress, LinkSpeed
nvidia-smi.exe --query-gpu=name,uuid,driver_version,memory.total --format=csv
```

标准桌面 RTX 5070 Ti 为 16GB 显存、Blackwell 架构；以这台机器的实际结果登记。[NVIDIA 规格](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/)

### 3.2 Windows NVIDIA 驱动

准备支持此型号、兼容 CUDA 13.x 的 NVIDIA Windows 正式驱动（R580 或更新的兼容版本）。已有满足要求的驱动可以保留。驱动更新如要求重启，只在新机无工作时安排本机重启。[CUDA 驱动兼容要求](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/index.html)

原因：现网候选 ComfyUI 镜像实测使用 PyTorch `2.11.0+cu130`，不是只看基础镜像标签里的 CUDA 12.8。镜像已包含 `sm_120` 架构，但仍须后续在新 GPU 执行真实算子和业务验收。

**只在 Windows 安装显示/GPU 驱动。WSL 内不要安装 Linux NVIDIA 显卡驱动、`cuda-drivers` 或会附带驱动的 CUDA 元包；本阶段也不需要安装 CUDA Toolkit、PyTorch、ComfyUI。** WSL 使用宿主提供的驱动接口。[NVIDIA WSL 指南](https://docs.nvidia.com/cuda/wsl-user-guide/)

### 3.3 网络

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
    Select-Object InterfaceAlias, IPAddress, PrefixLength
Test-NetConnection 10.3.34.11 -Port 443
```

通过标准：`TcpTestSucceeded : True`。由网络维护人保留新机的 Windows LAN 地址；可以使用 DHCP Reservation。不要随意指定一个可能冲突的 `10.3.34.x` 地址，也不要把 WSL 的 `172.x` NAT 地址当作设备固定地址。

Ubuntu 安装和 APT 更新需要可用的 DNS 与出网下载通道。企业网络下载失败时保留报错，不使用不明镜像站或关闭 TLS 校验。

### 3.4 磁盘与资源

```powershell
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
    Select-Object DeviceID,
      @{N='Size_GiB';E={[math]::Round($_.Size/1GB,1)}},
      @{N='Free_GiB';E={[math]::Round($_.FreeSpace/1GB,1)}}
```

请记录 Ubuntu 的 VHDX 所在 Windows 盘。按现有模型与镜像规模，**建议该盘预留至少 300GB，500GB 以上更方便后续版本缓存与临时文件**；这是本次部署空间规划，不是显卡硬性要求。空间不足时先反馈，不删除现有文件。

后续模型和 Docker 数据放在 WSL 的 Linux ext4 文件系统中。不要把 `/opt/imageclip`、Docker data-root 或业务临时目录放到 `/mnt/c` 这种 Windows 文件挂载路径。

本次先记录物理 CPU/RAM 和现有 `%USERPROFILE%\.wslconfig`，资源上限由主控按实机核定。**不要直接套用 4070 Ti 的 12 vCPU/32GB，或 3090-B 的 64 vCPU/64GB。** `.wslconfig` 会影响该用户的全部 WSL2 发行版；已有配置保留。[Microsoft WSL 配置说明](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)

## 4. 安装或检查 WSL2 Ubuntu

先检查：

```powershell
wsl.exe --version
wsl.exe --status
wsl.exe --list --verbose
```

如已存在 Ubuntu，记录名称、版本及所属用户；不要卸载、重建、`--unregister` 或移动已有发行版。已有 Ubuntu 24.04/其他版本也先回报，不原地降级。

全新机器按以下步骤准备 Ubuntu 22.04：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Initialize-GPUControlWindowsNode.ps1 `
    -Stage WslPlatform -Apply -Distro Ubuntu-22.04 -InstallDistro
```

如输出 `REBOOT_REQUIRED=true`，说明 Windows 组件需要本机重启：先结束新机工作，维护人安排重启，登录原账号后重新执行此命令。脚本不会自动重启。

如安装受商店/网络限制，可由本机 IT 按 Microsoft 官方渠道安装相同发行版。不要换成未知来源的打包环境。[Microsoft WSL 安装](https://learn.microsoft.com/en-us/windows/wsl/install)

首次进入：

```powershell
wsl.exe -d Ubuntu-22.04
```

按提示创建普通 Linux 用户，建议名为 `gpucontrol`。本地设置密码自行保管，无需发送。普通登录用户可以是默认 UID 1000，**不要强制改为容器 UID 10001**。

在 Ubuntu 中检查：

```bash
cat /etc/os-release
uname -r
ps -p 1 -o comm=
id
free -h
df -hT /
/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv
exit
```

期望 Ubuntu 22.04、WSL2、GPU 型号/UUID 与 Windows 相同。`ps` 应显示 `systemd`；若没有，第 6 节脚本会补充配置并提示重启该发行版。

## 5. 准备 Windows SSH 管理入口

返回新机管理员 Windows PowerShell，仍在准备包目录内。填写真实 IP；`$Distro` 必须等于 `wsl --list --verbose` 中的发行版名称：

```powershell
$WindowsIP = Read-Host '输入新 5070 Ti 的 Windows 局域网 IPv4 地址'
$Distro = 'Ubuntu-22.04'
$WslUser = 'gpucontrol'
$NodeName = 'worker-5070ti-01'
$KeyPath = (Resolve-Path .\gpu-control-5070ti-management.pub).Path
```

安装/配置 Windows OpenSSH Server：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Initialize-GPUControlWindowsNode.ps1 `
    -Stage WindowsSsh -Apply -WindowsIP $WindowsIP -NodeName $NodeName -PublicKeyPath $KeyPath
```

脚本将安装缺失的 OpenSSH Server、加入本次独立公钥、校验 SSH 配置、准备仅允许 `10.3.34.11` 来源的 TCP 22 防火墙规则。管理员账户可能使用 `C:\ProgramData\ssh\administrators_authorized_keys`；脚本会根据 SSH 有效配置识别路径并设置权限。[Microsoft 公钥认证说明](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)

如果旧 SSH 服务已运行，脚本不会直接重启它。看到 `SSH_CONFIG_RELOAD_REQUIRED=true` 时，由维护人确认本机没有需要保留的 SSH 会话，再在 Windows 本地执行 `Restart-Service sshd`。

发现已有端口占用、额外防火墙开放规则或自定义 SSH 设置时，脚本会提示核对。保留报错反馈，不删除无关规则。若新机原本已作为服务器提供 SSH，请先交回检查结果，由主控做增量配置，不直接执行本节 Apply。

## 6. 准备 WSL SSH 与管理权限

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Initialize-GPUControlWindowsNode.ps1 `
    -Stage WslSsh -Apply -Distro $Distro -WslUser $WslUser -PublicKeyPath $KeyPath -EnableWslSudo
```

此阶段会安装 Ubuntu OpenSSH Server、准备普通 Linux 账户、公钥认证和 SSH 服务。`-EnableWslSudo` 为该部署账号配置 WSL 内免密码 sudo，便于主控后续自动安装和维护；不需要把本地密码交给主控。作用范围是这台新机的目标 Ubuntu。

若提示 `WSL_RESTART_REQUIRED=true` 并以非零状态停止：已补好 systemd 配置，但尚未完成 SSH。确认目标新 Ubuntu 无工作后，**只重启该发行版**，再重复上面的 WslSsh 命令：

```powershell
wsl.exe --terminate $Distro
wsl.exe -d $Distro -- ps -p 1 -o comm=
```

不要执行会关闭所有发行版的 `wsl --shutdown`。若同时有其他 WSL 工作，先回报并安排维护时机。

检查：

```powershell
wsl.exe -d $Distro -- systemctl is-active ssh
wsl.exe -d $Distro -u $WslUser -- sudo -n true
```

期望 SSH 为 `active`，sudo 命令退出码为 0。两层 SSH 均只使用公钥，之后仍须由 4090 实际连接确认。

## 7. 转发 WSL SSH 并配置登录后恢复

在同一 Windows 用户的管理员 PowerShell 中执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Initialize-GPUControlWindowsNode.ps1 `
    -Stage Persistence -Apply -WindowsIP $WindowsIP -Distro $Distro -NodeName $NodeName
```

这一阶段只发布 `WindowsIP:2222 → 当前 WSLIP:22`。**本次不传 `-RuntimePorts`。** 后续业务部署才增加 8188、9100、9201、9301；不开放 Docker TCP 2375，WSL 默认不启用 DCGM 9400。

脚本把配置保存到 `C:\ProgramData\GPUControl\worker-5070ti-01`，注册 `GPUControl-worker-5070ti-01-WSL-Maintainer` 登录任务，维持 WSL 存活并每 60 秒核对动态 IP 与端口映射。

检查：

```powershell
netsh.exe interface portproxy show v4tov4
Get-ScheduledTask -TaskName 'GPUControl-worker-5070ti-01-WSL-Maintainer' |
    Select-Object TaskName, State, @{N='Owner';E={$_.Principal.UserId}}, @{N='LogonType';E={$_.Principal.LogonType}}
Get-Service sshd,iphlpsvc
```

恢复任务应为当前 Ubuntu 拥有者，`LogonType` 为 `Interactive`。本轮可保持用户登录并锁屏；不要注销或让新机进入睡眠。正式重启恢复与供电/睡眠策略将在业务部署前单独验证。

脚本针对 WSL NAT 网络。若新机已启用 mirrored 网络，不要强行改网络模式；把检查结果发回主控适配。

## 8. 验证主控 HTTPS，并提供 SSH 主机指纹

把本包公开 CA 安装到目标 Ubuntu 信任库，在 Windows PowerShell 执行：

```powershell
$CaWindowsPath = (Resolve-Path .\GPU_CONTROL_LAN_CA.crt).Path
$CaLinuxPath = (& wsl.exe -d $Distro -- wslpath -a $CaWindowsPath).Trim()
wsl.exe -d $Distro -u root -- install -m 0644 $CaLinuxPath /usr/local/share/ca-certificates/gpu-control-lan-ca.crt
wsl.exe -d $Distro -u root -- update-ca-certificates
wsl.exe -d $Distro -u root -- apt-get install -y --no-install-recommends curl
wsl.exe -d $Distro -- curl -fsS https://10.3.34.11/health/ready
```

期望返回 `status=ready`、`database=ok`、`redis=ok`；不要加 `-k` 跳过证书校验。若主控临时不可达，保留错误返回，不宣称就绪。

提供新机的**公开主机指纹**，让主控在首次连接时核验身份：

```powershell
& "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe" -lf "$env:ProgramData\ssh\ssh_host_ed25519_key.pub"
wsl.exe -d $Distro -u root -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

发送指纹文本即可，不发送去掉 `.pub` 后缀的私钥文件。用于授权的本包管理公钥指纹为：

```text
SHA256:ebHg6x1cFwMIZED+uZNP9heMwpUvFxi0i3nyyur+tVI
```

它与上述两个 SSH **主机指纹**用途不同，不应互相替代。

## 9. 维护方回执模板

请复制填写以下内容，附 `5070ti-preflight.txt` 和必要报错。不要附 `.env`、密码、私钥或业务授权文件。

```text
5070 Ti 主机预处理回执
完成时间：
维护人：
Windows 主机名：
Windows 版本 / build：
安装并拥有 WSL 的 Windows 账号（whoami）：
Windows 固定 IPv4 / 子网：
物理网卡 MAC / 链路速度：
地址保留已完成：是 / 否
CPU 型号 / 物理核数 / 逻辑线程数：
物理内存 GiB：
NVIDIA GPU 型号 / GPU UUID / 显存 MiB：
Windows NVIDIA 驱动版本：
WSL 版本 / Kernel：
Ubuntu 发行版名称 / Ubuntu 版本 / WSL1或2：
Ubuntu 所属 Windows 用户：
Linux 管理用户名 / UID：
Ubuntu PID 1 是否 systemd：
WSL 可见 CPU / 内存 / swap：
Ubuntu VHDX 所在 Windows 盘 / 该盘实际剩余空间：
WSL df -hT / 结果：
Windows -> 10.3.34.11:443：成功 / 失败
WSL -> HTTPS /health/ready 严格证书校验：成功 / 失败
Windows SSH 地址：<Windows-IP>:22
Windows SSH 登录用户名：
Windows SSH Ed25519 主机指纹：
WSL SSH 地址：<Windows-IP>:2222
WSL SSH 登录用户名：
WSL SSH Ed25519 主机指纹：
WSL sudo -n true：成功 / 失败 / 未配置
恢复任务名称 / State / Owner / LogonType：
是否能保持上述 Windows 用户登录并锁屏：
是否已做过新机重启后的恢复检查（未做如实写）：
是否已有 Docker Desktop、Docker Engine、其它业务或 GPU 程序：
是否已有合法安装的 Substance/MOF/Blender（仅记录，不复制许可证）：
未完成项与原始报错：
```

维护方只需把回执交回。主控随后会使用本次独立密钥核验 Windows 22 和 WSL 2222；主控尚未连接验证时，请写“主机侧已准备，待主控验证”，不要写“部署全部完成”。

## 10. 交接后的统一部署范围

以下保留初次 SSH 交接时的后续计划。该节点在 20260911 已完成运行环境与批准 GPU 工作流验收；当前状态见最终部署记录，无需重复这些初始化步骤：

1. 检查新机占用与目录，安装与现网匹配的 Docker Engine、containerd、Compose、NVIDIA Container Toolkit；WSL 不装 Linux GPU 驱动，也不新装 Docker Desktop。
2. 下发已验证的 ComfyUI、RealESRGAN、Blender Worker 镜像；按批准版本原样同步 ImageClip / ModelViewCreator 文件及模型，校验 SHA-256。
3. 配对健康节点的 Blender 镜像与批准技能包，不复制主控当前已知不一致的热改文件。
4. 配置 Node Agent、独立节点 HMAC、GPU 遥测、日志、监控和所需端口。集群密钥由主控经 SSH 下发，不经聊天转发。
5. 新节点以 `DISABLED/DRAINING` 隔离验收，真实检查 CUDA、最终业务输出、SSH 重连和恢复，再启用经过验证的能力。

初版按 16GB 显存预计可接入 ImageClip、roughness、RealESRGAN 和 CPU 资产处理。20260911 已另外完成三个 ModelView 工作流的原样最终输出 canary，并启用绑定此节点、GPU UUID、工作流版本与模板 SHA 的专用验收记录；全局 24000 MiB 合同保持不变，业务参数未修改。

Substance 和 Windows MOF 还涉及本机有效许可、用户配置及后端专用节点适配；“安装 WSL”并不自动获得这两项能力。本轮只记录是否已有合法环境。

20260910 初版仅经过静态检查。20260911 更新已在此 Windows 主机通过 PowerShell 5.1 解析与函数回归；转发脚本与现场文件摘要一致，SSH 与 WSL 自动恢复已实测。更新后的初始化脚本未对已部署主机重复执行整机初始化。任一阶段失败时，保留输出并交回，勿自行清理旧目录、关闭防火墙或重置系统。
