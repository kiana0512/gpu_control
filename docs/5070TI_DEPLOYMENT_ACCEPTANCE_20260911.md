# RTX 5070 Ti 部署与验收记录

更新日期：2026-09-11。最终控制面核对五机均为 ACTIVE/ONLINE，新节点 Node Agent 与 Asset Worker 均 ONLINE。已完成批准 GPU 模板的 9 次验收，最终生产统计为 22 条 SUCCEEDED、0 条 FAILED（含批次子项）；CPU 能力、RealESRGAN 公共 API 路由以及 WSL 发行版终止后的自动恢复也已验收。Codex 仍缺少独立认证；原生软件许可、DHCP 保留和 Windows 完整重启仍待完成。本文仅确认下列实测范围，不将安装成功视为所有旧节点功能均已对等。

## 主机与集群

| 节点 | 管理地址 | 系统与显存 | 本轮操作 |
|---|---|---|---|
| control-4090 | 10.3.34.11 | Ubuntu / 24 GB | 更新控制 API、Scheduler；保留 GPU 容器 |
| worker-3090-a | 10.3.34.12 | Ubuntu / 24 GB | 只读比对、维护时排空后恢复模式 |
| worker-3090-b | 10.3.34.14 | Windows / WSL2 / 24 GB | 只读比对、维护时排空后恢复模式 |
| worker-4070ti-animation-host-01 | 10.3.34.238 | Windows / WSL2 / 12 GB | 只读比对、维护时排空后恢复模式 |
| worker-5070ti-01 | 10.3.34.18 | Windows / WSL2 / 16303 MiB | 新部署 |

新机 Windows 主机名 `LILITHG-N0K4M0L`，账号 `lilithgames`；WSL 为 Ubuntu-22.04，默认管理用户 `gpucontrol`。20 CPU，WSL 可见约 46.86 GiB 内存及 12 GiB swap。GPU UUID `GPU-5fa565eb-b1de-57ed-d43b-03c602cecf06`，Windows NVIDIA 驱动 610.74。

## 远程管理与恢复设计

主控实际核对指纹并用专用密钥成功登录两个入口，WSL `sudo -n true` 成功：

| 入口 | Ed25519 主机指纹 |
|---|---|
| `lilithgames@10.3.34.18:22` | `SHA256:fl577ktGiSgd5TPsQJcGHDFQyrhQJysVyncPz8ORkAE` |
| `gpucontrol@10.3.34.18:2222` | `SHA256:fGGFNK20M+Mq+58CQyMgchXXTeD44sdqK4Pdj97Kl/k` |

主控私钥位于 `/srv/gpu-control/secrets/ssh/worker-5070ti-01-ed25519`，专用 known_hosts 位于同目录。使用 `StrictHostKeyChecking=yes`、`IdentitiesOnly=yes`，不在文档或日志中记录密钥正文。

保留预处理实际适配脚本及原 Maintainer 任务，增加独立 RuntimeProxy 任务转发 8188、9100、9201、9301；仅允许主控 10.3.34.11 访问。原 2222 转发职责不变。详见 [Windows 转发部署记录](5070TI_WINDOWS_RUNTIME_PROXY_DEPLOYMENT_20260911.md)。Windows 登录后每 60 秒检查 WSL 地址与转发。

主控经正常 `/admin/nodes/worker-5070ti-01/restart` 运维入口，成功完成专用 HMAC 签名、排空检查及新机 ComfyUI 重启，返回 `exit_code=0`。证据：`output/5070ti-deployment-20260911/control-path-restart.json`。

在新节点 DRAINING、GPU 与 Asset 当前任务均为 0 时，实际执行一次 `wsl --terminate Ubuntu-22.04`。既有 Maintainer 自动恢复发行版，未手动启动 WSL；发行版 systemd PID 1 已重启，Windows SSH、WSL SSH、8188/9100/9201/9301 服务、内网 TLS 与无交互 sudo 均复验通过。此项没有重启 Windows，WSL NAT 地址也未变化，验收范围不包含 Windows 开机、注销后恢复或 NAT 地址变化。证据：`output/5070ti-deployment-20260911/wsl-recovery/summary.json`。

