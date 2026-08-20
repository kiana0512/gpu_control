# GPU Control Markdown 分类目录

本目录覆盖仓库自有 Markdown。文件继续保留原路径，避免破坏部署手册、发布记录和外部交接中的既有链接。
第三方依赖下的 `node_modules/`、Python `site-packages/`、RetopoFlow 上游文档以及 `output/` 生成目录不属于仓库文档，不上传、不重新分类。

编号不是唯一键：仓库历史上存在 57、58、59、84 和 117 等重复编号，引用时必须使用完整文件名。

## 当前入口与治理

- `README.md`：产品范围、当前版本与最新变更入口。
- `AGENTS.md`：本仓库协作和生产安全约束。
- `CHANGELOG.md`：版本变更摘要。
- `CODEX_GPU_CONTROL_REFACTOR_PROMPT.md`：历史重构需求来源。
- `docs/00_START_HERE.md`：当前现场主线。
- `docs/00_REPOSITORY_AUDIT.md`：仓库审计基线。
- `docs/00_DOCUMENT_CATALOG.md`：当前分类规则。
- `docs/00_ASSET_SKILLS_CATALOG.md`：所有业务 Skill 的分类、来源和发布策略。
- `docs/IMPLEMENTATION_STATUS.md`、`docs/USER_INPUT_REQUIRED.md`：实现状态与现场输入门禁。

## 产品、架构、API 与安全

- `docs/01_*`—`docs/02_*`、`docs/12_*`—`docs/14_*`、`docs/19_*`、`docs/27_*`、`docs/29_*`：产品说明、架构、公共 API、调度、安全和核心算法审计。
- `docs/36_*`—`docs/43_*`、`docs/49_*`、`docs/51_*`—`docs/56_*`：批量抠图、动画管家和 Asset API/Skill 合同演进。
- `workflows/README.md`、`workflows/production/*/README.md`：生产工作流注册和业务工作流说明。
- `docker/comfyui/README.md`：ComfyUI 镜像构建边界。
- `tests/e2e/README.md`：端到端测试使用说明。

## 安装、部署、运维与验收

- `docs/03_*`—`docs/11_*`：网络、主机准备、镜像、模型、工作流和首次部署。
- `docs/15_*`—`docs/26_*`、`docs/28_*`、`docs/30_*`：日志、监控、备份、升级、故障、容量和现场验收。
- `docs/31_*`—`docs/35_*`、`docs/44_*`、`docs/46_*`、`docs/48_*`、`docs/50_*`：主机接入、部署、归档和回滚记录。
- `docs/62_*`—`docs/80_*`：可复现打包、生产排空、综合压测、稳定性热修与发布验收。
- `docs/115_*`—`docs/123_*`、`docs/128_*`—`docs/129_*`：四节点、4070Ti、Substance、调度和 ImageClip 的近期生产记录。
- `docs/adr/*.md`：不可变架构决策记录。

## UV、烘焙与 Windows 原生 Worker

- `docs/43_*`、`docs/52_*`—`docs/55_*`、`docs/58_2026-07-29_SUBSTANCE_*`、`docs/59_2026-07-29_ROUGHNESS_*`—`docs/61_*`：初始 UV/烘焙合同和四槽 Windows Baker。
- `docs/70_*`、`docs/77_*`—`docs/82_*`、`docs/105_*`、`docs/106_*`、`docs/111_*`、`docs/114_*`、`docs/124_*`—`docs/125_*`：UV QA、单位、缓存和发布策略演进。
- `docs/130_*`—`docs/134_*`：当前 UV 主线；原版恢复、拓扑代理修复、Max 兼容焊接、自动 MOF 路由和复杂多 Mesh 整单 MOF。
- `apps/mof_worker/README.md`：原生 Windows MOF Agent 安装和身份边界。
- `apps/substance_baker_agent/README.md`：Windows Substance Agent 运行约束。

## 自动重拓扑与高低模对齐

- `docs/84_2026-08-04_LI3D_AUTORETOPO_*`、`docs/85_*`—`docs/97_*`、`docs/99_*`—`docs/104_*`、`docs/107_*`—`docs/114_*`、`docs/117_2026-08-12_RETOPOLOGY_*`：Retopology V6、Direct V2、坐标恢复、输出合同和热修记录。
- `resources/retopology-v6/**/*.md`：V6 服务器包、Agent 提示和 Skill 源资料。
- `resources/retopology-direct-v2/**/*.md`：Direct V2 部署、提示和坐标/构建规则。
- `runtime/asset-skills/blender-align-bake-models/**/*.md`、`runtime/asset-skills/blender-retopology-compare-iterate/**/*.md`：发布批准的运行时 Skill 快照。

## GPU 图像业务与客户端交接

- `docs/32_*`、`docs/37_*`—`docs/42_*`、`docs/45_*`、`docs/47_*`、`docs/49_*`、`docs/56_*`—`docs/59_2026-07-29_MODELVIEW_*`、`docs/64_*`—`docs/69_*`：图像 API、动画管家、ModelView 和客户端交接演进。
- `docs/84_2026-08-05_PARTIAL_SUCCESS_*`、`docs/103_*`、`docs/118_*`—`docs/123_*`、`docs/126_*`—`docs/129_*`：部分成功、交互优先、四 GPU、ImageClip 与局部重绘当前记录。

## 发布制品说明

- `artifacts/**/README.md` 与 `artifacts/**/evidence/tests/*.md`：已跟踪的小型发布清单和验证文字；历史 LFS 分片保持不变。
- 新的模型、Canary FBX/Blend、Docker tar 和本地测试输出不得加入 Git 或 Git LFS。发布只提交源码、哈希、镜像身份和可复现命令。
- `resources/` 是可复现业务资源；`runtime/asset-skills/` 只保存批准的 Skill 文本/脚本；其他 `runtime/`、`output/` 和依赖目录均为本机生成内容。
