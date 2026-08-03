# 2026-08-03 Asset 失败归因、UV Advisory 候选修复与发布验收记录

日期：2026-08-03（Asia/Singapore）

状态：`SOURCE_FIXES_PRESENT_NOT_DEPLOYED`

适用范围：GPU Control `1.5.8` 候选、Linux Blender Worker `1.2.4` 候选、Windows Substance
Baker Agent v4 候选。

边界：本文记录 GPU Control 仓库内的执行、交付和运行时挂载修复；不修改 Blender UV/重拓扑
Skill 的算法、prompt、几何阈值或输出语义，也不清理、释放或重启 ComfyUI 模型缓存。

## 1. 先读结论

- 生产仍是 GPU Control `1.5.7`、source
  `11844e7f2ff5ea33db7e073b3f2af5c03b22085a`、数据库 `20260730_0011`、Linux
  Asset Worker `1.2.3` 和 Windows Baker Agent v3。
- 本轮源码修复尚未执行数据库迁移、镜像替换、Linux Worker 滚动更新或 Windows Agent 替换，不能
  对外声称“生产已修复”。候选 release commit、镜像 digest、脚本 SHA 和 canary job ID 均待发布窗口
  回填。
- UV 的目标生产策略是 `UV_QA_ENFORCEMENT=advisory`：几何 QA 不通过只附带告警，仍原子交付
  BLEND、FBX 和三份报告；文件缺失、空文件、错误输入身份、无效 JSON/QA 或 SHA/租约错误仍硬失败。
- PBR 的四个失败样本是 Windows PowerShell 对已完成进程返回空 `ExitCode` 造成的假失败；Baker 日志
  已出现 `Bake finished successfully`。候选修复不会把真实非零退出码放行。
- 两个自动重拓扑失败样本来自 Worker 对 Codex Skill 根目录的错误挂载假设，不是模型几何失败。
  候选 Worker 改为只管理两个明确业务 Skill 的子链接，并保留 Codex 自有的 `skills/.system`。

## 2. 本轮失败的精确归因

截图时间窗内可归为三类，不应继续用统一的“任务执行失败”文案掩盖根因：

| 类型 | 样本数 | 失败阶段/错误码 | 已核对根因 | 是否算法失败 |
| --- | ---: | --- | --- | --- |
| Substance PBR | 4 | `BAKING_TEXTURE_TRANSFER` / `SUBSTANCE_EXECUTION_FAILED` | Windows PowerShell 5.1 在 Baker 已完成且日志成功时仍暴露空 `ExitCode`；v3 将空值格式化成 `Substance Baker exited with code ` | 否，属于 Agent 结果判定假失败 |
| PBR UV | 2 | 约 60% / `UV_QA_FAILED` | BLEND/FBX 回读 QA 实测 `hard_edges_not_uv_boundary=16`；旧 Worker 的 `--strict` 在上传五件套前退出 | 是质量告警，但不应在 advisory 策略下阻断交付 |
| AI 重拓扑 | 2 | `RETOPOLOGY_BASELINE` / `BLENDER_EXECUTION_FAILED` | 运行时要求整个 `CODEX_HOME/skills` 必须是指向 `/opt/codex/skills` 的符号链接，与 Codex 正常的本地目录及 `.system` 冲突 | 否，属于运行时挂载合同错误 |

PBR 原生日志中的成功标志和产物存在只能证明本轮属于假失败，不代表以后所有空退出码都可无条件忽略。
候选实现采用“双证据”判定，详见下一节。

## 3. 候选修复合同

### 3.1 Substance Baker 空 ExitCode

Windows Agent 新增 `Assert-BakerCommandResult`，在 `WaitForExit()` 后执行 `Refresh()`，再按下表判定：

| 可观察 ExitCode | 完整刷新后的日志含 `Bake finished successfully` | 结果 |
| --- | --- | --- |
| `0` | 是 | 接受，继续上传并由 Asset API 校验产物 |
| `null` | 是 | 接受，兼容 PowerShell 5.1 已完成进程句柄的空值 |
| 非 `0` | 任意 | 失败，绝不被成功文案覆盖 |
| `0` 或 `null` | 否 | 失败，不能用“没有错误码”冒充成功 |

Asset API 既有的结果 ZIP、Baker 日志、必需贴图、manifest 和 SHA 门禁继续生效。本修复只消除
Agent 的假阴性，不降低服务端完整性要求。

### 3.2 UV QA 改由 Asset API 统一决策

这项能力必须由 **Asset API 1.5.8 候选和 Blender Worker 1.2.4 候选同时上线**：

1. `UV_PROCESS_V2` Worker 不再给 QA adapter 传 `--strict`，确保实测 BLEND QA、FBX 回读 QA 和
   五个非空制品能上传到服务端；
2. Asset API 解析两份 QA 后读取 `UV_QA_ENFORCEMENT`；
3. `advisory` 下几何 QA 未通过仍原子发布五件套，job 为 `SUCCEEDED`；
4. `strict` 下继续返回 `ASSET_QA_FAILED`，Worker 最终映射为 `UV_QA_FAILED`；
5. 历史 `UV_UNWRAP` 继续在 Worker 内 fail-fast，不受 V2 开关影响。

