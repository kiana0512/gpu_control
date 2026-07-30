# 2026-07-30 Retopology QA Advisory 热修复与压测准备记录

## 1. 结论

2026-07-30，系统所有者明确要求先恢复 AI 重拓扑可交付能力：几何质量阈值暂时不再把整单判为失败，
但外部 Retopology Skill/算法继续迭代，GPU Control 不修改其算法、模型或处理语义。

GPU Control 已在自身边界内实现并上线 `strict | advisory` 双模式。生产当前使用
`RETOPOLOGY_QA_ENFORCEMENT=advisory`：

- 几何 QA 未通过时，任务以 `SUCCEEDED` 交付候选，并持久化 `qa_warning`、完整 failures 和原始诊断；
- WebUI 显示“已交付 · QA 告警”，不得误报为“质量检查通过”；
- 输入/制品完整性、manifest/job/input/object 身份一致性、源对象保护及 PNG 有效性仍是硬门禁；
- 回滚到 `strict` 不需要迁移数据库，也不需要修改或重启外部 Skill/Worker。

当前实现已随 GPU Control `1.5.6` 热更新，发布状态为 `DEPLOYED_NOT_ACCEPTED`。这里的“已部署”只
说明控制面代码和交付合同在线，不表示 Retopology 算法质量、固定联合基准或七天稳定性已经验收。

## 2. 版本与部署证据

| 项目 | 值 |
|---|---|
| current source commit | `310a44c70c20f7cbfc601d19e19858380a61c20a` |
| current Asset API image | `unified-scheduler-asset-api:1.5.6` |
| current local image ID | `sha256:f83bed46d7540de4cf7d08e4cff8d7675dd7dd4675bf13d301226f5f5c4cb01f` |
| OCI version / revision | `1.5.6` / `310a44c70c20f7cbfc601d19e19858380a61c20a` |
| current rollback tag | `unified-scheduler-asset-api:rollback-1.5.5-20260730` |
| initial advisory source | `7fc7efd4f6e88283f4195b1bf07ba2b5b076429a`（历史 1.5.5 热修复） |
| deployment status | `DEPLOYED_NOT_ACCEPTED` |
| production mode | `advisory` |

初始 1.5.5 advisory 热修复只滚动 Asset API；随后 1.5.6 发布再次在 GPU/Asset 活动任务为 0 时，按
Scheduler → Asset API → API → Web 顺序逐服务更新。数据库、Redis、ComfyUI、GPU Worker、Asset
Worker 和外部 Retopology Skill 均未重启。部署后两个版本端点均返回
`build_version=1.5.6`、`version_aligned=true`、`provenance_complete=true`；四个容器健康，Scheduler
单主锁精确为 1，长期 `idle in transaction` 为 0。

## 3. 离线验证

- Python 全量：`272 passed / 5 skipped / 0 failed`；
- Ruff：通过；
- mypy：34 个源文件通过；
- compileall：通过；
- Control-plane / GPU-node Compose：通过；
- Web Vitest：`10 passed`，lint 与 production build 通过；
- 六 API synthetic fixture：Blender 5.1.2 离线生成、BLEND/FBX 回读、UV/对象名、ZIP manifest、
  SHA-256 和完整清单校验通过；
- 上述验证均未向生产 API 发送压测请求。

## 4. 生产优先与压测安全边界

Asset 与 Substance 的下一次 claim 现在始终先取 production，再取 test；各池内部继续按
`created_at,id` FIFO。GPU 调度原有 production-first 保持不变。

六 API 压测仍以“开始前 GPU/Asset 活动任务均为 0”为第一门禁。运行期间 watchdog 每 5 秒枚举
GPU/Asset 活动任务；发现不属于本次精确 test tenant 清单的工作即停止增压并退出，只清理本 session
登记的测试任务。已运行的测试工作不是可抢占任务，因此 watchdog 不能替代 zero-work 门禁。

本轮 synthetic fixture 根目录：
`/tmp/gpu-control-load-fixtures/synthetic-six-api-20260730-r1`。fixture、provenance 与 SHA 清单哈希：

- `fixtures.yaml`: `c552ecf380db140dfe83addc27659cc312dd6f01d826fdd275401cf1a7fd4d01`
- `provenance.json`: `4e1ddb6cd9349d8580d7e103d3b379102ad6404bd0f602870900d8e6337d050e`
- `SHA256SUMS`: `2e30a9f286c2dee80236bf77e85025819a5e11a55ac5fc067b0cb0d77430c221`

真实序列帧抠图及所有 Asset 任务清零后，三节点通过受审计 API 短暂切换为 `DRAINING`。首次执行发现
full backup 门禁仍引用旧的 `nodes.status` 字段，已改为当前模型的 `nodes.health`。随后独立恢复验证又
发现 GNU tar 会把生产任务树中的硬链接保存为 link member，而恢复信任边界只允许普通文件/目录；
归档现使用 `--hard-dereference` 将硬链接展开，并增加真实 hardlink 回归夹具。两项修复均通过 23 项
backup/restore 安全回归。

