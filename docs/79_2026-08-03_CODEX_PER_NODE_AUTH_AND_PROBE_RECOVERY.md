# 2026-08-03 Codex 三节点独立认证与 Worker 1.2.3 探针恢复记录

日期：2026-08-03（Asia/Singapore）

状态：`DEPLOYED_NOT_ACCEPTED`

范围：三台 Linux/WSL Asset Worker 的 Codex 认证、真实调用探针和 Worker 安全滚动更新。

边界：不修改 ImageClip、ModelViewCreator、Retopology Skill、外部 workflow、模型、prompt、图拓扑或输出语义。

## 1. 结论

三台 Asset Worker 已分别使用自己的可写持久化 `CODEX_HOME`，并以同一
`li3d/blender-worker:1.2.3` 镜像完成滚动更新。发布后的真实 `codex exec` 探针均返回精确
`CODEX_HEALTH_OK`：

| 节点 | Worker | 认证 | 探针 | 延迟 |
| --- | --- | --- | --- | ---: |
| 4090 主控 | `asset-control-4090` | `AUTHENTICATED` | `HEALTHY` | `7,382 ms` |
| 3090-A | `asset-worker-3090-a` | `AUTHENTICATED` | `HEALTHY` | `11,696 ms` |
| 3090-B | `asset-worker-3090-b` | `AUTHENTICATED` | `HEALTHY` | `6,655 ms` |

本次只替换 CPU Asset Worker。三台 ComfyUI 均未停止、启动或重启，没有调用模型释放接口，没有清理
模型缓存，也没有提交业务 prompt 伪造预热。该记录证明认证和真实调用探针恢复，不等同于重拓扑完整
业务验收；正式状态仍为 `DEPLOYED_NOT_ACCEPTED`。

## 2. 根因

原部署把同一只读 `auth.json` 复制到临时 `CODEX_HOME` 后运行 Codex。该设计有四个问题：

1. 多节点共享同一 refresh-token 链并并发刷新时，服务端会使旧 refresh token 失效，4090 与 3090-B
   出现 `refresh token was already used`、`token_expired` 和 `401 Unauthorized`；
2. 每次调用都从只读源重新复制认证文件，Codex 成功轮换后的 token 无法持久保留；
3. 旧探针只凭“认证 JSON 非空”就显示 `AUTHENTICATED`，没有证明真实模型调用成功；
4. GPU 节点 Worker 的 `SSL_CERT_FILE=/run/certs/lan-ca.crt` 是控制面内网单根证书。把它继承给 Codex
   子进程会替换系统公共 CA 集，3090-B 因此出现公共 TLS `UnknownIssuer`。

此外，旧 Codex 子进程缺少统一串行锁、输出上限和完整的超时终止/回收路径；探针与真实规划同时使用
同一节点认证、子进程输出阻塞或超时残留，都会进一步放大不稳定性。

## 3. 已部署的 Worker 变更

Worker 源码提交：

```text
5641cdf08c31b5cead6009fa309f1ce12bcab229
fix: isolate Codex auth per asset worker
```

前序已归档提交：

```text
d5391bf05e810d2259d219af88361a32ad0166bb
fix: renew long Substance leases and recover agents

024c04681c62f1ce2291e04a4ccd9d240d171dbf
docs: record Substance lease recovery hotfix
```

`5641cdf` 包含以下 Worker 运行时修复：

- 每个物理节点使用独立、可写、持久化的 `CODEX_HOME`；认证源只在运行目录首次为空时原子 bootstrap；
- `auth.json` 权限为 `0600`，运行目录为 `0700`，轮换后的 refresh token 保留在本节点运行目录；
- 三节点分别重新认证，不复制、复用或在文档中记录任何 token；
- 同一 Worker 内的健康探针和重拓扑 Codex 规划通过异步锁串行执行，避免同一认证并发刷新；
- 健康检查运行真实的只读、无工具 `codex exec`，只接受精确输出 `CODEX_HEALTH_OK`；
- `AUTH_REFRESH_REUSED`、`AUTH_UNAUTHORIZED`、`RATE_LIMITED`、`NETWORK_TLS`、
  `NETWORK_TIMEOUT` 和普通探针失败被分开归类；
- 仅从 Codex 子进程环境移除精确的内网 `SSL_CERT_FILE=/run/certs/lan-ca.crt`；Worker 到 Asset API 的
  TLS 路径继续使用原内网 CA；
- 探针增加节点稳定 jitter，避免三台机器同时刷新认证；
- Codex 规划具有硬超时、取消时 terminate/kill/reap、并行输出读取和 `16 MiB` 输出保护；
- 健康检查不会把认证内容、模型回复或 stderr 原文写入 API/Web，只上报状态、分类错误码和延迟。

