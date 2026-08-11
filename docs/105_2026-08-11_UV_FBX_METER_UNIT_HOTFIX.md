# 拓扑低模 UV 后 FBX 米制单位热修复（2026-08-11）

## 1. 用户可见故障

Li3D 自动拓扑已经成功生成并对齐低模，但低模继续经过 UV 服务后，在一键烘焙页面显示：

```text
高低模不匹配：尺寸差 9838.4%，中心偏移 3.3%，轮廓比例差 0.5%
```

对应生产链路：

- 拓扑任务：`d7fdc2e9-8d26-4947-bf8b-7538f9850ddf`，状态 `SUCCEEDED`；
- UV 任务：`24c999b1-18cb-40b4-8802-1c6a1810c8a9`，状态 `SUCCEEDED`；
- 拓扑低模：`UnitScaleFactor=100 / OriginalUnitScaleFactor=100`；
- UV 后低模：`UnitScaleFactor=1 / OriginalUnitScaleFactor=1`。

拓扑交付高低模的 Blender 全新 FBX 回读尺寸约为 `1.05 x 1.89 x 1.50 m`，本身已经对齐。问题发生在
批准 UV Skill 的默认 `FBX_SCALE_NONE` 导出边界：它把米制场景写成厘米制 FBX。Blender 会根据元数据
补偿该差异，但浏览器加载器直接使用原始坐标，因此低模看起来约大 100 倍。

## 2. 修复范围

新增 GPU Control 交付适配器 `blender_uv_fbx_units.py`，在 UV Skill 已生成 Blend 和 FBX 后执行：

1. 只打开新生成的 UV Blend，不修改或保存它；
2. 只选择其中非空 Mesh，临时处理共享网格的 FBX 材质序列化；
3. 使用 `METRIC / METERS / FBX_SCALE_UNITS` 重新导出交付 FBX；
4. 强制验证 `UnitScaleFactor=100` 和 `OriginalUnitScaleFactor=100`；
5. 清空 Blender 场景并重新导入该 FBX；
6. 对比导出前后的世界坐标中心、尺寸、对象数、顶点、面、循环和 UV 层；
7. 任何单位、尺寸或结构不一致都使任务失败，不发布制品；
8. 把完整单位与回读证据写入原 UV report 的 `fbx_unit_contract`。

批准的 `blender-pbr-uv` Skill 文件及其 SHA 白名单未修改。原始高模、原始低模、拓扑、UV、材质和旧制品
均未覆盖或删除。

## 3. 测试证据

- Python 3.11 专项：`37 passed, 3 skipped`；Ruff 全通过。
- 新适配器脚本 SHA-256：
  `ca5889965c5e3b5d72a6a05bf7c8beecc77a9e5fe133987d3db8807c3291277b`。
- 使用发生故障的真实 UV Blend 在 Blender `5.1.2` 中重导：
  - 单位：`1 / 1 -> 100 / 100`；
  - 中心最大误差：`0`；
  - 尺寸最大误差：`0`；
  - 顶点：`2605 -> 2605`；
  - 边：`4844 -> 4844`；
  - 面：`2313 -> 2313`；
  - 循环：`7635 -> 7635`；
  - UV 层：`1 -> 1`；
  - 材质槽：`1 -> 1`。
- 真实生产 canary `b5364345-8c95-41d5-bc9e-dbae3b2d800f`：
  - 使用同一份拓扑低模调用 `/api/v1/assets/uv/process`；
  - `asset-control-4090` 一次执行成功；
  - Blend、FBX、report、QA、FBX QA 五项正式制品原子发布；
  - 正式 report 的单位为 `100 / 100`，中心和尺寸误差均为 `0`。

## 4. 发布证据

| 项目 | 结果 |
|---|---|
| Git 提交 | `8517ad7fda3377d2a7bd2c0de03f3938628b39e9`，已推送 `origin/main` |
| Worker 版本 | `1.4.17-uv-fbx-meter-contract-v1` |
| 镜像 ID | `sha256:20ee34af498af5955835d176e4f228a813bf4599dc0490a6cd77bd0603d41f8e` |
| 三节点 | `control-4090`、`worker-3090-a`、`worker-3090-b` 均恢复 `ACTIVE / ONLINE` |
| 三 Worker | 新实例均为 `ONLINE / AUTHENTICATED / HEALTHY` |
| ComfyUI | 三台均未重启；ImageClip/ModelViewCreator 未修改 |
| 离线镜像 | `/srv/gpu-control/images/uv-fbx-meter-contract-v1-8517ad7/` |
| 归档大小 | `690539272` bytes |
| 归档 SHA-256 | `1f8f379772395048b4eb8d163dc3fdecbfa3805b7edf9a1f317d29080b28b11b` |

三台严格逐台执行 `DRAINING -> GPU/Asset/Comfy 队列为 0 -> 只替换 Blender Worker -> 新实例心跳与
Codex/RetopoFlow 探针健康 -> ACTIVE`。用户取消的压力测试没有恢复，本次只运行一个真实 UV canary。

## 5. 现有项目处理

历史错误 UV FBX 是不可变制品，继续保留作审计证据。服务器不会静默替换其路径或 SHA。对于已经在
烘焙页显示约 100 倍尺寸差的项目，需要重新执行一次 UV 或重新选择拓扑低模生成新的 UV 制品；所有
新任务都会自动执行米制单位适配和 FBX 全新场景回读。
