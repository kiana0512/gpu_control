# 六业务 API、100+ VU、GPU+CPU 混合负载测试手册

> 状态：`TOOLING IMPLEMENTED / R5 LOAD COMPLETED / CONTROL PLANE STABLE / ACCEPTANCE GATES PARTIAL`
> 日期：`2026-07-30`
> 适用范围：GPU Control 自有 API、admission、排队、调度、传输、产物和可观测性。
> 边界：没有修改 ImageClip 或 ModelViewCreator 的工作流、模型、参数、提示词、图拓扑或输出语义；
> 本手册和配套代码不会自动备份、部署、重启节点或更改生产配置。

2026-07-30 的生产 r5 已完整执行到 120 VU；权威数字、三项 fail-closed 原因和清场记录见
`73_2026-07-30_SIX_API_120VU_LOAD_RESULT.md`。本文件继续作为执行手册，不以手册内容替代
某次运行的原始结果和 SHA-256 证据。

## 1. 结论与入口

六接口混合负载入口已经改为 fail closed：

- `make load-test` 只生成计划，不创建 HTTP 客户端，不发送请求；
- `make load-test-execute` 才会尝试启动 Locust，但必须先通过环境授权、精确目标、确认令牌、
  scenario、外部素材和只读 HTTP 预检；
- 已知生产目标（包括 `10.3.34.11`）默认拒绝。它不是永久禁止，但只能在额外生产开关、变更单、
  有效窗口和“活动任务/队列均为 0”的生产预检全部通过后执行；
- Locust 文件本身会重复离线门禁，绕过 wrapper 不能绕过授权；
- 停止时只取消本 session 已经收到服务端 ID 且仍非终态的任务，不按租户、队列或时间范围批量取消。

配套文件：

| 文件 | 用途 |
|---|---|
| `scripts/run_six_api_load.py` | 默认 plan-only；显式 `--execute` 才启动 Locust |
| `tests/load/locustfile.py` | 六接口闭环、只读预检、分阶段 VU、指标、session teardown |
| `tests/load/scenarios/six_api_120.example.yaml` | 120 VU 计划示例；默认不可执行 |
| `tests/load/fixtures/six_api.example.yaml` | 外部素材路径合同示例 |
| `packages/gpu_control_core/load_testing.py` | 配置/素材校验、生产远端发布证据门禁、计划和结果清单；plan-only 不联网 |
| `tests/unit/test_load_testing.py` | 门禁、生产域、素材合同、脱敏和指标单测 |

本文件不代表任何真实压测已经通过。只有结果目录中的原始文件、`manifest.json` 和
`checksums.sha256` 可作为某次执行的证据。

## 2. 六个业务 API 与闭环

| 名称 | 资源 | 创建接口 | 状态/产物闭环 | 取消 |
|---|---|---|---|---|
| `imageclip_batch` | 三节点 GPU | `POST /api/v1/batches/imageclip-rgba` | 父批次 GET 至终态，下载最终 ZIP 并校验 body/header SHA-256 | 父批次 cancel；使用独立稳定 cancel 幂等键 |
| `modelview_roughness` | GPU | `POST /api/v1/services/modelview-roughness` | 同步等待并返回最终图片与 `X-Job-ID`，计入 `sync-e2e`；再 GET job 至终态并读取 artifact 合同 | job cancel |
| `uv_process` | CPU Asset Worker | `POST /api/v1/assets/uv/process` | GET asset job 至终态，下载全部最终产物并校验 SHA-256 | asset job cancel |
| `retopology_audit` | CPU Asset Worker | `POST /api/v1/assets/retopology/audit` | GET asset job；终态诊断/交付产物均按返回合同下载 | asset job cancel |
| `retopology_process` | CPU Asset Worker | `POST /api/v1/assets/retopology/process` | GET asset job，校验最终 QA/交付产物 | asset job cancel |
| `substance_bake` | 3090-B Windows fenced GPU Asset Worker | `POST /api/v1/assets/bake/process` | GET asset job，下载 bake 最终产物；物理 GPU fence 由服务端合同保持 | asset job cancel |

所有创建请求使用从环境注入的 `X-API-Key`。每次业务操作生成稳定的：

- `Idempotency-Key`：提交重试复用同一个值；
- `X-Request-ID`：绑定 session、API 和 ordinal；
- W3C `traceparent`：由同一 operation 确定性生成；
- `external_batch_id` 或 `external_asset_id`：前缀为 `loadtest:<session>`。

