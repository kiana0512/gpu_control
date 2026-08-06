# 批量 FBX 一键拓扑服务器接入

本包用于一次上传一个或多个静态高模 `.fbx`。每个 FBX 独立准备、独立完整调用
`blender-retopology-compare-iterate`，最后返回各自的低模 `.blend` 和统一结果 ZIP。

一份 FBX 代表一个独立资产。FBX 内的桶身、把手、扣件等多个 Mesh 会作为同一高模的组件保留；
不同道具必须分别上传为多个 FBX，批量入口不会把它们合并成一个 `SOURCE_HIGH`。

## 运行条件

- Python 3.10+。
- Blender 5.1.x；已用 Blender 5.1.2 实测。
- 已认证且可执行的 Codex CLI。
- Worker 能读写独立任务目录并执行 Blender headless。

## 安装

```bash
unzip blender-retopology-compare-iterate-server-package-v2.3.0.zip -d /opt/li3d/
cd /opt/li3d/blender-retopology-compare-iterate-server-package-v2.3.0
python3 server/verify_package.py
cp server/worker.env.example server/worker.env
```

在 `server/worker.env` 配置真实路径：

```dotenv
BLENDER_EXECUTABLE=/opt/blender/blender
CODEX_BIN=/usr/local/bin/codex
```

## 批量调用

```bash
set -a
. server/worker.env
set +a

python3 server/batch_retopology.py \
  --input /jobs/batch-001/chair.fbx \
  --input /jobs/batch-001/bucket.fbx \
  --input /jobs/batch-001/toolbox.fbx \
  --output-dir /jobs/batch-001/output \
  --job-root /jobs/runtime \
  --batch-id batch-001
```

批量入口按上传顺序逐个调用 `server/one_click_retopology.py`。每个 FBX 都会：

1. 复制到独立任务目录，原 FBX 不修改。
2. 用技能内的 `prepare_fbx_source.py` 创建独立 `SOURCE_HIGH` 和工作 Blend。
3. 把完整技能安装到该任务独立的 `CODEX_HOME/skills/`。
4. 以 `$blender-retopology-compare-iterate` 调用 Codex，只生成一个低模候选。
5. 保存后立即停止，不自动复查、修正或重试建模。

方法路由不会把直接减面整条能力删除：一体有机区域仍可受控直接减面；但预处理后的
`SOURCE_HIGH` 是 joined 对象不能作为选择依据。容器外壳加不规则内容物会按组件混合处理，
避免服务器把整个资产统一减成随机三角面。

一个文件失败不会阻止后续文件执行。批量终态为：

- 全部成功：`generated_for_user_inspection`
- 部分失败：`partial_failure`
- 全部失败：`failed`

输出目录包含：

```text
output/
├── batch-results.zip
├── batch_report.json
├── results/
│   ├── 001_chair_retopology.blend
│   ├── 002_bucket_retopology.blend
│   └── 003_toolbox_retopology.blend
└── logs/
```

`batch-results.zip` 包含批量报告和全部成功低模；失败项目的日志也会放入 ZIP。

## 单文件调用

原来的单 FBX 入口继续保留：

```bash
python3 server/one_click_retopology.py \
  --input /jobs/asset-001/model.fbx \
  --output /jobs/asset-001/model_retopology.blend \
  --job-root /jobs/runtime
```

## HTTP 接口映射

上传接口接收可重复的 multipart 字段 `assets`：

| 请求字段 | 批量 Worker 参数 |
|---|---|
| 每个上传后的 FBX 路径 | 重复一个 `--input` |
| 批量输出目录 | `--output-dir` |
| 任务工作根目录 | `--job-root` |
| 父任务 ID | `--batch-id` |

必须以参数数组启动进程，不要用 shell 拼接用户文件名。

## Docker Layer

```bash
docker build \
  --build-arg WORKER_IMAGE=现有Worker镜像@sha256:固定摘要 \
  -f Dockerfile.layer \
  -t li3d/blender-retopology-skill:v2.3.0 .
```

本 Layer 不替换现有 HTTP、队列、存储或鉴权，只加入完整技能、FBX 预处理、单文件入口和
批量入口。
