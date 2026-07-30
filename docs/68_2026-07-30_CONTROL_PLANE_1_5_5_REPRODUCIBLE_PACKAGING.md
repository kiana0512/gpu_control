# GPU Control 1.5.5 控制面候选打包门禁

> 状态：`PREPARED_NOT_EXECUTED`
> 范围：API、Scheduler、Asset API、Web 四个 GPU Control 自有镜像
> 禁止项：本流程不执行 Compose、容器启动、生产重启、数据库迁移、registry push、Git push 或
> Git LFS push。

## 1. 结论

`scripts/package_control_plane_release.py` 是默认只输出计划、显式 `--execute` 才工作的候选打包器。
它只接受当前 `HEAD` 的完整 40 位 SHA，并在构建前同时要求：

1. 工作树无已跟踪、未跟踪变更；
2. `origin` 是批准的 GPU Control 仓库；
3. 本地 `origin/main` 与远端 `refs/heads/main` 一致，候选 SHA 已包含在远端；
4. Python 包、Web package 与 lockfile 版本都等于目标版本；
5. 三个基础镜像先由本机 tag 解析为唯一 `name@sha256`，再作为 Dockerfile build arg；
6. 四个目标 tag 尚不存在，两个输出目录尚不存在，拒绝覆盖旧候选；
7. `artifacts/control-plane/1.5.5/release-parts/*.part-*` 确实由 Git LFS 规则覆盖；
8. 执行者再次输入由版本和完整 SHA 组成的精确确认令牌。

任何门禁失败都会在构建前关闭。脚本源码中没有 `docker compose`、`docker push`、`git push` 或
`git lfs` 执行路径。

## 2. 本机 Buildx / SBOM 事实

2026-07-30 只读检查结果：

| 项目 | 结果 |
|---|---|
| Docker Engine | `29.6.2` |
| Docker Buildx | `0.35.0` |
| BuildKit | `0.31.2`，`docker` driver |
| Docker / OCI exporter | 可用 |
| `--provenance` / `--sbom` 参数 | Buildx 支持 |
| 本地 `syft` / `cosign` / `oras` / Docker Scout | 不存在 |
| 已缓存 SBOM generator 镜像 | 未发现 |
| `registry.local` | 不作为本流程依赖；不修改 daemon、DNS 或网络 |

因此当前能可靠设计和验证的是：Buildx 同一次 solve 同时导出 Docker tar 与 OCI tar，从 OCI
attestation manifest 读取 provenance，并核对 in-toto subject 与该 OCI image manifest digest。

当前不能宣称“完全离线 SBOM 已就绪”。严格模式要求调用者提供已固定到
`name@sha256:<64 hex>` 的 SBOM generator；构建结束后脚本仍会从 OCI tar 读取 SBOM 并再次核对
subject。未提供 generator 时，只有显式 `--allow-pending-sbom` 才能生成
`CANDIDATE_ARCHIVE_ONLY` 包，其 SBOM 状态固定为 `PENDING_PINNED_SBOM_GENERATOR`。

不论是否有离线 SBOM，本地 OCI index digest 都不是 registry manifest digest。只要镜像没有推送
到可用 registry，`registry_manifest_digest` 和现有 `scripts/verify_release_identity.py` 的严格结论
就必须保持 `PENDING_REGISTRY_PUSH` / `PENDING_REGISTRY_SBOM_BINDING`。

## 3. 基础镜像固定

四个应用 Dockerfile 新增以下可覆盖 build args，日常 Compose 默认 tag 行为保持不变：

- `PYTHON_BASE_IMAGE`
- `NODE_BASE_IMAGE`
- `NGINX_BASE_IMAGE`

打包器不会直接使用可变 tag 构建，而会先从本地镜像的唯一 RepoDigest 解析
`python@sha256:...`、`node@sha256:...` 和 `nginx@sha256:...`。缺镜像、无 RepoDigest或出现多个匹配
值均 fail closed。BuildKit 最大 provenance 会记录最终解析材料；这里的“可复现”表示源码、版本、
基础镜像和构建材料可追溯，不承诺不同内核/BuildKit 上的压缩字节天然相同。

## 4. 使用方式（本轮均未执行构建）

只生成计划，不访问 Docker daemon、不联系远端、不写文件：

```bash
RELEASE_VERSION=1.5.5 \
RELEASE_REVISION=<已提交并推送的40位source-SHA> \
make release-package-plan
```

计划输出会给出精确确认令牌。正式严格打包还必须准备 digest-pinned generator：

```bash
RELEASE_VERSION=1.5.5 \
RELEASE_REVISION=<已提交并推送的40位source-SHA> \
RELEASE_SBOM_GENERATOR=<generator-name@sha256:64位digest> \
RELEASE_PACKAGE_CONFIRM=<计划输出的精确令牌> \
make release-package-execute
```

若只是需要在 registry 和 generator 就绪前生成不可上线的离线候选，可直接显式调用：

```bash
python3 scripts/package_control_plane_release.py \
  --version 1.5.5 \
  --revision <已提交并推送的40位source-SHA> \
  --allow-pending-sbom \
  --execute \
  --confirm PACKAGE_CONTROL_PLANE_1.5.5_<同一40位source-SHA>
```

该降级路径仍要求 clean/pushed SHA，且不会把 `PENDING` 写成 `PASS`。

## 5. 输出合同

默认完整候选输出目录：

```text
/tmp/gpu-control-control-plane-1.5.5-candidate/
├── gpu-control-control-plane-1.5.5-images.tar.gz
├── release-candidate-evidence.json
└── evidence/
    ├── <component>.inspect.json
    ├── <component>.build-metadata.json
    ├── <component>.provenance.intoto.json
    └── <component>.sbom.intoto.json  # 仅严格 generator 模式
```

Git LFS 候选目录：

```text
artifacts/control-plane/1.5.5/release-parts/
├── gpu-control-control-plane-1.5.5-images.tar.gz.part-00
├── ...                              # 每片最大 128 MiB
├── SHA256SUMS.txt
├── release-candidate-evidence.json
└── README.md
```

`artifacts/control-plane/1.5.5/evidence/` 是可持续追加的测试/审计证据目录，不属于镜像分片输出。
打包器只要求新的 `release-parts/` 不存在，并拒绝覆盖该子目录；因此已有 evidence 会原样保留，
也不会让正式打包预检永久失败。

组合包由一次 `docker image save` 导出四镜像，再使用固定 gzip `mtime=0` 压缩和 128 MiB 分片。
脚本校验 OCI labels、API/Scheduler/Asset API 的 build version/revision 环境变量、四个互异的本地
image ID、gzip 可读性、整包与每片 SHA-256。证据文件中的 LFS OID 只是内容哈希候选；只有在后续
独立审核的 `git add`/commit/push、`git lfs fsck` 和远端对象检查完成后才能改为已上传。

## 6. 与严格发布校验器的关系

本脚本负责生成“本地候选归档证据”，不替代、不调用、不放宽
`scripts/verify_release_identity.py`。可用 registry 恢复后，应在脚本之外完成镜像 push，取得四个
不可变 registry manifest digest，并导出与这些 digest 真实绑定的 in-toto SBOM，然后运行：

```bash
make verify-release-identity
```

只有该校验通过、隔离烟测和既有 drain/迁移/回滚门禁通过，才允许讨论生产部署。当前生产任务与
`1.5.4` 容器不由本打包器读取、停止或替换。
