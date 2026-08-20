# 自动 UV 后台判定与 Windows 原生 MOF 路由

## 结论

`POST /api/v1/assets/uv/process` 在调用方未指定 `options.algorithm` 时，现由后台自动判定：

- 单网格、多结构块、曲面证据强且没有明显硬表面特征：`mof_low_seam`；
- 硬表面、简单结构、多个网格对象、带 Modifier/Shape Key 或证据不足：`legacy_pbr`；
- 显式 `legacy_pbr` / `mof_low_seam` 仍保持原有语义；
- 不允许只凭面数启用 MOF，也不允许 MOF 不可用时静默回落原版。

MOF 展开继续由 `10.3.34.238` 上的 Windows 原生 PowerShell Agent、Windows Blender 5.2
和已授权 MinistryOfFlat 执行。控制与执行路径均不经过 WSL2。

## 判定证据

`uv-auto-classifier-v1` 联合使用：

- Mesh 对象数、面/点/边、manifold/boundary/non-manifold 边；
- 面连通分块数；
- 平滑面、手工 sharp 边；
- 近共面、曲面、陡折和极陡折边比例；
- Modifier 和 Shape Key 数量。

MOF 必须同时满足单网格、多面连通块、足够结构证据、复杂结构和强曲面证据，并通过硬表面
排除门。服务端使用同一纯函数重新计算 Worker 上报证据；版本、算法、资产类型或原因码任一
不一致，均以 `UV_AUTO_CLASSIFICATION_MISMATCH` 拒绝。

滚动兼容由 Worker 能力声明保证：只有声明 `uv_algorithms=[legacy_pbr, auto]` 的新 Worker
可领取 `auto` 预检。旧 Worker 只能继续领取显式原版任务，不会误执行 `auto`。

## 生产镜像

```text
asset_api_image: unified-scheduler-asset-api:1.5.16-uv-auto-routing-v1
asset_api_image_id: sha256:20d52767b08534f16114711deabdeaa868c99c45a6219e84a3afab530976f8cf
linux_worker_image: li3d/blender-worker:1.4.54-uv-auto-routing-v1
linux_worker_image_id: sha256:8dfcf25ee8d82086e719483aa38f1cc1ef5b7e6e62fa64069aff1a6fa00de69d
classifier_version: uv-auto-classifier-v1
classifier_script_sha256: 8984d8b4370807dcc7d80768f7a3ce2d58a943aa867fdfaa4cb5e01e4aaf55f8
windows_mof_worker: asset-worker-4070ti-mof-01
windows_mof_skill: mof-windows-native-1.0.9-2026.08.18-v2
```

生产 Asset API 与 `asset-control-4090` 分类 Worker 已切换；其余旧 Linux Worker 通过能力门禁
保持兼容，不能领取自动分类任务。Windows MOF Worker 保持原生部署且未改为 WSL 控制。

## 真实 `44.fbx` canary

正式 HTTPS API 请求未传算法，返回初始 `algorithm=auto / asset_profile=auto`。事件链为：

```text
asset-control-4090
  -> UV_CLASSIFYING
  -> UV_MOF_QUEUED
  -> asset-worker-4070ti-mof-01
  -> SUCCEEDED
```

```text
job_id: b74dcbfa-25a3-4fd7-a2ec-326f6b843f4c
input: 44.fbx
result: SUCCEEDED / 100%
attempt_count: 1
resolved_algorithm: mof_low_seam
resolved_profile: complex_non_hardsurface
reason: complex_multi_component_soft_surface
created_at: 2026-08-18T07:44:25Z
finished_at: 2026-08-18T07:44:35Z
```

实际证据为 1 个 Mesh、422 面、4 个面连通块、601 条 manifold 边；曲面边比例
`0.797005`，近共面边 `0.116473`，极陡折边 `0.031614`，无 Modifier、Shape Key 或 authored
sharp 边。因此服务端确认它是复杂非硬表面，并转交 Windows 原生 MOF。

五个制品均发布且磁盘 SHA 与数据库一致：

| 制品 | SHA-256 |
|---|---|
| `44_PBR_UV.blend` | `766ca74a5f5bcbc8b92177aebb8328b0640d358c56daf81ecfd4d4417a25d590` |
| `44_PBR_UV.fbx` | `647aee72b5ca96a49ea3856281924bbe437352e8efc4135937f56a3d25ef5fcc` |
| `44_PBR_UV_report.json` | `a0269c7edad1888f98549dfcb5b312cf822654ded21f9e957efaeaf5bdf92b7e` |
| `44_PBR_UV_QA.json` | `eea4f951ef90b669103135dc4b89f08453f8b9ce9521fcd6774677ce71e27109` |
| `44_PBR_UV_FBX_QA.json` | `222a7396bbb498a28d5979e80cc43ace8f794a391982882ad0fb102f1aff412d` |

报告、Blend QA 和 FBX 回读 QA 均记录 `algorithm=mof_low_seam`；两份 QA 都是
`passed=true / hard_failures=[]`。临时 canary 客户和密钥在验收后已停用并清除明文凭据。

## 验证与安全边界

- Blender 5.1.2 容器真实导入 `44.fbx`，分类结果与线上一致；
- Ruff 与 `git diff --check` 通过；
- 自动路由、滚动能力门、MOF 重排队和硬表面继续原版的定向回归通过；
- 发布前生产资产队列为零；历史任务与历史产物未修改；
- MOF Worker 不在线时，后台判定为 MOF 的任务明确失败为
  `UV_MOF_CAPACITY_UNAVAILABLE`，不会生成与所选算法不符的产物。
