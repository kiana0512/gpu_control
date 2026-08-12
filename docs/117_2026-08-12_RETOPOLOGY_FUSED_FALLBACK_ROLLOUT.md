# 2026-08-12 自动拓扑融合网格安全回退发布记录

## 范围

本次只更新 GPU Control 的 Direct V2 自动拓扑包、Asset API 和三台 Blender Worker。
ImageClip、ModelViewCreator、ComfyUI 工作流、模型参数及 Windows Substance Baker 均未修改。

## 修复内容

- 对布料覆盖木堆等导出后成为单一连续面的模型，分类邻接若确认无法安全拆分，禁止继续按法线或空间位置裁切面。
- 改为从只读 `SOURCE_HIGH` 的新副本执行一次 50% 受控减面，保留导出缝、硬部件边界和整体轮廓。
- 不生成或修改 UV，不执行方向审查，不执行 FBX 重新导入；交付门禁仍仅为无退化/零面积破面和有限坐标。
- 补齐一键入口的合并技能文件白名单，使新增的 `controlled_reduction_fallback.py` 能被运行时使用。

## 发布身份

- 部署源码：`68c9057328c75e4ffcc5edd64f5bf2feee7602c7`
- Direct V2 包：`3.0.24`
- 包清单 SHA-256：`e6bdc11ded860cfc71381af3eed16b2db2df424393779e84b1da7c548672d169`
- Blender Worker：`1.4.46-retopo-fused-fallback-v1`
- Worker 镜像 ID：`sha256:5dc488fa898332684612d9435dcdd4d4d580081346bae72bbd9076bc659ced4f`
- Asset API：`1.6.46-retopo-fused-fallback-v1`

## 滚动部署

发布前确认活动 Asset 作业为 0；随后仅滚动替换 `asset-api` 和三台 `blender-worker`。
`asset-control-4090`、`asset-worker-3090-a`、`asset-worker-3090-b` 最终均为
`ONLINE / 0 jobs / asset-skills-auto-retopo-align-v3.0.24`，远端 Worker 镜像 ID 与源码 revision 一致。

## 真实回归

回归任务：`5205ef28-5103-49e9-92e5-55c6a8c22f8f`，执行节点 `asset-worker-3090-b`。

- 终态：`SUCCEEDED`，API 记录执行耗时 126 秒；
- 高模：99,992 面；交付低模：49,996 面；
- 方法：`controlled_direct_reduction`，只执行一次固定回退脚本；
- 低模 UV 层：0，未生成或修改 UV；
- 退化面：0，重复面：0，非有限坐标：0；
- 坐标矩阵误差：0；中心误差比约 `6.99e-9`；尺寸误差比约 `0.0001937`；
- 方向审查：未执行；FBX 回读：未执行；交付门禁：`no_broken_faces`；
- 人工查看高低模渲染：布料、木堆和整体外轮廓保持一致，未再出现巨块吞并、长刺或撕裂。

发布过程中首次回归在建模前被旧入口白名单拒绝；修正白名单并更新包清单后重新发布，以上真实回归通过。
