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

## 2. 版本与部署证据

| 项目 | 值 |
|---|---|
| source commit | `7fc7efd4f6e88283f4195b1bf07ba2b5b076429a` |
| Asset API image | `unified-scheduler-asset-api:1.5.5-final-artifacts-7fc7efd` |
| Asset API image ID | `sha256:b9e87663db3177c0a65a71b9e34589f76ba8536658c0360b52ef16f254eb8151` |
| OCI version / revision | `1.5.5` / `7fc7efd4f6e88283f4195b1bf07ba2b5b076429a` |
| rollback tag | `unified-scheduler-asset-api:rollback-pre-retopo-advisory-20260730` |
| rollback image ID | `sha256:827053b49248ea22296fb3b78fb3012f1a158577f34921b30dcf140567ce0c3d` |
| production mode | `advisory` |

两次部署前均确认 Asset 活动任务为 0；只执行 Asset API 单服务滚动。API、Scheduler、GPU Worker、
Asset Worker、ComfyUI 和数据库均未重启。部署后 `/health/live` 返回 live，版本端点返回
`build_version=1.5.5`、`version_aligned=true`、`provenance_complete=true`，容器健康检查通过，三台
Asset Worker 心跳在线。

## 3. 离线验证

- Python 全量：`199 passed`；
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

真实序列帧抠图及所有 Asset 任务清零后，三节点通过受审计 API 短暂切换为 `DRAINING`，已创建
`/srv/gpu-control/backups/20260730T110423Z-full` 完整备份并通过脚本内 SHA-256 校验；完成后节点已
恢复 `ACTIVE`。首次执行同时发现 full backup 门禁仍引用旧的 `nodes.status` 字段，已改为当前模型的
`nodes.health` 并通过 23 项 backup/restore 安全回归；两份失败候选均没有 `BACKUP_COMPLETE`，不会被
压测门禁接受。独立 `restore.sh --verify-only` 通过后，再由 plan 校验工作流 SHA、三节点健康、test
client、CA、结果目录及确认令牌，并按
`1 → 10 → 25 → 50 → 100 → 120` 用户升压。

## 5. 回滚

若 advisory 造成非预期行为：

1. 等 Asset 活动任务为 0；
2. 设置 `RETOPOLOGY_QA_ENFORCEMENT=strict`；
3. 使用本记录中的 rollback tag 只重建 Asset API；
4. 验证健康、版本、三台 Asset Worker 心跳以及新的严格模式失败/诊断下载合同；
5. 不重启 GPU API、Scheduler、GPU Worker 或外部 Retopology Skill。

## 6. 当前交付与待补最终证据

- 首个生产 advisory 任务 `0554fcf1-50ae-40de-bc15-fdbcdd3805d7` 已以 `SUCCEEDED` 完成，
  `qa_warning=RETOPOLOGY_QUALITY_GATE_WARNING`；随后发现动画管家按 V5 正式 kind 过滤，旧响应中的
  `candidate_blend/candidate_fbx` 会落入诊断区。已把该任务的同一不可变字节发布为
  `blend/retopology_final.blend`（14,798,992 bytes，SHA-256
  `0dd443337087e30bb1fd2929cf6715c82460a1bb13b7c43c745c89a2c0757f6f`）和
  `fbx/retopology_final.fbx`（95,692 bytes，SHA-256
  `8d254b5f3aaea5f13b73b2b2f1bf9b2ed2147e6fac7f6bc0c69f014ab81058f7`）。永久实现会在
  strict 通过或 advisory 接受时自动发布这两个正式 kind；strict 失败仍保留 candidate 诊断语义。
  该永久实现已随本记录中的 `7fc7efd` 镜像上线并通过健康检查；
- 六 API 最大压测原始报告、阈值判定、节点利用率、调度分布及故障/修复闭环；
- 测试 client 禁用与压测后完整备份/恢复点。