## 4. 镜像与传输证据

三节点运行镜像：

```text
tag:       li3d/blender-worker:1.2.3
image ID:  sha256:0f419697d18506f0873aa5df950f9a97dacb9eb2b7080b4e9e4ae590aa5f7b46
digest:    li3d/blender-worker@sha256:0f419697d18506f0873aa5df950f9a97dacb9eb2b7080b4e9e4ae590aa5f7b46
local tag: registry.local:5000/li3d/blender-worker:1.2.3
```

本次发布时 `registry.local` 不可解析且本机 registry 服务未运行；上面的 registry 名称只是本机保留的
同镜像标签，**不表示镜像已推送到 registry**。实际分发使用下面的 gzip 归档，A/B 两端在加载前均核对
归档 SHA-256，加载后再核对 image ID。正式冻结前仍需把洁净构建产物发布到可用的不可变镜像仓库。

本次传输归档：

```text
/tmp/gpu-control-worker-image.ebWosa/li3d-blender-worker-1.2.3.tar.gz
SHA-256: 323268e870632e502331ca99b05688a92b41d28446ef66d6b498564235ded9dc
```

`/tmp` 归档是本次滚动传输介质，不是长期灾难恢复备份。正式回滚依赖三机仍保留的
`li3d/blender-worker:1.2.2` 和下节配置备份。

### 4.1 构建来源说明

Worker 主程序和 Compose 挂载变更均已进入 `5641cdf`。构建时工作树另有尚未提交的控制平面候选；
Dockerfile 会复制 `packages/`，因此本次运行身份应以完整不可变 image digest 为权威证据，不能仅凭
Git commit 宣称整个镜像可由 clean tree 字节级复现。候选中的 `gpu_control_core/settings.py` 不参与
Blender Worker 的认证与探针执行路径，但正式冻结前仍应在干净提交上重建并生成新的 digest，或归档
完整构建上下文。此限制不影响当前三节点使用同一镜像的事实，但当前版本不得标记为 `FROZEN`。

## 5. 三节点持久化挂载

| 节点 | Host 持久目录 | Container 目录 | Bootstrap 源 |
| --- | --- | --- | --- |
| 4090 主控 | `/opt/gpu-control/runtime/codex/control-4090-home` | `/home/assetworker/.codex` | `/run/secrets/codex-auth.json`（只读） |
| 3090-A | `/opt/gpu-control/runtime/codex/worker-3090-a-home` | `/home/assetworker/.codex` | `/run/secrets/codex-auth.json`（只读） |
| 3090-B | `/opt/gpu-control/runtime/codex/worker-3090-b-home` | `/home/assetworker/.codex` | `/run/secrets/codex-auth.json`（只读） |

控制面 Compose 使用：

```text
${CODEX_RUNTIME_ROOT}/control-4090-home:/home/assetworker/.codex
```

GPU 节点 Compose 使用：

```text
${CODEX_RUNTIME_ROOT}/${NODE_ID}-home:/home/assetworker/.codex
```

默认 `CODEX_RUNTIME_ROOT=/opt/gpu-control/runtime/codex`。Bootstrap 源只负责首次初始化；目录一旦存在
有效 `auth.json`，容器替换不会再用只读源覆盖节点已经轮换的认证。认证文件不得进入 Git、镜像、普通
日志、截图或本交付文档。

## 6. 配置备份与回滚基线

3090-A 与 3090-B 在替换前分别保留：

```text
/opt/gpu-control/deploy/gpu-node/compose.yaml.pre-codex-5641cdf
/opt/gpu-control/.env.pre-codex-5641cdf
```

4090 主控的 Compose 由 Git 提交回退；三台机器均保留旧镜像：

```text
li3d/blender-worker:1.2.2
```

本次没有数据库 schema 或数据迁移，没有重启 PostgreSQL、Redis、Nginx、Scheduler 或 ComfyUI，因而
无需执行数据库 downgrade。上述配置副本含环境配置，必须继续按生产敏感文件权限管理，不复制到普通
文档或非受控附件。

## 7. DRAINING 安全滚动流程

每次只处理一台节点，另外两台保持服务能力：

1. 将目标 GPU 节点置为 `DRAINING`，停止向该节点分配新任务；
2. 等待目标节点 GPU job、Asset job 和 Worker `current_jobs` 全部归零；若出现真实用户任务，暂停更新；
3. 记录当前 Worker 镜像和配置备份；确认目标节点持久化 Codex 目录权限正确；
4. 只替换该节点的 Asset Worker 为 `1.2.3`，不操作 ComfyUI 容器；
5. 等待 Worker 心跳恢复，执行节点自己的真实 Codex 探针；
6. 只有 `AUTHENTICATED / HEALTHY / CODEX_HEALTH_OK` 全部满足，才把该节点恢复到原接单模式；
7. 按 4090 → 3090-A → 3090-B 的单节点顺序重复，禁止三节点同时排空。