恢复检查发现 Windows W32Time 停止、时钟约落后 14 小时 19 分钟。随后启用时间服务并沿用既有 `time.windows.com,0x9` 来源执行同步，`w32tm /resync` 返回 0，Windows UTC 与主控/WSL 对齐；没有为此重启 Windows 或 WSL。后续确认见 `wsl-recovery/windows-time-confirm.json` 与 `windows-time-final.json`。

## 已部署环境

- Docker 29.6.2、containerd 2.2.6、Buildx 0.35.0、Compose 5.3.1、NVIDIA Container Toolkit 1.19.1，安装包 SHA-256 已核验；未安装 Linux GPU 驱动。
- 独立 CPython 3.11.13 与 Node Agent 虚拟环境，系统 Python 3.10 未替换。Node Agent 为 systemd 服务，使用新节点独立 HMAC；未改动旧节点密钥。
- ComfyUI `projects-0.2.6`，PyTorch 2.11.0+cu130，CUDA 13.0，实测 sm_120 可运行。
- RealESRGAN Worker 1.0.0，PyTorch 2.7.1+cu128，实际图像处理通过。
- Blender Worker 最终为 `1.4.75-uv-overlap-v28-pins1-codex-auth2`、Blender 5.1.2、CPU 并发 2，配套 465 个运行时文件逐项 SHA-256 一致。auth1 修正缺失认证分类；auth2 仅修正真实 Python 包 RECORD 中两处既有过期 hash/size，保留已验收模块与所有 UV/技能文件字节，核心包版本仍为 1.5.23。实际包记录、三套技能及嵌入资源验证全部通过。
- RetopoFlow 3.4.11 实际运行探针 HEALTHY。Codex CLI `0.146.0-alpha.3.1` 已安装，真实认证状态为 `MISSING / BLOCKED / AUTH_MISSING`；详见 [认证探针与镜像审计记录](5070TI_CODEX_AUTH_PROBE_20260911.md)。
- node-exporter 1.9.1、Alloy 1.9.1；Prometheus 新节点 `up=1`，Loki 已收到带 `worker-5070ti-01` 标签的 journal 和容器日志。新增 WSL 告警匹配，14 条告警规则通过 promtool 校验，Prometheus 以 HUP 重载。

专用 Compose 与初始登记清单位于 `deploy/gpu-node/5070ti/`。添加节点应使用 `scripts/bootstrap_nodes.py --config deploy/gpu-node/5070ti/node.yaml --add-only`；此模式拒绝覆盖已有节点，并强制新节点从 DISABLED/DRAINING 开始。不要以旧四节点全量 bootstrap 替代增量登记。

## 业务版本与功能验收

ImageClip 使用批准版本 `c39ed0b3b637f0a1435bbe10e5a3acf6bfca07bd`，ModelViewCreator 使用 `5521cf3b9faa278514ee4bd41fb47a9ae83517d4`，同时按实际批准生产载荷同步工作流和自定义节点。未修改外部仓库、图结构、提示词、采样参数或输出约定。

20 个模型文件均在新机完成大小及 SHA-256 验证，包含原 ImageClip 模型清单遗漏而生产流程实际使用的 `flux-2-klein-9b-fp8.safetensors`。补充清单保存在 GPU Control 自有目录 `deploy/gpu-node/5070ti/imageclip.models.manifest.yaml`。

| 功能 | 实际结果 | 验证范围 |
|---|---|---|
| ImageClip RGBA | PASS | 批准模板，最终节点 109，512×512 RGBA |
| ModelView 局部重绘 | PASS | 方图及 1536×1024 输入，最终节点 29，2048×2048 |
| ModelView 单视图生成 | PASS | 方图、横图、竖图，最终节点 29，2048×2048 |
| ModelView 单视图重绘 | PASS | 方图及横图，最终节点 29，2048×2048 |
| ModelView Roughness | PASS | 横图，批准清单指定最终节点 355，1248×832 |
| RealESRGAN | PASS（直连与公共 API） | 主控已登记第五节点；公共 API 实际返回新 5070 Ti 的 256×256 PNG，核对输出 SHA-256 |
| Blender UV | PASS | 临时隔离容器 cube 模型，最终 blend/FBX 与 UV QA |
| CPU 分类、坐标恢复与 FBX 回读 | PASS | 隔离 fixture，实际恢复源坐标并验证导出回读 |
| RetopoFlow | PASS | 实际初始化及 modal 运行探针，当前心跳 HEALTHY |
| 旧独立 ICP 合成配对 | 质量门拒绝；4090 基线同样拒绝 | 两端均为 `ALIGNMENT_ORIENTATION_AMBIGUOUS`，相同 gates/error/selected 等字段；未修改技能 |
| Codex 拓扑执行 | 尚未授权；未验收真实模型执行 | 新机独立 device-auth 在 15 分钟后超时，未复制旧设备凭据，也未自动刷新设备码 |
| Windows 原生 Substance / MOF | 未部署 | 新机未发现安装或可用许可证；原集群现有服务仍保留 |