客户端不信任服务端返回的跨域 URL。状态、取消和 job artifact 使用已知同源 API 路径；最终 artifact
URL 也必须是以单 `/` 开头的同源相对路径。HTTP 成功但缺少 job ID、最终产物、图片 body 或正确
SHA-256，仍记录为 contract failure。

## 3. 授权门禁

### 3.1 所有执行共同要求

执行前必须同时满足：

1. `ALLOW_LOAD_TEST=true`，其他拼写和值均不接受；
2. `LOAD_TEST_TARGET` 是不含用户信息、query、fragment 和路径的 HTTP(S) origin；
3. `LOAD_TEST_TARGET_ALLOWLIST` 含完全相同的 origin。仅 hostname、通配符、不同 scheme 或不同 port
   都拒绝，例如目标为 `https://staging.example:8443` 时 allowlist 必须包含这一完整值；
4. `LOAD_TEST_SESSION_ID` 唯一且只含安全 ASCII 字符；生产执行必须是小写规范形式的 UUIDv4
   （例如 `4c843c3a-4950-4ea2-8772-9e34c69a05ab`），禁止复用历史 session；
5. `LOAD_TEST_CONFIRMATION_TOKEN` 与当前环境域、session、target 和 test tenant 清单精确绑定；
6. `LOAD_TEST_API_KEYS` 至少一个，且预检逐个确认所有轮转 key 的 `client.kind=test` 与 admission 可用；
7. `LOAD_TEST_TENANT_IDS` 必填、不可重复，按相同顺序与 `LOAD_TEST_API_KEYS` 一一对应；该精确清单也是
   运行期 watchdog 判断资产任务是否属于本 session 的安全边界；
8. `LOAD_TEST_ADMIN_BEARER_TOKEN` 可调用只读 admin 预检；
9. HTTPS 使用可读的 `LOAD_TEST_CA_FILE`，不能用关闭 TLS 校验代替；
10. `LOAD_TEST_RESULT_DIR` 是显式指定且尚不存在的新目录；
11. scenario 含六个正权重、完整阈值、至少一个 100+ VU stage，且
    `weights_confirmed: true`；集群门禁不可降到三台健康 GPU、三台在线 CPU Asset Worker 以下，
    CPU slot 与 Windows Substance slot 分别校验且均不得为 0；
12. fixture manifest 的所有素材存在、非空并位于仓库外，元数据和 SHA 合同全部通过。

确认令牌是“精确执行意图绑定值”，不是 API 密钥。plan 会给出当前配置对应的
`expected_confirmation_token`。`release-binding` 是 `source_revision`、五个环境声明 digest，以及
`origin/main` 证据提交/路径/blob SHA 三元组的规范 JSON SHA-256；值的域如下：

```text
tenant-binding = SHA-256("<tenant-1>,<tenant-2>,...")
非生产：SHA-256("gpu-control-six-api:nonproduction:<session>:<target>:<tenant-binding>:<release-binding>:execute")
生产：  SHA-256("gpu-control-six-api:production:<change>:<start>:<end>:<backup-dir>:<session>:<target>:<tenant-binding>:<release-binding>:execute")
```

因此非生产令牌不能复用于生产；生产窗口、变更单、session、target 或 tenant 清单任一改变都必须
重新确认。

### 3.2 非生产环境

`LOAD_TEST_ENVIRONMENT` 只能是 `test`、`staging` 或 `development`。若 target 的 hostname 在已知
生产清单中，或 hostname 含 `prod`/`production`，即使环境变量伪装成 staging 也进入生产门禁并拒绝。

### 3.3 生产环境额外门禁

生产执行除共同要求外，还必须满足：

- `LOAD_TEST_ENVIRONMENT=production`；
- `ALLOW_PRODUCTION_LOAD_TEST=true`；
- `LOAD_TEST_CHANGE_ID` 非空且只含受支持字符；
- `LOAD_TEST_WINDOW_START`、`LOAD_TEST_WINDOW_END` 是带时区 RFC3339，当前时间处于窗口内；
- 从当前时刻计算，scenario 的全部 stage 必须能在窗口结束前完成；
- `LOAD_TEST_BACKUP_DIR` 指向 `scripts/backup.sh --mode full` 在本窗口前创建的最新完整恢复点；
- scenario 的 `maximum_preexisting_gpu_jobs` 和 `maximum_preexisting_asset_jobs` 必须均为 `0`；
- scenario 峰值必须在已批准的 100～120 VU 范围内，最后阶段必须等于本场景峰值；100 VU 正式场景
  不需要为了满足旧版 120 VU 硬编码而额外升压；