最终可恢复基线为 `/srv/gpu-control/backups/20260730T111645Z-full`：前后零任务门禁、双轮 SHA-256 和
独立 `restore.sh --verify-only` 均通过，三节点随后恢复 `ACTIVE`。旧的
`20260730T110423Z-full` 完成标记已改名为 `BACKUP_INVALID_HARDLINK_MEMBERS`，两份更早失败候选也没有
`BACKUP_COMPLETE`，因此都不会被压测门禁接受。压测 plan 仅绑定最终可恢复基线，并校验工作流 SHA、
三节点健康、test client、CA、结果目录及确认令牌，然后按
`1 → 10 → 25 → 50 → 100 → 120` 用户升压。

## 5. 回滚

若 advisory 造成非预期行为：

1. 等 Asset 活动任务为 0；
2. 设置 `RETOPOLOGY_QA_ENFORCEMENT=strict`；
3. 使用当前 `unified-scheduler-asset-api:1.5.6` 只重建 Asset API，使配置回到 strict，但保留 1.5.6
   的并发、artifact publish 和 Substance fence 修复；
4. 验证健康、版本、三台 Asset Worker 心跳以及新的严格模式失败/诊断下载合同；
5. 不重启 GPU API、Scheduler、GPU Worker 或外部 Retopology Skill。

只有需要完整版本回滚时才使用 1.5.5 rollback tag；此操作必须先按 74、75 号记录清理 1.5.6 的
Substance pending/fence/recovery 标签，并回滚四个控制面组件，不能把“QA 配置回滚”与“版本回滚”
混为一谈。

## 6. 当前交付与后续证据

- 首个生产 advisory 任务 `0554fcf1-50ae-40de-bc15-fdbcdd3805d7` 已以 `SUCCEEDED` 完成，
  `qa_warning=RETOPOLOGY_QUALITY_GATE_WARNING`；随后发现动画管家按 V5 正式 kind 过滤，旧响应中的
  `candidate_blend/candidate_fbx` 会落入诊断区。已把该任务的同一不可变字节发布为
  `blend/retopology_final.blend`（14,798,992 bytes，SHA-256
  `0dd443337087e30bb1fd2929cf6715c82460a1bb13b7c43c745c89a2c0757f6f`）和
  `fbx/retopology_final.fbx`（95,692 bytes，SHA-256
  `8d254b5f3aaea5f13b73b2b2f1bf9b2ed2147e6fac7f6bc0c69f014ab81058f7`）。永久实现会在
  strict 通过或 advisory 接受时自动发布这两个正式 kind；strict 失败仍保留 candidate 诊断语义。
  该永久实现起始于历史 `7fc7efd` 热修复，现已包含在生产 1.5.6 的 `310a44c` 镜像中并通过健康、
  版本和真实字节下载检查；
- 六 API r5 已完成到 120 VU，控制面窗口内稳定，但生命周期、同步 Roughness submit 口径和
  遥测完整性三项门禁 fail closed；原始报告摘要、节点利用率、正式 FBX/BLEND 验证、清场和
  未部署修复边界见 `73_2026-07-30_SIX_API_120VU_LOAD_RESULT.md`；
- 测试 client 禁用与压测后完整备份/恢复点。

## 7. r7 复测补记

1.5.6 上的 `sixapi-20260730-r7` 再次覆盖 Retopology process，并保持正式
`blend/retopology_final.blend`、`fbx/retopology_final.fbx` 与诊断产物并存。整轮六 API 共
`39,776` 次 HTTP 请求且无失败，业务阈值、生命周期、作用域恢复和清场均通过；唯一 fail-closed 项是
遥测相邻样本最大间隔 `7,699 ms`，比 `7,500 ms` 严格上限多 `199 ms`。关闭顺序已在源码
`682b2c3` 修复，但该代码修复本身不能冒充一轮新的通过结果。完整 r5/r7 数据见
`73_2026-07-30_SIX_API_120VU_LOAD_RESULT.md`。

## 8. R8 正式交付复测

独立会话 `sixapi-20260730-r8` 再次真实接纳 `10` 个 `retopology_process` 任务；`10/10` 均以
`SUCCEEDED` 完成，每个任务下载并校验 `23` 个产物，合计 `230` 次 artifact download，无 HTTP、业务
或产物合同失败。该结果继续验证 advisory 模式会保留诊断证据，同时把可用的 BLEND/FBX 作为正式
artifact 交付，不再因几何质量告警只返回日志。

R8 全轮共 `39,778` 个 HTTP 请求、0 失败，并以进程退出码 0 通过六 API、七项阈值、生命周期、遥测
和清场门禁；持久原始证据与 SHA 见
`76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md`。这仍不放宽 0 字节、SHA/size、manifest、输入身份
和源对象保护等硬门禁，也不替代动画管家固定 B 系列速度与质量联合验收。
