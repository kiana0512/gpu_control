# 2026-08-03 Asset 失败归因、UV Advisory 修复与滚动发布验收记录

日期：2026-08-03（Asia/Singapore）

状态：`DEPLOYED_NOT_ACCEPTED`（Asset 组件局部部署）

适用范围：GPU Control `1.5.8` 滚动发布、Linux Blender Worker `1.2.4`、Windows Substance
Baker Agent v5。

边界：本文记录 GPU Control 仓库内的执行、交付和运行时挂载修复；不修改 Blender UV/重拓扑
Skill 的算法、prompt、几何阈值或输出语义，也不清理、释放或重启 ComfyUI 模型缓存。

## 1. 先读结论

- 本轮已进入滚动发布：数据库已升级到 `20260803_0012`；Asset API 已运行 `1.5.8`、source
  `7f7fd197f86288ffbeeab622cc39199335e22c61`；4090 Linux Asset Worker 已运行 `1.2.4`、同一
  source revision；3090-A/B 已运行 `1.2.4`、revision `e2cab4c…`。三节点 Worker 相关源码与
  三项批准 Skill 文件 SHA 已核对一致，统一 OCI image digest/SBOM 仍待归档；四个 Windows Baker
  槽已运行 `substance-baker-2026.08.03-v5`。
- 控制平面尚未统一：GPU API、Scheduler 和 Web 仍运行 `1.5.7`、source
  `11844e7f2ff5ea33db7e073b3f2af5c03b22085a`。因此当前是“部分上线、部分验收”，不能写成
  `PRODUCTION_ACCEPTED`，也不能把局部镜像 ID 冒充完整四组件发布证据。
- 生产 Asset API 已显式使用 `UV_QA_ENFORCEMENT=advisory`：几何 QA 不通过只附带告警，仍原子交付
  BLEND、FBX 和三份报告；文件缺失、空文件、错误输入身份、无效 JSON/QA 或 SHA/租约错误仍硬失败。
- UV advisory 已由真实 job `653fb52a-ac73-48a6-91fa-f45770d5de53` 验证：QA 告警保留、job
  `SUCCEEDED`、五件套原子发布。另一个无告警 UV job `76111775-c1a6-4799-aef0-1c19fe38c03d`
  也在 3090-B 一次成功并交付五件套。
- PBR 的四个旧失败样本是 Windows PowerShell 对已完成进程返回空 `ExitCode` 造成的假失败。v5 四槽已
  `ONLINE/HEALTHY`；真实 job `71f288d1-0a44-4188-bdde-ba7bbb1c073c` 已一次成功并原子发布
  12 项制品，ComfyUI 连续性通过。非零退出或缺少成功 marker 仍硬失败。
- 两个自动重拓扑失败样本来自 Worker 对 Codex Skill 根目录的错误挂载假设，不是模型几何失败。
  Worker 已改为只管理两个明确业务 Skill 的子链接，并保留 Codex 自有的 `skills/.system`；真实 job
  `dfaa4370-8b0e-4ea6-842b-cd4c08c4f614` 已在 3090-B、attempt 1 完成，终态为
  `SUCCEEDED + RETOPOLOGY_QUALITY_GATE_WARNING`；22 项制品中包含正式 BLEND/FBX。该结果验收
  Skill 挂载和 advisory 交付链路，不表示候选几何已经通过全部质量目标。

## 2. 本轮失败的精确归因

截图时间窗内可归为三类，不应继续用统一的“任务执行失败”文案掩盖根因：

| 类型 | 样本数 | 失败阶段/错误码 | 已核对根因 | 是否算法失败 |
| --- | ---: | --- | --- | --- |
| Substance PBR | 4 | `BAKING_TEXTURE_TRANSFER` / `SUBSTANCE_EXECUTION_FAILED` | Windows PowerShell 5.1 在 Baker 已完成且日志成功时仍暴露空 `ExitCode`；v3 将空值格式化成 `Substance Baker exited with code ` | 否，属于 Agent 结果判定假失败 |
| PBR UV | 2 | 约 60% / `UV_QA_FAILED` | BLEND/FBX 回读 QA 实测 `hard_edges_not_uv_boundary=16`；旧 Worker 的 `--strict` 在上传五件套前退出 | 是质量告警，但不应在 advisory 策略下阻断交付 |
| AI 重拓扑 | 2 | `RETOPOLOGY_BASELINE` / `BLENDER_EXECUTION_FAILED` | 运行时要求整个 `CODEX_HOME/skills` 必须是指向 `/opt/codex/skills` 的符号链接，与 Codex 正常的本地目录及 `.system` 冲突 | 否，属于运行时挂载合同错误 |

