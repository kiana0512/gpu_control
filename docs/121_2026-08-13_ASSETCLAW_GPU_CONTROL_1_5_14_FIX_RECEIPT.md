# AssetClaw 对齐 GPU Control 1.5.14 修复回执

日期：2026-08-13  
节点：4070 Ti 动画管家（Windows `10.3.34.238`）  
控制面：4090 主控（`https://10.3.34.11`）

## 1. 修复结论

AssetClaw 的生产抠图入口现已统一为 GPU Control。动画管家继续负责动画任务编排、抽帧、状态展示、后处理和交付，但不再把任何生产抠图任务分配给本机秋叶 ComfyUI。

本机秋叶环境仅作为人工备份保留，不参与自动路由、容量回退或 OOM 回退。

## 2. 根因与修复

本次错误由两个旧配置同时造成：

1. AssetClaw 客户端仍校验旧版 ImageClip 工作流身份，导致 GPU Control 已接受或正在处理的批次被本地错误判为失败。
2. `MATTING_BACKEND_MODE=hybrid` 仍允许小任务选择本机 ComfyUI，因此 44 帧任务没有提交给主控。

已完成：

- 将生产路由固定为 `gpu_control`；`local`、`comfyui`、`hybrid` 请求在生产环境统一升级为 GPU Control。
- 删除独立图片流水线的“本机 GPU OOM 后再切集群”分支。
- 取消本地流水线串行锁和本地容量回退。
- 集群容量只作为观测信息，不阻止任务提交到主控队列。
- 修正 WebUI 的抠图阶段名称为 `GPU Control 抠图`。
- 修正热重载和基准制品脚本，避免旧配置再次写回。
- 保留历史任务的真实历史后端字段，不篡改历史记录；所有新任务均只走 GPU Control。

## 3. 当前唯一允许的工作流身份

```text
workflow_key: imageclip-rgba
workflow_version: 2026.08.12-c39ed0b-fp8-r1
pipeline_commit: c39ed0b3b637f0a1435bbe10e5a3acf6bfca07bd
pipeline_sha256: 07928d57852ed56ed37527960ec9955d867c0090456fda687fbcd12fecf1775c
output_node: SaveImage #102
```

批量接口：

```text
POST https://10.3.34.11/api/v1/batches/imageclip-rgba
```

AssetClaw 不直接访问任何 worker。

## 4. 三个故障任务的恢复映射

| 动画任务 | AssetClaw 抠图任务 | GPU Control batch | 帧数 | 修复后状态快照 |
|---|---|---|---:|---|
| `VID_B24CD85D25F8` | `COMFY_5B4D06E80FD0` | `8cc7a92b-59ed-4ba9-9025-83f58a28068d` | 97 | `DONE / SUCCEEDED / 97/97 / failed=0` |
| `VID_1FAD38F82BCD` | `COMFY_5122546659F5` | `decc2eb4-c953-4e3b-a325-07f2cbd142d0` | 97 | `RUNNING / 64/97 / failed=0` |
| `VID_651A647E680E` | `COMFY_989A6A4904DE` | `8c5504e4-bb79-4b1d-9c04-6fe76c0ec9bb` | 44 | `RUNNING / 21/44 / failed=0` |

状态快照时间约为 2026-08-13 10:54（Asia/Shanghai）。运行中的数字会继续增长。

## 5. 4090 主控核对项

主控侧只需按上表 batch ID 核对：

- 97 帧批次 `8cc7a92b-...` 已成功结束，结果已被 AssetClaw 验证并交付。
- 97 帧批次 `decc2eb4-...` 正在运行。
- 原先未提交的 44 帧批次现已创建为 `8c5504e4-...` 并正在运行。
- 三个客户端记录的 `backend` 均为 `gpu_control`。
- 当前运行批次 `failed=0`，客户端无身份校验错误。

## 6. 验证结果

- GPU Control 批次、结果校验、部分成功和任务重跑测试：`47 passed`。
- 动画管家健康接口：`ok=true`。
- WebUI：`http://127.0.0.1:5180/` 可访问，任务页已显示 97 帧完成及其余两单运行中。
- 滚动重载期间两个独立恢复 worker 均保持存活。
- 全仓运行时搜索未再发现旧身份 `2026.07.30-691770c-r1`、`SaveImage #25`、`MATTING_BACKEND_MODE=hybrid` 或本机 OOM 回退函数。

## 7. 后续行为约束

- GPU Control 暂时不可用时，动画管家应保持任务排队或明确报控制面不可用，不得静默回退本机抠图。
- `PARTIAL_SUCCESS` 是终态部分成功，必须保留成功帧并明确报告缺失帧，不得整体误判为失败。
- 后处理、动画合成和交付仍由 4070 Ti 动画管家继续执行。
