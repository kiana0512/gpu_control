# 2026-08-07 预烘焙坐标对齐 V1

## 授权与边界

用户明确授权修改 `/opt/gpu-control`，接入交接包
`Li3D拓扑UV烘焙坐标对齐-服务器修改交接包.zip`。交接包 SHA-256：
`cab55698d8b4ee2e8dde583599f8b16576db9f8941a52e2edf885a730936ae3c`。

本次只修改 GPU Control 的作业编排、Blender Worker 与 Web 展示。原始高模、
拓扑交付、UV 交付和外部 workflow 均不修改；坐标变换只作用于任务临时目录中的
烘焙副本。

## 实现合同

需要 high/low 的 Substance profile 先以同一个任务 ID 排队为
`BAKE_ALIGNMENT_V1`：

1. Blender Worker 校验不可变输入哈希；
2. 高模作为唯一坐标权威；
3. 使用可选 `alignment_manifest` 中完整 4x4
   `input_world_to_high_world`，禁止通过包围盒猜变换；
4. 生成 `bake_high.fbx`、`bake_low.fbx` 和可选 `bake_cage.fbx`；
5. 在空场景重新导入 FBX，检查中心、尺寸、顶点/面数量、单位、轴向和手性；
6. 只有报告和 FBX 回读门均通过，Asset API 才把同一个任务原子转换为
   `SUBSTANCE_BAKE_V1`，Windows Baker 只收到 `bake_*` 文件；
7. `bake_alignment_report.json` 作为任务制品持久保存。

`ao-self-v1` 不需要 high/low 投影，继续直接进入 Baker。外部工作流、模型、
提示词、采样参数和 Substance profile 参数均未改变。

## 失败闭锁

- 缺失或哈希错误输入：Worker 拒绝运行；
- 坐标/尺寸门失败：`BAKE_ALIGNMENT_FAILED`，不创建 Baker 输入；
- FBX 回读、单位、轴向或手性门失败：
  `BAKE_ALIGNMENT_READBACK_FAILED`，不进入 Substance；
- 源文件始终只读，输入 SHA-256 在真实 Blender 验证前后必须完全一致。

## 候选验证

- Python 语法检查：通过；
- `ruff check`：通过；
- 坐标/Baker/UV 契约及完整 PBR 双阶段集成：22 项通过；
- Asset API 全文件回归：105 项通过；另有 5 个既有 V5 拓扑 Worker 领取测试失败，
  失败位置不在本次差异；
- Blender `5.1.2` 真实 mesh-only FBX：对齐、导出、空场景回读通过；
- 输入 high/low SHA-256：处理前后相同；
- 平移 10 单位的低模负例：退出码 2、报告 `pass=false`、未生成烘焙文件；
- 带默认灯光的合成 FBX 触发 Blender 5.1 自带 FBX 灯光导入缺陷；业务合同为
  mesh-only FBX，因此未放宽输入或坐标门禁。

## 生产滚动与线上验收

代码提交：`a81c3931`。本地候选镜像：

| 组件 | 镜像 | Image ID |
| --- | --- | --- |
| Asset API | `unified-scheduler-asset-api:1.6.9-bake-alignment-v1` | `sha256:42f9fe60133041fdc267c0152b60d2d651d413f19ca383db1c3b8fd257e1741f` |
| Blender Worker | `li3d/blender-worker:1.4.4-bake-alignment-v1` | `sha256:03a42084767fc9abd368c72b602b0ef10d194a23aa32da0a1e49597f384c46fd` |
| Web | `gpu-control-web:1.5.10-bake-alignment-v1` | `sha256:08d7c481ecc6012780b2c62206247a434e5cfad968af5a2d6e90b3cd3f12be06` |

三个节点均按 `DRAINING → 确认 GPU/Asset 作业为 0 → 只替换 Blender Worker →
镜像/脚本哈希/Codex 心跳验证 → ACTIVE` 顺序滚动。六次 mode 变更均以
`codex-operator` 写入 `node.mode.change` 审计日志。最终三节点均为
`ACTIVE / ONLINE / 0 jobs`，三台 Linux Worker 均为
`ONLINE / AUTHENTICATED / HEALTHY`。

4090、3090-A、3090-B 的持久化 release pin 已更新为 Worker `1.4.4`；
3090-A 与 3090-B 更新前的环境文件分别备份为
`/opt/gpu-control/.env.pre-bake-alignment-a81c3931`。三台 ComfyUI 容器保持原实例：
`d306e1facb7b`、`1547af00e12f`、`95acf7b332f2`，未重启、未清模型缓存。

线上 mesh-only FBX canary：

- 任务：`7a2fb047-0750-4cff-93b0-d19b6806ba65`；
- 初始类型：`BAKE_ALIGNMENT_V1`；
- 同一任务 ID 原子转换为 `SUBSTANCE_BAKE_V1`；
- Windows `asset-worker-3090-b-windows-02` 完成真实 256px DirectX Normal 烘焙；
- 最终状态：`SUCCEEDED / 100%`；
- Baker 输入 ZIP 仅含 `input/bake_high.fbx`、`input/bake_low.fbx` 与
  `request.json`，`pre_bake_alignment.required=true`；
- 持久制品包括 `bake_alignment_report.json`、`asset_normal_dx.png`、
  `baker_result.json` 与 `baker.log`；
- 临时 canary 客户端已停用，容器内临时 API key 与测试 FBX 已删除。

当前状态为 `DEPLOYED_LOCAL_PENDING_GIT_PUBLISH`：生产运行与 canary 均已通过，
但提交尚未存在于 `origin/main`，因此不宣称远端正式发布或 registry 发布。
