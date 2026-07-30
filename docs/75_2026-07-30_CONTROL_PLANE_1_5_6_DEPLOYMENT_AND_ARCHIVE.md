# 2026-07-30 GPU Control 1.5.6 部署、归档与回滚证据

记录日期：2026-07-30；状态补记：2026-07-31

当前状态：`DEPLOYED_NOT_ACCEPTED`

范围：GPU Control API、Scheduler、Asset API、Web、部署与归档；不修改 ImageClip、
ModelViewCreator、Retopology Skill 或任何外部 workflow、模型、prompt、图拓扑和输出语义。

## 1. 结论

GPU Control `1.5.6` 四个控制面服务已经在生产活动任务为 0 的窗口按
Scheduler → Asset API → API → Web 顺序完成热更新，并通过健康、统一 source identity、版本端点、
Scheduler 单主锁、数据库事务和节点/Worker 空闲核对。Retopology 生产配置为 advisory：几何质量失败
保留告警和诊断，但满足输入身份、manifest、完整性和 SHA 硬门禁的同一不可变 BLEND/FBX 字节会以
正式 `blend`/`fbx` kind 交付，而不是只返回日志或候选预览。

“已部署”不等于“已生产验收”。registry manifest digest、固定 SBOM generator、动画管家固定 B 系列
速度基准、完整故障注入及连续七天观察未闭环，因此禁止写成 `FROZEN`、
`PRODUCTION_ACCEPTED` 或将本地 image ID 冒充 registry digest。

## 2. 统一源码与镜像身份

四个生产镜像均绑定已推送源码：
`310a44c70c20f7cbfc601d19e19858380a61c20a`。

| 组件 | 生产 tag | 本地不可变 image ID | registry manifest digest |
| --- | --- | --- | --- |
| API | `gpu-control-api:1.5.6` | `sha256:26f622257facfbc74199c6d266b2a02e31b28dad6910596f5c4bd8fecf458cf4` | `PENDING_REGISTRY_PUSH` |
| Scheduler | `gpu-control-scheduler:1.5.6` | `sha256:c2c420e6fa8fd2d8d84852e5b509f5248cf6e1e8b1239c6f8053eee5e3a6845b` | `PENDING_REGISTRY_PUSH` |
| Asset API | `unified-scheduler-asset-api:1.5.6` | `sha256:f83bed46d7540de4cf7d08e4cff8d7675dd7dd4675bf13d301226f5f5c4cb01f` | `PENDING_REGISTRY_PUSH` |
| Web | `gpu-control-web:1.5.6` | `sha256:54005b4091b37de0805f2561d3b53a3e470e2b1ed3a795ec6bd7b0e98b0ebc14` | `PENDING_REGISTRY_PUSH` |

四镜像 OCI label 均为 version `1.5.6`、revision `310a44c70c20f7cbfc601d19e19858380a61c20a`；
API 与 Asset API 的运行时版本/provenance 检查均对齐。上表第三列是本机 Docker image ID，不是 registry
digest；registry 未推送前第四列必须保持 `PENDING_REGISTRY_PUSH`。

## 3. 零任务热更新

发布前完成以下只读门禁：

- GPU job、父批次、Asset job 活动数均为 0；
- 三个 GPU 节点和所有目标 Worker 健康，没有遗留 fence、recovery、foreign queue 或活动 lease；
- 完整恢复基线、旧镜像 rollback tag 和新镜像 source identity 可定位；
- 全量代码、真实 PostgreSQL 锁、前端和 Compose 回归通过。

发布逐服务执行；只有前一服务健康后才继续下一项。没有重启 PostgreSQL、Redis、ComfyUI、GPU
Worker、Asset Worker，也没有迁移或改写外部业务 pipeline。生产非密钥部署指针更新为：

```text
APP_IMAGE_TAG=1.5.6
GPU_CONTROL_VERSION=1.5.6
GPU_CONTROL_REVISION=310a44c70c20f7cbfc601d19e19858380a61c20a
RETOPOLOGY_QA_ENFORCEMENT=advisory
```

上线后核对结果：

- 四个控制面服务健康；API/Asset API 为 `version_aligned=true`、
  `provenance_complete=true`；
- Scheduler advisory lock 精确为 1，锁连接不持有长期事务，数据库长期
  `idle in transaction` 为 0；
- 节点和 Worker 可接单，活动任务、队列和租约清零；
- Retopology advisory、leader epoch、上传/下载/取消竞态、两阶段 artifact publish、callback lease、
  Substance persistent fence/recovery 均为该 revision 的实现。

## 4. Retopology 正式交付证据

生产 advisory 任务对同一不可变候选字节发布了动画管家可识别的正式 kind：

| kind / filename | 大小 | SHA-256 |
| --- | ---: | --- |
| `blend/retopology_final.blend` | `14,798,992` bytes | `0dd443337087e30bb1fd2929cf6715c82460a1bb13b7c43c745c89a2c0757f6f` |
| `fbx/retopology_final.fbx` | `95,692` bytes | `8d254b5f3aaea5f13b73b2b2f1bf9b2ed2147e6fac7f6bc0c69f014ab81058f7` |

诊断 JSON、prompt、comparison 和 QA warning 继续保留，但不能替代正式 BLEND/FBX。advisory 只放宽
几何质量阈值；0 字节、SHA/size 不一致、manifest/输入身份冲突、源对象保护失败仍必须 fail closed。
切回 `strict` 只需要在零 Asset 活动任务窗口恢复配置并滚动 Asset API，不需要修改外部 Skill。

## 5. 回滚点

更新前保存以下可定位标签：