GPU 五类流程合计 9 次成功验收，模板 SHA-256 与启用注册版本逐项一致；只填写已声明的输入绑定。完整记录为 `output/5070ti-deployment-20260911/functional-acceptance.json`，SHA-256：`023c4583e07052f5aa604c3a23cfa6e83292fbdbfb6abc3a5f6b3d0a543ed974`。

首次 Roughness 验收因 ComfyUI 启动时模型尚未同步齐而收到 400；全部模型同步完毕、确认新机队列空后重启 ComfyUI，复验成功。保留原失败及复验记录。

最终 `final-routes` 复核的生产统计为 22 条 SUCCEEDED、0 条 FAILED，包含批次子项；最终证据索引见 [发布审计](5070TI_RELEASE_AUDIT_20260911.md)。具体流程与输入尺寸的覆盖仍以上述批准模板验收范围为准，不能推广为任意负载或所有输入均已覆盖。

RealESRGAN 控制器已显示 5/5 节点就绪，五节点路由通过 `REALESRGAN_NODES` 持久化到控制 Compose。公共 API 验收结果见 `canary/realesrgan-public-result.json`，响应明确来自 `worker-5070ti-01`，耗时约 0.138 秒，输出 SHA-256 已记录。

CPU 结论以 `cpu-acceptance/acceptance-summary.json` 为准。首次独立 RetopoFlow 测试因 `xvfb-run` 作为容器 PID 1 的启动通知问题超时；加 Docker `--init` 后实测通过，生产 Worker 的 Python 父进程不受该问题影响。旧 ICP 合成 fixture 在新节点与相同镜像/技能的 4090 基线上均被质量门拒绝，作为独立既有问题保留；该 fixture 不等同于已通过的生产源矩阵坐标恢复合同。没有为使验收变绿而更改业务技能。

旧四节点并非所有功能完全相同：4070 Ti 的 12 GB 环境保留较早 ModelView 配置，且未拥有另外两个新流程的完整载荷；本轮未修改其外部业务内容。Substance 原生许可在 3090B，MOF 原生许可在 4070 Ti。新 5070 Ti 的 GPU 五流程均按当前批准模板实测通过；完整功能对等仍需补齐新机独立 Codex 认证及所需原生软件许可。

## 控制 API / Scheduler 上线

新增每节点 HMAC 映射与增量登记，并为新机已实测的三类 24 GB 标称流程添加受控资源验收记录。业务注册清单仍保留原始 `min_vram_mb=24000`；调度例外限定新机 node ID、GPU UUID、具体流程版本与模板 SHA-256，记录运行时档案及验收证据 SHA-256，要求实测最低 16000 MiB。Node Agent 心跳不能提交或覆盖此记录。其他节点及必需类、模型、标签检查不变；运行配置变更须按审计要求撤销受影响记录并重新验收，不将此记录推广到任意 16 GB 配置。

最终控制端隔离源码修订为 `c61648fe96fd5be429cd1f7b470da43131b88204`，版本 `1.5.23.post3`。在已验收接入改动上增加批次加锁后的终态复核，防止异步交错将成功批次重新推进到 ASSEMBLING；同时向管理节点响应提供真实 Codex 心跳和探针有效期，供 UI 判断缓存状态是否过期。实际模块与源码修订的对应验证保存在 `output/5070ti-final-adapter-20260911/image-verification.json`。

