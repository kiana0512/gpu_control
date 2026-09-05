# Asset Skills 分类与发布目录

本仓库的业务 Skill 分为“运行时批准快照”和“功能包内源资料”两类。所有 Skill 都是 Markdown、YAML 或脚本小文件，直接使用普通 Git，不使用 Git LFS。Skill 目录内不放额外 README；详细说明放在 `references/` 或本目录。

## 运行时批准快照

| Skill | 分类 | 入口 | 资源 | 生产用途 |
|---|---|---|---|---|
| `blender-pbr-uv` | UV | `runtime/asset-skills/blender-pbr-uv/SKILL.md` | `references/`、`scripts/`、`agents/openai.yaml` | 原版确定性 PBR UV、严格 QA；复杂软表面和复杂多 Mesh 可转原生 Windows MOF |
| `blender-align-bake-models` | 烘焙前对齐 | `runtime/asset-skills/blender-align-bake-models/SKILL.md` | `scripts/`、`agents/openai.yaml` | 仅以位移、旋转、轴向和缩放对齐高低模，不改拓扑与 UV |
| `blender-retopology-compare-iterate` | 重拓扑审计 | `runtime/asset-skills/blender-retopology-compare-iterate/SKILL.md` | `references/`、`scripts/`、`agents/openai.yaml` | 低模重建、固定视图对照、拓扑和剪影验收 |

这三套快照由 `apps/blender_worker/src/gpu_control_blender_worker/bootstrap.py` 和
`scripts/verify_asset_skills.sh` 双重 SHA-256 固定。任何 Skill 内容变更必须同时更新两处清单、运行 Skill 结构校验和 Worker 合同测试。

## 功能包内源资料

| Skill | 分类 | 入口 | 关系 |
|---|---|---|---|
| `blender-auto-retopo-align` | Direct V2 重拓扑/坐标恢复 | `resources/retopology-direct-v2/blender-auto-retopo-align/SKILL.md` | 随 Direct V2 服务包构建，保留执行计划和坐标恢复参考 |
| `blender-retopology-compare-iterate` | Retopology V6 训练与执行 | `resources/retopology-v6/skill/blender-retopology-compare-iterate/SKILL.md` | V6 功能包的源资料；发布到 Worker 时以运行时批准快照及其哈希为准 |

## 发布边界

- 上传：`SKILL.md`、`agents/openai.yaml`、直接引用的 `references/`、业务 `scripts/` 和固定哈希清单。
- 不上传：`__pycache__/`、`.pyc`、Codex 登录目录、RetopoFlow 第三方运行副本、模型、Job 工作区和测试输出。
- 不在不同类别之间复制临时文件。运行时与 `resources/` 中确有重复的 Skill 必须明确说明来源，不能以文件名猜版本。
- 每次发布执行五个 Skill 目录的 `quick_validate.py`、
  `scripts/verify_asset_skills.sh --root runtime/asset-skills`、Python 合同测试和 Docker 启动校验。
