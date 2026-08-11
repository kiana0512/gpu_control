# Blender 自动拓扑与原坐标对齐服务器包 v3.0.10

本包合并两个正式技能：

1. 自动拓扑：读取新鲜高模、只生成一个低模候选。
2. 烘焙前对齐：同任务低模按高模原矩阵恢复坐标，只做变换与导出校验，不改拓扑或 UV。

它用于替换现有 `blender-retopology-compare-iterate-server-package-v2.5.0`。旧单文件调用参数和成功状态保持兼容；成功后额外输出烘焙高低模 FBX 与对齐报告。

## v3.0.10 行为

- 新生成低模在 UV 前必须修完本组件内的意外开放边界：实体端部封口、配对截面环 bridge，只有简单
  闭合缺口环才允许 holes-fill，并仅三角化新填充面；真实开口必须保留内外壁和 rim。
- 每次修复后重新实测，`boundary_edges == 0` 才能继续；高模、不同组件和计划中的负空间不允许参与
  自动修复，最终拓扑、形体、七视图与 FBX 回读门禁均不放宽。

## v3.0.9 行为

- 每次正式交付都对新鲜导出的高低模 FBX 运行独立形状接近度门禁；任一轴尺寸误差超过 3%、低到高
  P95 表面距离超过高模对角线 4%，或高到低 P95 超过 4%，均以
  `RETOPOLOGY_VISUAL_MISMATCH` 拒绝，避免把手/附件错位但包围盒中心正确的结果误报成功。
- 每次正式交付自动生成前、后、左、右、顶、底和透视 7 张不透明橙色低模加线框证据，并打包为
  `alignment_views.zip`。缺任一视图或渲染失败均禁止发布。
- 保留 v3.0.8 的生成低模重复点/退化面清理与一次有界早期重试；原高模仍只读，最终拓扑、UV、坐标
  和 FBX 回读门禁不放宽。

- 高模同时是形状依据和坐标依据。
- FBX、GLB、GLTF、OBJ 统一导入为一个只读 `SOURCE_HIGH`，同时记录源哈希、原始世界包围盒和
  静态源清单；不同上传格式不再分叉到旧的无角色 Blend 入口。
- FBX 因同位置重复点形成 triangle soup 时，只在服务器创建的临时工作副本上精确焊接邻接；原高模
  不改。工作副本必须保持面数/包围盒并成为闭合流形后，才可作为 controlled reduction 输入。
- 低模必须在 `source_high_local` 坐标生成。
- 同任务低模不运行 ICP；语义低模和高模表面差异不再触发错误的 ICP 修正。
- 自动移除纯展示平移，使高低模中心和矩阵回到原坐标。
- 对齐阶段禁止 Decimate、remesh、重建、三角化、UV 修改和几何修复。
- 对低模执行拓扑/UV 指纹、Blend 回读、FBX 新导入回读。
- 生成代理只写 `build.py`/`build_once.py` 而未真正执行 Blender 时，服务器只执行任务根目录内唯一、
  非符号链接且大小受限的既有构建脚本一次；这不是第二次建模，完成后仍经过相同报告、UV、拓扑、
  坐标和 FBX 回读门禁。脚本缺失、歧义或超限保持输出缺失硬失败；实际执行失败明确返回
  `BLENDER_EXECUTION_FAILED`。
- 补执行构建脚本前先以 `--disable-autoexec` 打开服务器已准备并校验的任务工作 Blend，保证脚本能读取
  `SOURCE_HIGH`；不再从空的 factory-startup 场景执行而误报 `SOURCE_HIGH missing`。
- `faces`、`triangles`、`uv_layers` 不再相信代理写入的可波动文本值；服务器以最终化 Blender 对
  generated Blend、保存后 Blend 和重新导入 FBX 的实际读取为权威，并将真实数值回填报告。报告漏字段
  不再误杀有效模型，真实无 UV、空低模或低模面数不小于高模仍由几何门禁硬拒绝。
- 服务器补执行脚本若失败，结果会保留执行退出码以及 Blender stdout/stderr 尾部，避免再次只显示
  `BUILD_SCRIPT_NOT_EXECUTED` 而丢失真实异常。
- 新生成低模在 modifier、曲线转换和 Join 后、创建 UV 前，必须用尺度相关的极小容差清理完全重合点、
  数值退化边/面及其产生的游离几何，并重算法线；清理只作用于新低模，禁止修改 `SOURCE_HIGH`、宽距离
  焊接、Decimate、remesh 或重建。
- 构建阶段约 40% 的新低模拓扑门禁失败可按队列上限重新生成一次；已进入最终对齐/FBX 回读阶段的失败
  继续禁止重试。首个坏候选永不发布，第二次仍不过门即终止。
- 低模缺少非空 UV 时在最终化阶段直接返回 `RETOPOLOGY_TOPOLOGY_INVALID`，并在交付门返回具体的
  `*_LOW_UV_MISSING` 缺陷代码。
- Codex 正常结束但把有效 Blend 保存到声明的兼容别名时，只复制该唯一候选到正式输出名；不修改几何，
  候选缺失或不唯一时仍硬失败并写结构化诊断。
- 任务私有 Codex 认证如发生安全刷新，会以源哈希和账户身份双重校验后原子回写节点持久认证；
  并发变化或身份不一致时拒绝覆盖。
- 低模使用不透明黄色/橙色显示，不隐藏，不用半透明或 X-ray。
- 坐标异常返回 `RETOPOLOGY_COORDINATE_MISMATCH`；低模存在开边、游离几何、重复/退化面或其他非流形
  缺陷时返回 `RETOPOLOGY_TOPOLOGY_INVALID`。最终对齐/回读失败不自动重跑；仅构建阶段坏候选允许一次
  有界新尝试。