该过程不清理 ComfyUI 输入、输出、模型或缓存，不调用 `/free`，不执行
`docker stop/start/restart` ComfyUI，也不更改任何业务 workflow。

## 8. 发布后验证

| 验证项 | 结果 |
| --- | --- |
| 三节点 Worker 镜像 | `1.2.3`，image ID/digest 完全一致 |
| Codex CLI | `codex-cli 0.146.0-alpha.3.1` |
| 4090 真实探针 | `AUTHENTICATED / HEALTHY / 7,382 ms` |
| 3090-A 真实探针 | `AUTHENTICATED / HEALTHY / 11,696 ms` |
| 3090-B 真实探针 | `AUTHENTICATED / HEALTHY / 6,655 ms` |
| Worker Codex 定向单元测试 | `13 passed` |
| 持久化认证 | 三个独立 Host 目录；容器替换后仍使用各自可写 home |
| ComfyUI | 未重启、未停止、未清缓存、未显式释放模型 |
| 外部 pipeline | 未修改 |

探针延迟只表示一次真实控制调用的端到端时间，不等同于重拓扑任务吞吐，也不应与 GPU 推理时间相加。
本次没有为了验收而创建生产重拓扑任务；后续自然业务任务仍需观察规划成功、取消、超时、artifact 和
完整阶段耗时。

## 9. 明确未部署的控制平面候选

以下内容在本记录生成时仍是工作树候选，**没有**随 Worker 1.2.3 部署到正在运行的 API、Asset API、
Scheduler 或 Web，不能写成已上线能力：

- API 按精确 Linux Worker ID 展示 Codex 状态，避免 3090-B Windows Baker 心跳覆盖展示；
- Asset API 仅允许拥有新鲜 `HEALTHY` Codex 探针的 Worker 领取 `RETOPOLOGY_PROCESS_V1`；
- 失败心跳保留历史 `codex_last_success_at`，不以空值覆盖最后成功证据；
- Codex 探针 freshness 配置与相关 API/Asset API 回归测试；
- Web 仅对健康探针计算平均延迟，并展示分类后的认证/TLS/超时错误；
- Substance 租约恢复闭锁的自动释放候选及其集成测试。

这些候选必须独立完成审查、提交、镜像构建、零任务门禁和逐服务滚动后，才能加入正式生产证据。Worker
镜像部署成功不能替代控制平面发布。

## 10. 回滚方案

单节点回滚，禁止同时回滚三台：

1. 将目标节点设为 `DRAINING`，等待 GPU/Asset/Worker 活动作业全部为 0；
2. 3090-A/B 可恢复对应的 `.env.pre-codex-5641cdf` 与
   `compose.yaml.pre-codex-5641cdf`；4090 使用 Git 把 Compose 回退到 `5641cdf` 的父版本；
3. 把 `ASSET_WORKER_IMAGE_TAG` 切回 `1.2.2`，只重建目标 Asset Worker；
4. 保留当前节点私有认证目录的受控备份，不删除、跨节点复制或公开其中的 `auth.json`；
5. 验证旧 Worker 心跳和 CPU Asset 能力后恢复目标节点原模式；
6. 全程不操作 ComfyUI，不清缓存，不替换外部 pipeline。

若问题只发生在某一节点认证，应只对该节点重新执行设备认证；不得把另一台节点的 `auth.json` 复制过来。
若 `1.2.2` 回滚恢复了旧的临时认证行为，应保持 Codex 依赖任务停止接纳，直至单节点认证验证完成，不能
用“文件存在”代替真实 `codex exec`。

## 11. 验收边界与待归档项

本记录关闭的是三节点共享/易失认证、错误 TLS 继承和虚假健康展示中的 Worker 运行时部分。正式
`PRODUCTION_ACCEPTED` 仍需：

- 从干净、已推送 source revision 可复现构建 Worker，并归档新 digest、SBOM 与签名；
- 对尚未部署的控制平面候选完成独立发布和回滚证据；
- 使用真实但受控的重拓扑作业验证规划、超时、取消、重启恢复和正式 FBX/BLEND artifact；
- 连续观察三节点 token 轮换不再发生 `AUTH_REFRESH_REUSED`；
- 回填 Git 远端同步、发布审计 request ID、操作者和最终签字。

完成上述项目之前，本次状态保持 `DEPLOYED_NOT_ACCEPTED`，不得标记为 `FROZEN` 或
`PRODUCTION_ACCEPTED`。
