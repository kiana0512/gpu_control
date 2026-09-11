# 5070 Ti 控制端及 Windows 接入独立审计

审计日期：2026-09-11。控制端源码基线为隔离 worktree
`/tmp/gpu-control-5070-resource-adapter` 的干净提交
`c61648fe96fd5be429cd1f7b470da43131b88204`，包括此前的专用 HMAC、增量登记、
显存验收记录及本次 Scheduler/TTL 修复。Windows 脚本与新节点 Compose 按主工作区当前文件审查。
主工作区原有未提交 UV/MOF/Asset/Web 改动不等于这个控制端镜像的发布内容；本审计不声称那些
历史改动均经过本轮验证，也没有修改 ImageClip/ModelViewCreator 外部业务文件。

## 按严重度排序的发现

### P1：Windows 交付脚本落后于已验收现场版本——已修复源码

原 `scripts/Update-GPUControlWslProxy.ps1` 第 157–158 行把单个字符串地址直接取 `[0]`，
PowerShell 实测 `"10.3.34.18"[0]` 为 `"1"`，导致已匹配的防火墙规则仍被每分钟删除重建。
原防火墙宽泛规则检查也没有现场的精确来源拒绝例外；在本机既有 Network Discovery 放行规则下，
即使已存在正确拒绝保护仍会拒绝修复转发。`Initialize-GPUControlWindowsNode.ps1` 存在同一交付差异。

经主代理授权，先归档原源码，再将现场已验收的 Update 文件原字节同步到主仓。
Initialize 同步现场防火墙检查，并补充未安装 WSL 时的只读检查容错，以及任务返回短账号名时
按 SID 比对拥有者。精确来源拒绝例外只接受现场 `10.3.34.11` 控制机与指定拒绝规则；
其他机器或缺少保护时仍走严格检查。没有把脚本重新部署到新机，没有重启现场任务或服务。

### P2：显存验收记录未自动绑定实际运行环境指纹——后续改进，当前须保持撤销流程

位置：`packages/gpu_control_core/workflow.py:146–151`。
`runtime_profile_sha256` 仅校验为 64 位十六进制字符串，未读取或比较运行镜像、启动参数、
模型集合的实际指纹。隔离复现中，加入不同运行镜像和 runtime 摘要后，最低门槛仍为 16000 MiB。
因此更换运行配置时旧验收记录不会自动失效。

当前新机已通过批准配置的真实任务验收；固定该配置并在变更前撤销受影响记录，可以继续运行。
后续应增加实际 runtime 指纹校验，或在变更工具中强制清除记录并重新验收。
这不影响现有精确 node ID、workflow key/version、模板 SHA、GPU UUID 检查，也不绕过类与标签检查。

### P2：原始预处理包仍包含旧源码——历史归档须明确标记

位置：`output/5070ti-onboarding-20260910/` 及同日期预处理 ZIP。
它们是当时实际交付的初版快照，不能作为修复后的最新安装包再次发送。
Git/LFS 保留历史证据时应明确标记其用途；后续维护以 `scripts/` 当前源码和 2026-09-11
部署记录为准。若再次发布可执行预处理包，应重新生成并记录新文件哈希。

## 已关闭的疑点与通过项

- 专用 HMAC：映射键、唯一性、长度和默认值检查有效；密钥不进入 Settings repr/model_dump。
  本次部署准备器额外检查新节点密钥不复用既有旧字段或映射密钥。旧节点的回退路径保留，
  新节点本次使用显式映射。错误输入测试没有输出测试密钥。
- 增量 bootstrap：新节点必须从 DISABLED/DRAINING 开始，已有节点不被更新。
  额外隔离双事务复现显示相同 host 的第二次注册被 `Node.base_url` 唯一约束拒绝，最终仅一条记录；
  不把这个疑点报告为重复注册漏洞。
- Scheduler：锁定批次行后再次检查终态，避免活跃 ID 快照过期导致
  `SUCCEEDED -> ASSEMBLING`。真实异步交错回归先复现错误，再验证修复后批次、子项、归档、
  事件均不变；相关批次测试 14 项通过，没有放宽状态机或隐藏其他 ValueError。
- API TTL：仅给已有管理节点响应添加两个服务端 TTL 字段，没有新建或改写 API 路由，
  没有更改鉴权依赖或请求语义。
- RealESRGAN 路由：审查开始时发现旧 Compose 仍硬编码四节点；主代理已将
  `deploy/control-plane/compose.yaml` 改为显式必填 `REALESRGAN_NODES` 配置。
  最新 Compose 展开证据列出五条，包括 `worker-5070ti-01 -> http://10.3.34.18:9301`，该项已关闭。
  此处确认配置持久化；实际 HTTP/业务路由验收由主部署记录单独给出。
