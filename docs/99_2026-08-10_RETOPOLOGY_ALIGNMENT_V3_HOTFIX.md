# 2026-08-10 自动拓扑高低模对齐 V3 热修复

## 目标

Li3D 自动拓扑产生的低模必须在交付前回到原始高模的世界位置、旋转和缩放，随后才能作为
Substance 烘焙低模使用。后处理只允许修改低模对象世界矩阵，不允许修改低模顶点、边、面、UV
或拓扑。

## 已定位根因

生产任务 `e3308c0c-c003-4d93-8b71-7d9201ed5cb2` 与
`e5f46a73-4061-47e5-807e-cfb0eaff9f24` 使用同一个 GLB，均在 8%、3–4 秒失败，错误为
`input does not have a valid Blend signature`。Codex 和拓扑构建尚未启动。

同一真实 GLB 的旧归一化路径存在两个确定性缺陷：

1. Blender 5.1 默认把归一化结果保存为 Zstd 压缩 Blend，而冻结 Direct V2 输入合同只接受原始
   `BLENDER` 文件头；
2. `--factory-startup` 创建的默认 Cube 未被删除。旧归一化场景同时包含 `Cube`（尺寸 `2×2×2`）
   和用户 `model`（尺寸约 `1.224×1.898×1.198`），会污染高模选择和尺寸依据。

## V3 交付规则

1. GLB、GLTF、OBJ 先在真正的空场景中导入，并显式以 `compress=False` 保存；压缩 Blend 也会先
   安全重存为原始 Blend；
2. Direct V2 生成完成后，以对应高模的世界 3×3 线性矩阵作为低模旋转和缩放权威；
3. 再把低模世界包围盒中心恢复到高模中心；
4. 全程校验高模指纹不变、低模 mesh SHA-256 不变；
5. 恢复后三轴尺寸相对误差必须全部不超过 5%，不允许用额外缩放掩盖错误几何；
6. 只导出低模 FBX，清空场景后重新导入，中心和尺寸回读必须通过；
7. 对象矩阵原本未变化时不写回 Blend，输入输出 SHA-256 必须完全相同；
8. Worker 与 Asset API 双重验证 `retopology_coordinate_restoration.v2` 和
   `retopology_direct_delivery.v4` 的动作、哈希、尺寸及 FBX 证据。

候选版本：Asset API `1.6.13-retopology-alignment-v3`、Linux Worker
`1.4.8-retopology-alignment-v3`。冻结 Direct V2.3.0 包、Codex 模型、提示词、拓扑方法和外部
ImageClip/ModelViewCreator 流水线均未修改。

## 验证

- 失败任务原始 7.7 MB GLB：归一化后仅剩用户 `model`，无默认 Cube；输出为 13.2 MB 原始
  `BLENDER` 文件；
- 压缩 Blend：Zstd 文件头成功重存为 `BLENDER` 文件头；
- 故意扰乱低模位置、旋转和缩放：恢复成功，中心残差为 0，低模 mesh 保持不变，FBX 回读尺寸
  最大误差约 `9.54e-7`；
- 原本对齐：`coordinate_action=unchanged`，交付 Blend SHA-256 与 Agent Blend 完全一致；
- 故意将低模真实几何缩小 50%：按 `0.5 > 0.05` 失败关闭，不发布错误制品；
- 目标合同测试：12 passed；
- Python 全量：524 passed、12 skipped；
- Ruff：通过；mypy strict：61 个源文件通过。

## 发布状态

2026-08-10 已完成生产滚动发布：

- Git 提交 `d80babc37c38e6ea03dbcae9b34924601cffcb78` 已推送 `origin/main`；
- Asset API `1.6.13-retopology-alignment-v3`，镜像 ID
  `sha256:5b350ff76074c48ad1278415f7e82d8fed77c9d0be45d5de5e644546b9426452`；
- 三台 Linux Blender Worker 均为 `1.4.8-retopology-alignment-v3`，镜像 ID
  `sha256:22dd8be7dfa1e18ca90846fb2c998ebadc77d4445a2709d6728c80822a6a2584`；
- 4090、3090-A 空闲后先更新并恢复 `ACTIVE`；3090-B 保持 `DRAINING` 完成真实任务
  `a6cde972-20ae-46f0-9b7d-47b7506716a9`，确认 `SUCCEEDED / current_jobs=0` 后才更新；
- 发布期间三台 ComfyUI 未重启、未换镜像、未清缓存，ImageClip/ModelViewCreator 工作流和模型未修改；
- 发布完成后三节点均为 `ACTIVE / ONLINE`，真实 ImageClip 队列重新在三张 GPU 上并行运行。

离线增量包位于 `/srv/gpu-control/images/retopology-alignment-v3-d80babc/`：

- `asset-api-1.6.13-retopology-alignment-v3.tar.zst`：89 MB，SHA-256
  `9d36585ce393b56b60a14d1d6dae97496015633e3e1ebef984973d6d7a85fdb7`；
- `blender-worker-1.4.8-retopology-alignment-v3.tar.zst`：661 MB，SHA-256
  `fe4e9e251459ea6e90dd56a8c9d9cdf961fd6dcbefd0a73d77ea1f535dbafa9`；
- 两个 Zstd 包均通过完整性测试，目录内 `SHA256SUMS` 可用于离线复核。

## 生产端到端回归

使用此前失败任务的同一份 7.7 MB GLB（项目 SHA-256
`82ec99fb0ed6ecd8ed15b03671d378a8f04910157c2b3f636eab0e24c323aa10`）通过正式
`POST /api/v1/assets/retopology/process` 重新提交。任务
`8974dcfd-1359-4dae-9127-2cb7c9bfcb7b` 在 254 秒内由原先的 8% 秒失败变为
`SUCCEEDED / delivery_ready=true / review_required=false`，交付 BLEND、FBX 和五份审计证据。

坐标恢复证据：

- 合同为 `retopology_direct_delivery.v4` / `retopology_coordinate_restoration.v2`；
- Agent 把生成低模沿 X 轴摆开约 `1.580024`，服务检测后执行
  `coordinate_action=translation_restored`；
- 恢复后高低模包围盒中心最大残差 `2.980232e-8`；
- 高模指纹不变、低模 mesh 不变、旋转缩放矩阵不变；只修改低模对象平移；
- 三轴尺寸相对误差为约 `1.98% / 4.89% / 0.45%`，最大 `4.8909%`，通过 5% 硬门禁；
- FBX 清空场景重新导入通过，中心最大误差 `2.980232e-8`，尺寸最大误差
  `1.192093e-7`；
- 最终 BLEND SHA-256
  `8f87e4244936413745e3e55601281839d6528b41f05d8feb86c89168bb3a1ee2`，FBX SHA-256
  `ec11429ea4e3b7d3f27f4da51b8e2b33df4d4c2fd57932b79c520fb08c6b6295`。

该回归证明服务端高低模世界坐标恢复和 FBX 交付门禁正常。它没有附带材质贴图，因此没有冒充
Substance 最终纹理烘焙验收；局部轮廓质量仍应由业务端四视图和实际 cage/贴图烘焙复核。
