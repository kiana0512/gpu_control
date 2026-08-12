# Blender 自动拓扑与原坐标对齐服务器包 v3.0.21

本包合并两个正式技能：

1. 自动拓扑：读取新鲜高模、只生成一个低模候选。
2. 烘焙前对齐：同任务低模按高模原矩阵恢复坐标，只做变换与导出校验，不改拓扑或 UV。

它用于替换现有 `blender-retopology-compare-iterate-server-package-v2.5.0`。旧单文件调用参数和成功状态保持兼容；成功后额外输出烘焙高低模 FBX 与对齐报告。

## v3.0.21 行为

- Direct V2 现在从 API 的只读请求清单接收用户拓扑意图；过去该字段虽然进入输入 ZIP，Worker
  却没有传给生成器，因此“布料减面、木堆外轮廓包络”会被观察启发式覆盖成逐根重建。
- 用户明确点名的区域与方法成为最高优先级建形约束；高模观察只定位边界和密度，不能静默删除
  区域或把用户要求的聚合包络改回逐件语义重建。请求数据不能修改命令、路径、源只读规则或门禁。
- 布料/皮革覆盖木堆、石堆或碎料堆时，两个区域先按高模几何互斥分离；聚合包络不得使用包含
  软表面的联合包围盒或联合截面，并必须在覆盖区保持于布料内侧，避免吞没悬垂和褶皱。
- 未增加方向图、FBX 回读、视觉评分、UV 或拓扑审查门禁；交付仍只执行现有无破面与坐标持久化检查。

## v3.0.19 行为

- generated-low 任务同时安装两个职责分离的技能：`blender-retopology-compare-iterate`
  按训练规则负责结构分析和建形，`blender-auto-retopo-align` 只负责坐标恢复和服务器输出。
- 禁止再根据对象名或整体包围盒把任意模型猜成圆角盒、球、柱等通用代理；构建前必须直接读取
  `SOURCE_HIGH` 的真实轮廓、截面、开口、负空间、组件和附件结构。
- 生成前只渲染一张不超过 512×512 的高模 Workbench 三分之四观察图，用于资产/区域分类，
  不渲染低模、不评分、不等待确认，也不作为交付门禁。
- 方法按区域路由：复杂连续模型或软表面从只读高模的新副本受控减面；机械/硬表面语义重建；
  混合资产把软区域受控减面、结构件语义重建、密集木堆/石堆等聚合区按整体外轮廓重建。
- disconnected mesh island 和面数阈值仅是测量，不能直接决定语义组件；混合资产记录
  `region_method_map`，不再把布料褶皱碎片误判为原木或把每个碎片逐件输出。
- 用户取消的交付门禁保持取消：不执行低模方向审查、拓扑流审查、轮廓评分、FBX 回读或 UV
  生成。一次有界高模只读分析只作为建形输入，不评分、不等待确认、不阻塞快速交付。
- FBX/OBJ/GLB/GLTF 准备阶段预先写入文本化 `semantic_measurements`，Codex 不再重复
  启动坐标测量；当清单不足以表达形状时，生成器最多追加一次有界高模只读结构分析。
- `result.json.timing_seconds` 记录准备、Codex、坐标恢复与发布分段耗时。
- generated-low 不再让诊断计划及其字段校验阻断几何生成；Codex 选择方法后立即构建，
  最终有效 Blend 和无破面结果才是交付门禁。

- 自动拓扑交付取消前后左右顶底透视方向渲染，不再生成 `alignment_views.zip`。
- 高低模 FBX 仍正常导出并绑定 SHA-256，但不再重新导入 FBX 验证。
- 自动拓扑阶段不生成或修改 UV；低模已有 UV 就原样保留，没有 UV 也允许交付，后续可由独立 UV 阶段处理。
- 硬门禁简化为 `no_broken_faces`：低模必须非空、坐标有效、面数少于高模且零面积/退化面为 0；
  开放边、非流形、游离、重复和面朝向只记录诊断，不阻止交付。
- 原始高模只读、坐标恢复、保存后 Blend 指纹、FBX 导出哈希、一次有界重试和单调进度保持不变。

## v3.0.12 行为

- 同一任务的一次有界重试使用任务级单调进度：第一次候选失败后进入 50% 边界，第二次进度映射到
  50%～99%，不再显示 40% 回到 0%/1%。
- 新鲜 FBX 的尺寸、双向表面距离或回读门禁已经失败时，废弃候选不再渲染七方向，直接切换生成方法；
  数值通过的正式候选仍必须生成完整七方向证据，质量门禁不放宽。

## v3.0.11 行为

- 控制面把权威 `attempt_count` 传给 Worker 和服务器包；第二次也是最后一次有界尝试时，生成器不会
  原样重复已失败的低密度语义代理。
