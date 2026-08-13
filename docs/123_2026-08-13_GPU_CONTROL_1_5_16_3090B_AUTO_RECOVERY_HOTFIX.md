# GPU Control 1.5.16：3090-B 烘焙排空自动恢复热修

日期：2026-08-13  
源码 revision：`3f0084b86436ac735cb7dbf50def4901b8568710`  
状态：`PRODUCTION_DEPLOYED / VERIFIED`

## 1. 故障现象

3090-B 在生产烘焙结束后可能长时间显示 `DRAINING/排空中`。调度器已经能按 5 分钟策略忽略过期
Substance 标签，但数据库的 Node mode 和旧标签不一定被主动回写，WebUI 因而显示为仍在排空，且
没有明确的自动恢复时间或回收状态。

## 2. 根因

过期清理原本位于 Windows Baker 的任务 claim 深层路径。空闲 Agent 心跳可能在 claim 的资源、身份
或并发门禁之前返回，不执行 reservation reconciliation。因此没有新烘焙 claim 时，过期软保护可能
一直等待下一次 claim 或管理员操作才被清理。

## 3. 修复

- 每次健康 Substance Baker 心跳都在同一个 Node 行锁事务内执行 reservation reconciliation；
- 无活动任务且 5 分钟软保护到期时，自动删除 `gpu_specialization` 和 Asset API drain owner，并将
  `DRAINING` 原子恢复为 `ACTIVE`；
- 活动 fence、待领取 reservation、recovery-required、GPU busy、外来队列仍保持 fail-closed；
- 管理员空闲解除同时清理已过期但仍残留的 Substance 标签；
- WebUI 显示预计自动恢复时刻；若处于清理交接，显示“下一次健康心跳自动恢复”，不再让用户误判；
- ImageClip、ModelView、粗糙度、Substance 业务管线和输出合同均未修改。

## 4. 验证

- 新增回归：旧 15 分钟标签按 5 分钟钳制后，健康 Baker 心跳自动恢复 `ACTIVE`；
- 原有 orphan Baker fail-closed、v7 claim、管理员硬门禁测试通过；
- 调度单元测试、全仓 Ruff 通过；
- Web：18/18、ESLint、生产构建通过；
- 零 GPU Job、零 Asset Job 安全窗口滚动；四个控制服务均 healthy；
- 生产 Baker 心跳已自动清除 3090-B 的过期 specialization；
- 发布后四个 GPU 节点均 `ONLINE/ACTIVE`，四个 GPU current_jobs 均为 0；
- API 与 Asset API 均返回 `1.5.16`、revision 精确匹配、`version_aligned=true`、
  `provenance_complete=true`；Web HTTPS 返回 200。

## 5. Docker 归档

四个控制面镜像的不可变 ID、合并包 SHA-256、分片和恢复命令见：

`artifacts/control-plane/1.5.16/release-parts/README.md`

本热修不需要重启 3090-B Windows、WSL、Baker、ComfyUI，也不替换外部管线。
