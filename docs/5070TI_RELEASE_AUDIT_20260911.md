# 5070 Ti 五节点发布、路由与代码审计 · 2026-09-11

5070 Ti 已部署并接入生产，五台 GPU 节点全部 `ACTIVE/ONLINE`。2026-09-11 11:45 +08:00 的只读统计为新节点 22 个生产 ImageClip 子任务成功、0 失败；GPU 功能验收另外包含 5 个批准工作流、9 次最终输出成功。
这不是所有原生软件及账号都已完成的声明：Codex 用户授权、Windows 原生许可、DHCP 保留与完整 Windows 重启验收仍列在 [总验收](5070TI_DEPLOYMENT_ACCEPTANCE_20260911.md) 中。

## 路由与运行验证

- 控制入口 `https://10.3.34.11` 使用公开 LAN CA 严格校验，ready 为 200，database/redis 为 ok。
- WebUI 已部署 `1.5.23-5070-ui.2`（350c388），29 个静态文件的 SHA-256 与构建结果一致。
- 前一版同功能 UI 完成全部 14 个导航页面及登录页验收；最终 ui.2 再验总览、节点、高清化、Codex、系统信息及登录页，全部成功，0 JavaScript 错误，相关 API 均成功。
- RealESRGAN 控制器为 5/5 节点就绪。通过正式 HTTPS API 将 canary 指向 5070 Ti，最终 256×256 PNG 成功，处理节点响应头符合指定目标。
- 5070 Ti Windows SSH、WSL SSH、原生 Node Agent HMAC、ComfyUI、Exporter、Alloy、Blender Worker 均经过实际连接验证。未认证 Node Agent 请求按预期 401。
- 已验证 WSL 发行版终止后由既有 Windows 任务自动拉起并恢复代理；不把它等同于完整 Windows 重启或 WSL VM 冷启动。

最终脱敏证据见 [发布目录 evidence](../artifacts/control-plane/5070ti-20260911/evidence)。

## 代码审计和已修复问题

| 问题 | 实际修复及验证 |
|---|---|
| 新节点独立 HMAC / 增量登记 | 显式 SecretStr 映射；拒绝重复 ID、物理身份和共享秘密；不覆盖旧四节点 |
| 16 GB 节点被统一 24 GB 门槛排除 | 基于已完成 canary 的节点、GPU UUID、工作流版本及模板 SHA 精确验收记录；保留全局 24 GB 注册门槛与其他能力限制 |
| 批任务终态竞态 | 数据库锁后复查终态，阻止 SUCCEEDED 再进入 ASSEMBLING；真实数据库复现先失败、修复后通过 |
| Codex 探针混淆授权与运行失败 | 4 个授权相关函数定向修复；API 暴露实际心跳/探针 TTL，Web 使用后端阈值并处理时间流逝 |
| Worker wheel 校验元数据陈旧 | auth2 仅修复 bootstrap/main 的 RECORD，现场全表无不匹配 |
| Compose 重建丢失第五个高清化节点 | 改为必填 `REALESRGAN_NODES`，正式环境五节点配置渲染验证通过 |
| Windows 交付源码落后于适配脚本 | 原样同步现场单地址与防火墙修复，Inspect 容错与 SID 归一化通过 WinPS5.1 回归 |
| 发布测试仍断言旧 Python 版本 | 更新为 `1.5.23.post3`，保持 Web/Worker 独立版本校验 |
| 上游原样校验清单被 *.sha256 忽略 | 狭窄忽略例外纳入原清单；6 个上游文件在两树逐个验证，业务源码零改动 |
| Windows 时间慢约 14 小时 | 启用既有 W32Time 并从原 NTP 来源同步，校验偏差约 10 毫秒；未手工设置时间或放宽阈值 |

控制源码、Windows 与证据边界详见 [独立审计](5070TI_CONTROL_AND_WINDOWS_AUDIT_20260911.md)。
保留的一项中等级别限制是显存验收记录中的 runtime 摘要目前没有与当前镜像/启动参数自动对比；任何 ComfyUI 镜像、参数、模型改变之前必须先撤销旧验收记录并重新跑最终输出 canary。当前已部署的固定环境与验收记录一致。

