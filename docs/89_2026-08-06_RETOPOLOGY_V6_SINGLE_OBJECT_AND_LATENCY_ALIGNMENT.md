# 2026-08-06 自动拓扑 V6 单对象交付与延迟对齐

## 结论

- 当前 `/api/v1/assets/retopology/process` 新任务固定使用 `retopology-v6`，不会按正常入口回落到旧 Direct V2。
- 三个生产节点挂载的 V6 Skill、正式 Agent prompt 和 policy 与 2026-08-06 批准内容逐字节一致。
- `retopology-direct-v2` 包仍保留在 Worker 镜像中用于历史任务/回滚兼容；它不是 V6 新任务的活动执行器。
- Worker `1.4.0-retopology-v6-merged` 在正式 Agent 生成后执行确定性的交付合并：一个 Blender/FBX 对象，机械组件仍是互不焊接的 disconnected mesh islands。
- 合并不执行 Decimate、Remesh、Boolean、Merge by Distance，也不改变顶点、边和面的数量。

## 冻结身份

| 资源 | SHA-256 |
| --- | --- |
| V6 Skill `SKILL.md` | `1b6519d3b725e89ca3beccaf5bc1de8dc5d3a1163b4dc2ce59cb5d0a277a61cf` |
| 正式 Agent prompt | `9174535a915e1075ef08346e03f5e3580f7d81f68c08eb613983e09f76c1728c` |
| V6 policy | `e6781d6158a93e571c944f5913a600838fe28fc2edc38a3b1909f649f66f3d3d` |
| 单对象交付脚本 | `ccde46b64203c9f9d11895b6d6bb208ac8074aa0d4aeec4f216210c88006008f` |

## 单对象交付合同

最终 `final_low.blend` 与 `final_low.fbx` 只导出一个名为
`LI3D_<job_id>_GAME_LOW` 的 mesh object。该对象保留：

- 所有正式低模组件的世界空间位置；
- 组件之间的开口、间距和负空间；
- 材质槽和实际网格；
- 原组件名称列表（Blender custom property）；
- 合并前后的对象数以及 vertex/edge/polygon 计数证据。

这里的“合并”是对象级 join，不是把把手、轴销、桶身焊成一个连续流形。下游应用可把它作为一个模型对象加载，同时不会因错误焊接而破坏机械结构。

已用任务 `a3cbd46b-0b32-4d64-8cf1-f2594a479e15` 的完成制品副本执行验证：

| 指标 | 合并前 | 合并后 |
| --- | ---: | ---: |
| Object | 7 | 1 |
| Vertex | 773 | 773 |
| Edge | 1520 | 1520 |
| Polygon | 758 | 758 |

FBX 重导出成功，合并阶段耗时约 2 秒。

## 八分钟耗时分析

该任务创建至开始约 0.2 秒，正式 V6 Agent 构建约 8 分 15 秒；瓶颈不是排队、上传或 GPU 调度。Agent 事件显示：

- 完整读取 Skill 与四份训练/生产参考；
- 输入 token 约 217 万，其中约 206 万命中 prompt cache；
- 执行 3 次 `build_low.py`；
- 执行 3 次补渲染；
- 最后再执行拓扑流审计和制品整理。

因此单任务延迟主要来自 Agent 多轮工具调用和重复构建/渲染。Worker 1.4.0 在不改变 Skill 算法的前提下明确执行上限：一次 inventory/plan、一次正式 build、一次 render/audit；只有首个构建命令失败或必需制品缺失时允许一次有证据的纠正，禁止无原因的试探性重建。

进度预计也已与 7200 秒硬超时拆开：正式构建初始 ETA 为 360 秒，独立 QA 为 180 秒，页面不再把最坏超时显示成“剩余 118 分钟”。

## 后续可选加速（不在本次自动启用）

1. 按 `source_sha256 + policy_sha256 + Blender version` 缓存只读高模 inventory 与七视图；重复提交同一高模时复用分析证据。
2. 恢复 4090 和 3090-B 的 Codex 认证，使三个 V6 Agent Worker 可并行接不同用户任务；这提高吞吐，不直接缩短单个模型的构建时间。
3. 为已验证的简单机械类别建立 V6 确定性构造模板，但这属于算法语义扩展，必须另行冻结样本、质量门和新 policy SHA 后才能上线。

不得以回退整体 Decimate、减少必要轮廓控制、返回中间预览或跳过最终 BLEND/FBX 的方式换取速度。

## 安全部署门禁

- 构建镜像不需要停止生产服务。
- 替换 Worker 前必须确认目标 Worker 没有 `RUNNING` 任务并先进入 draining。
- 2026-08-06 本次检查时 3090-A 有真实 V6 任务运行，禁止在该任务结束前替换该节点。
- 新镜像必须再次验证 V6 Skill/prompt/policy SHA 与本文一致，验证单对象脚本 SHA，并在空闲节点逐台滚动。