- 新节点 Compose 保留批准流程挂载为只读，服务经 Windows 指定端口代理接入。
  本次审查未发现为接入 5070 Ti 改写业务图、采样参数、模型或输出合同。
- Windows 管理密钥文件及维护目录 ACL 实测均关闭继承，仅 Administrators/SYSTEM FullControl；
  未读取密钥内容。短账号、完整账号、SID 三种任务身份形式均归一到同一拥有者。

## 验证证据与范围

本轮独立运行 HMAC、增量 bootstrap、显存 profile 单测 **46 项通过**。
Windows PowerShell **5.1.26100.8457** 完成两个脚本语法解析及只读回归：缺少 WSL、
原生命令 stderr、失败查询、发行版名称归一化、单字符串/数组地址、防火墙来源和端口检查。
已有 `tests/windows/test_mof_stage_output.ps1` 的纯文件日志部分通过；未在新 Windows 运行
缺少本机原生 Blender 的后半段。这些检查没有安装/卸载 WSL 或执行初始化 Apply 阶段。

- [控制端单测](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/control-audit-unit-tests.txt)
- [隔离复现结果](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/control-audit-reproduction.json)
- [复现程序](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/control-audit-reproduction.py)
- [Windows PowerShell 回归](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-source-sync/windows-powershell-tests.txt)
- [任务账号与 ACL 只读检查](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-source-sync/windows-identity-acl.json)
- [修复前后差异](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-source-sync/source-fixes.diff)
- [源码及行尾摘要](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-deployment-20260911/windows-source-sync/sync-summary.json)
- [最终 Compose 五节点展开](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-final-adapter-20260911/compose-persistence-check.json)

Update 当前源文件与现场原字节一致，SHA-256 为
`01f695a3dafa393a8c8bc2eb3a262919b2cfc9d7ba4e217ce1446f569a7bb1b0`。
Initialize 在现场版本上增加上述两项维护修复，因此摘要不同，且尚未回灌现场。
仓库 `*.ps1 text eol=crlf` 会归一化 Git blob 并在 checkout 生成 CRLF，可能改变工作树字节摘要；
应把源码归一化摘要与现场原字节摘要分别记录，不能用前者覆盖后者。
原始现场快照与其 SHA256SUMS 应保持原字节，避免行尾转换损坏证据一致性。

## 全量审计失败项的定向修复

主代理的已配置全量审计为 683 passed、16 skipped、2 failed；两项失败分别是 Python
发布版本断言仍为 `1.5.23`，以及隔离树缺少已存在于主工作区的 `UPSTREAM.sha256`。
本次仅将主工作区与隔离树 `tests/unit/test_release_identity.py` 的 Python 断言更新为
`1.5.23.post3`，Web/Web lock 的 `1.5.23` 断言保持。

主工作区
`workflows/production/modelview-inpaint/custom_nodes/Cherry_KleinWorkflowTools/UPSTREAM.sha256`
原字节保持，并原样复制到隔离树；清单 SHA-256 为
`9e86a244aa5f7d7cc9c7c418b695b2a88669b93d4d64a72dd0e6811350bca95e`。
两棵树的六个上游源文件均与清单逐项匹配，没有修改上游源文件。

使用 post3 API 镜像、只读源码挂载、既有测试依赖、禁用网络及测试专用伪造
`ASSET_WORKER_HMAC_SECRET`，重跑 `test_release_identity.py` 与
`test_modelview_truev3_bundle.py`：隔离树 **12 passed**，上述两项失败均已修复。

主工作区同两模块额外运行得到 **11 passed、1 failed**；剩余失败为原有未提交环境的
Worker 默认版本不一致：`.env.node.example` 保留 `1.4.55-uv-multimesh-mof-v1`，而
Worker Dockerfile、主环境示例和 Compose 使用 `1.4.72-mof-feedback-spatial-v1`。
没有为清除此历史差异而扩大本次修改，也没有据此宣称主工作区全量测试通过。
本段记录的是定向复测结果，主代理后续的全量复测结论另行记录。

证据目录：
[full-audit-targeted-fix](../artifacts/control-plane/5070ti-20260911/evidence/linked/5070ti-final-adapter-20260911/full-audit-targeted-fix/)，
包含两树测试日志和十二项上游文件哈希校验。此修复未执行 stage、commit、部署或服务重启。

主代理明确本次发布暂存会保留原 Worker 版本基线，不纳入上述主工作区原有的
`1.4.72` UV/MOF 默认版本改动；这些历史改动继续留在工作区、保持未暂存。
因此主工作区的额外 Worker 版本失败不能等同于本次干净发布树失败。
最终发布树仍须以主代理完成暂存后的验证结果为准，本修复不调整这些 Worker 默认版本。