PBR 原生日志中的成功标志和产物存在只能证明本轮属于假失败，不代表以后所有空退出码都可无条件忽略。
已滚动上线的 v5 实现采用“双证据”判定，详见下一节。

## 3. 已上线修复合同

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

这项能力必须由 **Asset API 1.5.8 和 Blender Worker 1.2.4 同时上线**；当前真实 UV canary 已证明
配套链路生效：

1. `UV_PROCESS_V2` Worker 不再给 QA adapter 传 `--strict`，确保实测 BLEND QA、FBX 回读 QA 和
   五个非空制品能上传到服务端；
2. Asset API 解析两份 QA 后读取 `UV_QA_ENFORCEMENT`；
3. `advisory` 下几何 QA 未通过仍原子发布五件套，job 为 `SUCCEEDED`；
4. `strict` 下继续返回 `ASSET_QA_FAILED`，Worker 最终映射为 `UV_QA_FAILED`；
5. 历史 `UV_UNWRAP` 继续在 Worker 内 fail-fast，不受 V2 开关影响。

`UV_QA_ENFORCEMENT` 源码默认值和当前生产 Asset API 均已配置为 `advisory`；生产仍显式保留该变量，
避免部署环境不透明：

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

Worker 1.2.4 启动入口先执行 fail-closed bootstrap：

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

以下是 2026-08-03 18:41（Asia/Singapore）的滚动发布事实，不是最终统一版本声明：

| 组件 | 当前运行事实 | 验收/缺口 |
| --- | --- | --- | --- |
| GPU API | `1.5.7` / source `11844e7…` | 1.5.8 尚未滚动 |
| Asset API | `1.5.8` / source `7f7fd197…` / local image ID `ab5d5f575a9f…` | `/version` provenance aligned；registry digest/SBOM 待归档 |
| Scheduler | `1.5.7` / source `11844e7…` | 1.5.8 尚未滚动 |
| Web | `1.5.7` / source `11844e7…` | 1.5.8 尚未滚动 |
| 数据库 | `20260803_0012` | 已升级；升级前备份 `/tmp/pre-asset-1.5.8.sql`，SHA-256 `3bd3a8c275c0ee0fbf4b8af69650d0d874539cf013c22a26f6d153d71f09864f`；downgrade 演练待回填 |
| 4090 Blender Worker | `1.2.4` / source `7f7fd197…` / local image ID `c6ce7df35a6d…` | UV/探针运行正常；registry digest/SBOM 待归档 |
| 3090-A/B Blender Worker | `1.2.4` / source `e2cab4c…` | A 完成 warning UV，B 完成 clean UV 和连续两笔重拓扑；Worker 相关源码与批准 Skill SHA 一致，统一 OCI digest/SBOM 待归档 |
| Windows Baker Agent | 四槽 `substance-baker-2026.08.03-v5` | 均 `ONLINE`，宿主进程探针 `HEALTHY/0`；真实 PBR canary 已一次成功 |
| UV 策略 | `UV_QA_ENFORCEMENT=advisory` | warning 与 clean 两类真实 canary 均通过 |
| Retopology 策略 | `RETOPOLOGY_QA_ENFORCEMENT=advisory` | warning canary 已成功交付 22 项制品 |

本地 image ID/RepoDigest 只能证明本次节点上的镜像身份，不能替代远端 registry manifest digest。本
快照生成时远端 Git 同步仍待完成；四控制面镜像统一 revision、registry digest、SBOM 和远端 commit
仍是最终发布门禁。

## 5. 滚动发布状态与剩余顺序

生产任务优先。本轮不得为“赶进度”取消真实任务，也不得通过停整台 3090-B、释放显存或清 ComfyUI
缓存来制造发布窗口。

已完成：