- `LOAD_TEST_SOURCE_REVISION` 和五个 `LOAD_TEST_*_IMAGE_DIGEST` 必须完整；五个 digest 声明必须逐项等于
  远端发布证据中对应组件的 `local_image_id`，不能用 tag 或 `PENDING_REGISTRY_PUSH`；
- `LOAD_TEST_RELEASE_EVIDENCE_COMMIT` 必须是当前 GitHub `origin/main` 的完整 tip `E`，本地 harness
  `HEAD` 也必须等于 `E` 且 tracked worktree clean；`LOAD_TEST_RELEASE_EVIDENCE_PATH` 必须指向该提交下
  `artifacts/control-plane/<version>/deployment/live-deployment-receipt.json`，
  `LOAD_TEST_RELEASE_EVIDENCE_SHA256` 必须等于 `git show` 所得原始 receipt blob 的 SHA-256；
- receipt 必须是 `gpu-control-live-deployment.v1`，且同时声明
  `deployment_status=DEPLOYED_NOT_ACCEPTED`、`deployed=true`、`production_accepted=false`；它必须绑定
  源码 `S`、候选证据 path/blob SHA、五组件 OCI 身份、七个实际容器及 Windows Substance v6 实装身份；
- `LOAD_TEST_SUBSTANCE_AGENT_SHA256` 必须是 3090-B Windows 上四个
  `substance-baker-2026.08.03-v6` Agent 共用的实装脚本 SHA-256；它与 Git blob 原始字节 SHA 分开记录，
  允许受控的 LF/CRLF 差异，但不允许四实例之间漂移；
- 生产域确认令牌完全匹配上述 change/window/backup/session/target/tenant 清单。

runner 和 Locust 会分别以固定、不经 shell 的 Git argv 复验远端 tip 和 live receipt，再沿 receipt 的
path/blob SHA 读取 `gpu-control-release-candidate.v2` 父证据，验证五组件 local image ID、offline OCI
manifest/config、OCI labels 与 source revision。候选证据必须标记
`CANDIDATE_ARCHIVE_ONLY / deployed=false / production_accepted=false`，不能直接授权生产。
发布证据提交 `E` 可以是镜像源码提交 `S` 之后的纯证据提交，但 `S` 必须是 `E` 的祖先且 receipt 与
候选证据都必须明确绑定 `S`。环境变量只用于一致性对照，不是权威来源；任何远端、checkout、提交、
路径、blob、源码或组件身份差异都在创建 HTTP client 前 fail closed。

远端离线证据通过后，runner 与 Locust 还会各自执行一轮固定只读 live inventory：本机五个控制面/Worker
容器使用 `/usr/bin/docker inspect --format={{.Image}}`，3090-A 与 3090-B Worker 使用固定 IP、用户、端口、
`BatchMode=yes`、`StrictHostKeyChecking=yes` 的 SSH argv 调用同一只读 inspect。七项实测 `.Image` 必须
逐项等于 receipt/候选证据的 `local_image_id`，三台 Blender Worker 必须一致；另以固定 SSH argv
在 3090-B 读取 Windows Agent 实装脚本 SHA。命令不经 shell，目标、容器名和远端命令均不可由环境
注入；实测 inventory 会进入 plan、preflight 和 summary。Locust 启动前与 `test_stop`、wrapper 子进程
退出后三处均重新核验 live inventory/脚本 SHA，运行中漂移会使本轮失败，不能沿用启动时的快照。

备份门禁完全离线读取并逐文件校验：目录/文件权限与 owner、`BACKUP_COMPLETE` 的
`STATUS=COMPLETE`/`MODE=full`、`BACKUP_MANIFEST` 的 `BACKUP_FORMAT=2`/`MODE=full`/
`QUIESCE_CHECK=ENFORCED_PRE_AND_POST`、marker 固定的 `SHA256SUMS` 摘要、SHA 清单精确覆盖及每个
payload 摘要。`CREATED_UTC` 必须早于批准窗口，且距窗口开始不超过 scenario 的
`max_backup_age_hours`（示例 24h）；所有顶层备份文件的最新 mtime 也必须早于窗口，防止在窗口开启
后重新生成清单或修改 payload；同一父目录若存在更新的 complete full 备份，也会拒绝旧目录。
此外必须存在 `database.dump`、PostgreSQL roles、Git bundle/worktree、repository config、敏感配置、
host data、Docker/Git 清单和前后两份 quiesce gate；数据库必须是 `PGDMP` custom format、Git bundle
必须有合法 bundle header，前后 gate 的五项计数都必须为 0。只有 `MODE=full` 标签的空壳目录会拒绝。