`UV_QA_ENFORCEMENT` 源码默认值仍为 `strict`。目标生产发布必须显式配置：

```text
UV_QA_ENFORCEMENT=advisory
```

advisory 成功时公开 job 的关键事实为：

```json
{
  "status": "SUCCEEDED",
  "delivery_ready": true,
  "artifacts_role": "delivery",
  "error": null,
  "options": {
    "qa_warning": {
      "code": "UV_QUALITY_GATE_WARNING",
      "enforcement": "advisory",
      "failed_qa": ["blend", "fbx_readback"],
      "failures": ["blend: ...", "fbx_readback: ..."]
    }
  }
}
```

SSE 终态 `details.event=asset.succeeded_with_warnings`，并返回
`warning_code=UV_QUALITY_GATE_WARNING`、`qa_enforcement`、`quality_gate_passed`、
`quality_failures` 和 `failed_qa`。应用端必须显示“已交付 · UV QA 告警”，同时开放全部五个下载项，
不能把 warning 映射成失败或等待人工发布。

五件套保持不变：

| kind | 文件 |
| --- | --- |
| `blend` | `<stem>_PBR_UV.blend` |
| `fbx` | `<stem>_PBR_UV.fbx` |
| `report` | `<stem>_PBR_UV_report.json` |
| `qa` | `<stem>_PBR_UV_QA.json` |
| `fbx_qa` | `<stem>_PBR_UV_FBX_QA.json` |

以下不是“QA 太严格”，即使在 advisory 下也必须硬失败且不发布半套结果：

- 五个制品任一个缺失或为空；
- report/QA 不是合法 JSON，或 `hard_failures` 不是数组、`passed` 不是 boolean；
- report 的输入文件身份与 job 源文件不一致；
- 租约、job 类型、取消安全点、artifact 路径、大小或 SHA 校验失败。

### 3.3 Codex 业务 Skill 挂载

候选 Worker 启动入口先执行 fail-closed bootstrap：

```text
CODEX_RUNTIME_HOME=/home/assetworker/.codex
CODEX_SKILLS_ROOT=/opt/codex/skills
UV_SKILL_ROOT=/opt/codex/skills/blender-pbr-uv
RETOPOLOGY_SKILL_ROOT=/opt/codex/skills/blender-retopology-compare-iterate
```

正确运行时布局为：

```text
/home/assetworker/.codex/skills/                         # 普通目录，不是整目录链接
├── .system/                                             # Codex 所有，原样保留
├── blender-pbr-uv -> /opt/codex/skills/blender-pbr-uv
└── blender-retopology-compare-iterate
    -> /opt/codex/skills/blender-retopology-compare-iterate
```

bootstrap 只创建这两个明确子链接；已有的非托管路径、悬空链接、错误目标、缺失 `SKILL.md`、相对路径或
越出批准 root 都 fail closed，绝不删除或覆盖现有目录。启动后 inspect、真实 Codex probe 和每次 heartbeat
都会复核链接；发现漂移立即上报：

```text
codex_probe_status=FAILED
codex_error_code=SKILL_MOUNT_INVALID
```

只有依赖 Codex 的 `RETOPOLOGY_PROCESS_V1` 领取受该健康门禁影响；UV、拓扑审计、PBR 和 GPU 推理
不能因为 Codex Skill 链接异常而停用。

## 4. 发布组件与版本边界

| 组件 | 生产当前 | 本轮候选 | 发布前必须回填 |
| --- | --- | --- | --- |
| Control plane | `1.5.7` / source `11844e7…` | `1.5.8` | release commit、四镜像 digest/SBOM |
| 数据库 | `20260730_0011` | `20260803_0012` | 备份、upgrade/rollback 记录 |
| Linux Blender Worker | `1.2.3` | `1.2.4` | 三节点同 commit/image digest |
| Windows Baker Agent | v3 | `substance-baker-2026.08.03-v5` | 脚本 SHA、四计划任务版本、单实例/进程探针 |
| UV 策略 | strict | 目标 `advisory` | 运行时 `/version`、job warning 和五件套 SHA |
| Retopology 策略 | advisory | advisory | 正式 BLEND/FBX canary 与 Skill 链接证据 |

工作区中的候选修改在形成干净 release commit 前不具备可部署身份；不能把当前 `HEAD`、未提交 diff 或
本地 image ID 写成正式 source/digest。

## 5. 强制安全发布顺序

生产任务优先。本轮不得为“赶进度”取消真实任务，也不得通过停整台 3090-B、释放显存或清 ComfyUI
缓存来制造发布窗口。

1. 等待 Asset job 为 0，并确认相关 Linux/Windows Worker 已 drain；控制面替换前同时确认没有会被
   重启影响的 GPU 真实任务；