- 当源拓扑与计划 guard 允许时，重试优先使用未修改高模的新副本做足够密度的受控降面；否则切换为
  实测截面的组件混合重建。形体、拓扑、七视图和 FBX 回读门禁保持不变。

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
- 对低模执行拓扑指纹与保存后 Blend 回读；自动拓扑交付不生成 UV，也不执行 FBX 新导入回读。
- 生成代理只写 `build.py`/`build_once.py` 而未真正执行 Blender 时，服务器只执行任务根目录内唯一、
  非符号链接且大小受限的既有构建脚本一次；这不是第二次建模，完成后仍经过相同报告、无破面、
  坐标和 FBX 导出门禁。脚本缺失、歧义或超限保持输出缺失硬失败；实际执行失败明确返回
  `BLENDER_EXECUTION_FAILED`。
- 补执行构建脚本前先以 `--disable-autoexec` 打开服务器已准备并校验的任务工作 Blend，保证脚本能读取
  `SOURCE_HIGH`；不再从空的 factory-startup 场景执行而误报 `SOURCE_HIGH missing`。
- `faces`、`triangles`、`uv_layers` 不再相信代理写入的可波动文本值；服务器以最终化 Blender 对
  generated Blend 和保存后 Blend 的实际读取为权威，并将真实数值回填报告。UV0 与保留已有 UV 都可
  交付；空低模、非有限坐标、低模面数不小于高模或存在零面积/退化面时由几何门禁硬拒绝。
- 服务器补执行脚本若失败，结果会保留执行退出码以及 Blender stdout/stderr 尾部，避免再次只显示
  `BUILD_SCRIPT_NOT_EXECUTED` 而丢失真实异常。
- 新生成低模在 modifier、曲线转换和 Join 后，只清理零面积/退化面并验证坐标为有限数值；自动拓扑
  阶段不创建 UV。收尾只作用于新低模，禁止修改 `SOURCE_HIGH`、宽距离焊接、Decimate、remesh 或重建。
- 构建阶段的新低模无破面门禁失败可按队列上限重新生成一次；已进入最终坐标恢复与导出阶段的失败
  继续禁止重试。首个坏候选永不发布，第二次仍不过门即终止。
- 自动拓扑不得生成或修改 UV；生成低模已有 UV 时原样保留，没有 UV 时也不补建 UV。
- Codex 正常结束但把有效 Blend 保存到声明的兼容别名时，只复制该唯一候选到正式输出名；不修改几何，
  候选缺失或不唯一时仍硬失败并写结构化诊断。
- 任务私有 Codex 认证如发生安全刷新，会以源哈希和账户身份双重校验后原子回写节点持久认证；
  并发变化或身份不一致时拒绝覆盖。
- 低模使用不透明黄色/橙色显示，不隐藏，不用半透明或 X-ray。
- 坐标异常返回 `RETOPOLOGY_COORDINATE_MISMATCH`；低模为空、含非有限坐标、面数不低于高模或存在
  零面积/退化面时返回 `RETOPOLOGY_TOPOLOGY_INVALID`。开放边、非流形、游离、重复和面朝向只记录诊断；
  最终坐标恢复或导出失败不自动重跑，仅构建阶段坏候选允许一次有界新尝试。

## 运行条件

- Python 3.10+
- Blender 5.1.x（已用 5.1.2 实测）
- 已认证可执行的 Codex CLI
- Worker 能读写独立任务目录并运行 Blender headless

## 安装与替换

推荐把每个版本解压到独立 release 目录，再切换服务配置或符号链接，保留旧版用于回滚：

```bash
unzip blender-auto-retopo-align-server-package-v3.0.21.zip -d /opt/li3d/releases/
cd /opt/li3d/releases/blender-auto-retopo-align-server-package-v3.0.21
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
  "topology_gate": "no_broken_faces",
  "uv_policy": "preserve_optional",
  "fbx_readback_performed": false,
  "direction_review_performed": false,
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

`RETOPOLOGY_COORDINATE_MISMATCH` 表示以下任一问题：坐标声明缺失、高低模解析不唯一、矩阵/中心/尺寸门失败、保存后拓扑指纹改变或手性改变。

`RETOPOLOGY_TOPOLOGY_INVALID` 表示低模为空、坐标非有限、面数不低于高模或存在零面积/退化破面。
开放边、非流形、游离、重复和面朝向只写入报告，不再阻止发布。

若错误发生在生成阶段，队列只允许一次新候选；最终化或坐标对齐错误不重试。
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
  -t li3d/blender-auto-retopo-align:v3.0.21 .
```

本 Layer 不替换现有 HTTP、队列、存储、鉴权或 Worker entrypoint，只加入合并技能和兼容入口。

## 上线前检查

1. `python3 server/verify_package.py` 必须通过。
2. 分别用一个已知 FBX 和 GLB 跑单文件 smoke test。
3. 确认输出 Blend 中高低模重合，低模为不透明黄色/橙色。
4. 确认 `bake_alignment_report.json` 中 `pass=true`、`uv_policy=preserve_optional`，保存前后的
   UV 指纹完全一致，且
   `fbx_readback.status` 与 `direction_review.status` 都是 `skipped_by_user_policy`。
5. 确认前端没有二次归零或居中。
6. 再把新 package root 切到生产 Worker。

回滚时只把 package root 切回 v2.5.0；不要删除失败任务目录，保留日志用于比较。