这些变量只使 Locust 有资格进入只读预检，不表示预检一定通过，也不授权重启、部署、断开节点、
修改业务工作流或清理其他任务。

## 4. 只读 HTTP 预检

预检在任何 VU spawn 之前执行。任何请求失败、返回 shape 不符、任一 active status/Asset 的 500 条
审计窗口饱和或字段缺失，
均停止测试。读取项包括：

- `/api/v1/scheduler/capacity`：测试 client、batch admission、queue/running/slot 计数；
- `/api/v1/assets/capacity`：在线 worker 和 slot 计数；
- `/api/v1/workflows` 与 `/admin/workflows`：批准版本已启用且 template SHA-256 精确匹配；
- `/admin/nodes`：健康、模式、外部忙碌、ImageClip commit 和 pipeline SHA；
- `/admin/jobs?client_kind=all&status=<active>&limit=500`：逐 active status 拼接 GPU work，避免历史总数
  达到 500 后误判；
- `/admin/asset-processing?limit=500`：活动 asset work、worker、Substance 槽位和五个 asset route 合同。
- `/admin/load-sessions/<uuidv4>/collisions`：按本次规范 UUIDv4 精确计数 GPU job、ImageClip batch 和
  Asset job 三个持久命名空间；任一计数非零即拒绝。该查询不依赖最近 500 条历史页，因此不会在长期
  累积 test 审计记录后误把“历史页已满”当成 session 冲突，也不会删除旧记录。

普通非生产按 scenario 中的最大既有任务数判断。生产额外要求以下权威计数全部等于 0：

- GPU `queue_depth`、`cluster.running_jobs`、`cluster.used_slots`；
- admin GPU 活动任务数；
- asset 活动任务数和 `asset_capacity.used_slots`；
- 每个批准 GPU 节点及每个在线 asset worker 的 `current_jobs`。

健康 GPU 节点必须达到 scenario 的最低数，示例为三台；每台计入的节点都必须
`ONLINE + ACTIVE + not external_busy`，并与批准的 ImageClip commit/pipeline SHA 完全一致。模板 SHA
必须从签名候选发布回填，示例中的 64 个 `0` 是故意的阻断占位符。

预检证据会写入 `preflight.json`，不记录 API key 或 bearer token。

## 5. 流量模型与阈值

示例权重是规划占位，不是生产事实：

```yaml
imageclip_batch: 42
modelview_roughness: 23
uv_process: 12
retopology_audit: 8
retopology_process: 10
substance_bake: 5
```

它表示约 70% GPU-consuming、30% CPU。正式运行前应按最近 7 天成功创建量或双方约定的代表窗口
重新统计，记录查询时间、过滤条件、总样本数和服务 owner 审批，再设
`weights_confirmed: true`。不能为追求吞吐而修改外部工作流的模型、分辨率、采样步数、参数或输出。

默认 stage：

| VU | spawn rate | hold |
|---:|---:|---:|
| 1 | 1/s | 60s |
| 10 | 2/s | 120s |
| 25 | 5/s | 180s |
| 50 | 10/s | 300s |
| 100 | 20/s | 600s |
| 120 | 10/s | 600s |

每个 VU 一次只运行一个完整 create → poll → terminal → artifact cycle。不会用高频裸 POST 制造大量
无法归属的后台任务。可配置阈值必须完整包含：

| 阈值 | 计算 |
|---|---|
| `http_failure_rate_percent` | Locust 所有 HTTP/contract validation 的失败比例 |
| `submit_p95_ms` | 五个异步创建接口 submit 中最差 P95；不混入同步推理时间 |
| `sync_e2e_p95_ms` | 可选；`modelview_roughness` 从上传到最终图片响应的同步端到端 P95 |
| `poll_p95_ms` | 状态 poll 中最差 P95 |
| `artifact_p95_ms` | artifact download 中最差 P95 |
| `queue_p95_ms` | 服务端 created/queued → started 的业务 P95 |
| `retry_rate_percent` | 全部记录到的 retry 次数 / 已创建业务任务数 |