| 组件 | rollback tag |
| --- | --- |
| API | `gpu-control-api:rollback-1.5.5-20260730` |
| Scheduler | `gpu-control-scheduler:rollback-1.5.5-20260730` |
| Asset API | `unified-scheduler-asset-api:rollback-1.5.5-20260730` |
| Web | `gpu-control-web:rollback-1.5.5-20260730` |

旧 Scheduler/Asset API 不理解 1.5.6 的 Substance pending/fence/recovery 标签。回滚前必须停止新测试
流量，等待或精确清理本 test session，确认 GPU/Asset/Windows Baker 全部为 0；只能删除
`substance_bake_drain_owner=asset-api` 明确拥有的标签，不能清除管理员或其他模块状态。若恢复证据不完整，
保持 3090-B DRAINING 并人工核对，禁止强行改回 ACTIVE。完整锁序和清场步骤见 74 号记录。

## 6. 离线归档与 Git LFS

离线归档当前状态是 `CANDIDATE_ARCHIVE_ONLY`。构建工作目录为
`/tmp/gpu-control-control-plane-1.5.6-candidate`；可交付大文件已拆分并以 Git LFS 对象保存在
`artifacts/control-plane/1.5.6/release-parts/`。

| 项目 | 值 |
| --- | --- |
| 合并文件 | `gpu-control-control-plane-1.5.6-images.tar.gz` |
| 合并文件大小 | `190,862,791` bytes |
| 合并 SHA-256 | `577b251c215f252f0920e7db007c9b5e5e2993db2b4dd17e846d0c8dacf5bb87` |
| part-00 | `134,217,728` bytes；`fc307632f1ce17973f99a0158e4abfe0f26ee0f2edd92ccd15adf226c5393e0a` |
| part-01 | `56,645,063` bytes；`bd2f5a2561f9d91c752f6e54bcfe6a15ab4cfb82fd7b7c757d360f1c8063a82d` |
| offline provenance | `VERIFIED_OFFLINE_OCI` |
| SBOM | `PENDING_PINNED_SBOM_GENERATOR` |
| registry identity | `PENDING_REGISTRY_PUSH` |

重组后必须先执行仓库提供的 SHA 清单校验和 `gzip -t`，再 `docker image load`。离线 OCI identity 只能
证明本地导出内容，不能填入 V4.1 回执的 registry digest 字段。

## 7. 代码与部署验证

1.5.6 发布源码完成：

- Python 全量 `272 passed / 5 skipped / 0 failed`；
- Scheduler 受影响专项 `42 passed`，真实 PostgreSQL 17 单主锁/接管 `5 passed`；
- Ruff、全项目 mypy、compileall、`git diff --check` 和两份 Compose render 通过；
- Web ESLint、Prettier、Vitest `10/10`、Vue 类型检查和 Vite production build 通过；
- API/Asset API 版本端点、四容器健康、单主锁、无长期 idle transaction、三节点与 Worker 心跳在线
  均在热更新后复核。

这些验证只证明代码、镜像身份和受控部署，不替代业务素材速度与输出质量联合验收。

## 8. r6/r7/r8 压测补充证据

r6 在开始业务负载前因 watchdog 初始 Admin 扫描触发 429 而 fail closed，运行约 1 秒且创建 0 个
任务。源码 `3e0ecd4` 增加扫描节流和 429/5xx 有界重试，保留了该安全失败证据。

r7 在 1.5.6 上完成 31 分钟、最高 120 VU 的六 API 有界压力：`39,776` 个 HTTP 请求、0 失败，
184 个任务登记、99 个成功并验证产物；六 API coverage、七项业务阈值、有界生命周期、生产 watchdog、
三 GPU 饱和、3/3 GPU 槽、13/13 Asset 槽和 120/120 清场均通过。唯一 fail-closed 项是 378 个有效
遥测样本中的最大相邻间隔 `7,699 ms`，比严格上限 `7,500 ms` 多 `199 ms`。

源码 `682b2c3` 已修复停止流程与在途采样的关闭顺序并通过 42 项 harness 回归；它不属于生产镜像
revision，也不能把 r7 原始失败改写为通过。新的独立运行只有在机器报告、manifest 和 checksum 全部
生成后才能另行补录。r7 原始证据与 SHA 见 73 号记录。

r8 已生成独立、机器可复核的通过结果：执行进程退出码 0，`39,778/0` HTTP 请求/失败，
`151/66` 登记/验证成功任务，六 API、七项阈值、生产让路、有界生命周期、作用域恢复、三卡饱和与
遥测完整性全部通过；teardown 为 `120/120 accepted`、`120/120 settled`。379 个有效样本的最大
间隔为 `5,004 ms`，关闭了 r7 的 199 ms 遥测红灯。持久证据在
`/srv/gpu-control/load-results/sixapi-20260730-r8`，完整结果与关键 SHA 见
`76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md`。

r8 同时确认生产 Scheduler loop lag Gauge 存在累计 deadline 的观测算法偏差。提交 `521ab58` 已加入
独立 500 ms probe 修复并完成专项测试，但尚未构建或部署，不能计入上文四个生产镜像，也不能据此
改变生产 source revision。该观测修复与 3090-B DCGM 缺口列入后续受控变更。

## 9. 尚未关闭的正式验收门禁

- 固定 B1/B6/B30/B64/B97/B300 及 `3×B97`；
- 同素材本机与 1/2/3 节点 A/B、三节点 B97 速度目标；
- 节点离线、损坏上传、prompt timeout、Scheduler restart、workflow drift、artifact tamper、cancel
  权限和幂等重放等完整故障矩阵；
- 固定 SBOM generator、registry push 与四组件 registry manifest digest；
- 10% → 50% → 全量灰度和连续七天观察；
- 动画管家与 GPU Control 双方最终签字。

上述任一项未完成时，唯一允许的总体状态仍是 `DEPLOYED_NOT_ACCEPTED`。
