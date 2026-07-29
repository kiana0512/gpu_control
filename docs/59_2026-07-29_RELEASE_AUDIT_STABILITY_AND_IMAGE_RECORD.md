# 2026-07-29 统一调度中心发布审计、稳定性与镜像记录

## 1. 发布结论

本轮变更通过真实三节点验收，可以进入交付：GPU 推理仍由 4090、3090-A、3090-B 三节点共同调度；
UV/重拓扑由独立 Asset Worker 队列执行，不占 GPU 槽；Codex CLI 与 RetopoFlow 运行状态有独立页面。
全程未修改 ImageClip、ModelViewCreator 仓库、工作流 JSON、模型、提示词或推理参数，也未重启
ComfyUI。

## 2. 已收口问题

- 移除新任务的人工复核状态与批准/驳回动作；严格 QA 通过即原子交付，失败返回结构化诊断。
- 历史 `WAITING_REVIEW` 数据由迁移脚本一次性收口，WebUI 只保留兼容读取，不再产生新人工复核任务。
- UV 增加退化 UV 面的自动修复和二次 QA；此前真实失败输入连续 6/6 一次成功。
- 重拓扑允许 `target_faces=50～50000`，支持 `topology_style=quad_dominant`，并在报告中公开
  quad/triangle/N-gon 统计。
- QuadriFlow 增加确定性几何预检；多组件、开放边界或非流形高模不会再因 Agent 建议波动而随机失败。
- RetopoFlow 在三台 Worker 中安装固定 revision，并通过真实 Blender operator 健康探针；没有把交互式
  RetopoFlow 虚构为无人值守算法。
- `/codex` 独立展示三台 CLI 的版本、认证、真实调用探针、当前/最近任务输入输出上下文。
- `/nodes` 只保留 GPU/ComfyUI 运维信息；资产任务详情默认只展示状态、进度、质量、最终模型和下载，
  API、SHA、事件、Agent 证据和日志折叠到高级诊断。
- 终态 elapsed 固定，不再随刷新继续增长；真实任务与隔离测试任务分栏、任务列表分页。

## 3. 三节点健康

| 节点 | GPU 推理 | Asset Worker | Codex CLI | RetopoFlow |
|---|---|---|---|---|
| control-4090 | ONLINE / ACTIVE | ONLINE，2 槽 | `0.146.0-alpha.3.1`，AUTHENTICATED / HEALTHY | HEALTHY |
| worker-3090-a | ONLINE / ACTIVE | ONLINE，3 槽 | `0.146.0-alpha.3.1`，AUTHENTICATED / HEALTHY | HEALTHY |
| worker-3090-b | ONLINE / ACTIVE | ONLINE，4 槽 | `0.146.0-alpha.3.1`，AUTHENTICATED / HEALTHY | HEALTHY |

RetopoFlow 固定 revision：`ac2570c5292c1dd90190fd3641b4dbc42cf4bd63`。

## 4. 真实验收数据

### Asset

- UV：真实历史失败 FBX，Worker 1.2.1，6/6 `SUCCEEDED`，全部 attempt=1；三台 Worker 均参与。
- 重拓扑：真实 BLEND + 4 张参考图，3/3 `SUCCEEDED`，全部 attempt=1；每单 23 件制品，
  SSE 事件 20 / 21 / 24 条，耗时 177 / 177 / 245 秒。
- 制品校验：响应体 SHA-256、API `sha256`、`X-Artifact-SHA256` 三方一致。

### GPU 7:3 混合压力

- 10 个独立测试客户、并发 10、目标提交 5 RPS；随机混合 7 个抠图和 3 个局部重绘。
- 接收 10/10、完成 10/10、失败 0、输出校验失败 0、限流重试 0、全部 attempt=1。
- 节点分配：4090=5、3090-A=3、3090-B=2；三台均真实执行。
- 提交延迟 p50/p95：0.868 / 1.866 秒。
- 排队延迟 p50/p95/max：78.603 / 220.836 / 220.836 秒。
- 端到端 p50/p95/max：189.614 / 273.870 / 273.870 秒。
- 生产队列在压测期间保持 0；测试客户和 Key 在验收后全部停用。

## 5. 代码与页面审计

- Ruff：通过。
- Python：93/93 测试通过，耗时 51.60 秒。
- Web：lint、生产构建、Vitest 3/3 通过。
- 浏览器：真实 HTTPS `/codex`、`/asset-processing` 检查，console error=0。
- `git diff --check`：通过。
- 未发现被提交的 API Key、密码、Bearer token 或生产密钥。

## 6. 镜像

- Asset Worker：`li3d/blender-worker:1.2.1`
- Image ID：`sha256:737f182435d6cb25e1b2c574ed5eedeb587da9bf043cf6aa49fe1c19cea95459`
- 正式归档：`/srv/gpu-control/images/li3d-blender-worker-1.2.1.tar.zst`
- Git LFS：`artifacts/asset-worker/1.2.1/li3d-blender-worker-1.2.1.tar.zst.part-00`
- 压缩归档 SHA-256：`7683a3da2bf3c33a27d2f2ce74878bcbfd479dda58d7bcc677c4e1e1b3634bde`
- Web：`gpu-control-web:1.5.1`（完整 Image ID 在发布前由 `docker image inspect` 固化）。
- Web Image ID：`sha256:8f6c9659dc6bd9013e1394af0dfa8e122aa1506e6a15171ddecbc6eddf244ede`

## 7. 机器可读证据

- `output/acceptance/asset-retopo-121.json`
- `output/acceptance/closure-20260729-1345-7to3.json`

客户端 API 合同与调用样例见 `58_2026-07-29_ASSET_V4_CLIENT_HANDOFF_AND_STABILITY.md`。