## 测试覆盖与边界

隔离的最终控制运行源码执行全部 unit/integration：初次结果为 **683 passed、16 skipped、2 failed**，耗时 386 秒。
两处失败均为上表发布资料问题；修复后相关两模块 **12 passed**。16 个跳过项保留原环境门禁，不声明已覆盖。
另外已完成控制配置/资源回归、批任务竞态回归、38 个 Codex 探针测试、Web 32 个单测、lint/TypeScript build、11 个浏览器状态矩阵以及 Windows PowerShell 5.1 解析与功能回归。

主仓存在本轮开始前尚未提交的 UV/MOF/CORS 迭代，未删除、回滚或隐式发布。其工作区 Worker 默认版本混用会造成额外历史一致性测试失败；本次发布索引只加入本轮控制、Web、路由、部署和授权探针修复，既有源码版本保持一致。
已部署 Worker 的当前模块原字节及基础镜像身份独立归档，不能把本次控制源码测试等同于未提交 UV 迭代的全面验收。
发布索引导出为独立快照，版本/上游原字节、RealESRGAN、Codex、新节点秘密映射、显存验收与增量登记共 **99 项定向测试通过**；控制源码逐字节对应已审 c61648f，Worker AST 仅 4 个认证探针定义变化。结果随发布证据保存。

CPU 验收的 UV、模型分类、RetopoFlow 和当前坐标恢复入口通过。另一条旧 ICP 合成样本被方向质量门拒绝，4090 基线与 5070 Ti 返回完全相同结果，按既有算法限制记录，未降低质量门或修改算法来制造成功。

## GitHub / Git LFS

发布目标为已有 `agent/four-gpu-4070ti-closure` 分支，不强推、不合并 main。
控制运行源码定位 `runtime/5070-control-c61648f`；Web 运行源码定位 `runtime/5070-web-350c388`。
大文件使用 Git LFS：最终 API/Scheduler/Web 镜像 2 片、auth2 Worker 镜像 6 片、运行资源 1 片、更新预处理 ZIP 1 片。
每片 SHA、组合归档 SHA、实际 Docker image ID 与恢复步骤见 [控制发布包](../artifacts/control-plane/5070ti-20260911/README.md) 和 [Worker 发布包](../artifacts/asset-worker/5070ti-20260911/README.md)。
不归档凭据、运行卷、CODEX_HOME、私钥、生产输入输出；公开管理公钥与 LAN CA 为明确允许的公开内容。上传前扫描本次 180 个暂存文件、两个运行源码标签内 805 个文件及 4 个镜像 Config，未检出已知运行秘密、私钥、OpenAI Key 或 JWT；10 个 LFS 指针的大小与本地全文件 SHA 均匹配（合计 970,680,958 字节）。原运行资源归档 465 个成员另做了名称与内容检查。

上传与远端校验回执在最终同步完成后追加。镜像为本地 Engine 导出；未声明 registry digest、SBOM 或七天稳定性验收。

## 清理

已清理本次完成传输的三个大文件，以及两个临时测试树中的重复 artifacts，共五项；旧 ui.1 临时控制归档仍保留作历史记录。
清理前验证路径身份、字节数、硬链接、运行容器依赖与已保留原件。主仓 LFS、外部流水线仓库、模型、生产任务、密钥及回滚容器/镜像保留。
临时 Web 预览进程经过 PID/命令核对后停止，端口 18751 已关闭。
五项删除逻辑体积 70,511,965,947 字节（65.669 GiB）；两个目录中的 338 个文件已逐项对主仓 Git blob / LFS pointer SHA 验证。statvfs 实测可用空间增加 **69,406,658,560 字节（64.640 GiB）**，这是检查时段的文件系统差值，可能包含同期归档写入影响。完整路径、身份、摘要和删除结果见 [清理回执](../artifacts/control-plane/5070ti-20260911/evidence/cleanup-receipt.json)。清理后 HTTPS ready、database 和 redis 正常。
