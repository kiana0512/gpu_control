# 2026-08-03 GPU Control 1.5.8 候选修复与安全发布计划

日期：2026-08-03（Asia/Singapore）

状态：`SOURCE_CANDIDATE_NOT_DEPLOYED`

范围：GPU Control API、Asset API、Scheduler、Web、Substance Agent、数据库迁移与发布脚本。

边界：不修改 ImageClip、ModelViewCreator、Retopology Skill、外部 workflow、模型、prompt、图拓扑、
采样参数或输出语义；不清理或释放 ComfyUI 模型缓存。

## 1. 当前结论

1.5.8 正在完成源码级修复和离线验证。由于生产后端仍有真实任务，本候选版本尚未执行数据库迁移、
容器替换、服务重启或节点 drain，也没有对生产 API 发起压力测试。当前生产控制平面继续运行 1.5.7。

候选版本要解决的核心问题：

- Substance 长任务租约失效后，不能关闭整台 3090-B，也不能在恢复证据不足时重复执行；
- Codex 健康必须由每台 Linux Asset Worker 自己的新鲜真实探针证明，Windows Baker 心跳不能覆盖它；
- UV 和拓扑审计不得被 Codex 状态误伤；只有依赖 Codex 的自动重拓扑领取需要健康门禁；
- Web 必须区分 `HEALTHY`、`STALE`、认证失败和真实调用失败，统计只使用可调度节点；
- 标准发布脚本必须同时构建 API、Scheduler、Asset API 和 Web，避免迁移后仍运行旧 Asset API。
- Substance Baker 已成功但 PowerShell 5.1 暴露空 `ExitCode` 时不能误判失败；真实非零退出或缺少
  `Bake finished successfully` marker 仍必须失败；
- UV V2 在 `advisory` 策略下应交付完整五件套并保留质量告警，不能在 Worker 内提前 strict 退出；
- Codex 运行时只管理批准的业务 Skill 子链接并保留 `CODEX_HOME/skills/.system`；挂载漂移必须以
  `SKILL_MOUNT_INVALID` fail closed，而不是让任务运行后才报模糊执行错误。

## 2. Substance 恢复安全模型

租约超时后只闭锁相关的 Windows Baker Worker，不关闭 GPU 节点、ComfyUI 或其他 CPU Worker。自动恢复
必须同时满足：

1. 旧租约已经过期，且相关 job 已进入明确终态；
2. Agent v4 使用新的 `agent_instance_id`，并报告自己的启动时间；
3. Windows 主机全局命名互斥锁证明同一稳定 Worker 同时只有一个 Agent；
4. 主机级进程探针返回 `HEALTHY` 且 `substance3d_baker.exe` 数量为 0；
5. 服务端先记录零进程观察时间，再等待一次严格晚于该时间的新 GPU 节点心跳；
6. 心跳签名、时间窗和 nonce 防重放全部通过；
7. 数据库中该稳定 Worker 没有 `CLAIMED`、`RUNNING` 或 `CANCELLING` 的 durable job。

任何 `FAILED`、未知进程数、过期证据、未来时间、重复 nonce、旧 Agent 代际或仍有 durable job 的情况都
fail closed。闭锁只影响该 Baker Worker；UV、重拓扑、GPU 推理和其他 Windows Baker 槽位按各自能力继续
调度。

## 3. Codex 探针与能力门禁

`/admin/nodes` 按 GPU 节点精确映射对应的 Linux Asset Worker，并同时要求 Worker 在线、心跳新鲜、认证
有效、真实 `codex exec` 探针健康且探针未过期，才标记 `scheduler_eligible=true`。

Asset API 的领取规则按任务能力隔离：

- `RETOPOLOGY_PROCESS_V1`：要求新鲜健康的 Codex 探针；
- UV、拓扑审计和 PBR 烘焙：不因 Codex 认证或探针异常被阻塞；
- Windows Substance Baker：使用独立 Worker 与租约，不作为 Linux Codex 健康来源。

## 4. Li3D 应用端 CA 诊断

Li3D 中“Asset V4 公司 CA 证书未配置或文件不可用”是应用端信任包/HTTP 客户端配置问题。服务器端证书链
和严格 TLS 校验已验证：

```text
CA URL: http://10.3.34.11/GPU_CONTROL_LAN_CA.crt
CA SHA-256: ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b
```