`sync_e2e_p95_ms` 为向后兼容的可选阈值；未配置时仍会在 `threshold_evaluation.observed` 单列观测值，
不会拿同步最终结果接口误判通用异步 submit SLA。结果同时提供 P50/P90/P95/P99、闭环吞吐、terminal status、admission status、重试、恢复、错误、
node/worker 分布、batch node distribution、artifact 数量/字节和 Locust 每 route 统计。缺失阈值测量
也判定不通过，而不是当成 0。联合验收默认使用 `lifecycle_mode: all_complete`，要求每条已登记任务
均进入成功业务终态且至少有一份校验通过的产物。最大压力场景可显式使用
`lifecycle_mode: bounded_stress`：必须已有校验成功子集，且停止时所有残留任务都经严格范围恢复、
取消或到达终态并再次观察到收敛；业务失败、poll timeout、artifact 合同失败和清场遗漏仍判失败。

测试期间每 5 秒额外只读采样 `/admin/nodes`、按每个 active status 查询的
`/admin/jobs?client_kind=all&status=...&limit=500`、
`/admin/asset-processing?limit=500`、scheduler capacity 和 asset capacity，写入脱敏的
`telemetry.jsonl`：

- 每个 GPU：`gpu_util_percent`、free/total VRAM、current/max jobs、mode、health；
- 每个 Asset Worker：status、current/max jobs；后端没有权威 CPU%，因此只报告
  `slot_occupancy_percent` P95/max，绝不由 load 或槽位伪造 CPU 利用率；
- 集群：GPU/Asset queue peak、used-slot peak、available-slot minimum；
- `summary.json`：每 GPU utilization P50/P90/P95/max、`>=90%` 饱和采样占比、显存最低值、槽位
  占用；预检纳入本次测试的每一台批准 GPU 都必须实际出现过 `>=90%` 样本，才满足“GPU 拉满”
  目标，不能用其中一台的高利用率掩盖另一台空闲；
- `six_api_coverage` 必须显示六项各至少形成一条服务端任务记录；随机权重恰好漏掉某项时，本轮
  即使 HTTP 指标良好也不算完整六接口证据。
- sampler 停止后额外采一条 `final_sample=true` 尾样本；完整性按实际采样窗口的连续 sequence、
  最大间隔、首样本和显式尾样本判断，不再用 Locust 总耗时倒推一个会受预检/清场影响的理论条数。

同一次采样也执行生产让路 watchdog。Asset admin 目前不提供 `client_kind`，所以只有 `client_id` 精确
命中本次 `LOAD_TEST_TENANT_IDS` 的活动任务才视为本 session；其他活动资产任务一律 fail closed。GPU
任务优先使用 admin 返回的 `client_kind`，并用 `tenant_id` 精确清单限定本 session；字段缺失时同样按
tenant 清单兜底。首次发现非本 session 活动任务即停止继续 spawn、退出 Locust，并由 `test_stop`
执行严格范围清场。任一 active status/Asset 的 500 条审计窗口饱和或返回 shape 漂移也立即停止。

采样只保留 node/worker ID、watchdog 冲突任务 ID/owner ID 和上述数值，不写 hostname、IP/agent
URL、labels、Codex task、文件名、job body、header 或凭据。任何采样 shape/数值/容量不变量错误、
超时或单次采集超过 5 秒都会写一条脱敏 error sample 并停止测试。Node Agent 的 GPU 源指标可能每
10 秒更新，因此 5 秒样本是时间加权观察，不应伪称每个样本都是一次新的硬件测量。

## 6. 外部 fixture 合同

真实素材不得放进 Git 仓库，也不得把凭据写入 YAML。使用
`tests/load/fixtures/six_api.example.yaml` 建立受控目录中的 manifest：

- ImageClip：ZIP 必须是 `ZIP_STORED`；manifest 的每帧 path、size、SHA-256 与 ZIP 精确一致；
- Roughness：代表性输入图片；
- UV：模型文件和可通过当前 `AssetCreateMetadata` 校验的 JSON；
- Retopology audit/process：`.blend`、metadata；process 的 reference filename 必须与 metadata 一致；
- Substance：low mesh、metadata，并按 profile 补齐 high mesh 和纹理。

建议将素材目录设为只读，由数据 owner 维护 SHA 清单。每次 plan 都会记录 path、存在性、size 和
SHA-256，但不把素材内容复制进结果目录；结果只复制 scenario 与 fixture manifest。

## 7. 执行前（Before）

### 7.1 变更、备份与现场冻结

