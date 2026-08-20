# 复杂多 Mesh FBX 整单 Windows 原生 MOF 路由

## 结论

自动 UV 判定已升级为 `uv-auto-classifier-v2`。一个 FBX 同时包含多个具备足够结构证据的
Mesh 时，后台把整项任务解析为：

```text
algorithm: mof_low_seam
asset_profile: complex_multi_mesh
reason: complex_multi_mesh_asset
```

任务不再给同一个 FBX 内的物体混用两套展开方法。Windows 原生 MOF Agent 只调用一次
MinistryOfFlat，随后按原始物体分组恢复，跨物体统一 Texel Density 与 0–1 排版。控制和
执行均在 Windows 原生 PowerShell / Blender 5.2 中完成，不经过 WSL2。

带 Modifier、Shape Key、结构证据不足或无法安全保留对象身份的多 Mesh 输入仍拒绝 MOF
自动路由并保守使用原版；不存在 MOF 失败后静默切换算法。

## 保留门禁

`mof_unwrap.py` 对多 Mesh 输入执行以下硬门禁：

- 每个原物体仅临时按 loose part 分离，绝不跨原物体 Join；
- 保留原 Mesh 对象数、对象名、Mesh 数据名、材质槽顺序、矩阵和几何摘要；
- 所有 face-bearing part 必须获得有效 MOF UV；
- 所有恢复后的物体一起统一密度并排版；
- 每个物体以及物体之间均要求 0 翻转、0 退化、0 越界、0 意外重叠；
- Linked Mesh datablock 当前 fail closed，不冒险破坏实例身份；
- Blend、FBX 回读、源 FBX 单位合同和五制品 SHA 仍按原门禁验收。

## 生产版本

```text
asset_api_image: unified-scheduler-asset-api:1.5.16-uv-multimesh-mof-v1
asset_api_image_id: sha256:a2cadc22072fd703103b9d978f873bb016ee1fcadf78ad600df1366ed7b319e6
linux_worker_image: li3d/blender-worker:1.4.55-uv-multimesh-mof-v1
linux_worker_image_id: sha256:9c224921b76e9f138044277bf292447b7cb163ba822d7450dd5e80a22171ae7c
classifier_version: uv-auto-classifier-v2
classifier_script_sha256: f18c6d1e359f7264f1e5c62bc8edbcb69e2243a28b4bb94401c10a3dd1e69849
mof_wrapper_sha256: 70e98027f64b4389ec1f7086bb363e5d4a7a686b9472d17fa840ecb01dbd946d
windows_mof_worker: asset-worker-4070ti-mof-01
windows_mof_skill: mof-windows-native-1.0.9-2026.08.19-v3
```

发布前生产资产队列为零。滚动发布顺序为 Asset API、Windows 原生 MOF Agent、Linux
分类 Worker。旧 Linux Worker 的 `auto` 能力不能领取 v2 预检，只有 `auto_v2` 能力可领取。
Linux Worker 首次启动因批准 Skill 仍固定旧 wrapper SHA 而被 fail-closed 门禁拦截；同步
新 Skill/Wrapper 哈希并重建后正常 ONLINE，期间没有资产任务被领取或丢失。

## 真实无人机多 Mesh canary

通过正式 HTTPS API 重新提交 `01_WRJ_M_ani.fbx`，请求未指定算法：

```text
job_id: bcc6eb36-6343-4feb-9dcc-3a2d784623a2
status: SUCCEEDED
worker: asset-worker-4070ti-mof-01
attempt_count: 1
elapsed_seconds: 80
resolved_algorithm: mof_low_seam
resolved_profile: complex_multi_mesh
```

分类证据：

```text
mesh_object_count: 2
face_count: 7744
face_component_count: 456
vertex_count: 7920
edge_count: 15241
manifold_edge_count: 7991
modifier_count: 0
shape_key_count: 0
```

MOF 实际处理两个原物体、452 个 face-bearing loose parts，插件阶段约 `72.165s`。输出仍是：

- `01_WRJ_M_ani`：7780 点 / 15018 边 / 7638 面；
- `Cardboard_Box_01_LOD1`：140 点 / 223 边 / 106 面。

每个对象的几何摘要、尺寸、矩阵和材质均与输入一致；跨对象 UV 重叠三角形对为 0。Blend
和 FBX 回读 QA 均为 `passed=true / hard_failures=[]`，两个输出 Mesh 均为 0 越界、0 翻转、
0 退化和 0 重叠，源 FBX 单位合同通过。

本次真实 MOF 结果存在明显 stretch advisory：无人机 `p90=24.46935 / p95=173.50009`，盒子
`p90=17.77215 / p95=28.47439`。它不属于当前硬失败项，因此任务按用户指定的整单 MOF
策略交付，但不能把本次结果描述为低拉伸；需要在视觉验收中重点检查贴图变形。

五个制品及 SHA：

| 制品 | SHA-256 |
|---|---|
| `01_WRJ_M_ani_PBR_UV.blend` | `a9ee08c8f7adce72fdc3b3131faa9ee929e937964f8a6ea5a958a2e6fcccb6de` |
| `01_WRJ_M_ani_PBR_UV.fbx` | `cf9a405ccbcacb13a8e65dbaf47b8bdef60981524d91dd424588acd696399cf7` |
| `01_WRJ_M_ani_PBR_UV_FBX_QA.json` | `3f7ae62a1735d12d41b54fe0b2589998ecdb17614151deaa7b57f043dcef3450` |
| `01_WRJ_M_ani_PBR_UV_QA.json` | `06a0188769ba70ec9435c45b5f01624b8281ef6b269f7bb23618be2ee44d050f` |
| `01_WRJ_M_ani_PBR_UV_report.json` | `ed8d3762c4dff48006ce593831f1c70524536f00afe311013b60f7094c6ccb07` |

## 验证

- 真实 Blender 5.2 导入原始 FBX，v2 分类结果为 `complex_multi_mesh / mof_low_seam`；
- 本机结构算子验证 2 Mesh 输入/输出边界、几何、材质、统一排版和跨对象重叠门禁；
- Python 3.11 后端回归：`521 passed, 5 skipped`；
- Ruff、Python 语法检查和 `git diff --check` 通过；
- Windows 原生 Agent 为 `ONLINE / current_jobs=0`，Skill 版本精确匹配；
- Linux `asset-control-4090` 为 `ONLINE / current_jobs=0`，新镜像无重启循环。