1. 数据库升级到 `20260803_0012`；
2. Asset API 更新到 1.5.8，并显式启用 UV/Retopology advisory；
3. Linux Worker 1.2.4 已在三节点恢复接单；
4. Windows Agent v5 四槽已 ONLINE，宿主 Baker 进程探针均为 `HEALTHY/0`；
5. PBR、UV warning/clean 和连续两笔 Retopology warning 均已得到真实生产终态制品证据。

剩余发布必须继续遵守：

1. 等当前新真实重拓扑任务完成后，再评估 GPU API、Web、Scheduler 的安全滚动窗口，Scheduler 最后；
2. 每个组件记录旧/新容器、source revision、image ID、registry digest、启动时间与健康结果；
3. 补齐三节点 Worker 精确 image digest、Windows 已安装脚本 SHA 和数据库升级前备份 SHA；
4. 对已生成 artifact 执行 API 下载，核对 body SHA、job JSON SHA 与 `X-Artifact-SHA256`；
5. 完成回滚演练、连续观察和远端 Git/registry/SBOM 归档后，才评估 `PRODUCTION_ACCEPTED`。

## 6. 必做 canary 与验收清单

### 6.1 PBR 假失败回归

- [x] 真实 job `71f288d1-0a44-4188-bdde-ba7bbb1c073c`，request ID
  `750d86c3ad51ede7e83c2724206f6f28`；
- [x] `asset-worker-3090-b-windows-02`、attempt 1，18:32:11 开始、18:32:52 完成，终态
  `SUCCEEDED`；
- [x] result schema `2`、profile `li3d-pbr-full-v2`、10 个 Baker command；
- [x] 10 个 command 均有原生成功 marker；PowerShell ExitCode 未观察到时保留
  `exit_code_observed=false / exit_code=null`，没有伪造为 `0`；
- [x] `comfyui_cache_policy=no_explicit_eviction_process_preserved`、
  `comfyui_container_restarted=false`、`comfyui_process_continuity_verified=true`；
- [x] Asset API 原子发布 12 项非空 artifact；
- [ ] 下面 SHA 来自数据库 artifact 元数据，尚未完成 API body/header 三重下载校验；
- [ ] 生产观察继续确认无重复执行、无错误解锁和无 ComfyUI 缓存清理。

| kind | bytes | 数据库 artifact SHA-256 |
| --- | ---: | --- |
| `ao` | 1,198,183 | `b92c7c96cbc1afdb7de685de5799a85c1cf4db9878e4e39ae0f745661cf71f05` |
| `base_color` | 5,166,866 | `684b146f754af77227b5754456bb4b04d1432a5d2363d5a03aa03351f11973e1` |
| `curvature` | 1,343,629 | `4b9aefd818fa4692d8bc2f178f36eb5d1c4410e67272fee3337201981ef9f7c0` |
| `log` | 13,000 | `90b4788f086c1c9ba55e4e27adad20622963489b4e131188879422294cd7390c` |
| `metallic` | 41,258 | `c75edfa88583c8dd145b46e72782b0ee55583adf1218ad5953edfce1c1a97a98` |
| `normal_dx` | 4,268,008 | `ab769e5fe678882caa5fbaafbfd80c082f940cfebfe142ae7a7ab526720197df` |
| `normal_gl` | 4,268,524 | `99744e999ad5fda5f482910a67f02ed86baf81c74b4a60dce93c1bfe1bebad2d` |
| `position` | 3,018,762 | `4bc2e26fb9729be468530fff88bc87e32713798c865ef143785c2e7e31f05398` |
| `result` | 6,824 | `3e1ab9f71eb328a0712005e9ac2f4e3c60a262ca37cdec8373af05ca20bdabe2` |
| `roughness` | 243,999 | `31e8f99b19296bd17c13158b07b576482d03ac9c574a1e7d1ac912bdaa78e21b` |
| `thickness` | 1,149,584 | `1a6a11d726601eabd785a6c92edec8e720456296f51be537fc74916cd49a25be` |
| `world_normal` | 4,287,956 | `22b5d3dfa39ab7bbc586783172e2f1f83fe0c63689ee8a7c27686cb96779fb78` |

### 6.2 UV advisory 回归