1. 指定测试负责人、平台观察人、业务 owner、停止权限人和 change ID；生产至少双人复核；
2. 确认窗口内没有发布、迁移、节点维护、模型同步或其他压测；
3. **生产必须先备份、后压测：**在批准窗口开始前，由授权运维人员按
   `docs/17_BACKUP_AND_RESTORE.md` 和
   `docs/62_2026-07-30_REPRODUCIBLE_BACKUP_AND_ROLLING_UPDATE.md` 执行
   `scripts/backup.sh --mode full`，不得使用 `--skip-quiesce-check`；独立执行 restore `--verify-only`，
   并在备份完成后按运行策略恢复三节点接单模式。压测工具本身绝不执行备份、Drain 或恢复；
4. 记录 GPU Control commit、镜像 digest、数据库 revision、批准 workflow version/template SHA、
   ImageClip commit/pipeline SHA 和节点清单；
5. 确认 fixture 许可、脱敏、只读权限和磁盘空间；
6. 从示例复制新的 scenario/fixture manifest，回填真实权重、批准 SHA 和阈值，不直接覆盖历史结果。

### 7.2 先生成无网络计划

以下命令只读本地配置与素材，不发 HTTP：

```bash
cd /opt/gpu-control
unset ALLOW_LOAD_TEST ALLOW_PRODUCTION_LOAD_TEST LOAD_TEST_CONFIRMATION_TOKEN
export LOAD_TEST_SCENARIO_FILE=/srv/gpu-control/load-plans/<session>/scenario.yaml
export LOAD_TEST_FIXTURE_MANIFEST=/srv/gpu-control/load-plans/<session>/fixtures.yaml
export LOAD_TEST_TARGET=https://staging.example
export LOAD_TEST_SESSION_ID=<canonical-lowercase-UUIDv4>
export LOAD_TEST_ENVIRONMENT=staging
export LOAD_TEST_TARGET_ALLOWLIST=https://staging.example
export LOAD_TEST_RESULT_DIR=/srv/gpu-control/load-results/<unique-session>
make load-test
```

检查 plan 的 `execution_blockers`、六权重、stage、resource mix、素材 SHA、workflow SHA 和 target。
不要仅因为 plan 输出了 token 就执行。

### 7.3 注入 secrets 与最终确认

API key、tenant 标识、admin bearer 只通过受控环境或 secret manager 注入：

```bash
export LOAD_TEST_API_KEYS=<comma-separated-test-client-keys>
export LOAD_TEST_TENANT_IDS=<required-unique-tenant-ids-matching-keys-one-to-one>
export LOAD_TEST_ADMIN_BEARER_TOKEN=<read-only-admin-token>
export LOAD_TEST_CA_FILE=/path/to/approved-ca.pem
export ALLOW_LOAD_TEST=true
export LOAD_TEST_CONFIRMATION_TOKEN=<token-from-reviewed-plan>
make load-test
```

第二次 plan 必须显示 `EXECUTION_ELIGIBLE` 才可申请执行；它仍不发 HTTP。

生产还必须在生成最终 token **之前**设置：

```bash
export LOAD_TEST_TARGET=https://10.3.34.11
export LOAD_TEST_TARGET_ALLOWLIST=https://10.3.34.11
export LOAD_TEST_ENVIRONMENT=production
export ALLOW_PRODUCTION_LOAD_TEST=true
export LOAD_TEST_CHANGE_ID=<approved-change-id>
export LOAD_TEST_WINDOW_START=<RFC3339-with-timezone>
export LOAD_TEST_WINDOW_END=<RFC3339-with-timezone>
export LOAD_TEST_BACKUP_DIR=/srv/gpu-control/backups/<latest-complete-full-backup>
export LOAD_TEST_SOURCE_REVISION=<40-char-image-source-commit-S>
export LOAD_TEST_API_IMAGE_DIGEST=sha256:<64-hex-local-image-id>
export LOAD_TEST_SCHEDULER_IMAGE_DIGEST=sha256:<64-hex-local-image-id>
export LOAD_TEST_ASSET_API_IMAGE_DIGEST=sha256:<64-hex-local-image-id>
export LOAD_TEST_WEB_IMAGE_DIGEST=sha256:<64-hex-local-image-id>
export LOAD_TEST_WORKER_IMAGE_DIGEST=sha256:<64-hex-local-image-id>
export LOAD_TEST_RELEASE_EVIDENCE_COMMIT=<40-char-origin-main-tip-E>
export LOAD_TEST_RELEASE_EVIDENCE_PATH=artifacts/control-plane/<version>/deployment/live-deployment-receipt.json
export LOAD_TEST_RELEASE_EVIDENCE_SHA256=<64-hex-git-blob-sha256>
export LOAD_TEST_SUBSTANCE_AGENT_SHA256=<64-hex-Windows-installed-v6-script-sha256>
```