| 组件 | 部署镜像 | 镜像 ID |
|---|---|---|
| API | `gpu-control-api:1.5.23.post3-5070-c61648f` | `sha256:1e2e087986fc3c069df272c588730e46d5ff63c075a2e0e211319c85565876d5` |
| Scheduler | `gpu-control-scheduler:1.5.23.post3-5070-c61648f` | `sha256:a797999f82f6711266f6bd6d46c2cf274d395820d79209b2856098728c7a4ecd` |
| Web | `gpu-control-web:1.5.23-5070-ui-350c388`，版本 `1.5.23-5070-ui.2` | `sha256:82dfe654a884fbc6c4e71a79b17a4c211d73eec9c20d5c4b19a1b3f3ff3da251` |
| 新节点 CPU Worker | `li3d/blender-worker:1.4.75-uv-overlap-v28-pins1-codex-auth2` | `sha256:0c6ea65709566114544710dcac3255b94ac71f53138f158e4a678c6abcacb992` |

表中为实际 Docker 镜像 ID，不能自动当作可从 registry 拉取的 manifest digest。Web 修订为 `350c388d83a7d64ed7bc5e9148c54d1b89d3f43d`；真实生产网关浏览器验收覆盖五节点、三个资源验收档案、桌面/移动宽度、RealESRGAN 五节点容量，以及新机“尚未授权”显示。离线缓存过期、认证失效和探针失败有独立状态，不把 CLI 安装或历史成功当作当前健康。证据见 `output/5070ti-web-adapter-20260911/gateway-verification/report.json` 和 `browser-report.json`。

最终网关复核进一步确认 Web 29 项静态资源 SHA 与发布产物一致，登录页及 5 个核心页面均无 JavaScript 错误，五机 ACTIVE/ONLINE；这组最终路由与资源校验记录由 [发布审计](5070TI_RELEASE_AUDIT_20260911.md) 汇总。

初次控制端切换前通过正常运维 API 排空原四节点，等待以下四个在途任务全部 SUCCEEDED 后切换，随后恢复原调度模式：`8539ce83-5ae1-4ea0-ad3e-41b59bdbd6de`、`50842225-1117-46cc-8538-4ad0404a8d31`、`6ebf9978-8b6a-4f19-9cc3-361cfafe050b`、`314024c0-6f14-4658-a0f0-5384862b49ed`。最终 post3 切换的复核与排空记录保存在 `output/5070ti-final-adapter-20260911/`。

生产 Compose 环境持久化 APP/SCHEDULER 镜像标签及新 HMAC 映射；精确保留原服务的网络、挂载和其他配置。旧容器以 `*-pre-5070-20260911` 停止保存，网络断开，供回滚使用。主仓库原有未提交 UV/MOF 等改动保留，未将其打入此次控制镜像。

auth2 仅在新机再次排空、确认无运行 Asset 任务后替换 CPU Worker；没有重启其 GPU 容器。随后由正常运维入口恢复 ACTIVE，03:37 UTC 的独立复核确认 ACTIVE、Worker ONLINE、RetopoFlow HEALTHY 及准确的 Codex MISSING 状态。Node Agent、Worker 修复及 Windows 维护脚本审计分别见 [Node Agent 记录](5070TI_NATIVE_NODE_AGENT_20260911.md)、[Codex 记录](5070TI_CODEX_AUTH_PROBE_20260911.md) 和 [控制端/Windows 审计](5070TI_CONTROL_AND_WINDOWS_AUDIT_20260911.md)。

镜像归档、GitHub/LFS 同步与清理结果另见 [最终发布审计](5070TI_RELEASE_AUDIT_20260911.md)，本文不将仍在进行的归档操作写成已完成。

## 待完成和验收边界

- Codex 独立设备授权已超时，仍需用户完成一次新授权及后续真实模型往返；当前只确认状态分类与部署正确，不宣称拓扑执行可用。
- 新机 Windows 原生 Substance/MOF 尚未部署，需本机可用安装包和许可。当前依赖原集群的既有原生服务，完整功能对等尚未验收。
- DHCP 保留 `10.3.34.18` 尚未由网络管理端确认。
- Windows 完整重启、注销/重新登录后的服务恢复，以及 WSL NAT 地址实际变化后的转发恢复尚未验收；目前保持 `lilithgames` 登录，可锁屏，避免注销和睡眠。未配置自动登录或保存 Windows 登录密码。
- 旧独立 ICP 合成 fixture 的基线质量拒绝仍作为独立问题保留；本轮未更改技能语义来消除该结果。
