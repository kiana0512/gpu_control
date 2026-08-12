# 自动拓扑快速交付与可选 UV 原样保留发布记录（2026-08-12）

## 1. 最终交付策略

本次只修改 Direct V2 自动拓扑生成低模链路，不修改 ImageClip、ModelViewCreator、Substance Baker、
独立 UV 服务或外部高低模纯变换对齐入口。

- 自动拓扑不创建、删除、重展、打包或修改 UV。
- 低模已有 UV 时必须原样保留；没有 UV 时同样允许交付，不在拓扑阶段补 UV。
- 硬门禁为 `no_broken_faces`：低模非空、顶点坐标有限、低模面数少于高模、零面积/退化面为 0。
- 开放边、非流形边、游离点边、重复点面和面朝向继续实测并写入诊断，但不阻止交付。
- 取消自动拓扑交付的七方向渲染和新鲜 FBX 重新导入；仍保留高模只读、源坐标恢复、保存后 Blend
  指纹、FBX 导出与 SHA-256、一次有界重试和单调进度。

## 2. 代码、包与测试

| 项目 | 结果 |
|---|---|
| 功能提交 | `546992746ea025fd5702630dddaf62291a481d0f`，已推送 `origin/main` |
| Direct V2 包 | `3.0.13` |
| 包清单 SHA-256 | `b5f96ee6f0201fe31eaa5018dd341dc924d458f88027f9c5fcd631b5c8dbcfe7` |
| 包自检 | `ok=true`，12 个技能文件，单文件/批量/外部低模三个入口完整 |
| Python 单元测试 | `375 passed, 4 skipped` |
| Direct V2 API/重试集成测试 | `6 passed` |
| 最新 UV 策略专项 | `29 passed` |
| Ruff / 编译 / diff | 通过 |

Blender 5.1.2 隔离实测使用同一低模的两个已有 UV 层执行坐标恢复、保存 Blend 与 FBX 导出：

- `uv_policy=preserve_optional`；
- generated Blend 与保存后 Blend 都实测 `uv_layers=2`；
- 保存前、变换应用后、保存后 Blend 回读的 `uv_hash` 完全一致；
- `topology_uv_unchanged=true`、`fbx_exported=true`。

## 3. 镜像与离线包

| 项目 | 生产证据 |
|---|---|
| Asset API | `unified-scheduler-asset-api:1.6.35-retopo-fast-delivery-v1`；image ID `sha256:e7bb293bf39e5fcce871185d15f434b39424d0a7c2372476278aa898999b50cd` |
| Blender Worker | `li3d/blender-worker:1.4.35-retopo-fast-delivery-v1`；image ID `sha256:84c3bf52de5c1da2c710f177c79e975e186f5c9844f13f5a5ce6ca8e36154771` |
| Asset API 离线包 | `/srv/gpu-control/images/retopo-fast-delivery-v1-5469927/asset-api.tar.zst`；92,819,669 bytes；SHA-256 `3c49ca92985111943c6af97e7b2967f6dc1f6b13990d8c9ab1e70ab2c8d1d75d` |
| Worker 离线包 | `/srv/gpu-control/images/retopo-fast-delivery-v1-5469927/blender-worker.tar.zst`；690,934,544 bytes；SHA-256 `cc75f9ab5778a7d0b6a8069b89630c1ab9cb290498dc026d500f86f2867aa573` |

两个镜像的 OCI version 与 revision 标签均已核对；两台 3090 在加载前重新计算 Worker 归档 SHA，
加载后的 image ID 与 4090 完全一致。

## 4. 安全滚动与生产状态

发布严格按 `control-4090 -> worker-3090-a -> worker-3090-b` 执行：

1. 管理 API 将目标节点置为 `DRAINING`；
2. 数据库再次确认节点、Linux Worker、3090-B 四个 Windows Baker 槽和全部活动资产任务为 0；
3. 只重建 Asset API 或对应 Blender Worker；
4. 校验镜像、包自检、Codex 认证/探针和 RetopoFlow 探针；
5. 恢复节点为 `ACTIVE`。

发布后结果：

- `control-4090`、`worker-3090-a`、`worker-3090-b` 均为 `ACTIVE / ONLINE / 0 jobs`；
- 三个 Linux Worker 均为 `ONLINE`、技能 `asset-skills-auto-retopo-align-v3.0.13`、
  `AUTHENTICATED / HEALTHY / RetopoFlow HEALTHY`；
- Asset API `/health/live` 返回 `status=live`；
- 3090-A 与 3090-B 的 ComfyUI 容器 ID、启动时间和 `RestartCount=0` 在替换前后完全不变；
- 没有停止、重启或修改外部 ComfyUI 工作流、模型、节点参数和输出语义。

