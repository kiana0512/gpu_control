# Retopology V6 训练管线恢复记录

日期：2026-08-06  
范围：仅 `RETOPOLOGY_PROCESS_V2`；不修改 UV、PBR、GPU 推理、Scheduler 或 ComfyUI。

## 现象与根因

生产任务 `08e1f411-1996-421d-957e-7397f477df94` 使用了
`engine_contract=retopology-direct-v2`。其 `generation_report.json` 记录：

- `Cube_LOW`：6 faces / 12 triangles；
- `Cylinder002_LOW`：210 faces / 444 triangles；
- `method_decision=semantic_reconstruction`；
- `actual_plugin_use=none`；
- 请求没有参考视图。

这条 Direct V2 路径只执行一次高模直出，明确禁用了生成后的比较、复盘和修正；它不是
2026-08-04 交付并训练验证的 V6 结构化拓扑管线。因此旋转硬表面被输出为稀疏、随机三角
面，口沿、加强圈、把手根部和桶身环线没有按训练规则重建。

## 权威版本核对

用户交付包 `Li3D_AutoRetopo_Server_Deployment_V6.zip` 与仓库冻结资源一致：

- `skill/.../SKILL.md` SHA-256：
  `1b6519d3b725e89ca3beccaf5bc1de8dc5d3a1163b4dc2ce59cb5d0a277a61cf`
- `prompts/formal-retopology-agent.md` SHA-256：
  `9174535a915e1075ef08346e03f5e3580f7d81f68c08eb613983e09f76c1728c`
- policy SHA-256：
  `e6781d6158a93e571c944f5913a600838fe28fc2edc38a3b1909f649f66f3d3d`

## 修复

1. 新任务恢复 `retopology_input.v6` / `engine_contract=retopology-v6`。
2. Worker 身份恢复为 `asset-skills-retopology-v6.0.0`，只有哈希一致的 V6 Worker 可以领取。
3. Worker 使用冻结的 V6 Skill、正式 Agent Prompt、自动预算、分组件方法选择、候选比较和
   最多两轮根因修正；不再把 `target_faces` 映射为整模 Decimate 参数。
4. V6 完成通道继续交付正式 BLEND 与 FBX；几何 QA 为 advisory 时只附带告警，不扣留可用
   模型文件。源文件哈希、制品完整性和身份校验仍是硬门禁。
5. 保留旧 Direct V2 完成端点，仅供更新前已领取的旧任务收尾；新任务不会再路由到它。
6. Worker 镜像构建时验证 V6 `RUNTIME_FILES.sha256`，防止只复制 `SKILL.md` 或运行时漂移。

## 兼容性

- 外部 API 路径仍为 `POST /api/v1/assets/retopology/process`。
- 旧客户端仍可提交历史字段；`target_faces` 等字段被忽略并返回兼容告警。
- 返回的任务状态、下载接口和 BLEND/FBX 类型保持不变。
- 现有 Direct V2 任务完成上传通道保留，避免滚动更新期间协议断裂。

## 镜像

- Asset API：`unified-scheduler-asset-api:1.6.6-retopology-v6-trained`
- Blender Worker：`li3d/blender-worker:1.3.8-retopology-v6-trained`

构建校验已确认：Asset API 同时注册 V6 正式完成与旧 Direct V2 完成路由；Worker 冻结资源
共 19 项全部通过 SHA 校验。未自动提交真实模型任务，结果几何由用户下一次拓扑请求验收。

## 生产滚动结果

更新时资产队列没有 `QUEUED`、`RUNNING` 或 `CANCEL_REQUESTED` 任务。只重建 Asset API 和
三台 Blender Worker，未重启 ComfyUI、Scheduler、GPU API、UV 或 PBR Windows Worker。

- GitHub `main`：`d889bed`（主体修复 `5990d43`，Skill 门禁 `2c011d8`）；
- Asset API image ID：
  `sha256:e7c093951281d1305c7a54a0f088df473d27329e2ca503b190ef03a83067bc9c`；
- Blender Worker image ID：
  `sha256:3b8fd931c68b3741d1d44be443da36afef1135902b79c833be4cfaf3340381bc`；
- `asset-control-4090`、`asset-worker-3090-a`、`asset-worker-3090-b`：均为
  `ONLINE / asset-skills-retopology-v6.0.0 / 0 jobs`；
- 三台 Worker 容器 `RestartCount=0`，revision label 均为
  `2c011d898c0e4d6f2cec4708eee257026e42d193`；
- 控制面 readiness：数据库与 Redis 均为 `ok`。

Direct V2 与 V6 使用独立 Codex Home，避免旧 Skill 软链接进入新任务。尚未自动提交真实
模型；用户下一次提交同一桶模型时，应在任务 options/事件中看到
`engine_contract=retopology-v6`，并以七视图接触表、线框接触表和 V6 计划作为质量证据。