- [x] 原失败素材 warning job `653fb52a-ac73-48a6-91fa-f45770d5de53`，request ID
  `a18f218a681f557e89f7f0b7052a5716`，3090-A、attempt 1，18:35:55 完成；
- [x] job `SUCCEEDED`、`delivery_ready=true`、`error=null`；
- [x] `options.qa_warning.code=UV_QUALITY_GATE_WARNING`、`enforcement=advisory`，保留
  `hard_edges_not_uv_boundary=16`；
- [x] SSE 终态 `asset.succeeded_with_warnings`；
- [x] 五件套均已原子登记；
- [x] clean job `76111775-c1a6-4799-aef0-1c19fe38c03d` 在 3090-B、attempt 1 完成，无
  `qa_warning`，同样登记五件套；
- [ ] 下面 SHA 来自数据库 artifact 元数据，尚未完成 API body/header 三重下载校验；
- [ ] strict 负例和 advisory 完整性负例已有源码自动测试，生产不执行破坏性故障注入。

| kind | bytes | warning job 数据库 artifact SHA-256 |
| --- | ---: | --- |
| `blend` | 18,811,074 | `9f1e572e5ffdeaf2355ada2d38be35de8ffc1df96ea9899ec8e35d18d63e46fb` |
| `fbx` | 9,437,244 | `bef1cff8a4c3129208deda2de217da4cd019af789daac0e1fac6801ebdff8a76` |
| `fbx_qa` | 1,776 | `f8c26921f7e0c14c0523ad852eb40d32f8b49f5c121ff5ab6c61a3e06de10920` |
| `qa` | 1,935 | `0f2297d61cac504400e23ef3a7a381a47308e579ab9090f384408515ccab6868` |
| `report` | 2,990 | `3fb960dfe56a8af3f3c3951d310f7bb29f9ad06733331964ea821815f9064b6d` |

### 6.3 Codex Skill 挂载与重拓扑回归

- [x] 三台 Worker 心跳中的真实 Codex probe 均为 `AUTHENTICATED + HEALTHY`；
- [x] 真实 job `dfaa4370-8b0e-4ea6-842b-cd4c08c4f614`，request ID
  `9b123789ee3a67a4576aec5d69eca901`，3090-B、attempt 1，18:41:40 完成；
- [x] 终态 `SUCCEEDED`，SSE 为 `asset.succeeded_with_warnings`；
- [x] warning 为 `RETOPOLOGY_QUALITY_GATE_WARNING/advisory`，`audit_passed=true`，同时明确保留
  `FACE_COMPONENTS_LOST=1`、`topology_goal_met=false`、
  `automatic_final_promotion_allowed=false`；
- [x] 原子登记 22 项 artifact：正式 BLEND/FBX、comparison、12 张 high/reference/generated 四视图、
  audit/baseline/manifest/process report 和三项 Agent 证据；
- [x] 第二笔真实 job `91da155b-d4d9-4159-9614-4b3e9123bbe2` 也在 3090-B 连续成功并登记
  22 项 artifact，部署后未再出现 Skill 挂载失败；
- [x] `retopology_final.blend` 为 41,192,196 bytes，数据库 SHA-256
  `a9468048ae8f5acc9f5184e1ccc73f111fa8646369ef6b2e9fdef4cf49b290b3`；
- [x] `retopology_final.fbx` 为 33,196 bytes，数据库 SHA-256
  `d1225622b5b144445c10f50b914aaa219ef3ea6de8c36f62eccd9f92b6ea770b`；
- [x] `retopology_comparison.png` 为 1,127,001 bytes，数据库 SHA-256
  `6862725e5717562ef4637240ab6f55c2c408f3f8d2d18d70a92262446c56793d`；
- [ ] 22 项 SHA 当前只核对到数据库 artifact 元数据，尚未执行 API body/header 三重下载校验；
- [ ] Skill 悬空/漂移 fail-closed 已由隔离自动测试覆盖，不在生产节点主动破坏链接。

## 7. 源码验证与尚未完成项

已上线源码包含针对下列合同的自动测试：

