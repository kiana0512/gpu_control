# 2026-08-06 自动拓扑 V6 结构化重拓扑热修

## 结论

生产任务 `019b388f-6341-4ad6-8ac0-be429f96f957` 的 `execution_plan.json`
明确选择了 `controlled_direct_reduction`。其交付虽然保持单对象 BLEND/FBX，但布线门禁同时报告
细长三角、异常长宽比、高点价与未证明的自适应密度。这证明 V6.0.0 中“复杂一体资产允许受控直接减面”
仍会把高模密度坍缩误当成重拓扑，不能继续作为生产方法。

V6.0.1 已撤销全部直接减面例外。机械、有机一体和混合资产均必须执行结构化重拓扑。

## 新生产不变量

- 正式计划只接受 `semantic_reconstruction` 或 `hybrid_per_component`。
- 组件不再接受 `controlled_direct_reduction`。
- 一体有机件使用 RetopoFlow 辅助绘制、轮廓环、局部 cage/patch 与有界 Shrinkwrap 拟合。
- Decimate、voxel remesh、QuadriFlow remesh 和同类密度坍缩只能作为一次性诊断，生成网格及其清理派生物不能交付。
- Worker 在 Schema 验证后再次检查计划方法，并扫描 Agent 生成的 Blender Python 脚本；发现禁止生成器即以
  `RETOPOLOGY_V6_DIRECT_REDUCTION_FORBIDDEN` 失败关闭。
- Worker 必须收到当前 policy SHA；旧策略排队任务以
  `RETOPOLOGY_V6_POLICY_SUPERSEDED` 拒绝，不能在新 Worker 上伪装成新策略执行。
- 高模继续保持只读和 SHA 不变；最终仍是一个 BLEND/FBX mesh object，原组件保留为 disconnected islands。

## 冻结身份

| 项目 | 值 |
| --- | --- |
| Policy | `li3d-retopology-v6@6.0.1` |
| Policy SHA-256 | `e7b24c93c11d550ac9fedd167ff23f9ddd70cba4db014caaf2e157cddeafb266` |
| Worker image | `li3d/blender-worker:1.4.1-retopology-v6-structured` |
| Asset API image | `unified-scheduler-asset-api:1.6.7-retopology-v6-structured` |

其他 Skill、prompt、contract 与参考资料的逐文件 SHA 以
`resources/retopology-v6/RUNTIME_FILES.sha256` 为唯一运行时清单。

## 独立 QA Schema 修复

V6.0.0 的 result schema 允许 `metrics` 任意对象，不能被严格 Structured Outputs 接受，导致独立 QA
在模型调用前返回 `invalid_json_schema`。V6.0.1 将 result 中每个 gate 的 `metrics` 固定为必需的
`summary` 字符串，完整定量数据继续写入 `qa_report.json`。服务端继续独立校验 job、policy、source、
artifact SHA 和 publish/gate 一致性。

## 队列与滚动保护

上线时发现 3090-A 有一条 V6.0.0 真实任务运行，另有两条旧策略任务排队。为避免打断正在运行任务，
先让它使用旧 API/Worker 完成回执；两条排队任务暂存为 `PAUSED_ROLLOUT`，禁止在切换窗口继续进入旧
直接减面路径。新策略上线后，旧策略任务必须重新提交以获得新的 policy identity，不能原地篡改输入包
manifest、请求哈希或审计身份。

## 验证要求

- 镜像构建阶段必须通过 `verify_runtime_resources()`，19 个冻结文件逐项匹配 SHA。
- 结构化计划正例必须通过；`controlled_direct_reduction` 计划与包含 `DECIMATE` 的 Agent 脚本必须失败关闭。
- Asset API 必须返回 `asset-skills-retopology-v6.0.1` 和新 policy SHA。
- 替换 Worker 前必须确认该 Worker `current_jobs=0`；只替换 Asset API/Blender Worker，不重启
  Scheduler、Web、ComfyUI、GPU 推理或 Windows Substance。
- 用户重新提交一个代表性模型后，检查 `execution_plan.method`、组件方法、Agent 脚本证据、七视图、
  线框与最终 BLEND/FBX，确认不含直接减面路径。

## 生产滚动回执

2026-08-06 16:10–16:24（Asia/Singapore）完成以下滚动：

| 项目 | 实际值 |
| --- | --- |
| Asset API image ID | `sha256:46efb8871362c289a2d86c6415a49a7832dc00e38b19f1689fd30a57decaa8e7` |
| Worker image ID | `sha256:51841477fc04f6e7613e8521806e19cbbb7cb8ac0d3038b133dd52fda2d1a8db` |
| 已更新 | Asset API、`asset-control-4090`、`asset-worker-3090-a` |
| 未触碰 | Scheduler、Web、ComfyUI、GPU 推理、Windows Substance |
| 3090-A | `ONLINE`、`AUTHENTICATED`、`HEALTHY`、Skill `v6.0.1`、0 任务 |
| 4090 | `ONLINE`、Skill `v6.0.1`、0 任务；Codex 认证仍为 `EXPIRED/FAILED` |
| 3090-B | 仍为 Skill `v6.0.0`；Codex 认证 `EXPIRED/FAILED`，不会领取 V6 Agent 任务；SSH 公钥认证未开放，未强行变更 |

4090 与 3090-A 容器内实测一致：

- policy SHA：`e7b24c93c11d550ac9fedd167ff23f9ddd70cba4db014caaf2e157cddeafb266`
- formal prompt SHA：`fa52a4148721bff360780a164c2cc9979a2c2185a442fc4c92b21c6e7763b2b2`
- Skill SHA：`de51c6697d531bcc39fe30207cbf0e39f6fd21f6b88da0a9ea06cee3406faad6`

滚动过程中 Bootstrap 正确拒绝过一次“新挂载 Skill + 旧信任根”组合；在 Worker 未接单的窗口更新
`APPROVED_SKILL_FILE_SHA256` 后重新构建。最终 4090 与 3090-A 均为 `running`、`RestartCount=0`。
两条切换前创建且绑定 V6.0.0 policy SHA 的排队任务已明确结束为
`RETOPOLOGY_V6_POLICY_SUPERSEDED`，没有执行旧直接减面；客户端需以新 external asset ID 重提。