## 运行条件

- Python 3.10+
- Blender 5.1.x（已用 5.1.2 实测）
- 已认证可执行的 Codex CLI
- Worker 能读写独立任务目录并运行 Blender headless

## 安装与替换

推荐把每个版本解压到独立 release 目录，再切换服务配置或符号链接，保留旧版用于回滚：

```bash
unzip blender-auto-retopo-align-server-package-v3.0.10.zip -d /opt/li3d/releases/
cd /opt/li3d/releases/blender-auto-retopo-align-server-package-v3.0.10
python3 server/verify_package.py
cp server/worker.env.example server/worker.env
```

在 `server/worker.env` 设置真实路径：

```dotenv
BLENDER_EXECUTABLE=/opt/blender/blender
CODEX_BIN=/usr/local/bin/codex
```

验证成功后，把原自动拓扑 Worker 的 package root 切换到本目录。不要同时运行旧包和新包处理同一个 job id。

## 单文件兼容调用

原来的主要参数不变：

```bash
set -a
. server/worker.env
set +a

python3 server/one_click_retopology.py \
  --input /jobs/asset-001/model.fbx \
  --output /jobs/asset-001/model_retopology.blend \
  --job-root /jobs/runtime
```

成功后产生：

```text
/jobs/asset-001/
├── model_retopology.blend
└── model_retopology.bake/
    ├── bake_alignment.blend
    ├── bake_alignment_report.json
    ├── bake_high.fbx
    └── bake_low.fbx
```

`model_retopology.blend` 是兼容旧接口的已对齐 Blend；`.bake/` 是新增烘焙侧文件。

成功 `result.json` 继续返回：

```json
{
  "status": "generated_for_user_inspection",
  "bake_alignment_status": "aligned",
  "alignment_mode": "source_matrix_restore",
  "topology_uv_preserved": true,
  "fbx_readback_passed": true,
  "low_display": "opaque_yellow"
}
```

## 批量调用

每个 FBX/GLB/GLTF/OBJ 是独立资产，互不合并：

```bash
python3 server/batch_retopology.py \
  --input /jobs/batch-001/chair.fbx \
  --input /jobs/batch-001/toolbox.fbx \
  --output-dir /jobs/batch-001/output \
  --job-root /jobs/runtime \
  --batch-id batch-001
```

批量 ZIP 同时包含兼容 Blend 和每个资产的 `.bake/` 目录。单个失败不会阻止后续文件；失败项不自动重试。

## 外部旧低模对齐

只有低模不是本任务生成、坐标关系未知时，才使用 ICP 安全门：

```bash
python3 server/align_existing_low.py \
  --high /jobs/external/high.fbx \
  --low /jobs/external/low.fbx \
  --output-dir /jobs/external/aligned \
  --job-root /jobs/runtime
```

该入口保留镜像、朝向、中心、尺寸和表面误差门，并输出七视图。门失败时不写最终结果，也不会改低模拓扑。

## HTTP/队列映射

现有上传层仍把每个资产路径作为一个 `--input` 参数，把任务输出 Blend 作为 `--output`。Worker 必须使用参数数组启动进程，不要用 shell 拼接用户文件名。

后端可以继续以 `status == generated_for_user_inspection` 判断生成完成，同时增加：

- 必须检查 `bake_alignment_status == aligned` 才允许进入烘焙。
- 必须把 `bake_high.fbx` 和 `bake_low.fbx` 成对传给烘焙器。
- 前端或上传器不得再次居中、归零或覆盖返回模型的矩阵、位置、旋转、缩放、单位或轴向。
- 如果下游烘焙器自动归一化导入，必须关闭，或对高低模应用完全相同的变换。

## 错误处理

`RETOPOLOGY_COORDINATE_MISMATCH` 表示以下任一问题：坐标声明缺失、高低模解析不唯一、矩阵/中心/尺寸门失败、拓扑或 UV 指纹改变、手性改变、FBX 回读不一致。

`RETOPOLOGY_TOPOLOGY_INVALID` 表示低模不是可烘焙闭合流形，例如存在边界边、游离边点、重合点面、
退化面、多面非流形边或错误面朝向。失败件不会发布。

若错误发生在生成阶段且进度低于最终化门槛，队列只允许一次新候选；最终化、对齐或 FBX 回读错误不重试。
检查任务目录中的：

- `generation_report.json`
- `finalize_stdout.log`
- `finalize_stderr.log`
- `artifacts/aligned/bake_alignment_report.json`（如果已生成）

## Docker Layer

```bash
docker build \
  --build-arg WORKER_IMAGE=现有Worker镜像@sha256:固定摘要 \
  -f Dockerfile.layer \
  -t li3d/blender-auto-retopo-align:v3.0.10 .
```

本 Layer 不替换现有 HTTP、队列、存储、鉴权或 Worker entrypoint，只加入合并技能和兼容入口。

## 上线前检查

1. `python3 server/verify_package.py` 必须通过。
2. 分别用一个已知 FBX 和 GLB 跑单文件 smoke test。
3. 确认输出 Blend 中高低模重合，低模为不透明黄色/橙色。
4. 确认 `bake_alignment_report.json` 中 `pass`、`topology_uv_unchanged` 和 `fbx_readback.pass` 全为 `true`。
5. 确认前端没有二次归零或居中。
6. 再把新 package root 切到生产 Worker。

回滚时只把 package root 切回 v2.5.0；不要删除失败任务目录，保留日志用于比较。
