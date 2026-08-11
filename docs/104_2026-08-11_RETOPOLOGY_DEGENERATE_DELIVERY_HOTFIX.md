# 自动拓扑交付退化几何热修复（2026-08-11）

## 1. 用户可见故障与根因

Li3D 自动拓扑任务 `437c4d37-047d-4963-a1cf-fa24926dbca5` 在 98% 失败，完整 Blender 错误为：

```text
RuntimeError: UV-prepared low has invalid geometry
```

该任务的 Direct V2 拓扑生成、Codex 调用和初始七方向渲染均已完成。失败点位于拓扑完成后的
`blender_retopology_bake_postprocess.py`：交付低模在 UV 准备后仍含零面积/退化面，严格几何门禁拒绝
发布。因此这不是 Worker 离线、Codex 认证失效或拓扑请求未执行。

## 2. 修复范围

新增 `cleanup_delivery_degenerate_geometry()`，只允许作用于从原低模复制出的 `BAKE_LOW` 交付副本：

1. 坐标非有限值继续直接失败，不猜测修复；
2. 没有退化面时严格 no-op；
3. 有退化面时，只消解数值零长度边，删除仍低于同一尺度面积阈值的零面积面；
4. 只清理由上述删除产生的悬空边和悬空点，并重算法线；
5. UV 前检查一次；UV 步骤若引入退化面，再进行一次相同的有界清理；
6. 每次清理的前后顶点、边、面、退化面及删除数量写入正式对齐报告。

明确禁止合并邻近点、重网格、减面、按高模重建、镜像或非等比缩放。原始高模和原始低模仍保留、
隐藏且以指纹证明未改变；正常低模的拓扑、材质和有效 UV 不变。后续纯变换对齐、七方向检查、视觉
QA、低模面数/UV 门禁以及 FBX 全新场景回读仍全部执行。

## 3. 测试证据

- Python 3.11 专项：`23 passed, 3 skipped`；跳过项仅为非 Blender Python 环境没有 Blender NumPy。
- Ruff、Python compile 和 Git diff 检查通过。
- Blender `5.1.2` 内核合成用例：1 个正常三角面与 1 个共线退化面输入，结果仅删除 1 个退化面，
  正常面和有效 UV 保留，`degenerate_faces: 1 -> 0`。
- Blender `5.1.2` 正常模型 no-op 用例：清理前后 mesh/UV SHA 指纹完全相同。
- 镜像内 Direct V2 包校验通过；镜像内脚本 SHA 与 Worker 固定白名单一致。

## 4. 发布证据

| 项目 | 结果 |
|---|---|
| Git 提交 | `98b66d957536f0215d631377da3920c4b5c6409f`，已推送 `origin/main` |
| Worker 版本 | `1.4.16-retopology-degenerate-cleanup-v1` |
| 镜像 ID | `sha256:dd9cff402f5d13d26ab6829e9bc74269a791850b55d12555e330b5e672835b50` |
| 后处理脚本 SHA-256 | `917ca181f7239dc82c24b205bdb68d7babd223daa621b550f41340a55c8f680b` |
| 三节点 | `control-4090`、`worker-3090-a`、`worker-3090-b` 均已滚动并恢复 `ACTIVE` |
| 三 Worker | 均为 `ONLINE / AUTHENTICATED / HEALTHY`，新 `agent_instance_id` |
| ComfyUI | 三台均未重启，外部 ImageClip/ModelViewCreator 未修改 |
| 离线镜像 | `/srv/gpu-control/images/retopology-degenerate-cleanup-v1-98b66d9/` |
| 归档大小 | `688153069` bytes |
| 归档 SHA-256 | `9d4e16f7b4d13b655aa37c3035742704e21f2242abb5aa27d34bfbb298928cf3` |

滚动更新严格逐台 `DRAINING -> current_jobs=0 -> 只替换 Blender Worker -> 新鲜心跳与探针 -> ACTIVE`。
更新期间 3090-A 的真实拓扑任务自然完成后才替换；3090-B 的第一次尝试因独立视觉 QA 判定轮廓/尺度/
部件位置不匹配而正常拒绝，并自动转交已更新的 4090 Worker 进行第二次尝试；第二次生成的低模仍因
表面误差 `0.133956 > 0.070` 和尺寸误差 `0.247981 > 0.100` 被拒绝，没有中断或伪造成功。

原退化面失败任务 `437c4d37-047d-4963-a1cf-fa24926dbca5` 还执行了一次输入 SHA 不变的管理员确认
重试。重试不再出现 `UV-prepared low has invalid geometry`，因此确认 `1.4.16` 已覆盖原故障点；新生成
低模随后通过中心与尺寸门禁，但表面误差为 `0.0831222`，高于严格上限 `0.070`，最终仍以
`RETOPOLOGY_QA_FAILED` 拒绝。该结果说明退化几何修复已生效，但本次随机生成的低模仍不是可安全交付
的同形模型；系统没有放宽门禁，也没有发布正式 BLEND/FBX。

## 5. 当前限制

该修复只处理可安全证明为零面积的无效交付几何，不会把视觉不匹配、不同模型、错误镜像、长刺、
穿插、无 UV 或 FBX 回读不一致强行放行。此类结果仍会失败并保留诊断，这是预期的质量保护行为。

如果 Direct V2 生成的低模本身与高模形状不同，纯平移、旋转、轴向和整体缩放无法修复；必须由拓扑
生成阶段产生新的同形低模。批准的 Direct V2 `2.3.0` 技能包、模型和单次构建语义本次未被修改。

用户已取消压力测试，本次热修复未启动合成压测流量。
