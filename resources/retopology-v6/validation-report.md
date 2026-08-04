# Bundle 本地验证报告

验证时间：2026-08-04（Asia/Singapore）

## 已通过

| 检查 | 结果 |
|---|---|
| 技能结构 | `quick_validate.py`：`Skill is valid!` |
| 技能/部署 Python | 全部 `.py` 通过 Python 3.11 `py_compile` |
| JSON Schema 自身 | 三个 Schema 通过 Draft 2020-12 `check_schema` |
| JSON 正向样例 | request/plan/result 三个样例通过各自 Schema |
| JSON 负向门禁 | 含 `target_faces` 请求被拒绝；`publish_allowed=true` 且 construction 失败或源变更的结果被拒绝 |
| 策略语义 | `budget_mode=automatic`、用户面数禁用、正式候选数为 1 |
| YAML | Compose、Kubernetes 和技能 agent YAML 均可解析 |
| Bash | `verify_cluster.sh`、`build_runtime.sh` 通过 `bash -n` |
| Runtime 预检逻辑 | 使用 Blender 5.1.2、本地临时可写目录、V6 策略/技能/CA 通过；真实服务器还需用真实 Codex 和 Secret 重跑 |
| TLS 健康 | 使用包内 CA 并保持证书/主机名验证，`/health/live` 与 `/health/ready` 均通过 |
| CA | SHA-256 `ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b` |
| 金样 | 398068352 bytes；SHA-256 `bc27c8d31835cdb6ca1b49203db616256b3af4ff800e1c973fd8beeb28650959` |
| Secret 静态扫描 | 运行时代码/配置/文档未发现常见真实 Secret 模式；公开 CA 和历史审计证据按说明保留 |
| Bundle/ZIP 完整性 | `FILES.sha256` 逐文件验证通过；ZIP CRC、内部 88 个文件哈希和无 `__pycache__/.pyc` 检查通过 |

## 当前环境不能完成

| 检查 | 原因 | 上线前动作 |
|---|---|---|
| 生产 Worker/控制面源码编译与单测 | 本机没有生产源码访问 | 管理员提供仓库/源码包及 commit SHA 后执行 docs/07、09 |
| Docker 镜像构建/扫描 | 本机无 Docker CLI/daemon，且 V6 实现镜像尚未由生产源码构建 | 在受控构建机执行 `scripts/build_runtime.sh` |
| Kubernetes dry-run | 本机无 kubectl 和集群访问 | 在 canary namespace 执行 server-side/client dry-run |
| V6 API 冒烟与 N01–N08 全量 | V6 Worker 尚未部署 | canary 部署后执行 `smoke_submit.py` 和 docs/05 金样矩阵 |
| 生产 Secret/权限验证 | 本包故意不含 Secret | 在服务器运行 `preflight.py --require-secrets` |
| 负载、故障注入、回滚演练 | 需要 canary 集群与运维权限 | 全部 P0 通过后再灰度 |

这些未完成项属于真实生产权限/源码阻断，不能用文档或本地假结果替代。