应用应把该 CA 作为受控资源随包分发或首次启动下载并核对 SHA-256，再显式加入 Asset V4 HTTP 客户端的
信任存储。不得使用 `verify=false`。GPU Control 服务端不需要为此关闭 TLS；应用源码不在本仓库，需在
Li3D 仓库修复配置和资源打包。

## 5. 强制发布顺序

Agent v4 与旧 Asset API 双向不兼容：旧 API 会拒绝 v4 新字段，新 API 会把 v3 Agent 保持为
`DRAINING`。因此禁止直接热替换 Agent，必须在真实任务清空后按以下顺序执行：

1. 停止新的 Substance 接单，只 drain 3090-B Windows Baker；
2. 确认全部 Baker job、Baker 进程和相关租约为 0；
3. 备份数据库与当前 1.5.7 配置、镜像身份；
4. 执行迁移 `20260803_0012`；
5. 更新控制平面四镜像，尤其是 Asset API；
6. 安装 Agent v4，确认四个稳定 Windows Worker 全部 ONLINE、进程探针健康；
7. 先以单个 Baker 槽位 canary，再恢复其余槽位；
8. 验证 UV、重拓扑、PBR、GPU 推理队列互不误阻塞后再恢复正常接单。

任一步失败立即保持 `DRAINING` 并回滚到 1.5.7；不得通过关闭 3090-B GPU 节点或清理 ComfyUI 缓存来
解除租约。

## 6. 发布门禁与待回填证据

以下源码门禁已在与生产网络隔离、源码只读挂载且最多 2 CPU/2 GiB 的容器中完成。这里的精确数量对应
前序候选 `52ecad10…`，早于随后加入的 UV/PBR/Skill 修复，不能作为最终 release commit 的全量通过数：

- [x] Python 全量单元/集成测试：`315 passed, 6 skipped`；
- [x] Asset API 全量：`38 passed`；Substance 专项：`19 passed`；Agent、调度与 DB claim：`40 passed`；
- [x] nonce 顺序重放和并发重放均返回 `409 ASSET_WORKER_REQUEST_REPLAY`；
- [x] Web：`16 passed`，`vue-tsc`、ESLint、Prettier 和 Vite 生产构建全部通过；
- [x] Ruff、Compose config 和 `git diff --check` 通过；PowerShell 仅有预期的 LF→CRLF 属性提示；
- [x] 一次性 SQLite 从空库升级到 migration head `20260803_0012`；

本轮新增源码已经配套增加 PBR 退出码真值表、UV advisory/strict/完整性、Skill 子链接 bootstrap/漂移
探针和 Worker `1.2.4` 镜像身份测试；最终候选仍必须重新完成并回填：

- [ ] 最终干净 release commit 上的全量 Python、Ruff、mypy 与 migration 测试；
- [ ] 最终 Web 类型、lint、格式和生产构建；
- [ ] Compose config 与 `git diff --check`；
- [ ] 真实 PBR、UV advisory 和重拓扑 canary，以及全部 artifact SHA。

以下发布和生产证据仍未完成，状态必须保持 `SOURCE_CANDIDATE_NOT_DEPLOYED`：

- [ ] 四个 1.5.8 镜像的 source revision、image ID/digest、SBOM 和归档 SHA-256；
- [ ] 已发布 source commit 与远端分支证据；
- [ ] 生产任务清空、drain、canary、回滚演练和发布后状态 JSON；
- [ ] 连续观察窗口内无重复执行、无错误解锁、无队列能力串扰。

Web 生产构建只有既有的第三方 PURE 注释和 `Dashboard` 约 504 KiB chunk 警告；没有类型、lint、格式或
构建错误。当前环境没有可用 Browser 插件或仓库内 Playwright 配置，因此浏览器 DOM/响应式视觉回归仍
需在 canary 前补做，不能用 Vite 构建替代真实浏览器验收。

本轮三类失败的证据、精确候选合同、组件配套关系和回填表见
[82_2026-08-03_ASSET_FAILURES_UV_ADVISORY_AND_RELEASE_ACCEPTANCE.md](82_2026-08-03_ASSET_FAILURES_UV_ADVISORY_AND_RELEASE_ACCEPTANCE.md)。

本文件不构成 `FROZEN` 或 `PRODUCTION_ACCEPTED`，也不声称候选代码已经上线。
