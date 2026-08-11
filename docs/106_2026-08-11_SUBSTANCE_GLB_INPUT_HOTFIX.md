# Substance Baker GLB 输入合同热修复（2026-08-11）

## 1. 用户可见故障

Li3D 一键模型烘焙在高低模检查通过后，提交阶段返回：

```text
HTTP 422
BAKE_INPUT_INVALID: Substance Baker inputs must be FBX or OBJ
```

生产 Nginx 在 `2026-08-11 11:43:06 +08:00` 和 `11:43:31 +08:00` 记录到两次 422。请求在
Asset API 文件名白名单处被拒绝，未创建任务、未进入排队、未调用 Windows Baker，因此该故障与
高低模对齐、UV、贴图内容、Worker 槽位或 Substance 执行本身无关。

根因是 Li3D 项目高模经常为单文件二进制 glTF（`.glb`），而 GPU Control 的 Baker 输入白名单仍只
允许 `.fbx` 和 `.obj`。生产 Adobe `substance3d_baker.exe 15.1.0` 实际具备 GLB 原生读取能力，
接口合同落后于真实客户端交付格式。

## 2. 修复范围

- Baker mesh 输入白名单从 `FBX / OBJ` 扩展为 `FBX / OBJ / GLB`；`low_mesh`、`high_mesh` 和
  可选 `cage_mesh` 使用同一安全合同。
- GLB 直接交给固定版本 Windows Substance Baker，不经过 Blender 转换，因此不改变位置、旋转、
  轴向、缩放、拓扑、UV、材质或对象名。
- 仍拒绝 `.blend`；仍不接受可能依赖外部 `.bin`/贴图文件的 `.gltf`，避免上传不完整场景。
- 固定 profile、分辨率、缓存上限、Windows GPU 围栏、租约、SHA-256 和原子发布规则均未改变。
- ImageClip、ModelViewCreator、三台 ComfyUI 工作流、模型和推理参数均未修改。

此前 422 没有生成任务和幂等记录，客户端可直接使用原高低模重新点击烘焙；不需要重新拓扑、重新
对齐或重新展 UV。

## 3. 验证证据

### 3.1 生产 Baker 原生读取

在 3090-B 当前生产 `substance3d_baker.exe 15.1.0` 上执行只读 `info --list-all`，真实 GLB：

- 输入 SHA-256：`a1e3b04de97b11de564ce6e53b95f02954a297f0008183ac63a4f5974f6b32d8`；
- 成功识别 Mesh、`14,556` 顶点、`15,452` 面、材质、UV0、包围盒与全局变换；
- 无格式解析错误。

### 3.2 自动化回归

- Ruff：通过；
- Asset API GLB 接受/BLEND 拒绝专项：`2 passed`；
- Substance API、四槽 Agent、租约、围栏和发布回归：`56 passed, 54 deselected`；
- 仓库既有 `test_release_identity.py` 仍有一项与本修复无关的历史基线不一致：测试硬编码
  `1.5.11`，而当前仓库/生产 API 基线为 `1.5.12`；未把它误报为本次通过。

### 3.3 真实生产 canary

任务 `20412247-a0c4-44b7-8acf-97dd1b40a73d` 使用 GLB 低模和 GLB 高模调用
`POST /api/v1/assets/bake/process`：

- profile：`pbr-core-v1`，分辨率 `256`；
- 入队包内文件：`asset_low.glb`、`asset_high.glb`；
- Worker：`asset-worker-3090-b-windows-01`；
- 终态：`SUCCEEDED / 100%`，尝试次数 `1`，端到端 `5s`；
- 正式发布 `asset_ao.png`、`asset_normal_dx.png`、`baker.log` 和 `baker_result.json`；
- `delivery_ready=true`，无错误。

canary 完成后容量恢复为 `7 Worker / 13 槽位 / 0 使用 / 13 可用`，其中 CPU `9/9` 可用、
Substance `4/4` 可用；控制机 ComfyUI 保持原启动时间并为 `healthy`。

## 4. 发布与回滚证据

| 项目 | 结果 |
|---|---|
| 功能提交 | `41a4afca091dee8b15fd4ac9acb3988d24d1eb19` |
| Asset API | `1.6.17-substance-glb-input-v1` |
| 镜像 ID | `sha256:64b13ef572589bca38703040b0ae8d65c91e5c8156efa1a6cc90a2e83093891b` |
| 运行时源码 revision | `41a4afca091dee8b15fd4ac9acb3988d24d1eb19` |
| 健康状态 | `healthy`；数据库 ready，7 个 Worker 全部在线可调度 |
| 离线镜像 | `/srv/gpu-control/images/substance-glb-input-v1-41a4afc/unified-scheduler-asset-api-1.6.17-substance-glb-input-v1.tar.zst` |
| 归档大小 | `92,262,965` bytes（解压后 Docker tar `92,713,984` bytes） |
| 归档 SHA-256 | `771766f81167700e6782bd45ec428cadee2ffbf70c3bd50f7da1159c091f5266` |
| 归档校验 | `zstd -t` 通过 |
| 回滚镜像 | `unified-scheduler-asset-api:1.6.16-retopology-v6-client-filename-v1` |

发布前确认资产任务为 0，只替换 Asset API 容器；API、Scheduler、Web、三台 Blender Worker 和三台
ComfyUI 均未重启。用户已取消的压力测试没有恢复，本轮只执行一个有界真实 GLB Baker canary。
