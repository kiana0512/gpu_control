# 3090-B Windows + WSL2 混合节点部署与远程接管方案

文档日期：2026-07-28
文档状态：`DESIGN LOCKED / NOT DEPLOYED`
适用节点：`worker-3090-b`
控制中心：`control-4090`（`10.3.34.11`）

> 本文只固化 3090-B 重装后的目标架构、实施顺序、远程接管方式和验收标准。编写本文没有修改、
> 重启或部署任何生产后端，也没有改变 ImageClip、ModelViewCreator 或其工作流。

## 1. 最终结论

3090-B 可以重装为 Windows，并继续作为 4090 统一调度中心完全管理的下属节点。推荐采用“一台物理机、
两个受控运行时”的结构：

```text
统一调度中心 4090（唯一控制权威，10.3.34.11）
  │
  ├─ 心跳、调度、租约、审计、任务状态、制品校验
  │
  └─ 3090-B 物理节点（固定身份，目标地址 10.3.34.14）
       ├─ Windows 原生运行时
       │    ├─ Windows Host Agent
       │    ├─ Substance/SD、烘焙及其他 Windows 专属软件
       │    ├─ Windows GPU/CPU/磁盘指标
       │    └─ WSL 启停、端口转发、故障恢复和 GPU 所有权控制
       │
       └─ WSL2 Ubuntu 22.04 运行时
            ├─ Docker Engine + NVIDIA GPU
            ├─ ComfyUI
            ├─ ImageClip 抠图管线
            ├─ ModelViewCreator 局部重绘管线
            ├─ Linux Node Agent
            └─ Linux 指标与日志
```

4090 仍然是唯一调度入口。业务调用方不直接访问 Windows、WSL、ComfyUI 或烘焙软件；任务均通过统一
调度中心 API 进入，最终状态与产物也由 4090 汇总和发布。

### 1.1 操作系统选择

- **推荐 Windows 11 Pro/Enterprise**：WSL2、Docker 和网络能力更完整，长期维护风险更低。
- 如果业务软件明确只能使用 **Windows 10 22H2**，本方案仍可实施，但必须接受其已于
  2025-10-14 结束支持带来的安全和兼容风险，并固定可验证的软件版本，不能依赖自动升级。
- Windows 原生软件的最终选择以实际烘焙软件支持矩阵和许可证为准；WSL 内仍固定 Ubuntu 22.04，
  与现有 Linux 节点保持最大兼容性。

官方参考：

- Windows 10 支持周期：<https://learn.microsoft.com/lifecycle/announcements/october-14-2025-products-end-of-support>
- WSL 安装：<https://learn.microsoft.com/windows/wsl/install>
- WSL systemd：<https://learn.microsoft.com/windows/wsl/systemd>
- WSL 网络：<https://learn.microsoft.com/windows/wsl/networking>
- NVIDIA CUDA on WSL：<https://docs.nvidia.com/cuda/wsl-user-guide/index.html>
- Docker WSL2：<https://docs.docker.com/desktop/features/wsl/>

## 2. 物理身份：重装不得新建第二个 3090-B

以下信息是 3090-B 的唯一物理身份，系统重装、Windows 用户名变化、WSL 虚拟网卡变化都不能改变它：

| 字段 | 固定值 | 说明 |
|---|---|---|
| GPU Control 节点 ID | `worker-3090-b` | 保留现有数据库身份 |
| 展示名称 | `3090-B` | Web UI 的物理节点名称 |
| 物理网卡 MAC | `2c:f0:5d:76:7b:70` | 唯一机器身份，不使用 WSL 虚拟 MAC |
| GPU UUID | `GPU-092a5184-5857-d196-5df2-efa9503368aa` | RTX 3090 的唯一 GPU 身份 |
| 目标主机 IP | `10.3.34.14/24` | 优先由 DHCP 按物理 MAC 保留 |
| 默认网关 | `10.3.34.1` | 安装完成后重新实测 |
| 控制中心 | `10.3.34.11` | 只接受该主控的业务控制连接 |
| 原 Linux 主机名 | `lilithgames3` | 仅作历史追踪，不作为唯一身份 |