scheme/port 必须按真实部署填写，不能照抄示例猜测。plan-only 不访问 GitHub，因此生产计划会保留
“远端证据尚未复验”阻塞；它仍会给出绑定全部声明的 token。双人核对 token、窗口、target 与证据三元组
后再导出 `LOAD_TEST_CONFIRMATION_TOKEN`。`--execute` 会先完成远端复验，只有复验后的落盘
`plan.json` 才能成为 `EXECUTION_ELIGIBLE`。任何变量改变都重新 plan 和审批。

## 8. 执行中（During）

唯一执行入口：

```bash
make load-test-execute
```

wrapper 会拒绝已存在的结果目录，建立 plan/configuration 后启动固定 Locust argv。执行期间至少观察：

- API failure、429/5xx、submit/poll/artifact P95/P99；
- GPU/asset queue depth、oldest queued age、admission、scheduler loop/decision lag；
- PostgreSQL lock/connection、Redis error、gateway timeout、artifact I/O；
- 三台 GPU 的 health、模式、显存、温度、功耗、OOM、ComfyUI queue/history 和节点分布；
- CPU worker load/memory/heartbeat、3090-B Substance fence/槽位；
- `loadtest:<session>` 的 request/trace/external IDs，确认没有混入非本 session 任务。

watchdog 是自动二次保险，不替代生产 zero-work 门禁和独占测试窗口。当前调度不抢占已经 `RUNNING`
的 test job；停止 Locust 与发出本 session cancel 也不是同步强杀，真实任务可能仍需等待该测试任务在
安全点退出。因此生产环境仍默认要求开始前 GPU/asset 全部为 0，并在窗口内冻结普通业务提交。Asset
与 Substance 的下一次空闲 claim 会先选 production、再选 test，且两类各自保持 FIFO；这只能保护
尚未 claim 的任务，不能宣称运行中任务能瞬时让路。

遇到以下任一情况立即停止升压并终止 Locust：

- 出现非本 session 的活动业务、有人开始生产发布/维护；
- 节点离线、foreign queue、workflow/commit/SHA 漂移、数据库锁异常或数据一致性异常；
- OOM、持续高温、磁盘逼近安全线、artifact 校验失败；
- 失败率、retry rate、queue P95 或 route P95 超过审批阈值并持续两个观察周期；
- queue 持续单调增长且在一个预期服务周期内不恢复；
- 监控、审计或停止负责人失联。

优先发送一次正常中断，让 Locust 的 `test_stop` 执行 session teardown。禁止为“清空现场”而重启
服务、删除数据库记录、把其他任务标记为 `FAILED/CANCELLED`，也禁止全租户批量 cancel。

## 9. 停止与清理

正常停止先遍历内存 session registry，再做一次严格限定的只读 admin recovery scan，只对仍无终态且
可证明属于本轮的服务端 ID 调用已知 cancel URL：

- 使用创建该任务的 API key index；
- batch cancel 使用 `<external_batch_id>:cancel`；
- Asset 与 ImageClip 必须同时匹配独占测试 tenant、本轮 `started_at` 之后和
  `loadtest:<session_id>:` external ID；
- 同步 Roughness 没有 external ID，只允许匹配独占测试 tenant、本轮开始时间和固定
  `workflow_key=modelview-roughness`；
- 同一测试 tenant 中出现任何无法证明归属的活动行时 recovery scan fail closed，不猜测、不取消；
- 记录每个 cancel 的 status code，并持续 GET 到终态或清场超时；结果写入 `teardown.json`；
- 任一 GPU active status 或 Asset 审计窗口达到 500、owner/created_at/类型缺失或 tenant-key 映射异常
  均判清场证据失败；GPU 历史总数达到 500 不再误伤 active scan。不删除 job 或 artifact，也绝不触碰
  production/非 allowlist tenant。

这使“服务端已创建同步 Roughness job、客户端尚未收到 `X-Job-ID` 就被正常停止”的窗口可自动收敛。
`kill -9` 或主机掉电仍可能跳过整个 `test_stop`；这种情况必须按变更流程以相同三重范围做人工对账，
不能改成全租户批量取消。

## 10. 执行后（After）

