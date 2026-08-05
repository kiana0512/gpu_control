# Blender 一键拓扑服务器接入

这个包只解决一件事：服务器收到一个 Blender 高模文件后，完整调用
`blender-retopology-compare-iterate` 技能，生成新的低模 Blend。

## 包内文件

- `blender-retopology-compare-iterate/`：当前技能完整快照，共 6 个源文件。
- `server/one_click_retopology.py`：服务器 Worker 的一键入口。
- `server/agent_prompt.md`：把输入、输出和对象名传给技能的任务模板。
- `server/worker.env.example`：4 个必要运行参数。
- `server/verify_package.py`：安装前自检。
- `Dockerfile.layer`：把本包叠加到现有 Worker 镜像。
- `examples/`：请求与生成报告格式。

没有自动 QA、旧失败证据、Golden 文件、渲染器或第二套拓扑算法。

## 运行条件

现有 Worker 需要提供：

- Python 3.10 以上；本包脚本只使用标准库。
- Blender；推荐与当前 Codex 拓扑环境一致的 Blender 5.1.2。
- 可执行的 Codex CLI，并已通过服务器密钥完成认证。
- Codex Worker 能执行 Blender headless 命令，并能读写单个任务目录。
- 原有的 HTTP 上传、队列、对象存储和鉴权。它们仍由现有服务器负责。

## 安装

直接安装到已有 Worker：

```bash
unzip blender-retopology-compare-iterate-server-package-v2.zip -d /opt/li3d/
cd /opt/li3d/blender-retopology-compare-iterate-server-package-v2
python3 server/verify_package.py
cp server/worker.env.example server/worker.env
```

修改 `server/worker.env` 中两个真实路径：

```dotenv
BLENDER_EXECUTABLE=/opt/blender/blender
CODEX_BIN=/usr/local/bin/codex
```

如果使用现有 Worker 镜像构建：

```bash
docker build \
  --build-arg WORKER_IMAGE=你的现有Worker镜像@sha256:固定摘要 \
  -f Dockerfile.layer \
  -t li3d/blender-retopology-skill:v2 .
```

这个 Layer 不替换原有 Worker 入口，只加入技能和一键调用器。

## 一键调用

服务器把上传文件保存后，只需要调用一次：

```bash
set -a
. /opt/li3d/blender-retopology-compare-iterate-server-package-v2/server/worker.env
set +a

python3 /opt/li3d/blender-retopology-compare-iterate-server-package-v2/server/one_click_retopology.py \
  --input /jobs/asset-001/source.blend \
  --output /jobs/asset-001/retopology.blend \
  --high H01_HIGH \
  --high H02_HIGH \
  --job-root /jobs/runtime
```

`--high` 可重复；建议客户端把用户在 Blender 中选中的高模对象名一并提交。没有传
`--high` 时，代理会按技能规则识别文件中的高模 Mesh，同时保留无关旧低模。

成功时标准输出返回：

```json
{
  "status": "generated_for_user_inspection",
  "output": "/jobs/asset-001/retopology.blend",
  "assets": []
}
```

HTTP 接口只需把现有请求字段映射为上述参数：

| HTTP/队列字段 | 一键脚本参数 |
|---|---|
| 上传后的 Blend 绝对路径 | `--input` |
| 新输出 Blend 绝对路径 | `--output` |
| 选中的高模对象名数组 | 多个 `--high` |
| 任务工作目录 | `--job-root` |

不要用 shell 拼接用户输入；服务端应以参数数组调用脚本。API 收到任务后可返回原有
`job_id`，Worker 进程退出码即为任务成功或失败。

## 技能没有遗漏的调用链

一键脚本会自动完成：

1. 复制输入 Blend 到独立任务目录，原文件不修改。
2. 核对技能恰好包含当前 6 个源文件。
3. 把完整技能复制到该任务的 `CODEX_HOME/skills/blender-retopology-compare-iterate/`。
4. 渲染任务提示词，并明确调用 `$blender-retopology-compare-iterate`。
5. Codex 读取完整 `SKILL.md`、构造规则、经验规则和计划格式。
6. 每个高模在生成前写 shape-authority plan，并运行技能自带的 guard。
7. Codex 真正调用 Blender；每个高模只生成一个低模。
8. Blender 保存输出和 `generation_report.json` 后立即停止。
9. 一键脚本只检查文件是否成功生成、报告字段是否齐全和源文件哈希是否不变；不做
   几何复查，也不会自动重试建模。

这四项不能在服务器实现里删掉：任务独立 `CODEX_HOME`、完整技能目录、带 `$技能名`
的提示词、生成前 plan guard。只复制 `SKILL.md` 或只把技能名写进普通提示词，都不算
完整接入。

## 输出与失败

成功输出：

- 指定的新 `.blend`；包含保留的高模和每个指定高模对应的一个低模。
- 任务目录内的 `generation_report.json`、`result.json`、Codex 事件和错误日志。
- 状态固定为 `generated_for_user_inspection`。

以下情况直接失败，不自动重跑：

- 技能缺文件或哈希复制失败。
- 输入不是有效路径下的 `.blend`。
- 高模计划 guard 不通过。
- Codex/Blender 退出失败或超时。
- 输出 Blend 或生成报告没有写出。

## 更新技能

以后技能修改时，只替换包内整个
`blender-retopology-compare-iterate/` 目录，不要手工挑文件。然后运行：

```bash
python3 server/verify_package.py
```

验证通过后重新构建 Worker 镜像。旧任务使用旧镜像，新任务使用新镜像，避免运行中
技能版本漂移。