2. 备份数据库、生产 `.env`、当前镜像身份、Windows v3 脚本和计划任务定义；
3. 先升级数据库到 `20260803_0012`；
4. 发布 1.5.8 Asset API，再按 API、Web、Scheduler（最后）的顺序更新控制面；
5. 仅在 Asset API 已支持 v4 字段后更新 Windows Agent，先单槽 canary，再恢复其余三槽；
6. Linux Worker 按 4090 → 3090-A → 3090-B 单节点 drain/替换/验活，3090-B 最后；
7. 每台 Worker 返回服务前核对 build version、source revision、两个业务 Skill 子链接和真实 Codex
   probe；
8. 完成下面三项 canary 后才恢复常规接单。

## 6. 必做 canary 与验收清单

### 6.1 PBR 假失败回归

- [ ] 使用此前能稳定出现原生成功 marker 的输入；
- [ ] 单槽执行并记录原始 PowerShell/Baker 日志；
- [ ] job `SUCCEEDED`，结果 ZIP 和必需贴图齐全；
- [ ] 下载每项 artifact 并验证 body SHA、JSON SHA、`X-Artifact-SHA256` 一致；
- [ ] 构造非零退出码和缺少 marker 的负例，均必须失败；
- [ ] 3090-B ComfyUI 容器 ID/启动时间不变，模型缓存未清理。

### 6.2 UV advisory 回归

- [ ] 使用已知 `hard_edges_not_uv_boundary=16` 的原失败素材；
- [ ] job `SUCCEEDED`、`delivery_ready=true`、`error=null`；
- [ ] `options.qa_warning.code=UV_QUALITY_GATE_WARNING`；
- [ ] SSE 为 `asset.succeeded_with_warnings`；
- [ ] 五件套全部可下载且逐项 SHA 一致；
- [ ] 切回 strict 的隔离测试仍得到 `ASSET_QA_FAILED/UV_QA_FAILED`；
- [ ] 空 artifact、错误 report input 和无效 QA 在 advisory 下仍硬失败。

### 6.3 Codex Skill 挂载回归

- [ ] `skills` 是普通目录，`.system` 和 `auth.json` 均保留；
- [ ] 两个业务 Skill 是指向批准路径的精确子链接；
- [ ] 三台 Worker 的真实 Codex probe 均为 `AUTHENTICATED + HEALTHY`；
- [ ] 使用此前 `persistent skill mount is invalid` 的 BLEND 重跑自动重拓扑；
- [ ] 正式 `retopology_final.blend/fbx` 可下载并校验 SHA；
- [ ] 人工制造悬空/漂移链接的隔离测试上报 `SKILL_MOUNT_INVALID`，且 UV 仍可领取。

## 7. 源码验证与尚未完成项

候选源码已包含针对下列合同的自动测试：

- PBR `null/0/nonzero ExitCode × success marker` 真值表；
- UV advisory 五件套成功告警、strict 失败和 advisory 完整性硬失败；
- `UV_PROCESS_V2` 不在 Worker 内 strict、历史 `UV_UNWRAP` 保持 strict；
- Codex `.system/auth` 保留、子链接幂等、错误目标/悬空/非托管路径拒绝；
- inspect、probe、heartbeat 的 `SKILL_MOUNT_INVALID` 漂移检测；
- Worker `1.2.4` OCI version/revision 与启动 bootstrap 合同。

但“存在测试代码”不等于发布完成。合并前仍须回填最终全量 pytest、Ruff、mypy、Compose、迁移、Web
构建结果；部署后仍须回填真实三机/Windows canary、版本 JSON、artifact SHA 和连续观察结果。

## 8. 回滚

- UV：将 Asset API/Worker 一起回滚到上一已验版本；不能只回滚其中一端。`UV_QA_ENFORCEMENT=strict`
  可恢复严格策略，但不替代二进制兼容性回滚。
- Linux Worker：单节点保持 `DRAINING`，恢复 `1.2.3` 镜像和原配置，核对无运行任务后再 `ACTIVE`。
- Windows Agent：停止对应 v4 计划任务，确认无 Baker 进程/租约后恢复已备份 v3；Agent/API 协议代际必须
  匹配。
- 控制面/数据库：按 80 号发布计划的备份与迁移回滚执行。回滚期间不删除 job、artifact 或审计证据。
- 任一回滚都不得关闭整台 3090-B 或清理 ComfyUI 模型缓存。

## 9. 发布后证据回填

```text
release commit:
API image digest:
Asset API image digest:
Scheduler image digest:
Web image digest:
Worker 1.2.4 image digest:
Windows Agent v4 SHA-256:
database before/after:
production UV_QA_ENFORCEMENT:
PBR canary job/request/artifact SHA report:
UV advisory canary job/request/artifact SHA report:
Retopology canary job/request/artifact SHA report:
three-node Codex probe JSON:
rollback artifact locations:
observation start/end:
final status: DEPLOYED_NOT_ACCEPTED / PRODUCTION_ACCEPTED
```

在以上发布证据和联合观察完成前，本文件状态保持 `SOURCE_FIXES_PRESENT_NOT_DEPLOYED`。