1. 确认 `records.json` 中所有已知任务处于终态，或 `teardown.json` 有精确取消结果；
2. 用只读 admin/capacity 检查 queue 回到基线，不更改其他任务；
3. 检查 `summary.json` 的 `threshold_evaluation.passed`，并人工复核 P50/P90/P95/P99、吞吐、重试、
   recovery、错误和节点/worker 分布；
4. 对 artifact SHA mismatch、缺失 measurement、未知终态、非对称节点分配和拖尾逐项说明；
5. 运行 `sha256sum -c checksums.sha256`；wrapper 在 Locust 完全退出后会刷新清单，覆盖最后 flush 的
   CSV/HTML/JSON；
6. 保存 change、观察记录、图表截图/导出、异常时间线和是否通过的签字；
7. 只有证据完整且 owner 接受后，才把本次结果写成 `PASS`；被中止、预检失败或证据缺失应准确写
   `ABORTED`、`PREFLIGHT_REFUSED` 或 `INCONCLUSIVE`。
8. 本机生成的 `manifest.json` 和 `summary.json` 固定写入
   `external_anchor_status=PENDING_GIT_PUBLISH`；最终脱敏 result manifest 尚未提交并推送到批准的
   GitHub `origin/main` 前，即使阈值通过也不能标记正式 `PRODUCTION_ACCEPTED`。

结果目录布局：

```text
<result-dir>/
├── configuration/
│   ├── scenario.yaml
│   └── fixtures.yaml
├── plan.json
├── preflight.json
├── telemetry.jsonl
├── events.jsonl
├── records.json
├── summary.json
├── teardown.json
├── locust.html
├── locust.json
├── locust_stats.csv
├── locust_stats_history.csv
├── locust_failures.csv
├── manifest.json
└── checksums.sha256
```

Locust 可能按是否出现 exception 省略空的可选 CSV；以 `manifest.json` 的实际清单为准。

## 11. 结果归档与恢复边界

结果清单不含 API key/bearer 值，只记录 key 数量和每个任务使用的 key index。结果仍包含内部 target、
任务 ID、fixture 路径和性能数据，应存放在受控审计目录。建议在结果目录外创建归档：

```bash
cd /srv/gpu-control/load-results
sha256sum -c <session>/checksums.sha256
tar -czf <session>.tar.gz <session>/
sha256sum <session>.tar.gz > <session>.tar.gz.sha256
```

把归档和摘要复制到独立受控存储后再次验证。不要将真实 fixture、secrets 或未脱敏日志提交 Git。
Git 只允许归档脱敏后的最终 manifest/摘要及其 SHA；外部 commit/ref 回执形成后，才能在单独验收记录
中解除 `PENDING_GIT_PUBLISH`，不得原地重写已产生的结果包再重新计算 checksums。

负载工具只创建业务任务，不迁移 schema、不改节点模式、不更新镜像，因此正常停止不需要数据库
restore。若压测暴露真实数据/系统损坏，先冻结现场并保存故障快照，再依据已验证
`BACKUP_COMPLETE` 和 `docs/62_2026-07-30_REPRODUCIBLE_BACKUP_AND_ROLLING_UPDATE.md` 的隔离恢复
流程处理；不能为了快速恢复指标而覆盖生产数据库。

## 12. 离线验证与当前证据状态

代码合入或修改后，应在依赖完整的隔离开发环境执行：

```bash
.venv/bin/ruff check packages/gpu_control_core/load_testing.py scripts/run_six_api_load.py tests/load/locustfile.py tests/unit/test_load_testing.py
.venv/bin/mypy packages/gpu_control_core/load_testing.py
.venv/bin/pytest tests/unit/test_load_testing.py -q
python3 -m compileall -q packages/gpu_control_core/load_testing.py scripts/run_six_api_load.py tests/load/locustfile.py tests/unit/test_load_testing.py
```

这些命令均不启动 Locust、不访问目标、不重启服务。1.5.6 最终源码的 load harness 回归为
`41 passed / 0 failed`；完整禁网仓库回归为 `272 passed / 5 skipped / 0 failed`，Ruff 与全项目 mypy
也通过。生产 r5 已实际完成 1→10→25→50→100→120 VU，权威结果和清场证据见 73 号文档，不能再写成
`NOT EXECUTED`。

修正版 r6 已完成离线计划和 82GB 完整备份复验，状态为 `EXECUTION_ELIGIBLE`、六接口素材齐全、
无 blocker；在本段记录时尚未发送 r6 流量。只有 r6 结果目录、manifest/checksums、终态清场与本文阈值
全部通过后，才能将 r6 写成容量通过。