历史数据库曾看到 B 从 `10.3.34.4` 上报，目标固定地址则为 `10.3.34.14`。上线前必须由网络管理员按
MAC `2c:f0:5d:76:7b:70` 保留 `.14`，并确认 `.14` 没有被其他设备占用。不能只在 WSL 内设置地址：
对局域网提供服务的是 Windows 物理网卡。

建议把逻辑运行时登记为：

| 运行时 | 建议 ID | 主要能力 |
|---|---|---|
| 物理节点 | `worker-3090-b` | 汇总健康、物理资源和调度状态 |
| Windows | `worker-3090-b-windows` | Windows 烘焙、宿主管理、GPU 仲裁 |
| WSL2 | `worker-3090-b-wsl` | 抠图、局部重绘、Linux 容器服务 |
| Windows Asset Worker | `asset-worker-3090-b-windows` | Windows 专属资产作业执行器 |

任务详情记录实际运行时，但 Web UI 顶层只展示一个 3090-B 物理节点，避免把同一张 GPU 显示成两台设备。

## 3. 责任边界

### 3.1 用户需要现场完成的一次性操作

1. 在 BIOS 中开启 CPU 虚拟化，并设置来电自动开机。
2. 安装 Windows；优先 Windows 11，确需 Windows 10 时使用 22H2 并记录完整 build。
3. 安装有线网卡驱动和 NVIDIA Windows 驱动，确认 Windows 下 `nvidia-smi.exe` 能识别 RTX 3090。
4. 保证物理网卡获得 `10.3.34.14`，网络管理员完成 MAC 地址保留。
5. 在管理员 PowerShell 中启用 OpenSSH Server，安装 4090 的 SSH 公钥。
6. 如 Windows 专属软件需要图形界面登录、许可证或 Adobe 激活，由用户完成一次激活并确认命令行工具可用。
7. 不在聊天中发送 Windows 密码；远程管理使用专用管理员账号和 SSH 公钥。

### 3.2 SSH 建立后可由 4090 远程完成

1. 安装和配置 WSL2 Ubuntu 22.04。
2. 在 WSL 内安装 Docker Engine、systemd 服务、GPU Control、ComfyUI、Node Agent 和监控。
3. 导入固定版本镜像，恢复外置模型和业务仓库的批准版本，逐项校验 SHA-256/Git commit。
4. 安装 Windows Host Agent、开机计划任务、WSL 保活和动态端口转发。
5. 配置仅允许 4090 访问的 Windows 防火墙规则。
6. 配置 GPU 独占租约，联调 Windows 与 WSL 运行时切换。
7. 执行冷启动、热启动、断电恢复、网络恢复、真实任务、产物完整性及回滚验收。

## 4. 首次远程接管的最小入口

安装完 Windows 后，在 **管理员 PowerShell** 中完成 OpenSSH。以下是运行手册模板，正式执行时先检查
现状，再按实际 Windows 版本调整；公钥必须从 4090 的现有管理公钥读取，不在文档中硬编码。

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic

# Windows 自带规则存在时，将来源限制为 4090；不存在时创建专用规则。
if (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Enabled True -Profile Any
    Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -RemoteAddress 10.3.34.11
} else {
    New-NetFirewallRule -Name GPUControl-SSH-From-4090 `
        -DisplayName "GPU Control SSH from 4090" `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 `
        -RemoteAddress 10.3.34.11
}
```

若 SSH 登录账号属于本机 Administrators 组，公钥位置为：

```text
C:\ProgramData\ssh\administrators_authorized_keys
```

该文件应只允许 `SYSTEM` 和 `Administrators` 读取。现场完成后，用户只需把以下非敏感信息提供给维护方：

```text
WINDOWS_IP=10.3.34.14
WINDOWS_USER=<SSH 管理账号>
HOSTNAME=<Windows 主机名>
WINDOWS_EDITION=<Windows 10/11 + edition>
WINDOWS_BUILD=<winver 完整版本>
NVIDIA_DRIVER=<nvidia-smi 显示版本>
SD_PRODUCT=<准确产品名称>
SD_VERSION=<准确版本>
SD_CLI_PATH=<命令行程序路径，若已知>
```

4090 首次只做只读探测：

```bash
ping -c 3 10.3.34.14
ssh -o BatchMode=yes '<WINDOWS_USER>@10.3.34.14' \
  'powershell -NoProfile -Command "$PSVersionTable; Get-ComputerInfo | Select WindowsProductName,WindowsVersion,OsBuildNumber; nvidia-smi.exe -L"'
```

## 5. 磁盘与目录布局

WSL 的高频 Linux 文件必须放在 WSL ext4 VHDX 内，不放在 `/mnt/c`，以避免权限、符号链接和小文件 I/O
问题。3090-B 现有磁盘历史信息为：系统盘约 1 TB、`sda` 约 1.8 TB、`nvme1n1` 约 3.6 TB、
`nvme2n1` 约 466 GB；重装时必须重新核对盘符和物理盘序号，禁止仅按历史名称格式化磁盘。

推荐布局：

```text
Windows C:\                    Windows、驱动、Host Agent、少量日志
Windows D:\GPUControl\        Windows 专属软件、作业 staging、最终资产缓存
大容量 NVMe:\WSL\            Ubuntu 22.04 的 ext4.vhdx

WSL /opt/gpu-control           GPU Control 当前固定源码
WSL /opt/imageclip             ImageClip Git 工作树（只部署批准 commit）
WSL /opt/modelviewcreator      ModelViewCreator Git 工作树（只部署批准 commit）
WSL /srv/comfyui/models        模型真源/软链接布局
WSL /srv/comfyui/runtime       ComfyUI 持久运行数据
WSL /srv/gpu-control/images    离线镜像与 SHA-256
WSL /srv/gpu-control/jobs      任务 staging，不作为最终状态真源
```

Windows 和 WSL 之间的大文件交接使用一个受控交换目录，每个任务写独立 staging，校验完成后原子发布；
禁止从应用的临时、预览或缓存目录直接返回用户产物。

## 6. WSL2 与 Linux GPU 服务

### 6.1 WSL 基线

- 发行版固定为 Ubuntu 22.04。
- `/etc/wsl.conf` 启用 systemd。
- Windows 只安装 NVIDIA Windows 驱动；WSL 内不得再安装 Linux 内核显示驱动。
- WSL 内可安装 CUDA 用户态组件，但必须以当前 ComfyUI 固定镜像的实际需要为准。
- Docker Engine 运行在 WSL 内，以最大程度复用现有 Linux compose、systemd、目录和运维脚本。
- 不能仅依靠 WSL systemd 保证开机运行；Windows 启动计划任务负责唤醒 WSL、启动服务和刷新端口转发。

### 6.2 管线一致性硬门槛

3090-B WSL 返回 `ACTIVE` 前，必须与 4090、3090-A 对齐：

1. GPU Control 固定 commit 与部署清单一致。
2. ComfyUI 使用固定 tag 和 Image ID，禁止 `latest`，禁止 `docker commit`。
3. ImageClip 与 ModelViewCreator 分别是用户批准的远程 commit，三个节点 Git HEAD 一致且工作树干净。
4. 外置模型逐文件 SHA-256 一致；符号链接目标存在。
5. API 节点检查缺失数量为 0，`pip check` 通过。
6. 只允许批准的最终输出节点成为返回产物；预览、中间过程和下载缓存不能进入最终归档。

外部业务仓库的工作流 JSON、节点、模型、提示词、图拓扑及输出语义不属于 GPU Control 的修改边界。
部署过程只能同步批准的上游版本，不得为了兼容 Windows/WSL 私自修改管线。

## 7. 网络、端口和地址发布

Windows 10 的 WSL2 默认是 NAT 网络，WSL 地址可能在启动后变化。因此主控永远连接物理主机
`10.3.34.14`，Windows 启动脚本把固定宿主端口转发到当前 WSL 地址。

| Windows 宿主端口 | 目标 | 用途 | 允许来源 |
|---:|---|---|---|
| 22 | Windows OpenSSH | PowerShell/宿主管理 | `10.3.34.11`，必要时临时开放管理网段 |
| 2222 | WSL:22 | Linux/WSL 运维 | `10.3.34.11` |
| 8188 | WSL:8188 | ComfyUI | `10.3.34.11` |
| 9201 | WSL:9201 | Linux Node Agent | `10.3.34.11` |
| 9301 | Windows Host Agent | 宿主健康、控制和 GPU 仲裁 | `10.3.34.11` |
| 9100 | WSL:9100 | Linux node exporter | `10.3.34.11` |
| 9182 | Windows:9182 | windows_exporter | `10.3.34.11` |
| 9400 | WSL:9400 | NVIDIA/DCGM 指标（可用时） | `10.3.34.11` |

端口转发脚本每次启动和 WSL 地址变化后重新读取 WSL IP，并幂等刷新 `netsh interface portproxy`。
Windows 防火墙是局域网入口的最终边界；WSL 内防火墙不能替代 Windows 入站限制。

上线前还必须验证从 3090-B 主动访问 4090 的 HTTPS/API/监控地址，以及从 4090 主动访问上述管理端口，
避免只有单向 ping 成功却无法心跳或领取任务。

## 8. Node Agent 必须完成的 Windows/WSL 兼容改造

当前 Linux Node Agent 会从默认 WSL 网卡读取虚拟 MAC 和 172.x 地址，这些值不能作为物理节点身份；
WSL 内 `nvidia-smi`/NVML 某些利用率与进程查询也可能受限。3090-B 上线前必须实现并测试：

1. `NODE_ADVERTISE_IP=10.3.34.14` 是强制发布地址，而不只是自动探测失败时的 fallback。
2. `NODE_MAC_ADDRESS=2c:f0:5d:76:7b:70` 是受信配置，由主控 HMAC 验证，不采集 WSL 虚拟 MAC。
3. GPU UUID 仍需实时读取并核对为 `GPU-092a5184-5857-d196-5df2-efa9503368aa`。
4. 增加可选择的 GPU 指标提供者：WSL 查询不可用时，由 Windows Host Agent 调用
   `nvidia-smi.exe` 上报；单项指标不可用只能显示 `unknown/degraded`，不能把整个节点判为离线。
5. 心跳分别表达 `physical_host`、`windows_runtime`、`wsl_runtime` 和 `comfyui` 健康，不能用一个 200
   掩盖另一运行时故障。
6. 主控按宿主发布地址生成 `http://10.3.34.14:8188` 和 `:9201`，并验证心跳来源、HMAC、物理 MAC
   与 GPU UUID；不能登记 WSL 172.x 地址。

这些改造完成并通过自动化测试之前，B 保持 `OFFLINE/DISABLED`，不得为了让 Web UI 变绿而伪造心跳。

## 9. Windows Host Agent 与开机自愈

Windows Host Agent 是 4090 控制 Windows 节点的最小可信面，至少提供：

- Windows 启动时间、版本、磁盘、CPU、内存、物理 IP/MAC 和 GPU 指标；
- WSL 发行版、WSL 地址、Docker、ComfyUI、Linux Node Agent 健康状态；
- 幂等执行 `start-wsl`、`stop-wsl`、`refresh-portproxy`、`prewarm-comfyui`；
- 申请、续租和释放物理 GPU 独占权；
- HMAC、时间戳、nonce 防重放和动作审计；
- 只允许白名单动作，不提供任意命令执行 API；日常深度维护仍通过 SSH。

Windows 计划任务以 `SYSTEM` 身份在开机时执行：

```text
启动 Windows Host Agent
  → 唤醒指定 WSL 发行版
  → 等待 WSL systemd ready
  → 刷新 2222/8188/9201/9100/9400 端口转发
  → 启动 Docker、Node Agent、exporter
  → 检查 ComfyUI（只有 GPU 租约属于 WSL 时才启动/预热）
  → 向 4090 发送物理节点和两个运行时心跳
  → 全部身份与版本校验成功后进入 ACTIVE
```

另一个 watchdog 每 30 秒检查 WSL 地址、端口、Agent 和租约；自动恢复动作需要限频。连续失败达到阈值后
将节点置为 `DEGRADED` 或 `OFFLINE` 并告警，不能无限重启造成任务抖动。

## 10. Windows 与 WSL 的 GPU 互斥

Windows 原生烘焙和 WSL ComfyUI 共享同一张 RTX 3090，不能各自认为 GPU 空闲。必须新增以物理 GPU
为键的强租约：

```text
resource_key = worker-3090-b/GPU-092a5184-5857-d196-5df2-efa9503368aa
owner_runtime = windows | wsl
owner_job_id
fencing_token
lease_expires_at
```

### 10.1 WSL 推理任务

1. Scheduler 只在 GPU 租约属于 WSL 或可无损转给 WSL 时向 B 分配抠图/局部重绘。
2. 第一个任务取得 WSL GPU 租约并预热对应工作流。
3. 同一工作流连续任务复用缓存；只有工作流切换或 Windows 请求 GPU 时才按现有安全流程释放显存。
4. 最后任务结束后可保留短时 warm lease；不能因 warm lease 阻止已排队的高优先级 Windows 作业。

### 10.2 Windows GPU 烘焙任务

1. 4090 将 B 的 WSL GPU 调度状态改为 `DRAINING`，停止新任务进入。
2. 等待当前 ComfyUI 任务自然完成；不得杀死生产任务。
3. 调用 ComfyUI 安全释放模型/显存，验证没有运行中的 prompt。
4. 取得带 fencing token 的 Windows GPU 租约后启动烘焙。
5. Windows 作业完成、校验和原子发布最终产物后释放租约。
6. 如 WSL 队列仍有任务，恢复 ComfyUI、预热需要的工作流并把节点设回 `ACTIVE`。

### 10.3 Windows CPU-only 作业

CPU-only 作业可与 WSL GPU 作业并发，但必须设置 CPU、内存、磁盘 I/O 和最大并发上限。若影响 GPU API
p95 或出现 swap/磁盘拥塞，Asset Worker 自动降低并发。

### 10.4 人工占用

如果用户在 Windows 桌面手工启动 GPU 软件，Host Agent 将物理 GPU 标为 `EXTERNAL_BUSY`。Scheduler
停止向 B 发新任务，当前任务自然排空。只有 Host Agent 确认外部进程退出后才能恢复自动调度。

所有状态转换必须通过数据库事务和 fencing token 完成。仅依赖进程检查、文件锁或 Redis 通知无法防止
网络分区后的旧 Worker 继续写入结果。

## 11. Windows 烘焙任务合同

Windows 专属作业复用统一调度中心的 Asset Job、Asset Worker、租约、幂等、审计、制品 SHA-256 和
原子发布机制，不另建一个不可追踪的旁路服务。

建议任务输入：

- 输入 ZIP 或 multipart 文件及逐件 SHA-256；
- `external_asset_id`、`generation`、幂等键；
- high/low mesh、cage、UV、材质与预设的明确路径映射；
- 准确的软件产品、版本、CLI 路径和 profile 版本；
- 分辨率、padding、输出通道及其他已批准参数；
- 预期最终文件集合和命名规则。

执行必须在独立 staging 目录完成。成功前检查：进程退出码、日志错误、最终文件集合、文件非空、尺寸/
通道、命名、逐件 SHA-256 和任务 generation。全部通过后才把 staging 原子发布为 output，并将父任务
标为 `SUCCEEDED`。预览图、缓存、日志贴图和中间烘焙结果不能进入用户结果。

如果“SD”指 Adobe Substance 3D Automation Toolkit，应固定实际 CLI 名称和版本。Designer 15.0 起
命令行 baker 名称从 `sbsbaker.exe` 调整为 `substance3d_baker.exe`，不能在脚本中猜测名称。

官方参考：

- <https://experienceleague.adobe.com/en/docs/substance-3d/bakers/getting-started/software-interface/substance-3d-automation-toolkit>
- <https://experienceleague.adobe.com/en/docs/substance-3d-designer/using/release-notes/version-15-0>
- <https://experienceleague.adobe.com/en/docs/substance-3d/bakers/getting-started/availability-per-software>

## 12. Web UI 目标显示

3090-B 顶层卡片显示物理状态，不将 Windows/WSL 重复计为两张 GPU：

```text
3090-B  ONLINE / ACTIVE
物理地址 10.3.34.14 · RTX 3090 · 当前 GPU 所有者：WSL2

运行时
  Windows  ONLINE  · 烘焙 Worker READY · 0/并发上限
  WSL2     ONLINE  · ComfyUI READY      · 1/1 GPU

版本与一致性
  ImageClip <commit>  MATCHED
  ModelView <commit>  MATCHED
  Windows Baker <product/version> VERIFIED
```

需要明确显示 `DRAINING`、`EXTERNAL_BUSY`、`PORT_FORWARD_DEGRADED`、`PIPELINE_MISMATCH`、
`LICENSE_REQUIRED` 等原因，不能只显示笼统的离线。父任务列表保持“一批业务任务一行”；内部帧、子作业、
运行时、重试、制品和日志放在任务详情，不能用每一帧刷屏。

## 13. 分阶段实施顺序

### Phase 0：重装前冻结与备份

- 确认 B 为 `OFFLINE` 且没有运行任务和未上传制品。
- 导出现有 B 节点配置、版本、模型清单和关键日志；不把旧节点密钥写入文档或 Git。
- 保留主控数据库中的 `worker-3090-b`，不删除并重建。
- 网络管理员确认 `.14` DHCP 保留和 MAC。

### Phase 1：Windows 最小基线

- 安装 Windows、驱动、更新、OpenSSH、公钥和受限防火墙。
- 验证 4090 可免密 SSH，Windows 可主动访问 4090。
- 安装并激活 Windows 专属软件，记录精确版本和 CLI 能力。

### Phase 2：WSL 与数据平面

- 安装 WSL2 Ubuntu 22.04 并把 VHDX 放到大容量 NVMe。
- 安装 Docker Engine和 systemd 基础服务。
- 同步 GPU Control 固定源码、固定容器镜像、业务仓库批准 commit 和外置模型。
- 在本机离线校验，不向生产 Scheduler 宣告 `ACTIVE`。

### Phase 3：兼容层和宿主管理

- 部署 Node Agent 的 advertise IP、物理 MAC 和指标 provider 改造。
- 部署 Windows Host Agent、计划任务、watchdog、端口转发和防火墙规则。
- 部署物理 GPU 租约和 Windows/WSL fencing。

### Phase 4：隔离验收

- B 保持 `DRAINING`，单独执行 ComfyUI 冷/热任务和 Windows 烘焙样例。
- 验证所有最终产物、SHA-256、审计、任务状态与错误恢复。
- 模拟 WSL 重启、Windows 重启、端口变化、Agent 崩溃、任务超时和网络短断。

### Phase 5：灰度上线

- 先开放少量抠图任务，再开放局部重绘；观察延迟、显存和失败率。
- 再开放 Windows 资产作业，验证 DRAINING 和 GPU 所有权切换。
- 全部硬门槛通过后恢复为正式 `ACTIVE`，更新部署记录。

## 14. 验收清单

### 14.1 身份与安全

- [ ] Windows 物理 IP 为 `10.3.34.14`，MAC 为 `2c:f0:5d:76:7b:70`。
- [ ] GPU UUID 为 `GPU-092a5184-5857-d196-5df2-efa9503368aa`。
- [ ] 主控数据库仍只有一个 `worker-3090-b` 物理节点。
- [ ] SSH 使用公钥；业务端口仅允许 `10.3.34.11`。
- [ ] HMAC、时间戳、nonce、防重放和审计生效，日志不输出密钥。

### 14.2 启动和网络

- [ ] 冷启动 Windows 后无需桌面登录，Host Agent、WSL、Docker 和 Node Agent 自动恢复。
- [ ] WSL 地址变化后宿主转发自动刷新。
- [ ] 4090 可访问 `8188/9201/9301/9100/9182`；按实际启用情况检查 `9400`。
- [ ] B 可主动访问 4090 API、TLS、时间同步和日志/监控入口。

### 14.3 Linux GPU 服务

- [ ] Windows `nvidia-smi.exe` 和 WSL 容器内 GPU 检测都识别同一张 RTX 3090。
- [ ] ComfyUI 固定镜像 ID 正确，容器 healthy。
- [ ] ImageClip/ModelView 三节点 commit、模型 SHA-256 和节点集合完全一致。
- [ ] `pip check`、节点缺失检查、最终输出检查全部通过。
- [ ] 真实抠图和局部重绘请求成功，返回的只有批准最终结果。

### 14.4 Windows 任务与 GPU 仲裁

- [ ] Windows 专属软件产品、版本、许可证和 CLI 经过真实作业验证。
- [ ] Windows 请求 GPU 时 WSL 先 DRAINING，当前任务自然完成。
- [ ] Windows 和 WSL 不会同时持有同一 GPU 租约。
- [ ] 旧 fencing token 无法更新进度、完成任务或发布产物。
- [ ] Windows 作业结束后 WSL 可自动恢复并预热。
- [ ] CPU-only 作业并发不会破坏 GPU API 的稳定性。

### 14.5 故障恢复

- [ ] Windows 断电重启后节点自动恢复并重新握手。
- [ ] WSL、Docker、ComfyUI 或 Agent 单独崩溃时状态准确且恢复限频。
- [ ] 任务运行中网络短断不会产生两个执行者或重复发布。
- [ ] 不完整产物永远不可下载；重试仍保持幂等和 generation 隔离。
- [ ] 主控不可达时 B 停止领取新任务，不在孤岛状态自行执行旧队列。

## 15. 回滚策略

任何硬门槛失败时：

1. 将 B 保持 `DRAINING` 或 `DISABLED`，4090 与 3090-A 继续承担现有 GPU 任务。
2. 不删除主控中的 B 身份，不修改 A/4090 业务管线。
3. WSL 服务可回退到上一固定 GPU Control commit 和容器 tag；模型与业务仓库仍须保持批准版本。
4. Windows Host Agent 或端口转发失败时，不允许绕过 HMAC/防火墙直接把 ComfyUI 暴露给局域网。
5. Windows 烘焙不可用时只暂停 Windows capability，不应把整个 3090-B 物理节点伪报健康。
6. 若 Windows/WSL 组合无法满足稳定性，再评估恢复 Linux；磁盘恢复前先导出审计和待取回产物。

## 16. 后续实现待办（尚未执行）

- [ ] 为 Node Agent 增加强制发布 IP、物理 MAC 覆盖和 Windows GPU 指标 provider。
- [ ] 引入 `PhysicalNode / ExecutionRuntime / ResourceLease` 或等价持久模型。
- [ ] 实现 Windows Host Agent 与 HMAC 白名单动作协议。
- [ ] 实现 Windows 开机任务、WSL watchdog 和动态端口转发脚本。
- [ ] 扩展 Asset Worker capability，支持 Windows 专属软件版本与 GPU requirement。
- [ ] 实现跨 Windows/WSL 的 GPU 独占、DRAINING、fencing 和恢复状态机。
- [ ] 更新 Web UI 为单物理节点、多运行时和 GPU 当前所有者视图。
- [ ] 增加 Windows/WSL 安装、健康、断电恢复和真实任务自动化验收脚本。
- [ ] 完成生产灰度后另写部署记录，记录精确版本、commit、镜像 ID、模型哈希和验收证据。

在这些待办完成、Windows 已可 SSH 接管且生产验收通过之前，3090-B 在管理面保持真实的
`OFFLINE/DISABLED` 或 `DRAINING`，不人工伪造在线状态。