- PBR `null/0/nonzero ExitCode × success marker` 真值表；
- UV advisory 五件套成功告警、strict 失败和 advisory 完整性硬失败；
- `UV_PROCESS_V2` 不在 Worker 内 strict、历史 `UV_UNWRAP` 保持 strict；
- Codex `.system/auth` 保留、子链接幂等、错误目标/悬空/非托管路径拒绝；
- inspect、probe、heartbeat 的 `SKILL_MOUNT_INVALID` 漂移检测；
- Worker `1.2.4` OCI version/revision 与启动 bootstrap 合同。

本轮已核实的源码门禁：

- 最终工作树全量 unit：`234 passed`；
- 全量 integration：`116 passed, 5 skipped`；
- Ruff：全部通过；
- 本轮相关 4 个文件的 mypy：通过；这不表示全仓 mypy 通过，全仓仍有既有无关问题；
- 两份 Compose config：均可解析。

这些门禁和单次 canary 仍不等于全量发布完成。最终归档前还需完成四控制面统一版本、API artifact
三重 SHA、统一 OCI image digest/SBOM、回滚演练和连续观察。

## 8. 回滚

- UV：将 Asset API/Worker 一起回滚到上一已验版本；不能只回滚其中一端。`UV_QA_ENFORCEMENT=strict`
  可恢复严格策略，但不替代二进制兼容性回滚。
- Linux Worker：单节点保持 `DRAINING`，恢复 `1.2.3` 镜像和原配置，核对无运行任务后再 `ACTIVE`。
- Windows Agent：停止对应 v5 计划任务，确认无 Baker 进程/租约后恢复已备份版本；Agent/API 协议代际必须
  匹配。
- 控制面/数据库：按 80 号发布计划的备份与迁移回滚执行。回滚期间不删除 job、artifact 或审计证据。
- 任一回滚都不得关闭整台 3090-B 或清理 ComfyUI 模型缓存。

## 9. 发布后证据回填

```text
deployed Asset API / 4090 Worker source: 7f7fd197f86288ffbeeab622cc39199335e22c61
GPU API image digest: PENDING (production remains 1.5.7)
Asset API local image ID: sha256:ab5d5f575a9f30482e35698626b9671d5420198906614ecdbffcc6b2a7ec15ff
Asset API registry digest / SBOM: PENDING
Scheduler image digest: PENDING (production remains 1.5.7)
Web image digest: PENDING (production remains 1.5.7)
4090 Worker 1.2.4 local image ID: sha256:c6ce7df35a6d85e00da48f001b2523c95afe715a0b9e04cc716296f7f6bd1049
3090-A/B Worker 1.2.4 image digest: PENDING
Windows Agent v5 source script SHA-256: 0ed748231efaec16db4d5cf0a2df7d7b5f5a601985442a59ce19669fa636a1e7
Windows installed script SHA-256: PENDING
database before/after: 20260730_0011 -> 20260803_0012
database backup path / SHA: PENDING
production UV_QA_ENFORCEMENT: advisory
production RETOPOLOGY_QA_ENFORCEMENT: advisory
PBR canary: 71f288d1-0a44-4188-bdde-ba7bbb1c073c / SUCCEEDED / 12 artifacts / DB SHA only
UV advisory canary: 653fb52a-ac73-48a6-91fa-f45770d5de53 / SUCCEEDED_WITH_WARNING / 5 artifacts / DB SHA only
UV clean canary: 76111775-c1a6-4799-aef0-1c19fe38c03d / SUCCEEDED / 5 artifacts / DB SHA only
Retopology canaries: dfaa4370-8b0e-4ea6-842b-cd4c08c4f614 and 91da155b-d4d9-4159-9614-4b3e9123bbe2 / SUCCEEDED_WITH_WARNING / 22 artifacts each / DB SHA only
API body/header artifact SHA report: PENDING
three-node Codex probe JSON: AUTHENTICATED + HEALTHY snapshot observed; raw JSON PENDING
rollback artifact locations:
observation start/end:
remote Git commit / registry manifests / SBOM: PENDING
final status: DEPLOYED_NOT_ACCEPTED
```

在统一 OCI image digest/SBOM、artifact API 三重 SHA、回滚与联合观察完成前，本文件状态保持
`DEPLOYED_NOT_ACCEPTED`，不得标记
`PRODUCTION_ACCEPTED`。
