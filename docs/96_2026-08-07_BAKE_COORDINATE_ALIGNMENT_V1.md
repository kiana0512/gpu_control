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

本文首次提交时状态为 `CANDIDATE_NOT_DEPLOYED`。生产滚动部署后补充镜像 ID、
节点排空与线上验收结果。
